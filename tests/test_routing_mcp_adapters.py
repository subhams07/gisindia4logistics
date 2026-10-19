"""Public MCP canonical routing, strict nested transport and safe errors."""
import copy
import json
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pytest
from fastapi import HTTPException

from mcp_server import server, tools
from server.models.phase1 import HighwayRoutePlanRequest, RoutePoint, VehicleType
from server.services.error_contract import route_error_contract
from server.services.exceptions import (
    AmbiguousLocationError, DataIntegrityError, InvalidLocationError,
    LocationNotFoundError, RouteNotAvailableError, UnsupportedScenarioError,
)
from server.services.routing_service import RoutingService
from tests.test_mcp_transport_regressions import _handshake, _rpc, _stdio, _success, _strict_json
from tests.test_routing_edge_cases import graph, location

RICH = "gis_plan_highway_route"
VALID = {"origin": {"type": "district", "district_code": 521},
         "destination": {"type": "hub", "hub_type": "port", "name": "JNPT"}}
LEGACY = {"origin_lat": 22, "origin_lon": 78, "dest_lat": 22.01, "dest_lon": 78.01}


def call(args, name=RICH):
    return server.handle_rpc_request(_rpc(1, "tools/call", {"name": name, "arguments": args}))


def failure(response):
    assert response["result"]["isError"] is True
    result = _strict_json(response["result"]["content"][0]["text"])
    assert "C:/private" not in json.dumps(result)
    return result


def test_generated_schema():
    schemas = {t["name"]: t["inputSchema"] for t in server.TOOLS_METADATA}
    assert schemas[RICH] == HighwayRoutePlanRequest.model_json_schema()
    for field in ("origin", "destination"):
        assert schemas[RICH]["properties"][field]["discriminator"]["propertyName"] == "type"
    for model in ("CoordinateLocation", "DistrictLocation", "HubLocation"):
        assert schemas[RICH]["$defs"][model]["additionalProperties"] is False
    assert schemas["gis_highway_route_and_tolls"]["properties"]["vehicle_type"]["enum"] == [v.value for v in VehicleType]


INVALID = [
    {"origin": {"district_code": 521}},
    *[{"origin": {"type": "district", "district_code": v}} for v in (True, "521", 521.0, 0)],
    *[{"origin": {"type": "coordinate", "latitude": v, "longitude": 78}} for v in (True, "22", 91)],
    {"origin": {"type": "district", "district_code": 521, "extra": 1}},
    {"destination": {"type": "hub", "hub_type": "port", "name": "JNPT", "extra": 1}},
    {"destination": {"type": "hub", "hub_type": "unknown", "name": "JNPT"}},
    {"destination": {"type": "hub", "hub_type": "port"}},
    *[{"include_route_geometry": v} for v in (1, "true", None)],
    {"geometry_detail": "bad"}, {"geometry_detail": True},
    {"vehicle_type": "bad"}, {"vehicle_type": True}, {"unknown": 1},
    {"origin": None}, {"destination": []},
]

# Both discriminated endpoints must enforce identical strictness.
INVALID += [{"destination": delta["origin"]} for delta in INVALID if "origin" in delta]
INVALID += [{"origin": []}, {"destination": None}, {"origin": "Pune"},
            {"destination": True}, {"origin": {"type": "district"}},
            {"destination": {"type": "coordinate", "latitude": 22}},
            {"include_route_geometry": "false"}, {"include_route_geometry": 0}]


@pytest.mark.parametrize("delta", INVALID)
def test_invalid_before_data_or_calculation(monkeypatch, delta):
    def forbidden(*a, **k):
        pytest.fail("Invalid input loaded data or reached calculation")
    monkeypatch.setattr(tools, "_get_store", forbidden)
    monkeypatch.setattr(server, "tool_plan_highway_route", forbidden)
    assert failure(call({**VALID, **delta})) == {
        "status": 422, "code": "invalid_request", "message": "Request input is invalid.",
    }


def test_initialized_stdio_committed_batch(tmp_path):
    started = datetime.now(timezone.utc)
    calls = [{**VALID, "vehicle_type": v.value} for v in VehicleType]
    calls += [{**VALID, "geometry_detail": detail} for detail in ("full", "simplified")]
    calls += [{**VALID, "include_route_geometry": False, "geometry_detail": detail}
              for detail in ("full", "simplified")]
    calls += [VALID, {**VALID, "geometry_detail": "none", "include_route_geometry": False}]
    valid_count = len(calls)
    calls += [{**VALID, **delta} for delta in INVALID]
    requests = _handshake() + [
        _rpc(i, "tools/call", {"name": RICH, "arguments": args})
        for i, args in enumerate(calls, 4)
    ]
    requests += [_rpc(80 + i, "tools/call", {"name": "gis_highway_route_and_tolls", "arguments": {
        "origin_lat": 18.5204, "origin_lon": 73.8567,
        "dest_lat": 18.95, "dest_lon": 72.95, "vehicle_type": v.value,
    }}) for i, v in enumerate(VehicleType)]
    requests += [_rpc(100, "ping")]
    responses, _ = _stdio(requests, tmp_path)
    finished = datetime.now(timezone.utc)
    results = [_success(r) for r in responses[3:3 + valid_count]]
    assert len({r["distance_km"] for r in results}) == 1
    assert all(a["drive_time_hours"] < b["drive_time_hours"] for a, b in zip(results[:5], results[1:5]))
    for index, result in enumerate(results):
        assert result["origin"]["district_code"] == 521
        assert result["destination"]["type"] == "hub"
        assert (result["route_geometry"] is not None) == (index in (5, 6))
        assert result["route_quality"]["warnings"]
        assert result["metadata"]["limitations"]
        stamp = datetime.fromisoformat(result["metadata"]["generated_at_utc"].replace("Z", "+00:00"))
        assert stamp.utcoffset().total_seconds() == 0
        assert started <= stamp <= finished
        assert "tolls_encountered_count" not in result
    assert len(results[6]["route_geometry"]["coordinates"]) <= len(results[5]["route_geometry"]["coordinates"])
    for response in responses[3 + valid_count:-6]:
        assert failure(response)["status"] == 422
    legacy = [_success(r) for r in responses[-6:-1]]
    assert len({r["distance_km"] for r in legacy}) == 1
    assert all(r["distance_km"] > 0 and r["drive_time_hours"] > 0 for r in legacy)
    for vehicle, result in zip(VehicleType, legacy):
        rate = 340 if vehicle == VehicleType.MAV_20T else 120 if vehicle == VehicleType.LMV else 220
        assert result["estimated_toll_cost_inr"] == result["tolls_encountered_count"] * rate
        assert "not official" in result["toll_estimation_method"]
        assert len(result["origin_snapped"]) == len(result["destination_snapped"]) == 2
    assert responses[-1]["result"] == {}


ERRORS = [AmbiguousLocationError("C:/private", [{"name": "Pune", "district_code": 521, "path": "C:/private"}]),
          InvalidLocationError("C:/private"), LocationNotFoundError("C:/private"),
          DataIntegrityError("C:/private"), RouteNotAvailableError("C:/private"),
          UnsupportedScenarioError("C:/private"), ValueError("invalid location C:/private"),
          RuntimeError("C:/private")]


@pytest.mark.parametrize("error", ERRORS)
@pytest.mark.parametrize("name,args,handler", [
    (RICH, VALID, "tool_plan_highway_route"),
    ("gis_highway_route_and_tolls", LEGACY, "tool_highway_route_and_tolls"),
])
def test_safe_domain_matrix(monkeypatch, error, name, args, handler):
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(server, handler, fail)
    assert failure(call(args, name)) == route_error_contract(error)


@pytest.mark.parametrize("status", [400, 409, 422, 404, 503, 500])
def test_http_status_only_bridge(monkeypatch, status):
    from server.services.error_contract import domain_error_from_status
    def fail(**kwargs):
        raise HTTPException(status, detail={"message": "C:/private", "candidates": [
            {"district": "Pune", "district_code": 521, "private": "C:/private"},
            {"district_code": True},
        ]})
    monkeypatch.setattr(server, "tool_get_district_scorecard", fail)
    result = failure(call({"district_name_or_code": "Pune"}, "gis_get_district_scorecard"))
    expected = route_error_contract(domain_error_from_status(status))
    if status == 409:
        expected["candidates"] = [{"district": "Pune", "district_code": 521}]
    assert result == expected


@pytest.mark.parametrize("args,name", [({}, RICH), ({}, "gis_highway_route_and_tolls"),
    ({**LEGACY, "origin_lat": 91}, "gis_highway_route_and_tolls"),
    ({"latitude": True, "longitude": 78}, "gis_find_nearest_facilities")])
def test_flat_and_missing_input_safe_422(args, name):
    assert failure(call(args, name)) == {"status": 422, "code": "invalid_request", "message": "Request input is invalid."}


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    service = RoutingService()
    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [(0, 1)])
    store.data_dir = tmp_path
    points = [location(service, p) for p in (base, base + (1000, 0))]
    args = {key: {"type": "coordinate", "latitude": p.latitude, "longitude": p.longitude}
            for key, p in zip(("origin", "destination"), points)}
    monkeypatch.setattr(tools, "store", store)
    return store, args


@pytest.mark.parametrize("surface", ["rich", "legacy"])
def test_one_canonical_engine_and_diagnostics(synthetic, surface):
    store, args = synthetic
    original = RoutingService.calculate_highway_route
    observed = []
    def spy(self, **kwargs):
        observed.append(kwargs)
        result = original(self, **kwargs)
        result.route_quality.warnings.append("sentinel warning")
        result.metadata.limitations.append("sentinel limitation")
        return result
    with patch.object(RoutingService, "calculate_highway_route", spy):
        if surface == "rich":
            result = _success(call(args))
        else:
            result = _success(call({"origin_lat": args["origin"]["latitude"], "origin_lon": args["origin"]["longitude"],
                "dest_lat": args["destination"]["latitude"], "dest_lon": args["destination"]["longitude"]},
                "gis_highway_route_and_tolls"))
            assert "not official" in result["toll_estimation_method"]
    assert len(observed) == 1
    assert observed[0]["store"] is store
    assert result["route_quality"]["warnings"][-1] == "sentinel warning"
    assert result["metadata"]["limitations"][-1] == "sentinel limitation"


def test_actual_graph_corruption(synthetic):
    store, args = synthetic
    store.nh_graph.data[0] = float("nan")
    assert failure(call(args))["status"] == 503


def test_actual_impossible_graph(synthetic):
    store, args = synthetic
    service = RoutingService()
    base = np.array(service.to_proj.transform(78, 22))
    disconnected = graph([base, base + (200000, 0)], [])
    disconnected.data_dir = store.data_dir
    tools.store = disconnected
    endpoint = location(service, base + (200000, 0))
    args = copy.deepcopy(args)
    args["destination"] = {"type": "coordinate", "latitude": endpoint.latitude, "longitude": endpoint.longitude}
    assert failure(call(args))["code"] == "route_not_available"


@pytest.mark.parametrize("field,value", [("latitude", True), ("longitude", "78"),
                                         ("latitude", float("nan"))])
def test_direct_rich_wrapper_strict_before_store(monkeypatch, field, value):
    def forbidden():
        pytest.fail("invalid direct wrapper loaded data")
    monkeypatch.setattr(tools, "_get_store", forbidden)
    origin = {"type": "coordinate", "latitude": 22, "longitude": 78, field: value}
    with pytest.raises(ValueError):
        tools.tool_plan_highway_route(origin, VALID["destination"])


@pytest.mark.parametrize("surface", ["rich", "legacy"])
def test_engine_model_validation_is_data_failure(synthetic, surface):
    _, args = synthetic
    def broken(self, **kwargs):
        return RoutePoint(latitude="not a coordinate", longitude=78)
    with patch.object(RoutingService, "calculate_highway_route", broken):
        if surface == "rich":
            result = call(args)
        else:
            result = call(LEGACY, "gis_highway_route_and_tolls")
    assert failure(result) == route_error_contract(DataIntegrityError(""))


@pytest.mark.parametrize("surface", ["rich", "legacy"])
def test_zero_movement(synthetic, surface):
    _, args = synthetic
    args = {**args, "destination": args["origin"], "geometry_detail": "full"}
    if surface == "rich":
        result = _success(call(args))
        assert result["route_geometry"] is None
    else:
        p = args["origin"]
        result = _success(call({"origin_lat": p["latitude"], "origin_lon": p["longitude"],
            "dest_lat": p["latitude"], "dest_lon": p["longitude"]}, "gis_highway_route_and_tolls"))
        assert result["tolls_encountered_count"] == result["estimated_toll_cost_inr"] == 0
    assert result["distance_km"] == result["drive_time_hours"] == 0
    assert any("Zero movement" in line for line in result["route_quality"]["warnings"])


def test_initialized_stdio_error_contract_matrix(tmp_path):
    bootstrap = '''
from mcp_server import server
from server.services.exceptions import (AmbiguousLocationError, InvalidLocationError,
    LocationNotFoundError, DataIntegrityError, RouteNotAvailableError)
errors = [AmbiguousLocationError('C:/private', [{'name': 'Pune', 'district_code': 521, 'path': 'C:/private'}]),
    InvalidLocationError('C:/private'), LocationNotFoundError('C:/private'),
    DataIntegrityError('C:/private'), RouteNotAvailableError('C:/private'),
    ValueError('invalid input C:/private')]
def fail(**kwargs):
    raise errors.pop(0)
server.tool_plan_highway_route = fail
server.run_stdio_server()
'''
    requests = _handshake()
    for i in range(6):
        requests += [_rpc(4 + 2*i, "tools/call", {"name": RICH, "arguments": VALID}),
                     _rpc(5 + 2*i, "ping")]
    responses, stderr = _stdio(requests, tmp_path, bootstrap)
    expected = [409, 422, 404, 503, 422, 500]
    assert [failure(response)["status"] for response in responses[3::2]] == expected
    assert all(response["result"] == {} for response in responses[4::2])
    assert failure(responses[3])["candidates"] == [{"name": "Pune", "district_code": 521}]
    assert "C:/private" in stderr  # diagnostics are allowed only off protocol


@pytest.mark.parametrize("shape", [None, [], "bad", 1, True])
def test_argument_container_protocol_errors(shape):
    response = call(shape)
    assert response["error"]["code"] == -32602


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_rich_calculated_nonfinite_stays_error(synthetic, value):
    _, args = synthetic
    original = RoutingService.calculate_highway_route
    def invalid(self, **kwargs):
        return original(self, **kwargs).model_copy(update={"distance_km": value})
    with patch.object(RoutingService, "calculate_highway_route", invalid):
        assert failure(call(args)) == route_error_contract(ValueError(""))
