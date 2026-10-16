"""Regression coverage for JSON-only MCP stdio and successful router adapters."""

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
TOOL_NAMES = {
    "gis_get_district_scorecard",
    "gis_calculate_intermodal_freight_cost",
    "gis_find_nearest_facilities",
    "gis_highway_route_and_tolls",
    "gis_plan_highway_route",
    "gis_simulate_port_catchment",
    "gis_plot_villages_map",
}


def _rpc(request_id, method, params=None):
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    return request


def _handshake():
    return [
        _rpc(1, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "transport-regression", "version": "1"},
        }),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _rpc(2, "ping"),
        _rpc(3, "tools/list"),
    ]


def _stdio(requests, tmp_path, bootstrap=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["GIS4LOGISTICS_CACHE_PATH"] = str(tmp_path / "graph-cache")
    env["GIS4LOGISTICS_OUTPUT_PATH"] = str(tmp_path / "outputs")
    command = [sys.executable, "-m", "mcp_server.server"]
    if bootstrap is not None:
        command = [sys.executable, "-c", bootstrap]
    process = subprocess.run(
        command, cwd=ROOT, env=env,
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True, text=True, encoding="utf-8", timeout=180, check=False,
    )
    assert process.returncode == 0, process.stderr
    # Parse EVERY line, including initialization messages and blank lines.
    responses = [_strict_json(line) for line in process.stdout.splitlines()]
    assert len(responses) == sum("id" in request for request in requests)
    assert [response["id"] for response in responses] == [
        request["id"] for request in requests if "id" in request
    ]
    assert all(response["jsonrpc"] == "2.0" for response in responses)
    return responses, process.stderr


def _success(response):
    assert "error" not in response, response
    result = response["result"]
    assert not result.get("isError", False), result
    assert result["content"][0]["type"] == "text"
    return _strict_json(result["content"][0]["text"])


def _strict_json(text):
    def reject_constant(value):
        raise AssertionError(f"Non-standard JSON constant: {value}")

    return json.loads(text, parse_constant=reject_constant)


def test_real_stdio_handshake(tmp_path):
    responses, _ = _stdio(_handshake(), tmp_path)
    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    assert responses[1]["result"] == {}
    metadata = responses[2]["result"]["tools"]
    assert {tool["name"] for tool in metadata} == TOOL_NAMES
    assert len(metadata) == len(TOOL_NAMES)
    schemas = {tool["name"]: tool["inputSchema"] for tool in metadata}
    assert schemas["gis_calculate_intermodal_freight_cost"]["anyOf"] == [
        {"required": ["origin_district"]}, {"required": ["origin_district_code"]},
    ]
    assert schemas["gis_highway_route_and_tolls"]["required"] == [
        "origin_lat", "origin_lon", "dest_lat", "dest_lon",
    ]
    from server.models.phase1 import HighwayRoutePlanRequest, VehicleType
    assert schemas["gis_plan_highway_route"] == HighwayRoutePlanRequest.model_json_schema()
    assert schemas["gis_highway_route_and_tolls"]["properties"]["vehicle_type"]["enum"] == [v.value for v in VehicleType]


def test_handshake_never_initializes_store(tmp_path):
    bootstrap = """
import sys
from server.dependencies import DataStore

def forbidden_initialization(self):
    raise AssertionError('handshake must not initialize or build the graph')

DataStore.initialize_store = forbidden_initialization
from mcp_server.server import run_stdio_server
assert DataStore._instance is None
assert 'gisindia4logistics.sdk' not in sys.modules
assert 'scripts.analyze.plot_villages' not in sys.modules
assert 'matplotlib.pyplot' not in sys.modules
run_stdio_server()
assert DataStore._instance is None
assert 'gisindia4logistics.sdk' not in sys.modules
assert 'scripts.analyze.plot_villages' not in sys.modules
assert 'matplotlib.pyplot' not in sys.modules
"""
    responses, _ = _stdio(_handshake(), tmp_path, bootstrap)
    assert responses[1]["result"] == {}


def test_real_stdio_successful_committed_data_calls(tmp_path):
    requests = _handshake()
    calls = [
        ("gis_calculate_intermodal_freight_cost", {"origin_district": "Pune", "payload_tons": 25.0}),
        ("gis_highway_route_and_tolls", {
            "origin_lat": 18.5204, "origin_lon": 73.8567,
            "dest_lat": 18.9500, "dest_lon": 72.9500,
        }),
        ("gis_simulate_port_catchment", {"alpha": 0.9, "beta": 1.7}),
        ("gis_get_district_scorecard", {"district_name_or_code": "Pune"}),
        ("gis_find_nearest_facilities", {"latitude": 28.6139, "longitude": 77.2090, "top_k": 2}),
        ("gis_plot_villages_map", {"state": "Haryana", "district": "Ambala", "output_format": "both"}),
    ]
    requests.extend(
        _rpc(i, "tools/call", {"name": name, "arguments": arguments})
        for i, (name, arguments) in enumerate(calls, 4)
    )
    requests.extend([
        _rpc(10, "tools/call", {"name": "gis_find_nearest_facilities", "arguments": {
            "latitude": True, "longitude": 77,
        }}),
        _rpc(11, "ping"),
    ])
    responses, stderr = _stdio(requests, tmp_path)
    freight, route, shares, district, facilities, plotted = map(_success, responses[3:9])
    assert freight["origin_district"].lower() == "pune"
    for mode in ("road", "conventional_rail", "dfc_rail"):
        assert isinstance(freight[mode], dict)
        assert freight[mode]["cost_per_ton_inr"] > 0
        assert freight[mode]["total_shipment_cost_inr"] > 0
    assert route["distance_km"] > 0
    assert route["drive_time_hours"] > 0
    assert len(route["origin_snapped"]) == len(route["destination_snapped"]) == 2
    assert len(shares) == 12
    assert sum(share["captured_districts_count"] for share in shares) > 0
    assert 99 <= sum(share["market_share_districts_pct"] for share in shares) <= 101
    assert district["district"].lower() == "pune"
    assert len(facilities["toll_plazas"]) == 2
    assert plotted["villages_count"] > 0
    for key in ("html_map_path", "png_map_path"):
        assert Path(plotted["outputs"][key]).is_relative_to(tmp_path)
    assert Path(plotted["outputs"]["png_map_path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "<html" in Path(plotted["outputs"]["html_map_path"]).read_text(encoding="utf-8").lower()
    assert responses[9]["result"]["isError"] is True
    assert responses[10]["result"] == {}
    assert "Wrote static map" in stderr


@pytest.mark.parametrize("wrapper,router,args", [
    ("tool_highway_route_and_tolls", "legacy_highway_route", {
        "origin_lat": 18.5, "origin_lon": 73.8, "dest_lat": 19.0, "dest_lon": 73.0,
    }),
    ("tool_simulate_port_catchment", "simulate_port_gravity", {}),
])
@pytest.mark.parametrize("model_result", [False, True])
def test_wrappers_recursively_serialize_router_results(monkeypatch, wrapper, router, args, model_result):
    from mcp_server import tools

    class NestedValue(BaseModel):
        recorded_on: date
        recorded_at: datetime
        values: list[int]

    class Envelope(BaseModel):
        nested: NestedValue

    nested = NestedValue(
        recorded_on=date(2026, 1, 2),
        recorded_at=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc), values=[1, 2],
    )
    payload = Envelope(nested=nested) if model_result else {
        "nested": nested, "sequence": (nested, {"deep": [nested]}), "optional": None,
    }
    response = [payload] if router == "simulate_port_gravity" else payload
    injected = SimpleNamespace(marker="injected")
    monkeypatch.setattr(tools, "store", injected)

    def delegate(*, req, store):
        assert store is injected
        return response

    monkeypatch.setattr(tools, router, delegate)
    result = getattr(tools, wrapper)(**args)
    serialized = json.loads(json.dumps(result))
    item = serialized[0] if isinstance(serialized, list) else serialized
    assert item["nested"] == {
        "recorded_on": "2026-01-02", "recorded_at": "2026-01-02T03:04:00Z", "values": [1, 2],
    }
    if not model_result:
        assert item["sequence"][1]["deep"][0] == item["nested"]
        assert item["optional"] is None


def test_lazy_store_compatibility_tracks_singleton_replacement(monkeypatch):
    from mcp_server import tools
    from server.dependencies import DataStore

    first = SimpleNamespace(marker="first")
    second = SimpleNamespace(marker="second")
    monkeypatch.setattr(DataStore, "_instance", first)
    assert tools.store.marker == "first"
    monkeypatch.setattr(DataStore, "_instance", second)
    assert tools.store.marker == "second"

    def delegate(*, code_or_name, store):
        assert code_or_name == "Pune"
        assert store is second
        return {"marker": store.marker}

    monkeypatch.setattr(tools, "get_district_scorecard", delegate)
    assert tools.tool_get_district_scorecard("Pune") == {"marker": "second"}


def test_store_initialization_logs_without_stdout(monkeypatch, tmp_path, capsys, caplog):
    from server.dependencies import DataStore

    store = DataStore()
    store.data_dir = tmp_path
    monkeypatch.setattr(store, "_build_highway_graph", lambda: None)
    with caplog.at_level("INFO", logger="server.dependencies"):
        store.initialize_store()
    assert capsys.readouterr().out == ""
    assert "Initializing GIS4Logistics" in caplog.text
    assert "DataStore initialized" in caplog.text


def test_real_stdio_map_both_formats(tmp_path):
    responses, stderr = _stdio(_handshake() + [
        _rpc(4, "tools/call", {"name": "gis_plot_villages_map", "arguments": {
            "state": "Haryana", "district": "Ambala", "output_format": "both",
        }}),
        _rpc(5, "ping"),
    ], tmp_path)
    result = _success(responses[3])
    assert result["status"] == "success"
    assert result["villages_count"] > 0
    html_path = Path(result["outputs"]["html_map_path"])
    png_path = Path(result["outputs"]["png_map_path"])
    assert html_path.is_relative_to(tmp_path)
    assert png_path.is_relative_to(tmp_path)
    assert "<html" in html_path.read_text(encoding="utf-8").lower()
    assert png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "Wrote static map" in stderr
    assert responses[4]["result"] == {}


def test_stdio_errors_keep_session_usable(tmp_path):
    responses, stderr = _stdio(_handshake() + [
        _rpc(4, "tools/call", {"name": "gis_find_nearest_facilities", "arguments": {
            "latitude": 100, "longitude": 77,
        }}),
        _rpc(5, "tools/call", {"name": "gis_plot_villages_map", "arguments": {
            "state": "Haryana", "district": "Ambala", "output_format": "invalid",
        }}),
        _rpc(6, "tools/call", {"name": "missing_tool"}),
        _rpc(7, "missing_method"),
        _rpc(8, "tools/call", {"name": "gis_find_nearest_facilities", "arguments": [1]}),
        _rpc(9, "ping", []),
        _rpc(10, "ping"),
    ], tmp_path)
    assert responses[3]["result"]["isError"] is True
    assert responses[4]["result"]["isError"] is True
    assert [response["error"]["code"] for response in responses[5:9]] == [
        -32601, -32601, -32602, -32602,
    ]
    assert responses[9]["result"] == {}
    assert "MCP tool call failed" in stderr


def test_stdio_rejects_malformed_and_nonstandard_json(tmp_path):
    process = subprocess.run(
        [sys.executable, "-m", "mcp_server.server"], cwd=ROOT,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        input='not-json\n{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":NaN}}\n'
        + json.dumps(_rpc(2, "ping")) + "\n",
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert process.returncode == 0, process.stderr
    responses = [_strict_json(line) for line in process.stdout.splitlines()]
    assert len(responses) == 3
    assert [response["error"]["code"] for response in responses[:2]] == [-32700, -32700]
    assert responses[2] == {"jsonrpc": "2.0", "id": 2, "result": {}}
    assert "Failed to parse MCP request" in process.stderr


def test_observational_missing_scalars_are_null(monkeypatch):
    import numpy as np
    import pandas as pd

    from mcp_server import tools

    monkeypatch.setattr(tools, "store", SimpleNamespace())
    monkeypatch.setattr(tools, "get_district_scorecard", lambda **kwargs: {
        "district": "Pune", "nearest_port": {"name": pd.NA, "road_distance_km": np.float64("nan")},
        "share_villages_within_10km_rail": float("nan"), "recorded_on": pd.NaT,
        "district_code": np.int64(521),
    })
    assert tools.tool_get_district_scorecard("Pune") == {
        "district": "Pune", "nearest_port": {"name": None, "road_distance_km": None},
        "share_villages_within_10km_rail": None, "recorded_on": None, "district_code": 521,
    }
    monkeypatch.setattr(tools, "get_nearest_hubs", lambda **kwargs: {
        "ports": [{"operator": float("nan"), "distance_straight_line_km": np.float64(2.5)}],
    })
    assert tools.tool_find_nearest_facilities(18.5, 73.8) == {
        "ports": [{"operator": None, "distance_straight_line_km": 2.5}],
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_calculated_results_are_errors(monkeypatch, value):
    from mcp_server import server, tools

    monkeypatch.setattr(tools, "store", SimpleNamespace())
    monkeypatch.setattr(tools, "simulate_freight_cost", lambda **kwargs: {
        "road": {"total_shipment_cost_inr": value},
    })
    response = server.handle_rpc_request(_rpc(1, "tools/call", {
        "name": "gis_calculate_intermodal_freight_cost", "arguments": {"origin_district": "Pune"},
    }))
    assert response["result"]["isError"] is True
    assert _strict_json(response["result"]["content"][0]["text"]) == {
        "status": 500, "code": "internal_error", "message": "An internal routing error occurred.",
    }
    monkeypatch.setattr(tools, "get_nearest_hubs", lambda **kwargs: {
        "ports": [{"operator": float("nan"), "distance_straight_line_km": value}],
    })
    with pytest.raises(ValueError, match="Non-finite value"):
        tools.tool_find_nearest_facilities(18.5, 73.8)


def test_model_json_mode_cannot_hide_nonfinite_calculation():
    from pydantic import ConfigDict

    from server.services.serialization import serialize_result

    class Calculation(BaseModel):
        model_config = ConfigDict(ser_json_inf_nan="null")
        total_shipment_cost_inr: float

    result = Calculation(total_shipment_cost_inr=float("nan"))
    assert result.model_dump_json() == '{"total_shipment_cost_inr":null}'
    with pytest.raises(ValueError, match="Non-finite value"):
        serialize_result({"nested": [result]})
    with pytest.raises(ValueError, match="Non-finite value"):
        serialize_result({"observation": float("inf")}, missing_nan=True)


def test_failed_store_initialization_does_not_publish_partial_singleton(monkeypatch):
    from server.dependencies import DataStore

    monkeypatch.setattr(DataStore, "_instance", None)
    attempts = []

    def initialize(self):
        attempts.append(self)
        if len(attempts) == 1:
            raise RuntimeError("Initialization failed")

    monkeypatch.setattr(DataStore, "initialize_store", initialize)
    with pytest.raises(RuntimeError, match="Initialization failed"):
        DataStore.get_instance()
    assert DataStore._instance is None
    assert DataStore.get_instance() is attempts[1]
    assert DataStore.get_instance() is attempts[1]


def test_stdio_nested_models_and_invalid_calculation(tmp_path):
    # Deliberate adapter test doubles, not substituted upstream observations.
    bootstrap = """
import logging
import sys
from datetime import datetime, timezone
from pydantic import BaseModel
from mcp_server import server

logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)

class Reading(BaseModel):
    recorded_at: datetime

def result(**kwargs):
    print('legacy progress output')
    logging.info('adapter diagnostic')
    if kwargs['district_name_or_code'] == 'invalid':
        return {'road': {'total_shipment_cost_inr': float('nan')}}
    return {'nested': [Reading(recorded_at=datetime(2026, 1, 2, tzinfo=timezone.utc))]}

server.tool_get_district_scorecard = result
server.run_stdio_server()
"""
    responses, stderr = _stdio(_handshake() + [
        _rpc(4, "tools/call", {"name": "gis_get_district_scorecard", "arguments": {
            "district_name_or_code": "valid",
        }}),
        _rpc(5, "tools/call", {"name": "gis_get_district_scorecard", "arguments": {
            "district_name_or_code": "invalid",
        }}),
        _rpc(6, "ping"),
    ], tmp_path, bootstrap)
    assert _success(responses[3]) == {"nested": [{"recorded_at": "2026-01-02T00:00:00Z"}]}
    assert responses[4]["result"]["isError"] is True
    assert _strict_json(responses[4]["result"]["content"][0]["text"]) == {
        "status": 500, "code": "internal_error", "message": "An internal routing error occurred.",
    }
    assert responses[5]["result"] == {}
    assert "legacy progress output" in stderr
    assert "adapter diagnostic" in stderr


def test_rpc_null_id_and_invalid_request_shapes():
    from mcp_server.server import handle_rpc_request

    assert handle_rpc_request(_rpc(None, "ping")) == {"jsonrpc": "2.0", "id": None, "result": {}}
    assert handle_rpc_request({"jsonrpc": "2.0", "method": "ping", "params": []}) is None
    for request in ([], {"jsonrpc": "2.0", "id": 1}, _rpc([], "ping"), _rpc(True, "ping")):
        assert handle_rpc_request(request)["error"]["code"] == -32600


@pytest.mark.parametrize("name,args", [
    ("gis_get_district_scorecard", {"district_name_or_code": "Pune", "unknown": 1}),
    ("gis_find_nearest_facilities", {"latitude": 18, "longitude": 73, "top_k": 2.5}),
    ("gis_find_nearest_facilities", {"latitude": 18, "longitude": 73, "top_k": 2.0}),
    ("gis_find_nearest_facilities", {"latitude": True, "longitude": 73}),
    ("gis_calculate_intermodal_freight_cost", {"origin_district_code": 521.0}),
    ("gis_calculate_intermodal_freight_cost", {"origin_district": "Pune", "payload_tons": True}),
    ("gis_highway_route_and_tolls", {"origin_lat": "18", "origin_lon": 73, "dest_lat": 19, "dest_lon": 74}),
    ("gis_simulate_port_catchment", {"alpha": True}),
    ("gis_plot_villages_map", {"state": "Haryana", "district": "Ambala", "unknown": 1}),
])
def test_argument_types_and_unknown_keys_rejected_before_dispatch(monkeypatch, name, args):
    from mcp_server import server, tools

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid arguments reached tool dispatch")

    for attribute in (
        "tool_get_district_scorecard", "tool_find_nearest_facilities",
        "tool_calculate_intermodal_freight_cost", "tool_highway_route_and_tolls",
        "tool_simulate_port_catchment",
    ):
        monkeypatch.setattr(server, attribute, forbidden)
    monkeypatch.setattr(tools, "tool_plot_villages_map", forbidden)
    response = server.handle_rpc_request(_rpc(1, "tools/call", {"name": name, "arguments": args}))
    assert response["result"]["isError"] is True


def test_freight_schema_matches_sdk_signature():
    import inspect

    from gisindia4logistics.sdk import calculate_freight_cost
    from mcp_server.server import TOOLS_METADATA
    from mcp_server.tools import tool_calculate_intermodal_freight_cost

    schema = next(tool["inputSchema"] for tool in TOOLS_METADATA
                  if tool["name"] == "gis_calculate_intermodal_freight_cost")
    sdk_parameters = inspect.signature(calculate_freight_cost).parameters
    tool_parameters = inspect.signature(tool_calculate_intermodal_freight_cost).parameters
    assert set(schema["properties"]) == set(sdk_parameters) == set(tool_parameters)
    assert "origin_district" not in schema.get("required", [])
    assert schema["anyOf"] == [
        {"required": ["origin_district"]}, {"required": ["origin_district_code"]},
    ]
    assert schema["additionalProperties"] is False
    for name, parameter in sdk_parameters.items():
        assert tool_parameters[name].default == parameter.default


@pytest.fixture(scope="module")
def committed_store(tmp_path_factory):
    from server.config import settings
    from server.dependencies import DataStore

    old_cache, old_output = settings.CACHE_PATH, settings.OUTPUT_PATH
    isolated = tmp_path_factory.mktemp("mcp-committed")
    settings.CACHE_PATH, settings.OUTPUT_PATH = isolated / "cache", isolated / "outputs"
    try:
        store = DataStore()
        store.initialize_store()
        yield store
    finally:
        settings.CACHE_PATH, settings.OUTPUT_PATH = old_cache, old_output


@pytest.mark.parametrize("payload", [20.0, 40.0])
@pytest.mark.parametrize("target_port", [None, "Paradip Port"])
@pytest.mark.parametrize("overrides", [{}, {"truck_payload_tons": 30.0, "road_linehaul_rate": 2.5}, {
    "road_handling_cost": 100.0, "toll_spacing_km": 50.0, "toll_cost_per_plaza": 300.0,
    "rail_base_class_rate": 1.7, "rail_first_mile_rate": 4.0, "rail_first_mile_handling": 110.0,
    "rail_handling_and_siding": 210.0, "rail_commercial_speed_kmh": 30.0,
    "rail_yard_detention_hours": 10.0, "dfc_linehaul_rate": 1.2, "dfc_handling_cost": 170.0,
    "dfc_commercial_speed_kmh": 65.0, "dfc_yard_transfer_hours": 2.0,
    "inventory_holding_rate": 8.0,
}])
@pytest.mark.parametrize("identity", [
    {"origin_district": "Pune"},
    {"origin_district": "Bilaspur", "origin_state": "Chhattisgarh"},
    {"origin_district": "Bilaspur", "origin_state": "Himachal Pradesh"},
    {"origin_district_code": 406},
    {"origin_district_code": 30},
])
def test_committed_full_rest_sdk_mcp_freight_parity(
    monkeypatch, committed_store, payload, target_port, overrides, identity,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gisindia4logistics import sdk
    from mcp_server import server, tools
    from server.dependencies import get_data_store
    from server.routers.simulation import router

    monkeypatch.setattr(sdk, "get_data_store", lambda: committed_store)
    monkeypatch.setattr(tools, "store", committed_store)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_data_store] = lambda: committed_store
    args = {**identity, "payload_tons": payload, "target_port": target_port,
            "commodity_name": "Cement", **overrides}
    request = {**identity, "payload_tons": payload, "target_port": target_port,
               "commodity_name": "Cement"}
    if overrides:
        request["custom_parameters"] = overrides
    with TestClient(app) as client:
        rest = client.post("/simulate/freight-cost", json=request)
    assert rest.status_code == 200, rest.text
    sdk_result = sdk.calculate_freight_cost(**args)
    mcp_result = _success(server.handle_rpc_request(_rpc(1, "tools/call", {
        "name": "gis_calculate_intermodal_freight_cost", "arguments": args,
    })))
    assert rest.json() == sdk_result == mcp_result


def test_committed_mcp_quantity_capacity_and_null_fallback(monkeypatch, committed_store):
    from mcp_server import tools
    from server.models.schemas import CostParametersOverride

    monkeypatch.setattr(tools, "store", committed_store)
    twenty = tools.tool_calculate_intermodal_freight_cost("Pune", payload_tons=20)
    forty = tools.tool_calculate_intermodal_freight_cost("Pune", payload_tons=40)
    capacity = tools.tool_calculate_intermodal_freight_cost("Pune", payload_tons=40, truck_payload_tons=40)
    for mode in ("road", "conventional_rail", "dfc_rail"):
        assert twenty[mode]["cost_per_ton_inr"] == forty[mode]["cost_per_ton_inr"]
        assert forty[mode]["total_shipment_cost_inr"] == pytest.approx(
            2 * twenty[mode]["total_shipment_cost_inr"], abs=0.1,
        )
    assert capacity["road"]["cost_per_ton_inr"] < forty["road"]["cost_per_ton_inr"]
    assert capacity["conventional_rail"] == forty["conventional_rail"]
    assert capacity["dfc_rail"] == forty["dfc_rail"]
    assert tools.tool_calculate_intermodal_freight_cost(
        "Pune", **dict.fromkeys(CostParametersOverride.model_fields),
    ) == twenty


def test_committed_duplicate_resolution_and_scorecard_state(monkeypatch, committed_store):
    from fastapi import HTTPException

    from mcp_server import server, tools

    monkeypatch.setattr(tools, "store", committed_store)
    duplicates = committed_store.travel_time_df[
        committed_store.travel_time_df.district.str.lower() == "bilaspur"
    ]
    assert set(duplicates.state) == {"Chhattisgarh", "Himachal Pradesh"}
    ambiguous = server.handle_rpc_request(_rpc(1, "tools/call", {
        "name": "gis_calculate_intermodal_freight_cost", "arguments": {"origin_district": "Bilaspur"},
    }))
    assert ambiguous["result"]["isError"] is True
    assert _strict_json(ambiguous["result"]["content"][0]["text"])["code"] == "ambiguous_location"
    with pytest.raises(HTTPException):
        tools.tool_get_district_scorecard("Bilaspur")
    for state, code in (("Chhattisgarh", 406), ("Himachal Pradesh", 30)):
        by_state = _success(server.handle_rpc_request(_rpc(2, "tools/call", {
            "name": "gis_get_district_scorecard", "arguments": {
                "district_name_or_code": "Bilaspur", "state": state,
            },
        })))
        by_code = tools.tool_get_district_scorecard(str(code))
        assert by_state == by_code
        assert by_state["state"].lower() == state.lower()
        assert tools.tool_calculate_intermodal_freight_cost(
            "Bilaspur", origin_state=state,
        ) == tools.tool_calculate_intermodal_freight_cost(origin_district_code=code)


@pytest.mark.parametrize("identity", [
    {}, {"origin_district": ""}, {"origin_district_code": 0},
    {"origin_district": "Bilaspur", "origin_state": "Maharashtra"},
    {"origin_district": "Pune", "origin_district_code": 406},
])
def test_committed_bad_origin_is_tool_error(monkeypatch, committed_store, identity):
    from mcp_server import server, tools

    monkeypatch.setattr(tools, "store", committed_store)
    result = server.handle_rpc_request(_rpc(1, "tools/call", {
        "name": "gis_calculate_intermodal_freight_cost", "arguments": identity,
    }))
    assert result["result"]["isError"] is True


def test_mcp_requires_complete_freight_response(monkeypatch):
    from mcp_server import tools

    monkeypatch.setattr(tools, "store", SimpleNamespace())
    monkeypatch.setattr(tools, "simulate_freight_cost", lambda **kwargs: {"origin_district": "Pune"})
    with pytest.raises(ValueError):
        tools.tool_calculate_intermodal_freight_cost("Pune")


def test_model_field_serializer_cannot_hide_invalid_calculation():
    from pydantic import field_serializer

    from server.services.serialization import serialize_result

    class Calculation(BaseModel):
        cost: float

        @field_serializer("cost")
        def serialize_cost(self, value):
            return None

    result = Calculation(cost=float("nan"))
    assert result.model_dump() == {"cost": None}
    with pytest.raises(ValueError, match="Non-finite value"):
        serialize_result(result)
