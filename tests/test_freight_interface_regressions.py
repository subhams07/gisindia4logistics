"""Bounded freight interface parity and financial validation regressions."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from server.services.error_contract import domain_error_from_status, route_error_contract
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from gisindia4logistics import cli, sdk
from server.dependencies import get_data_store
from server.models.schemas import CostParametersOverride, FreightCostSimulationRequest
from server.routers.simulation import router


@pytest.fixture
def interfaces(monkeypatch):
    row = {
        "district": "Pune", "state": "Maharashtra", "district_code": 521,
        "nearest_port_name": "JNPT", "port_road_distance_km": 150.0,
        "port_drive_time_hours": 3.0, "freight_terminal_road_distance_km": 30.0,
        "is_island": False,
    }
    store = SimpleNamespace(travel_time_df=pd.DataFrame([row]), port_matrix_df=pd.DataFrame([
        {**row, "drive_hours_to_paradip": 20.0, "road_km_to_paradip": 1250.0}
    ]))
    monkeypatch.setattr(sdk, "get_data_store", lambda: store)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_data_store] = lambda: store
    with TestClient(app) as client:
        yield store, client


@pytest.mark.parametrize("payload", [20.0, 40.0])
@pytest.mark.parametrize("overrides", [{}, {"road_linehaul_rate": 2.5, "truck_payload_tons": 30.0}])
@pytest.mark.parametrize("target", [None, "Paradip Port"])
def test_rest_sdk_parity(interfaces, payload, overrides, target):
    _, client = interfaces
    request = {"origin_district": "Pune", "payload_tons": payload, "target_port": target}
    if overrides:
        request["custom_parameters"] = overrides
    response = client.post("/simulate/freight-cost", json=request)
    assert response.status_code == 200
    assert sdk.calculate_freight_cost("Pune", target_port=target, payload_tons=payload, **overrides) == response.json()


def test_mass_does_not_change_unit_cost(interfaces):
    twenty = sdk.calculate_freight_cost("Pune", payload_tons=20)
    forty = sdk.calculate_freight_cost("Pune", payload_tons=40)
    assert forty["road"]["cost_per_ton_inr"] == twenty["road"]["cost_per_ton_inr"]
    assert forty["road"]["total_shipment_cost_inr"] == 2 * twenty["road"]["total_shipment_cost_inr"]


@pytest.mark.parametrize("identity,status", [
    ({"origin_district": "Pune", "origin_district_code": 999}, 422),
    ({"origin_district": "Wrong", "origin_district_code": 521}, 422),
    ({"origin_district_code": 999}, 404),
    ({"origin_district": "Pune", "origin_state": "Gujarat"}, 422),
])
def test_contradictory_identity(interfaces, identity, status):
    _, client = interfaces
    assert client.post("/simulate/freight-cost", json=identity).status_code == status
    with pytest.raises(type(domain_error_from_status(status))) as exc:
        sdk.calculate_freight_cost(**identity)
    assert route_error_contract(exc.value)["status"] == status


@pytest.mark.parametrize("code", [0, -1])
def test_nonpositive_id(interfaces, code):
    _, client = interfaces
    assert client.post("/simulate/freight-cost", json={"origin_district": "Pune", "origin_district_code": code}).status_code == 422


@pytest.mark.parametrize("identity", [{"origin_district": "Pune"}, {"origin_district_code": 521}])
def test_duplicate_identity(interfaces, identity):
    store, client = interfaces
    duplicate = {**store.travel_time_df.iloc[0].to_dict(), "state": "Gujarat"}
    store.travel_time_df = pd.concat([store.travel_time_df, pd.DataFrame([duplicate])], ignore_index=True)
    assert client.post("/simulate/freight-cost", json=identity).status_code == 409
    response = client.post("/simulate/freight-cost", json={**identity, "origin_state": "Maharashtra"})
    assert response.status_code == 200
    assert response.json()["state"] == "Maharashtra"
    store.travel_time_df = pd.concat([store.travel_time_df, store.travel_time_df.iloc[[0]]], ignore_index=True)
    assert client.post("/simulate/freight-cost", json={**identity, "origin_state": "Maharashtra"}).status_code == 409


@pytest.mark.parametrize("field", list(CostParametersOverride.model_fields))
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), -1.0])
def test_invalid_financial_overrides(field, value):
    with pytest.raises(ValidationError):
        CostParametersOverride(**{field: value})


@pytest.mark.parametrize("field", ["truck_payload_tons", "toll_spacing_km", "rail_commercial_speed_kmh", "dfc_commercial_speed_kmh"])
def test_zero_divisors(field):
    with pytest.raises(ValidationError):
        CostParametersOverride(**{field: 0})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 0, -1])
def test_invalid_mass(interfaces, value):
    with pytest.raises(ValidationError):
        FreightCostSimulationRequest(origin_district="Pune", payload_tons=value)
    with pytest.raises(ValidationError):
        sdk.calculate_freight_cost("Pune", payload_tons=value)


@pytest.mark.parametrize("field", ["payload_tons", "road_linehaul_rate", "truck_payload_tons"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_rest_nonfinite_strings(interfaces, field, value):
    _, client = interfaces
    body = {"origin_district": "Pune"}
    if field == "payload_tons":
        body[field] = value
    else:
        body["custom_parameters"] = {field: value}
    assert client.post("/simulate/freight-cost", json=body).status_code == 422


def test_zero_road_baseline(interfaces):
    _, client = interfaces
    overrides = {"road_linehaul_rate": 0, "road_handling_cost": 0,
                 "toll_cost_per_plaza": 0, "inventory_holding_rate": 0}
    response = client.post("/simulate/freight-cost", json={"origin_district": "Pune", "custom_parameters": overrides})
    assert response.status_code == 200
    data = response.json()
    assert data["optimal_mode"] == "Road Trucking"
    for field in ["modal_shift_savings_pct", "modal_shift_savings_per_ton_inr", "modal_shift_savings_total_inr"]:
        assert data[field] == 0
    json.dumps(data, allow_nan=False)


@pytest.mark.parametrize("field", ["port_road_distance_km", "port_drive_time_hours", "freight_terminal_road_distance_km"])
@pytest.mark.parametrize("value", [float("inf"), -1.0])
def test_bad_source_data(interfaces, field, value):
    store, client = interfaces
    store.travel_time_df.loc[0, field] = value
    assert client.post("/simulate/freight-cost", json={"origin_district": "Pune"}).status_code == 503


def test_overflow_not_success(interfaces):
    _, client = interfaces
    assert client.post("/simulate/freight-cost", json={"origin_district": "Pune", "payload_tons": 1e308}).status_code == 422


def test_matrix_code_mismatch(interfaces):
    store, client = interfaces
    store.port_matrix_df.loc[0, "district_code"] = 999
    assert client.post("/simulate/freight-cost", json={"origin_district": "Pune", "target_port": "Paradip Port"}).status_code == 404


def test_cli_parameters(interfaces, monkeypatch, capsys):
    _, client = interfaces
    monkeypatch.setattr("sys.argv", ["gisindia4logistics", "cost", "--origin", "Pune", "--origin-code", "521",
                                    "--state", "Maharashtra", "--payload", "40", "--truck-payload", "30",
                                    "--road-rate", "2.5", "--rail-rate", "1.7", "--inventory-rate", "8"])
    cli.main()
    output = capsys.readouterr().out
    response = client.post("/simulate/freight-cost", json={"origin_district": "Pune", "origin_district_code": 521,
                           "origin_state": "Maharashtra", "payload_tons": 40, "custom_parameters": {
                               "truck_payload_tons": 30, "road_linehaul_rate": 2.5,
                               "rail_base_class_rate": 1.7, "inventory_holding_rate": 8}})
    assert f"{response.json()['road']['cost_per_ton_inr']:,.2f}" in output
    assert "40.0 Tonnes" in output


@pytest.mark.parametrize("args", [["--payload", "NaN"], ["--road-rate", "Infinity"], ["--truck-payload", "0"],
                                  ["--payload", "0"], ["--origin-code", "999"], ["--dfc-rate", "-1"]])
def test_cli_invalid_input(interfaces, monkeypatch, capsys, args):
    monkeypatch.setattr("sys.argv", ["gisindia4logistics", "cost", "--origin", "Pune", *args])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "error:" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("field", list(CostParametersOverride.model_fields))
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", -1])
def test_override_bounds_rest_sdk(interfaces, field, value):
    _, client = interfaces
    body = {"origin_district": "Pune", "custom_parameters": {field: value}}
    assert client.post("/simulate/freight-cost", json=body).status_code == 422
    with pytest.raises(ValidationError):
        sdk.calculate_freight_cost("Pune", **{field: value})


@pytest.mark.parametrize("field", list(CostParametersOverride.model_fields))
def test_zero_override_bounds_rest_sdk(interfaces, field):
    _, client = interfaces
    divisors = {"truck_payload_tons", "toll_spacing_km", "rail_commercial_speed_kmh", "dfc_commercial_speed_kmh"}
    body = {"origin_district": "Pune", "custom_parameters": {field: 0}}
    response = client.post("/simulate/freight-cost", json=body)
    if field in divisors:
        assert response.status_code == 422
        with pytest.raises(ValidationError):
            sdk.calculate_freight_cost("Pune", **{field: 0})
    else:
        assert response.status_code == 200
        assert sdk.calculate_freight_cost("Pune", **{field: 0}) == response.json()


@pytest.mark.parametrize("overrides", [
    {"road_linehaul_rate": 1e308}, {"truck_payload_tons": 1e-308},
    {"toll_spacing_km": 1e-308}, {"rail_commercial_speed_kmh": 1e-308},
    {"dfc_commercial_speed_kmh": 1e-308},
])
def test_finite_override_overflow_rejected(interfaces, overrides):
    _, client = interfaces
    response = client.post("/simulate/freight-cost", json={"origin_district": "Pune", "custom_parameters": overrides})
    assert response.status_code == 422
    with pytest.raises(type(domain_error_from_status(422))) as exc:
        sdk.calculate_freight_cost("Pune", **overrides)
    assert route_error_contract(exc.value)["status"] == 422


@pytest.mark.parametrize("payload", [20, 40])
@pytest.mark.parametrize("capacity", [None, 30])
@pytest.mark.parametrize("port", [None, "Paradip Port"])
def test_cli_full_result_parity(interfaces, monkeypatch, capsys, payload, capacity, port):
    _, client = interfaces
    actual = sdk.calculate_freight_cost
    captured = []

    def record_result(**kwargs):
        result = actual(**kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(sdk, "calculate_freight_cost", record_result)
    argv = ["gisindia4logistics", "cost", "--origin-code", "521", "--payload", str(payload)]
    body = {"origin_district_code": 521, "payload_tons": payload}
    if capacity is not None:
        argv += ["--truck-payload", str(capacity)]
        body["custom_parameters"] = {"truck_payload_tons": capacity}
    if port is not None:
        argv += ["--port", port]
        body["target_port"] = port
    monkeypatch.setattr("sys.argv", argv)
    cli.main()
    response = client.post("/simulate/freight-cost", json=body)
    assert response.status_code == 200
    assert captured == [response.json()]
    output = capsys.readouterr().out
    assert f"{payload:.1f} Tonnes" in output
    for mode in ["road", "conventional_rail", "dfc_rail"]:
        assert f"{captured[0][mode]['total_shipment_cost_inr']:,.2f}" in output


def test_legacy_sdk_reexports_canonical_functions(interfaces):
    from gis4logistics import sdk as legacy

    for name in legacy.__all__:
        assert getattr(legacy, name) is getattr(sdk, name)
    assert legacy.calculate_freight_cost("Pune", None, 40, 2.5, 340, 1.7, 1.12, 8) == sdk.calculate_freight_cost(
        "Pune", payload_tons=40, road_linehaul_rate=2.5, toll_cost_per_plaza=340,
        rail_base_class_rate=1.7, dfc_linehaul_rate=1.12, inventory_holding_rate=8,
    )


def test_nested_sdk_json_serialization():
    class Observation(BaseModel):
        observed_at: datetime

    observation = Observation(observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    result = sdk._to_dict({"observations": [observation]})
    assert result == {"observations": [{"observed_at": "2026-01-01T00:00:00Z"}]}
    json.dumps(result, allow_nan=False)


def test_zero_baseline_sdk_parity(interfaces):
    _, client = interfaces
    overrides = {"road_linehaul_rate": 0, "road_handling_cost": 0,
                 "toll_cost_per_plaza": 0, "inventory_holding_rate": 0}
    response = client.post("/simulate/freight-cost", json={"origin_district": "Pune", "custom_parameters": overrides})
    assert sdk.calculate_freight_cost("Pune", **overrides) == response.json()


@pytest.mark.parametrize("value", [None, float("nan")])
def test_missing_terminal_distance_keeps_existing_fallback(interfaces, value):
    store, client = interfaces
    store.travel_time_df.loc[0, "freight_terminal_road_distance_km"] = value
    response = client.post("/simulate/freight-cost", json={"origin_district": "Pune"})
    assert response.status_code == 200
    assert sdk.calculate_freight_cost("Pune") == response.json()


@pytest.mark.parametrize("flag", ["--payload", "--truck-payload", "--road-rate", "--rail-rate",
                                 "--dfc-rate", "--inventory-rate", "--toll-cost"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-1"])
def test_cli_numeric_bounds(interfaces, monkeypatch, capsys, flag, value):
    monkeypatch.setattr("sys.argv", ["gisindia4logistics", "cost", "--origin", "Pune", f"{flag}={value}"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "error:" in captured.err.lower()
    assert not captured.out


def test_non_dfc_response_and_nullable_defaults(interfaces):
    store, client = interfaces
    store.travel_time_df.loc[0, "state"] = "Tamil Nadu"
    response = client.post("/simulate/freight-cost", json={
        "origin_district": "Pune", "payload_tons": None,
        "custom_parameters": {name: None for name in CostParametersOverride.model_fields},
    })
    assert response.status_code == 200
    result = sdk.calculate_freight_cost("Pune", payload_tons=None)
    assert result == response.json()
    assert result["dfc_rail"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("field", ["drive_hours_to_paradip", "road_km_to_paradip"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_target_port_source_bounds(interfaces, field, value):
    store, client = interfaces
    store.port_matrix_df.loc[0, field] = value
    body = {"origin_district": "Pune", "target_port": "Paradip Port"}
    assert client.post("/simulate/freight-cost", json=body).status_code == 503
    with pytest.raises(type(domain_error_from_status(503))) as exc:
        sdk.calculate_freight_cost(**body)
    assert route_error_contract(exc.value)["status"] == 503


@pytest.mark.parametrize("name", ["Bilaspur", "Hamirpur"])
def test_real_freight_state_disambiguation(interfaces, name):
    from pathlib import Path

    store, client = interfaces
    data = Path(__file__).resolve().parents[1] / "data/analysis"
    store.travel_time_df = pd.read_csv(data / "nh_district_travel_time_summary.csv")
    store.port_matrix_df = pd.read_csv(data / "nh_district_port_matrix.csv")
    rows = store.travel_time_df[store.travel_time_df.district.str.lower() == name.lower()]
    assert len(rows) > 1
    assert client.post("/simulate/freight-cost", json={"origin_district": name}).status_code == 409
    for state in rows.state.unique():
        body = {"origin_district": name, "origin_state": f" {state.lower()} "}
        response = client.post("/simulate/freight-cost", json=body)
        assert response.status_code == 200, response.text
        assert sdk.calculate_freight_cost(**body) == response.json()


@pytest.mark.parametrize("code", [498])
def test_real_dhule_freight_code_and_state(interfaces, code):
    from pathlib import Path

    store, client = interfaces
    data = Path(__file__).resolve().parents[1] / "data/analysis"
    store.travel_time_df = pd.read_csv(data / "nh_district_travel_time_summary.csv")
    store.port_matrix_df = pd.read_csv(data / "nh_district_port_matrix.csv")
    body = {"origin_district": "Dhule", "origin_district_code": code,
            "origin_state": " maharashtra ", "target_port": "Paradip Port"}
    response = client.post("/simulate/freight-cost", json=body)
    assert response.status_code == 200, response.text
    assert sdk.calculate_freight_cost(**body) == response.json()


@pytest.mark.parametrize("target", [None, "Paradip Port"])
def test_island_route_remains_422(interfaces, target):
    store, client = interfaces
    store.travel_time_df.loc[0, "state"] = "Lakshadweep"
    store.travel_time_df.loc[0, "port_road_distance_km"] = float("nan")
    body = {"origin_district": "Pune", "target_port": target}
    assert client.post("/simulate/freight-cost", json=body).status_code == 422
    with pytest.raises(type(domain_error_from_status(422))) as exc:
        sdk.calculate_freight_cost(**body)
    assert route_error_contract(exc.value)["status"] == 422


@pytest.mark.parametrize("target,status", [("Port", 409), ("Nonexistent Port", 404), ("!!!", 422)])
def test_target_port_error_mapping(interfaces, target, status):
    _, client = interfaces
    assert client.post("/simulate/freight-cost", json={"origin_district": "Pune", "target_port": target}).status_code == status


def test_freight_state_alias(interfaces):
    store, client = interfaces
    store.travel_time_df.loc[0, "state"] = "Odisha"
    body = {"origin_district": "Pune", "origin_state": " Orissa "}
    response = client.post("/simulate/freight-cost", json=body)
    assert response.status_code == 200
    assert sdk.calculate_freight_cost(**body) == response.json()


@pytest.mark.parametrize("field", ["origin_district", "origin_state", "target_port"])
def test_freight_blank_identifiers(interfaces, field):
    _, client = interfaces
    body = {"origin_district_code": 521, field: " "}
    assert client.post("/simulate/freight-cost", json=body).status_code == 422


@pytest.mark.parametrize("field", ["port_road_distance_km", "port_drive_time_hours"])
def test_missing_mainland_source_is_503_not_island(interfaces, field):
    store, client = interfaces
    store.travel_time_df.loc[0, field] = float("nan")
    assert client.post("/simulate/freight-cost", json={"origin_district": "Pune"}).status_code == 503
