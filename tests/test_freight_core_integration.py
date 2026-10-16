"""Bounded W2-b live/batch delegation; only synthetic redirected producer I/O."""
from dataclasses import asdict, replace
import inspect
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from scripts.analyze import freight_cost_core as core
from scripts.analyze import intermodal_cost_engine as engine
from server.dependencies import get_data_store
from server.models.schemas import CostParametersOverride, FreightCostSimulationRequest
from server.routers import simulation


ROW = {
    "state": "Maharashtra", "district": "Synthetic", "district_code": 521,
    "pop_2011": 1234, "nearest_port_name": "Synthetic Port", "is_island": False,
    "port_road_distance_km": 725.1234, "port_drive_time_hours": 11.2345,
    "freight_terminal_road_distance_km": 30.,
}
ROUTE = ["port_road_distance_km", "port_drive_time_hours"]
COLUMNS = ["state", "district", "district_code", "pop_2011", "is_island", "target_port",
           "road_distance_km", "road_drive_time_hours", "road_cost_per_ton_inr",
           "rail_cost_per_ton_inr", "dfc_cost_per_ton_inr", "optimal_mode",
           "modal_shift_savings_inr", "modal_shift_savings_pct", "break_even_distance_km"]


@pytest.fixture
def interfaces():
    store = SimpleNamespace(travel_time_df=pd.DataFrame([ROW]), port_matrix_df=None)
    app = FastAPI()
    app.include_router(simulation.router)
    app.dependency_overrides[get_data_store] = lambda: store
    with TestClient(app, raise_server_exceptions=False) as client:
        yield store, client


@pytest.fixture
def batch(tmp_path, monkeypatch):
    root = tmp_path / "custom-data"
    (root / "analysis").mkdir(parents=True)
    source = root / "analysis/nh_district_travel_time_summary.csv"
    source.write_text("synthetic fixture supplied by read_csv spy", encoding="utf-8")
    output = tmp_path / "modal.csv"
    # Compatibility setup also exercises the old calculation body for actual red.
    monkeypatch.setattr(engine, "DATA_DIR", root)

    def run(frame, params=None):
        def read(path, *args, **kwargs):
            assert Path(path) == source
            return frame.copy(deep=True)
        monkeypatch.setattr(engine.pd, "read_csv", read)
        keywords = {"data_dir": root} if "data_dir" in inspect.signature(engine.compute_modal_costs).parameters else {}
        return engine.compute_modal_costs(params, output, **keywords)
    return run, root, output


def live(frame, **kwargs):
    return simulation.simulate_freight_cost(FreightCostSimulationRequest(
        origin_district_code=521, **kwargs), SimpleNamespace(travel_time_df=frame, port_matrix_df=None))


def expected(unit, shipment, state, payload):
    def breakdown(mode, total):
        return simulation.ModalCostBreakdown(cost_per_ton_inr=round(mode.cost_per_ton_inr, 1),
            total_shipment_cost_inr=round(total, 1), transit_time_hours=round(mode.transit_time_hours, 1))
    return dict(origin_district="Synthetic", state=state, target_port="Synthetic Port",
        road_distance_km=round(unit.road.distance_km, 1), road=breakdown(unit.road, shipment.road),
        conventional_rail=breakdown(unit.conventional_rail, shipment.conventional_rail),
        dfc_rail=None if unit.dfc_rail is None else breakdown(unit.dfc_rail, shipment.dfc_rail),
        optimal_mode=unit.optimal_mode,
        modal_shift_savings_per_ton_inr=round(unit.modal_shift_savings_per_ton_inr, 1),
        modal_shift_savings_total_inr=round(shipment.modal_shift_savings_total_inr, 1),
        modal_shift_savings_pct=round(unit.modal_shift_savings_pct, 1),
        break_even_distance_km=unit.break_even_distance_km)


@pytest.mark.parametrize("columns", [ROUTE[:1], ROUTE[1:], ROUTE])
def test_missing_required_columns_live_503_batch_source_failure(interfaces, batch, columns):
    store, client = interfaces
    frame = store.travel_time_df.drop(columns=columns)
    store.travel_time_df = frame
    assert client.post("/simulate/freight-cost", json={"origin_district_code": 521}).status_code == 503
    run, _, output = batch
    with pytest.raises(core.SourceValidationError):
        run(frame)
    assert not output.exists()


@pytest.mark.parametrize("terminal", ["absent", None, float("nan"), pd.NA, 150.])
@pytest.mark.parametrize("state", [" maharashtra ", "Orissa", "Tamil Nadu"])
@pytest.mark.parametrize("overrides", [{}, {"truck_payload_tons": 30., "road_linehaul_rate": 2.5,
    "inventory_holding_rate": 8., "road_handling_cost": None}])
def test_displayed_financial_parity_fallback_state_quantity(batch, terminal, state, overrides):
    row = {**ROW, "state": state, "freight_terminal_road_distance_km": terminal}
    if terminal == "absent" if isinstance(terminal, str) else False:
        del row["freight_terminal_road_distance_km"]
    frame = pd.DataFrame([row])
    run, _, _ = batch
    result = run(frame, core.resolve_parameters(overrides)).iloc[0]
    canonical = core.normalize_freight_state(state)
    term = None if "freight_terminal_road_distance_km" not in row or pd.isna(terminal) else terminal
    unit = core.calculate_unit_costs(ROW[ROUTE[0]], ROW[ROUTE[1]], term, canonical, overrides)
    shipment = core.scale_shipment(unit, 37.)
    response = live(frame, payload_tons=37., custom_parameters=overrides)
    assert response == expected(unit, shipment, canonical, 37.)
    assert result.state == state  # normalization affects calculation, not displayed batch identity
    assert result.district == ROW["district"] and result.district_code == 521 and result.pop_2011 == 1234
    assert result.target_port == ROW["nearest_port_name"]
    assert result.road_distance_km == response["road_distance_km"]
    assert result.road_drive_time_hours == round(ROW[ROUTE[1]], 2)
    for column, name in [("road_cost_per_ton_inr", "road"), ("rail_cost_per_ton_inr", "conventional_rail"),
                         ("dfc_cost_per_ton_inr", "dfc_rail")]:
        if getattr(unit, name) is None:
            assert pd.isna(result[column]) and response[name] is None
        else:
            assert result[column] == response[name].cost_per_ton_inr
            assert response[name].total_shipment_cost_inr == round(getattr(shipment, name), 1)
            assert abs(response[name].cost_per_ton_inr - getattr(unit, name).cost_per_ton_inr) <= .05
    assert result.optimal_mode == response["optimal_mode"]
    assert result.modal_shift_savings_inr == response["modal_shift_savings_per_ton_inr"]
    assert result.modal_shift_savings_pct == response["modal_shift_savings_pct"]
    assert result.break_even_distance_km == response["break_even_distance_km"]


@pytest.mark.parametrize("distance", [0., 32.5, 150.])
@pytest.mark.parametrize("zero", [False, True])
def test_zero_distance_zero_baseline_ties(batch, distance, zero):
    row = {**ROW, ROUTE[0]: distance, ROUTE[1]: 0.}
    params = core.FreightCostParameters()
    if zero:
        positive = {"truck_payload_tons", "toll_spacing_km", "rail_commercial_speed_kmh",
                    "dfc_commercial_speed_kmh", "rail_circuity_factor", "dfc_circuity_factor"}
        params = core.FreightCostParameters(**{k: 0. for k in asdict(params) if k not in positive})
    run, _, _ = batch
    frame = pd.DataFrame([row])
    result = run(frame, params).iloc[0]
    response = live(frame, custom_parameters={k: v for k, v in asdict(params).items()
                                             if k in CostParametersOverride.model_fields})
    # Road wins the actual zero-cost ties, not every positive default trip.
    expected_mode = "Road Trucking" if zero or distance < 150. else "Dedicated Freight Corridor (DFC)"
    assert result.optimal_mode == response["optimal_mode"] == expected_mode
    assert result.road_cost_per_ton_inr == response["road"].cost_per_ton_inr
    assert result.modal_shift_savings_pct == response["modal_shift_savings_pct"]
    assert result.modal_shift_savings_inr == response["modal_shift_savings_per_ton_inr"]
    if expected_mode == "Road Trucking":
        assert result.modal_shift_savings_pct == result.modal_shift_savings_inr == 0.
    else:
        assert result.modal_shift_savings_pct > 0. and result.modal_shift_savings_inr > 0.


@pytest.mark.parametrize("field", ROUTE + ["freight_terminal_road_distance_km"])
@pytest.mark.parametrize("value", [True, False, "1", -1., float("inf"), -float("inf")])
def test_corrupt_stored_numbers_fail_live_and_whole_batch(interfaces, batch, field, value):
    store, client = interfaces
    frame = pd.DataFrame([ROW, {**ROW, "district": "Corrupt", "district_code": 522, field: value}], dtype=object)
    store.travel_time_df = frame
    assert client.post("/simulate/freight-cost", json={"origin_district_code": 522}).status_code == 503
    run, _, output = batch
    output.write_bytes(b"prior output")
    with pytest.raises(core.SourceValidationError):
        run(frame)
    assert output.read_bytes() == b"prior output"
    assert sorted(p.name for p in output.parent.iterdir()) == ["custom-data", "modal.csv"]


@pytest.mark.parametrize("field", ROUTE)
@pytest.mark.parametrize("value", [None, float("nan"), pd.NA])
def test_partial_null_live_503_batch_corruption_without_write(interfaces, batch, field, value):
    store, client = interfaces
    frame = pd.DataFrame([{**ROW, field: value}], dtype=object)
    store.travel_time_df = frame
    assert client.post("/simulate/freight-cost", json={"origin_district_code": 521}).status_code == 503
    run, _, output = batch
    output.write_bytes(b"prior output")
    with pytest.raises(core.SourceValidationError):
        run(frame)
    assert output.read_bytes() == b"prior output"


@pytest.mark.parametrize("value", [None, float("nan"), pd.NA])
def test_paired_null_route_is_unreachable_not_island(batch, value):
    frame = pd.DataFrame([{**ROW, ROUTE[0]: value, ROUTE[1]: value}], dtype=object)
    run, _, _ = batch
    result = run(frame).iloc[0]
    assert not result.is_island and result.optimal_mode == "N/A (Island / Isolated)"
    assert result.district_code == 521 and result.pop_2011 == 1234
    for column in COLUMNS[6:11] + COLUMNS[12:]:
        assert pd.isna(result[column])


@pytest.mark.parametrize("flag", [pd.NA, float("nan"), None, False])
def test_nullable_island_flag_does_not_invent_island(batch, flag):
    run, _, _ = batch
    result = run(pd.DataFrame([{**ROW, "is_island": flag}], dtype=object)).iloc[0]
    assert not result.is_island
    assert result.optimal_mode != "N/A (Island / Isolated)"


def test_geographic_islands_and_disconnected_all_na_rows_preserved(batch):
    run, _, output = batch
    rows = [{**ROW, "state": "andaman & nicobar", "district": "Original Island", "district_code": 638,
             "is_island": pd.NA, ROUTE[0]: None, ROUTE[1]: None, "nearest_port_name": None},
            {**ROW, "district": "Disconnected", "district_code": 522, ROUTE[0]: None, ROUTE[1]: None}]
    result = run(pd.DataFrame(rows, dtype=object))
    assert len(result) == 2 and result.district.tolist() == [r["district"] for r in rows]
    assert result.state.tolist() == [r["state"] for r in rows]
    assert result.district_code.tolist() == [638, 522] and result.pop_2011.tolist() == [1234, 1234]
    assert result.is_island.tolist() == [True, False]
    assert result.optimal_mode.tolist() == ["N/A (Island / Isolated)"] * 2
    assert pd.isna(result.iloc[0].target_port)
    assert list(result.columns) == COLUMNS
    assert result[COLUMNS[6:11] + COLUMNS[12:]].isna().all().all()
    assert output.exists()


@pytest.mark.parametrize("field", ROUTE + ["freight_terminal_road_distance_km"])
def test_island_and_unreachable_corruption_not_silently_skipped(batch, field):
    run, _, output = batch
    rows = [{**ROW, "state": "Lakshadweep", "is_island": True, ROUTE[0]: None, field: -1.}]
    with pytest.raises(core.SourceValidationError):
        run(pd.DataFrame(rows))
    assert not output.exists()


def test_missing_stored_code_column_is_503(interfaces):
    store, client = interfaces
    store.travel_time_df = store.travel_time_df.drop(columns="district_code")
    assert client.post("/simulate/freight-cost", json={"origin_district_code": 521}).status_code == 503


@pytest.mark.parametrize("overrides", [{"road_linehaul_rate": 1e308}, {"toll_spacing_km": 1e-308},
                                       {"rail_commercial_speed_kmh": 1e-308}])
def test_calculation_overflow_live_422_batch_categorized_no_write(interfaces, batch, overrides):
    _, client = interfaces
    assert client.post("/simulate/freight-cost", json={"origin_district_code": 521,
        "custom_parameters": overrides}).status_code == 422
    run, _, output = batch
    with pytest.raises(core.CalculationError):
        run(pd.DataFrame([ROW]), overrides)
    assert not output.exists()


def test_shipment_overflow_stays_422(interfaces):
    _, client = interfaces
    assert client.post("/simulate/freight-cost", json={"origin_district_code": 521, "payload_tons": 1e308}).status_code == 422


@pytest.mark.parametrize("nested", [False, True])
def test_mutated_typed_request_revalidated_before_source(nested):
    req = FreightCostSimulationRequest(origin_district_code=521, custom_parameters={})
    if nested:
        req.custom_parameters.__dict__["truck_payload_tons"] = 0.
    else:
        req.__dict__["payload_tons"] = -1.
    with pytest.raises(HTTPException) as exc:
        simulation.simulate_freight_cost(req, SimpleNamespace(travel_time_df=None))
    assert exc.value.status_code == 422


@pytest.mark.parametrize("error,status", [(core.SourceValidationError, 503), (core.ParameterValidationError, 422),
    (core.ShipmentValidationError, 422), (core.CalculationError, 422), (ValueError, 500), (RuntimeError, 500)])
def test_core_categories_no_message_heuristics(interfaces, monkeypatch, error, status):
    _, client = interfaces
    def fail(*args, **kwargs):
        raise error("private source path and misleading parameter text")
    monkeypatch.setattr(core, "calculate_unit_costs", fail)
    response = client.post("/simulate/freight-cost", json={"origin_district_code": 521})
    assert response.status_code == status
    assert "private" not in response.text


def test_engine_output_validation_is_503_not_request_422(interfaces, monkeypatch):
    _, client = interfaces
    original = core.calculate_unit_costs
    def bad(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, road=replace(result.road, transit_time_hours=float("nan")))
    monkeypatch.setattr(core, "calculate_unit_costs", bad)
    assert client.post("/simulate/freight-cost", json={"origin_district_code": 521}).status_code == 503


def test_both_adapters_delegate_core_spies_and_scale_quantity(batch, monkeypatch):
    calls = {name: [] for name in ("calculate_unit_costs", "normalize_freight_state", "scale_shipment")}
    for name in calls:
        original = getattr(core, name)
        def spy(*args, _name=name, _original=original, **kwargs):
            calls[_name].append((args, kwargs))
            return _original(*args, **kwargs)
        monkeypatch.setattr(core, name, spy)
    run, _, _ = batch
    run(pd.DataFrame([ROW]))
    batch_counts = {name: len(values) for name, values in calls.items()}
    assert batch_counts["calculate_unit_costs"] == 1 and batch_counts["normalize_freight_state"] >= 1
    response = live(pd.DataFrame([ROW]), payload_tons=37., custom_parameters={"truck_payload_tons": 30.})
    assert len(calls["calculate_unit_costs"]) == 2
    assert len(calls["normalize_freight_state"]) > batch_counts["normalize_freight_state"]
    assert calls["scale_shipment"][-1][0][1] == 37.
    unit = calls["scale_shipment"][-1][0][0]
    assert response["road"].total_shipment_cost_inr == round(unit.road.cost_per_ton_inr * 37., 1)


def test_custom_root_no_repo_fallback_positional_signature_reexports(tmp_path):
    signature = inspect.signature(engine.compute_modal_costs)
    assert list(signature.parameters) == ["params", "output_csv", "data_dir"]
    assert signature.parameters["data_dir"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["params"].default is None and signature.parameters["output_csv"].default is None
    assert engine.FreightCostParameters is core.FreightCostParameters
    assert engine.ir_telescopic_freight_rate is core.ir_telescopic_freight_rate
    with pytest.raises(core.SourceValidationError):
        engine.compute_modal_costs(None, tmp_path / "modal.csv", data_dir=tmp_path / "absent")
    assert not (tmp_path / "modal.csv").exists()


def test_actual_synthetic_csv_atomic_output_and_circuity(tmp_path, monkeypatch):
    root = tmp_path / "custom"
    (root / "analysis").mkdir(parents=True)
    pd.DataFrame([ROW]).to_csv(root / "analysis/nh_district_travel_time_summary.csv", index=False)
    output = tmp_path / "out.csv"
    output.write_bytes(b"previous")
    monkeypatch.setattr(engine, "DATA_DIR", tmp_path / "do-not-use")
    params = core.FreightCostParameters(rail_circuity_factor=2., dfc_circuity_factor=3.)
    result = engine.compute_modal_costs(params, output, data_dir=root)
    unit = core.calculate_unit_costs(ROW[ROUTE[0]], ROW[ROUTE[1]], 30., "Maharashtra", params)
    assert result.iloc[0].rail_cost_per_ton_inr == round(unit.conventional_rail.cost_per_ton_inr, 1)
    assert result.iloc[0].dfc_cost_per_ton_inr == round(unit.dfc_rail.cost_per_ton_inr, 1)
    pd.testing.assert_frame_equal(result, pd.read_csv(output))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["custom", "out.csv"]


def test_atomic_write_failure_keeps_previous_output_and_cleans_temp(batch, monkeypatch):
    run, _, output = batch
    output.write_bytes(b"prior")
    def fail(self, path, **kwargs):
        Path(path).write_bytes(b"partial")
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(pd.DataFrame, "to_csv", fail)
    with pytest.raises(OSError):
        run(pd.DataFrame([ROW]))
    assert output.read_bytes() == b"prior"
    assert sorted(p.name for p in output.parent.iterdir()) == ["custom-data", "modal.csv"]
