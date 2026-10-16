"""Real full-namespace HTTP adapters over the frozen canonical foundation."""
import importlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from server.config import settings
from server.dependencies import DataStore, get_data_store
from server.models.phase1 import HighwayRoutePlanRequest, VehicleType
from server.models.schemas import RouteRequest
from server.routers import routing
from server.services import routing_facade as facade
from server.services.error_contract import route_error_contract
from server.services.exceptions import (
    AmbiguousLocationError, DataIntegrityError, InvalidLocationError,
    LocationNotFoundError, RouteNotAvailableError,
)
from server.services.routing_service import RoutingService
from server.services.serialization import serialize_result
from tests.test_routing_edge_cases import graph, location

app_module = importlib.import_module("server.app")
PLAN = "/api/v1/route/highway/plan"
LEGACY = "/api/v1/route/highway"


@pytest.fixture
def synthetic(tmp_path):
    service = RoutingService()
    base = np.array(service.to_proj.transform(78, 22))
    xy = [base, base + (1000, 40), base + (2000, 0), base + (3000, 80), base + (4000, 0)]
    store = graph(xy, [(i, i + 1) for i in range(4)])
    store.data_dir = tmp_path
    refs = [dict(type="coordinate", latitude=p.latitude, longitude=p.longitude)
            for p in (location(service, xy[0]), location(service, xy[-1]))]
    return store, dict(origin=refs[0], destination=refs[1])


@pytest.fixture
def client(synthetic):
    store, _ = synthetic
    previous = dict(app_module.app.dependency_overrides)
    app_module.app.dependency_overrides[get_data_store] = lambda: store
    with TestClient(app_module.app, raise_server_exceptions=False) as client:
        yield client
    app_module.app.dependency_overrides.clear()
    app_module.app.dependency_overrides.update(previous)


def legacy_body(body):
    return {key: [body[key]["latitude"], body[key]["longitude"]]
            for key in ["origin", "destination"]}


@pytest.mark.parametrize("surface", ["rich", "legacy"])
def test_patchable_facade_one_call(client, synthetic, surface):
    store, body = synthetic
    if surface == "rich":
        expected = facade.plan_highway_route(HighwayRoutePlanRequest.model_validate(body), store)
        symbol, url, payload = "plan_highway_route", PLAN, body
    else:
        payload = legacy_body(body)
        expected = facade.legacy_highway_route(RouteRequest.model_validate(payload), store)
        symbol, url = "legacy_highway_route", LEGACY
    with patch.object(facade, symbol, return_value=expected) as sentinel, \
            patch.object(facade, "metadata_for_store", side_effect=AssertionError("metadata twice")):
        response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    assert sentinel.call_count == 1
    assert sentinel.call_args.args[1] is store
    assert response.json() == serialize_result(expected)
    assert not any(hasattr(routing, name) for name in ["dijkstra", "np", "transformer_to_lcc"])


@pytest.mark.parametrize("mutation", [
    {"origin": {"latitude": 22., "longitude": 78.}},
    {"origin": {"type": "unknown", "latitude": 22., "longitude": 78.}},
    {"origin": {"type": "coordinate", "latitude": True, "longitude": 78.}},
    {"origin": {"type": "coordinate", "latitude": "22", "longitude": 78.}},
    {"origin": {"type": "coordinate", "latitude": 22., "longitude": 78., "secret": "C:/secret"}},
    {"origin": {"type": "district", "district_code": True}},
    {"origin": {"type": "district", "district_code": "521"}},
    {"origin": {"type": "hub", "hub_type": "port", "code": 123}},
    {"origin": {"type": "district", "district_code": 521, "extra": 1}},
    {"origin": {"type": "hub", "hub_type": "port", "name": "JNPT", "extra": 1}},
    {"include_route_geometry": 1}, {"include_route_geometry": "false"},
    {"geometry_detail": "invalid"}, {"vehicle_type": "invalid"}, {"extra": "C:/secret"},
])
def test_strict_request_fixed_safe_422(client, synthetic, mutation):
    _, body = synthetic
    response = client.post(PLAN, json={**body, **mutation})
    assert response.status_code == 422
    assert response.json() == {"detail": {"status": 422, "code": "invalid_request", "message": "Request input is invalid."}}
    assert "C:/secret" not in response.text


@pytest.mark.parametrize("field", ["origin", "destination"])
def test_required_request_fields(client, synthetic, field):
    _, body = synthetic
    body = deepcopy(body)
    body.pop(field)
    response = client.post(PLAN, json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


def test_request_model_validation_error_safe(client, synthetic):
    _, body = synthetic
    try:
        HighwayRoutePlanRequest.model_validate({"origin": "C:/secret"})
    except ValidationError as error:
        with patch.object(facade, "plan_highway_route", side_effect=error):
            response = client.post(PLAN, json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": {"status": 422, "code": "invalid_request", "message": "Request input is invalid."}}
    assert "C:/secret" not in response.text


@pytest.mark.parametrize("vehicle", [v.value for v in VehicleType])
def test_all_five_vehicles_real_http(client, synthetic, vehicle):
    store, body = synthetic
    body = {**body, "vehicle_type": vehicle}
    rich = client.post(PLAN, json=body)
    assert rich.status_code == 200, rich.text
    expected = serialize_result(facade.plan_highway_route(HighwayRoutePlanRequest.model_validate(body), store))
    actual = rich.json()
    actual.pop("metadata")
    expected.pop("metadata")
    assert actual == expected
    legacy = client.post(LEGACY, json={**legacy_body(body), "vehicle_type": vehicle})
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["distance_km"] == round(actual["distance_km"], 1)
    assert "not route-plaza intersection" in legacy.json()["toll_estimation_method"]


@pytest.mark.parametrize("detail,include", [("none", True), ("full", False),
    ("simplified", False), ("full", True), ("simplified", True)])
def test_geometry_controls(client, synthetic, detail, include):
    _, body = synthetic
    data = client.post(PLAN, json={**body, "geometry_detail": detail, "include_route_geometry": include}).json()
    assert (data["route_geometry"] is not None) == (include and detail != "none")
    assert data["route_quality"]["geometry_simplified"] == (include and detail == "simplified")
    if data["route_geometry"]:
        coords = data["route_geometry"]["coordinates"]
        assert len(coords) >= 2
        assert coords[0] == [round(body["origin"]["longitude"], 5), round(body["origin"]["latitude"], 5)]
        assert coords[-1] == [round(body["destination"]["longitude"], 5), round(body["destination"]["latitude"], 5)]


def test_default_and_simplification(client, synthetic):
    _, body = synthetic
    assert client.post(PLAN, json=body).json()["route_geometry"] is None
    full = client.post(PLAN, json={**body, "geometry_detail": "full"}).json()
    simplified = client.post(PLAN, json={**body, "geometry_detail": "simplified"}).json()
    assert len(simplified["route_geometry"]["coordinates"]) < len(full["route_geometry"]["coordinates"])
    assert full["distance_km"] == simplified["distance_km"]


@pytest.mark.parametrize("same", [True, False])
def test_zero_same_snap_legacy_warnings(client, synthetic, same):
    _, body = synthetic
    body = deepcopy(body)
    body["destination"] = deepcopy(body["origin"])
    if not same:
        body["destination"]["latitude"] += .00001
    data = client.post(LEGACY, json=legacy_body(body)).json()
    warning = "Zero movement" if same else "same snap node"
    assert any(warning in w for w in data["route_quality"]["warnings"])
    if same:
        assert data["distance_km"] == data["drive_time_hours"] == data["estimated_toll_cost_inr"] == 0
    else:
        assert data["route_quality"]["quality"] == "low_confidence"


@pytest.mark.parametrize("error", [AmbiguousLocationError("C:/secret", candidates=[{"name": "Safe"}]),
    InvalidLocationError("C:/secret"), LocationNotFoundError("C:/secret"),
    DataIntegrityError("C:/secret"), RouteNotAvailableError("C:/secret"),
    ValueError("C:/secret"), RuntimeError("C:/secret")])
@pytest.mark.parametrize("surface", ["rich", "legacy"])
def test_domain_and_unexpected_safe_matrix(client, synthetic, error, surface):
    _, body = synthetic
    symbol = "plan_highway_route" if surface == "rich" else "legacy_highway_route"
    with patch.object(facade, symbol, side_effect=error):
        response = client.post(PLAN if surface == "rich" else LEGACY,
                               json=body if surface == "rich" else legacy_body(body))
    contract = route_error_contract(error)
    assert response.status_code == contract["status"]
    assert response.json() == {"detail": contract}
    assert "C:/secret" not in response.text
    assert "X-GIS4L-Data-Version" in response.headers


def test_nan_checked_before_response_model(client, synthetic):
    store, body = synthetic
    result = facade.plan_highway_route(HighwayRoutePlanRequest.model_validate(body), store)
    result.distance_km = float("nan")
    with patch.object(facade, "plan_highway_route", return_value=result):
        response = client.post(PLAN, json=body)
    # Shared serialization rejects calculated NaN with ValueError: safely 500,
    # never silently null and never reclassified as invalid client input.
    assert response.status_code == 500
    assert response.json() == {"detail": route_error_contract(ValueError(""))}


def test_headers_actual_root_a_b_a(client, synthetic, tmp_path, monkeypatch):
    store, body = synthetic
    monkeypatch.setattr(settings, "DATA_PATH", tmp_path / "WRONG")
    for root_name in ["A", "B"]:
        root = tmp_path / root_name
        root.mkdir()
        (root / "version_manifest.json").write_text(json.dumps({
            "api_version": "api-" + root_name, "data_version": "data-" + root_name,
            "model_version": "model-" + root_name}))
    for root_name in ["A", "B", "A"]:
        store.data_dir = tmp_path / root_name
        for url, payload in [(PLAN, body), (LEGACY, legacy_body(body))]:
            response = client.post(url, json=payload)
            assert response.status_code == 200, response.text
            metadata = response.json()["metadata"]
            for field, header in [("api_version", "X-GIS4L-API-Version"),
                ("data_version", "X-GIS4L-Data-Version"), ("model_version", "X-GIS4L-Model-Version")]:
                assert response.headers[header] == metadata[field] == field.split("_")[0] + "-" + root_name


def test_liveness_and_header_only_are_data_lazy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", tmp_path)
    (tmp_path / "version_manifest.json").write_text('{"data_version":"lazy"}')
    with patch.object(DataStore, "get_instance", side_effect=AssertionError("must not initialize")):
        with TestClient(app_module.app, raise_server_exceptions=False) as client:
            for url, status in [("/health/live", 200), ("/docs", 200), ("/does-not-exist", 404)]:
                response = client.get(url)
                assert response.status_code == status, response.text
                assert response.headers["X-GIS4L-Data-Version"] == "lazy"
            preflight = client.options(PLAN, headers={"Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST"})
            assert preflight.status_code in (200, 400)  # configured CORS origin policy
            assert preflight.headers["X-GIS4L-Data-Version"] == "lazy"
            assert client.head(PLAN).status_code == 405


def test_untouched_http_exception_status(client):
    response = client.get("/api/v1/route/isochrones/ports")
    assert response.status_code == 404
    assert response.json() == {"detail": "Port isochrones not generated"}


@pytest.mark.parametrize("corrupt", [True, False])
def test_actual_corrupt_graph_vs_impossible(client, synthetic, corrupt):
    store, body = synthetic
    if corrupt:
        store.nh_distance_graph.data[0] = float("nan")
        status, code = 503, "data_integrity"
    else:
        service = RoutingService()
        base = store.nh_node_xy[0]
        impossible = graph([base, base + (1000, 0), base + (500000, 0), base + (501000, 0)], [(0, 1), (2, 3)])
        store.__dict__.update(impossible.__dict__)
        point = location(service, store.nh_node_xy[-1])
        body["destination"] = dict(type="coordinate", latitude=point.latitude, longitude=point.longitude)
        status, code = 422, "route_not_available"
    response = client.post(PLAN, json=body)
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code


@pytest.mark.parametrize("kind,available", [("district", False), ("hub", False), ("district", True), ("hub", True)])
def test_actual_source_unavailable_vs_missing(client, synthetic, kind, available):
    store, body = synthetic
    if kind == "district":
        store.districts_df = pd.DataFrame({"district_code": [521], "district": ["Pune"], "state": ["Maharashtra"]}) if available else None
        body["origin"] = {"type": "district", "district_code": 999}
    else:
        store.hubs_dict = {"ports": pd.DataFrame({"name": ["Other"]}) if available else None}
        body["origin"] = {"type": "hub", "hub_type": "port", "name": "Missing"}
    response = client.post(PLAN, json=body)
    assert response.status_code == (404 if available else 503), response.text


@pytest.mark.parametrize("conflict", [True, False])
def test_actual_district_conflict_vs_ambiguity(client, synthetic, conflict):
    store, body = synthetic
    store.districts_df = pd.DataFrame({"district_code": [1, 2],
        "district": ["Repeated", "Repeated"], "state": ["Maharashtra", "Gujarat"]})
    body["origin"] = ({"type": "district", "district_code": 1, "state": "Gujarat"}
                      if conflict else {"type": "district", "district": "Repeated"})
    response = client.post(PLAN, json=body)
    assert response.status_code == (422 if conflict else 409), response.text
    if not conflict:
        assert response.json()["detail"]["candidates"] == [
            {"district": "Repeated", "state": "Maharashtra", "district_code": 1},
            {"district": "Repeated", "state": "Gujarat", "district_code": 2}]


def test_committed_pune_521_jnpt_rich_parity(client, monkeypatch):
    store = DataStore.get_instance()
    monkeypatch.setitem(app_module.app.dependency_overrides, get_data_store, lambda: store)
    body = {"origin": {"type": "district", "district_code": 521},
            "destination": {"type": "hub", "hub_type": "port", "name": "JNPT"},
            "vehicle_type": "2_AXLE_TRUCK", "geometry_detail": "simplified"}
    before = datetime.now(timezone.utc)
    expected = serialize_result(facade.plan_highway_route(HighwayRoutePlanRequest.model_validate(body), store))
    response = client.post(PLAN, json=body)
    assert response.status_code == 200, response.text
    actual = response.json()
    # Fresh generated timestamp is the only permitted parity difference.
    assert before <= datetime.fromisoformat(actual["metadata"]["generated_at_utc"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    actual["metadata"].pop("generated_at_utc")
    expected["metadata"].pop("generated_at_utc")
    assert actual == expected
