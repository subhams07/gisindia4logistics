"""Bounded carry-forward simulation/map debt, not stage-3 route adapters."""
import json
import math
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from shapely.geometry import Point

from mcp_server import tools
from server.dependencies import get_data_store
from server.models.schemas import FreightCostSimulationResponse, PortMarketShareItem
from server.routers import simulation


@pytest.fixture
def interfaces(monkeypatch):
    row = {"district_code": 521, "district": "Pune", "state": "Maharashtra",
           "nearest_port_name": "JNPT", "port_road_distance_km": 150.0,
           "port_drive_time_hours": 3.0}
    cols = list(simulation.PORT_MATRIX_DATA)
    matrix = pd.DataFrame([
        {"district_code": 521, cols[0]: 5.0, cols[1]: 10.0},
        {"district_code": 522, cols[0]: 10.0, cols[1]: 5.0},
        {"district_code": 523, cols[0]: 0.0, cols[1]: None},
    ])
    store = SimpleNamespace(travel_time_df=pd.DataFrame([row]), port_matrix_df=matrix,
                            districts_df=pd.DataFrame({"district_code": [521, 522, 523],
                                                       "pop_2011": [100000, 200000, 300000]}))
    monkeypatch.setattr(tools, "store", store)
    app = FastAPI()
    app.include_router(simulation.router)
    app.dependency_overrides[get_data_store] = lambda: store
    with TestClient(app, raise_server_exceptions=False) as client:
        yield store, client


def assert_gravity_response(data):
    TypeAdapter(list[PortMarketShareItem]).validate_python(data)
    json.dumps(data, allow_nan=False)
    assert len(data) == 12
    for item in data:
        assert all(math.isfinite(item[key]) for key in (
            "market_share_districts_pct", "captured_population_cr"))


@pytest.mark.parametrize("alpha,beta", [(None, None), (0.85, 1.65), (0, 0), (0, 2), (2, 0), (10, 10)])
def test_gravity_ordinary_zero_defaults_and_full_mcp_parity(interfaces, alpha, beta):
    store, client = interfaces
    response = client.post("/simulate/port-gravity", json={"alpha": alpha, "beta": beta})
    assert response.status_code == 200, response.text
    result = response.json()
    assert_gravity_response(result)
    assert tools.tool_simulate_port_catchment(alpha=alpha, beta=beta) == result
    a = 0.85 if alpha is None else alpha
    b = 1.65 if beta is None else beta
    counts = dict.fromkeys((name for name, _ in simulation.PORT_MATRIX_DATA.values()), 0)
    populations = dict.fromkeys(counts, 0)
    for _, row in store.port_matrix_df.iterrows():
        utility = {name: cap ** a / ((row[col] / 5) ** b)
                   for col, (name, cap) in simulation.PORT_MATRIX_DATA.items()
                   if col in row and pd.notna(row[col]) and row[col] > 0}
        if utility:
            winner = max(utility, key=utility.get)
            counts[winner] += 1
            populations[winner] += {521: 100000, 522: 200000}[row.district_code]
    assert {x["port_name"]: x["captured_districts_count"] for x in result} == counts
    assert {x["port_name"]: x["captured_population_cr"] for x in result} == {
        name: round(pop / 1e7, 2) for name, pop in populations.items()}


@pytest.mark.parametrize("capacity,alpha", [(1e308, 2), (1e-308, 10)])
def test_client_gravity_power_range_is_422(interfaces, capacity, alpha):
    _, client = interfaces
    response = client.post("/simulate/port-gravity", json={"alpha": alpha,
        "custom_port_capacities": {"Paradip Port": capacity}})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("capacity,alpha", [(1e308, 0), (1e308, 1), (1e-308, 1)])
def test_representable_extreme_capacity_and_zero_beta_remain_supported(interfaces, capacity, alpha):
    _, client = interfaces
    response = client.post("/simulate/port-gravity", json={"alpha": alpha, "beta": 0,
        "custom_port_capacities": {"Paradip Port": capacity}})
    assert response.status_code == 200, response.text
    assert_gravity_response(response.json())


def test_custom_capacity_exact_tie_keeps_first_port_order(interfaces):
    store, client = interfaces
    cols = list(simulation.PORT_MATRIX_DATA)
    store.port_matrix_df[cols[1]] = store.port_matrix_df[cols[0]]
    response = client.post("/simulate/port-gravity", json={"alpha": 2, "beta": 2,
        "custom_port_capacities": {"Paradip Port": 100, "Deendayal Port (Kandla)": 100}})
    assert response.status_code == 200
    result = response.json()
    assert_gravity_response(result)
    counts = {item["port_name"]: item["captured_districts_count"] for item in result}
    assert counts["Paradip Port"] == 2
    assert counts["Deendayal Port (Kandla)"] == 0


def test_unknown_capacity_key_is_422_even_without_matrix_column(interfaces):
    _, client = interfaces
    response = client.post("/simulate/port-gravity", json={
        "custom_port_capacities": {"Paradip Port": 150, "Typo Port": 10}})
    assert response.status_code == 422, response.text
    assert "Typo Port" not in response.text


@pytest.mark.parametrize("field", ["alpha", "beta"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", -1, 11])
def test_rest_mcp_gravity_exponent_validation(interfaces, field, value):
    _, client = interfaces
    assert client.post("/simulate/port-gravity", json={field: value}).status_code == 422
    with pytest.raises((HTTPException, ValidationError)):
        tools.tool_simulate_port_catchment(**{field: value})


@pytest.mark.parametrize("value", [float("inf"), -1, "bad", 1e-308, 1e308])
def test_stored_gravity_time_range_is_503_rest_and_mcp(interfaces, value):
    store, client = interfaces
    col = next(iter(simulation.PORT_MATRIX_DATA))
    store.port_matrix_df[col] = pd.Series([value, 5.0, None], dtype=object)
    response = client.post("/simulate/port-gravity", json={"beta": 10})
    assert response.status_code == 503, response.text
    with pytest.raises(HTTPException) as exc:
        tools.tool_simulate_port_catchment(beta=10)
    assert exc.value.status_code == 503


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -1, "bad"])
def test_stored_gravity_population_nonfinite_is_503(interfaces, value):
    store, client = interfaces
    store.districts_df["pop_2011"] = pd.Series([value, 200000, 300000], dtype=object)
    assert client.post("/simulate/port-gravity", json={}).status_code == 503
    with pytest.raises(HTTPException) as exc:
        tools.tool_simulate_port_catchment()
    assert exc.value.status_code == 503


@pytest.mark.parametrize("columns", [None, ["district_code"], ["pop_2011"]])
def test_missing_gravity_population_source_is_503_rest_and_mcp(interfaces, columns):
    store, client = interfaces
    store.districts_df = None if columns is None else store.districts_df[columns]
    response = client.post("/simulate/port-gravity", json={})
    assert response.status_code == 503, response.text
    with pytest.raises(HTTPException) as exc:
        tools.tool_simulate_port_catchment()
    assert exc.value.status_code == 503


@pytest.mark.parametrize("field", ["district", "state", "nearest_port_name"])
@pytest.mark.parametrize("value", [None, float("nan"), "", " ", 123, "missing-column"])
def test_freight_required_source_text_by_explicit_code_is_503(interfaces, field, value):
    store, client = interfaces
    if value == "missing-column":
        store.travel_time_df = store.travel_time_df.drop(columns=field)
    else:
        store.travel_time_df[field] = pd.Series([value], dtype=object)
    response = client.post("/simulate/freight-cost", json={"origin_district_code": 521})
    assert response.status_code == 503, response.text
    assert "521" not in response.text
    with pytest.raises(HTTPException) as exc:
        tools.tool_calculate_intermodal_freight_cost(origin_district_code=521)
    assert exc.value.status_code == 503


def test_freight_supported_numeric_defaults_and_full_mcp_response(interfaces):
    _, client = interfaces
    response = client.post("/simulate/freight-cost", json={"origin_district_code": 521})
    assert response.status_code == 200
    result = response.json()
    FreightCostSimulationResponse.model_validate(result)
    assert tools.tool_calculate_intermodal_freight_cost(origin_district_code=521) == result
    assert result["road"]["cost_per_ton_inr"] == round(150 * 3.3 + 150 / 65 * 340 / 20 + 3 * 7.5 + 140, 1)
    json.dumps(result, allow_nan=False)


@pytest.fixture
def map_data(tmp_path, monkeypatch):
    import matplotlib
    matplotlib.use("Agg")
    from scripts.analyze import plot_villages
    root = tmp_path / "data"
    village_dir = root / "administrative" / "villages"
    village_dir.mkdir(parents=True)
    # marker file: tool_plot_villages_map checks the directory the loader reads
    (root / "administrative" / "india_districts_lgd.geojson").write_text("{}")
    for state, district in [("odisha", "Cuttack"), ("delhi", "New Delhi"),
                            ("chhattisgarh", "Bilaspur"),
                            ("uttar_pradesh", "Hamirpur"), ("himachal_pradesh", "Hamirpur")]:
        gdf = gpd.GeoDataFrame({"district": [district], "village": ["Map fixture"],
                               "dist_rail_station_km": [3.0]}, geometry=[Point(77, 28)], crs="EPSG:4326")
        gdf.to_file(village_dir / f"{state}_habitations.geojson", driver="GeoJSON")
    gpd.GeoDataFrame({"district": ["Bilaspur", "Hamirpur"], "village": ["Fixture A", "Fixture B"],
                      "dist_rail_station_km": [3.0, 4.0]},
                     geometry=[Point(77, 28), Point(77.1, 28.1)], crs="EPSG:4326").to_file(
                         village_dir / "himachal_pradesh_habitations.geojson", driver="GeoJSON")
    monkeypatch.setattr(plot_villages, "DATA_DIR", root)
    monkeypatch.setattr(tools.settings, "OUTPUT_PATH", tmp_path / "output")
    return tmp_path


@pytest.mark.parametrize("alias,canonical,district", [(" Orissa ", "Odisha", "Cuttack"),
                                                        ("NCT of Delhi", "Delhi", "New Delhi")])
def test_map_alias_real_html_png(map_data, alias, canonical, district, capsys):
    result = tools.tool_plot_villages_map(alias, district, output_format="both")
    assert result["state"] == canonical
    assert result["villages_count"] == 1
    assert capsys.readouterr().out == ""
    for key, filename in result["outputs"].items():
        path = Path(filename)
        assert path.is_relative_to(map_data / "output" / "maps")
        assert canonical.lower() in path.name
        if key == "html_map_path":
            assert "<html" in path.read_text(encoding="utf-8")
        else:
            assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("district,states", [("Bilaspur", ["Chhattisgarh", "Himachal Pradesh"]),
                                            ("Hamirpur", ["Uttar Pradesh", "Himachal Pradesh"])])
def test_same_name_cross_state_map_outputs_never_overwrite(map_data, district, states, capsys):
    first = tools.tool_plot_villages_map(states[0], district, output_format="both")
    before = {key: Path(path).read_bytes() for key, path in first["outputs"].items()}
    second = tools.tool_plot_villages_map(states[1], district, output_format="both")
    for key in before:
        assert first["outputs"][key] != second["outputs"][key]
        assert Path(first["outputs"][key]).read_bytes() == before[key]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("state", ["../secret", "Haryana/..", "imaginary"])
def test_map_invalid_state_rejected_before_read(map_data, monkeypatch, state):
    monkeypatch.setattr(gpd, "read_file", lambda *a, **k: pytest.fail("Must not read invalid state"))
    with pytest.raises(HTTPException) as exc:
        tools.tool_plot_villages_map(state, "Bilaspur")
    assert exc.value.status_code == 422


def test_map_metric_validation_before_read(map_data, monkeypatch):
    monkeypatch.setattr(gpd, "read_file", lambda *a, **k: pytest.fail("Must not read invalid metric"))
    with pytest.raises(ValueError, match="Unsupported accessibility metric"):
        tools.tool_plot_villages_map("Orissa", "Cuttack", metric="../secret")


def test_map_resolved_input_escape_rejected_before_read(map_data, monkeypatch):
    target = map_data / "data/administrative/villages/odisha_habitations.geojson"
    original = Path.resolve

    def escaped(path, *args, **kwargs):
        return map_data / "private.geojson" if path == target else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", escaped)
    monkeypatch.setattr(gpd, "read_file", lambda *a, **k: pytest.fail("Must not read escaped path"))
    with pytest.raises(HTTPException) as exc:
        tools.tool_plot_villages_map("Orissa", "Cuttack")
    assert exc.value.status_code == 422
    assert "private" not in str(exc.value.detail)


@pytest.mark.parametrize("alpha,beta", [(0.85, 1.65), (0, 0), (0, 2), (2, 0), (10, 10)])
def test_committed_gravity_winner_counts_and_population_unchanged(interfaces, alpha, beta):
    store, client = interfaces
    data_dir = Path(__file__).resolve().parents[1] / "data"
    store.port_matrix_df = pd.read_csv(data_dir / "analysis/nh_district_port_matrix.csv")
    store.districts_df = pd.read_csv(data_dir / "demographic/district_population_estimates.csv")
    population = store.districts_df.dropna(subset=["district_code"]).set_index("district_code")["pop_2011"].to_dict()
    counts = dict.fromkeys((name for name, _ in simulation.PORT_MATRIX_DATA.values()), 0)
    populations = dict.fromkeys(counts, 0)
    for _, row in store.port_matrix_df.iterrows():
        utility = {name: cap ** alpha / ((float(row[col]) / 5) ** beta)
                   for col, (name, cap) in simulation.PORT_MATRIX_DATA.items()
                   if col in row and pd.notna(row[col]) and float(row[col]) > 0}
        if utility:
            winner = max(utility, key=utility.get)
            counts[winner] += 1
            populations[winner] += population.get(row.get("district_code"), 0)
    response = client.post("/simulate/port-gravity", json={"alpha": alpha, "beta": beta})
    assert response.status_code == 200, response.text
    result = response.json()
    assert_gravity_response(result)
    assert tools.tool_simulate_port_catchment(alpha=alpha, beta=beta) == result
    assert {x["port_name"]: x["captured_districts_count"] for x in result} == counts
    assert {x["port_name"]: x["captured_population_cr"] for x in result} == {
        name: round(pop / 1e7, 2) for name, pop in populations.items()}


@pytest.mark.parametrize("columns", [
    ["port_road_distance_km"],
    ["port_drive_time_hours"],
    ["port_road_distance_km", "port_drive_time_hours"],
])
def test_freight_absent_required_route_columns_are_503(interfaces, columns):
    store, client = interfaces
    store.travel_time_df = store.travel_time_df.drop(columns=columns)
    response = client.post("/simulate/freight-cost", json={"origin_district_code": 521, "payload_tons": None})
    assert response.status_code == 503, response.text
    with pytest.raises(HTTPException) as exc:
        tools.tool_calculate_intermodal_freight_cost(origin_district_code=521, payload_tons=None)
    assert exc.value.status_code == 503


def test_freight_none_shipment_quantity_keeps_default_with_valid_route(interfaces):
    store, client = interfaces
    response = client.post("/simulate/freight-cost", json={"origin_district_code": 521, "payload_tons": None})
    assert response.status_code == 200, response.text
    result = response.json()
    row = store.travel_time_df.iloc[0]
    assert result["road_distance_km"] == row.port_road_distance_km
    assert result["road"]["transit_time_hours"] == row.port_drive_time_hours
    unit_cost = row.port_road_distance_km * 3.3 + row.port_road_distance_km / 65 * 340 / 20 + row.port_drive_time_hours * 7.5 + 140
    assert result["road"]["cost_per_ton_inr"] == round(unit_cost, 1)
    assert result["road"]["total_shipment_cost_inr"] == round(unit_cost * 20, 1)
    assert tools.tool_calculate_intermodal_freight_cost(origin_district_code=521, payload_tons=None) == result


@pytest.mark.parametrize("capacities", [{"Paradip Port": 0}, {"Paradip Port": -1},
                                       {"Paradip Port": "Infinity"}, {"Paradip Port": "NaN"}])
def test_client_capacity_bounds_remain_422(interfaces, capacities):
    _, client = interfaces
    assert client.post("/simulate/port-gravity", json={"custom_port_capacities": capacities}).status_code == 422


@pytest.mark.parametrize("field,endpoint", [("travel_time_df", "freight-cost"), ("port_matrix_df", "port-gravity")])
def test_unavailable_source_is_503(interfaces, field, endpoint):
    store, client = interfaces
    setattr(store, field, None)
    response = client.post(f"/simulate/{endpoint}", json={"origin_district_code": 521} if endpoint == "freight-cost" else {})
    assert response.status_code == 503


def test_unknown_stored_freight_state_is_503(interfaces):
    store, client = interfaces
    store.travel_time_df["state"] = "Invalid source state"
    assert client.post("/simulate/freight-cost", json={"origin_district_code": 521}).status_code == 503
