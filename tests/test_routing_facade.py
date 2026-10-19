"""Bounded shared facade tests; synthetic helper reused, no source generation."""
import json
import os
import subprocess
import sys
from unittest.mock import patch

import numpy as np
import pytest
from pydantic import ValidationError

from server.config import settings
from server.models.phase1 import CoordinateLocation, HighwayRoutePlanRequest, VehicleType
from server.models.schemas import RouteRequest, RouteResponse
from server.services import routing_facade as facade
from server.services.routing_service import RoutingService
from tests.test_routing_edge_cases import graph, location


@pytest.fixture
def synthetic(tmp_path):
    service = RoutingService()
    base = np.array(service.to_proj.transform(78, 22))
    xy = [base, base + (1000, 40), base + (2000, 0), base + (3000, 80), base + (4000, 0)]
    store = graph(xy, [(i, i + 1) for i in range(4)])
    store.data_dir = tmp_path
    points = [location(service, p) for p in (xy[0], xy[-1])]
    refs = [CoordinateLocation(latitude=p.latitude, longitude=p.longitude) for p in points]
    return store, refs


def rich(refs, **kwargs):
    return HighwayRoutePlanRequest(origin=refs[0], destination=refs[1], **kwargs)


def legacy(refs, **kwargs):
    return RouteRequest(origin=[refs[0].latitude, refs[0].longitude],
                        destination=[refs[1].latitude, refs[1].longitude], **kwargs)


@pytest.mark.parametrize("surface", ["rich", "legacy"])
def test_exactly_one_engine(synthetic, surface):
    store, refs = synthetic
    original = RoutingService.calculate_highway_route
    calls = []

    def spy(self, *args, **kwargs):
        calls.append(kwargs)
        return original(self, *args, **kwargs)

    with patch.object(RoutingService, "calculate_highway_route", spy):
        if surface == "rich":
            facade.plan_highway_route(rich(refs), store)
        else:
            facade.legacy_highway_route(legacy(refs), store)
    assert len(calls) == 1
    assert calls[0]["store"] is store
    assert calls[0]["geometry_detail"] == "none"


@pytest.mark.parametrize("detail,include", [("none", True), ("full", False),
                                           ("simplified", False), ("full", True), ("simplified", True)])
def test_geometry(synthetic, detail, include):
    store, refs = synthetic
    result = facade.plan_highway_route(rich(refs, geometry_detail=detail,
                                             include_route_geometry=include), store)
    assert (result.route_geometry is not None) == (include and detail != "none")
    assert result.route_quality.geometry_simplified == (include and detail == "simplified")
    if result.route_geometry:
        assert result.route_geometry["coordinates"][0] == [round(refs[0].longitude, 5), round(refs[0].latitude, 5)]


def test_vehicle_distance_time_and_rates(synthetic):
    store, refs = synthetic
    service = RoutingService()
    base = np.array(service.to_proj.transform(78, 22))
    # Long enough to distinguish every class after canonical 2dp time rounding.
    longer = graph([base, base + (65000, 0)], [(0, 1)])
    longer.data_dir = store.data_dir
    refs = [CoordinateLocation(latitude=p.latitude, longitude=p.longitude)
            for p in (location(service, base), location(service, base + (65000, 0)))]
    store = longer
    results = [facade.plan_highway_route(rich(refs, vehicle_type=v), store) for v in VehicleType]
    assert len({r.distance_km for r in results}) == 1
    assert all(a.drive_time_hours < b.drive_time_hours for a, b in zip(results, results[1:]))


def test_display_simplification_actual_vertices(synthetic):
    store, refs = synthetic
    full = facade.plan_highway_route(rich(refs, geometry_detail="full"), store)
    simplified = facade.plan_highway_route(rich(refs, geometry_detail="simplified"), store)
    assert len(full.route_geometry["coordinates"]) > len(simplified.route_geometry["coordinates"])
    assert full.distance_km == simplified.distance_km
    assert simplified.route_quality.geometry_tolerance_m == 50
    assert full.route_quality.geometry_tolerance_m is None


@pytest.mark.parametrize("distance,count", [(0, 0), (39, 0), (40, 1), (65, 1), (130, 2)])
@pytest.mark.parametrize("vehicle,rate", [(v.value, 340 if v == VehicleType.MAV_20T else
                                         120 if v == VehicleType.LMV else 220) for v in VehicleType])
def test_legacy_thresholds_and_rates(synthetic, distance, count, vehicle, rate):
    store, refs = synthetic
    result = facade.plan_highway_route(rich(refs), store).model_copy(update={"distance_km": float(distance)})
    with patch.object(RoutingService, "calculate_highway_route", return_value=result) as engine:
        envelope = facade.legacy_highway_route(legacy(refs, vehicle_type=vehicle), store)
    assert engine.call_count == 1
    assert envelope["tolls_encountered_count"] == count
    assert envelope["estimated_toll_cost_inr"] == count * rate
    assert envelope["origin_snapped"] == [result.origin_snapped.latitude, result.origin_snapped.longitude]
    assert envelope["drive_time_formatted"] == result.drive_time_formatted
    assert set(RouteResponse.model_fields) == set(envelope)
    RouteResponse.model_validate(envelope)
    json.dumps(envelope, allow_nan=False)
    assert "not official" in envelope["toll_estimation_method"]


def test_zero_and_same_snap_warnings(synthetic):
    store, refs = synthetic
    zero = facade.legacy_highway_route(legacy([refs[0], refs[0]]), store)
    assert zero["distance_km"] == zero["drive_time_hours"] == zero["estimated_toll_cost_inr"] == 0
    assert any("Zero movement" in w for w in zero["route_quality"]["warnings"])
    nearby = refs[0].model_copy(update={"latitude": refs[0].latitude + .00001})
    result = facade.legacy_highway_route(legacy([refs[0], nearby]), store)
    assert result["route_quality"]["quality"] == "low_confidence"
    assert any("same snap node" in w for w in result["route_quality"]["warnings"])


def test_actual_roots_and_refresh(synthetic, tmp_path, monkeypatch):
    store, refs = synthetic
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    for root, version in [(root_a, "A"), (root_b, "B")]:
        root.mkdir()
        (root / "version_manifest.json").write_text(json.dumps({"data_version": version}))
    monkeypatch.setattr(settings, "DATA_PATH", tmp_path / "wrong")
    for root, version in [(root_a, "A"), (root_b, "B"), (root_a, "A")]:
        store.data_dir = root
        result = facade.plan_highway_route(rich(refs), store)
        assert result.metadata.data_version == version
        assert facade.metadata_for_store(store).data_version == version
    (root_a / "version_manifest.json").write_text('{"data_version": 17}')
    assert facade.metadata_for_store(store).data_version == "A"
    facade.clear_routing_facade_cache()
    metadata = facade.metadata_for_store(store)
    assert metadata.data_version == "unknown"
    assert any("unavailable or invalid" in line for line in metadata.limitations)


def test_no_explicit_store_creation_or_error_reclassification(synthetic):
    store, refs = synthetic
    with patch.object(facade, "get_data_store", side_effect=AssertionError("not lazy")):
        facade.metadata_for_store(store)
        facade.plan_highway_route(rich(refs), store)
    with patch.object(RoutingService, "calculate_highway_route", side_effect=ValueError("invalid location")):
        with pytest.raises(ValueError, match="invalid location"):
            facade.plan_highway_route(rich(refs), store)


@pytest.mark.parametrize("field,value", [("vehicle_type", "NOT_A_VEHICLE"),
                                         ("include_route_geometry", 1),
                                         ("geometry_detail", "unknown")])
def test_mutated_typed_request_rejected_before_loading(synthetic, field, value):
    _, refs = synthetic
    request = rich(refs)
    setattr(request, field, value)
    with patch.object(facade, "get_data_store", side_effect=AssertionError("data loaded")), \
            patch.object(RoutingService, "calculate_highway_route") as engine:
        with pytest.raises(ValidationError):
            facade.plan_highway_route(request)
    engine.assert_not_called()


@pytest.mark.parametrize("transport", ["typed", "mapping"])
@pytest.mark.parametrize("reference,field,value", [
    (dict(type="coordinate", latitude=22, longitude=78), "latitude", True),
    (dict(type="coordinate", latitude=22, longitude=78), "longitude", "78"),
    (dict(type="district", district_code=521), "district_code", True),
    (dict(type="district", district_code=521), "district_code", 521.0),
    (dict(type="hub", hub_type="port", name="JNPT"), "hub_type", "unknown"),
])
def test_mutated_nested_reference_rejected_before_loading(synthetic, reference, field, value, transport):
    _, refs = synthetic
    request = HighwayRoutePlanRequest(origin=reference, destination=refs[1])
    setattr(request.origin, field, value)
    if transport == "mapping":
        request = {"origin": request.origin, "destination": request.destination}
    with patch.object(facade, "get_data_store", side_effect=AssertionError("data loaded")), \
            patch.object(RoutingService, "calculate_highway_route") as engine:
        with pytest.raises(ValidationError):
            facade.plan_highway_route(request)
    engine.assert_not_called()


def test_mutated_legacy_coordinate_list_rejected_before_loading(synthetic):
    _, refs = synthetic
    request = legacy(refs)
    request.origin.clear()
    with patch.object(facade, "get_data_store", side_effect=AssertionError("data loaded")), \
            patch.object(RoutingService, "calculate_highway_route") as engine:
        with pytest.raises(ValidationError):
            facade.legacy_highway_route(request)
    engine.assert_not_called()


def test_import_is_data_lazy():
    code = '''import sys
from unittest.mock import patch
import server.dependencies as deps
with patch.object(deps.DataStore, "get_instance", side_effect=AssertionError("data loaded")), patch.object(deps.DataStore, "__init__", side_effect=AssertionError("store created")):
 import server.services.routing_facade
assert "matplotlib.pyplot" not in sys.modules
'''
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    completed = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_root_scoped_collaborators_and_cache_bound(synthetic, tmp_path):
    store, refs = synthetic
    facade.clear_routing_facade_cache()
    for number in range(10):
        store.data_dir = tmp_path / str(number)
        facade.metadata_for_store(store)
        service = facade._service_for_store(store)
        assert service.resolver.data_dir == store.data_dir.resolve()
        assert service.metadata_service.manifest_path == store.data_dir.resolve() / "version_manifest.json"
    assert facade._service_for_root.cache_info().currsize == 8
    facade.plan_highway_route(rich(refs), store)


def test_raw_fixture_compatibility_and_graph_revalidation(synthetic):
    from server.services.exceptions import DataIntegrityError

    store, refs = synthetic
    del store.data_dir
    assert facade._service_for_store(store).resolver.data_dir == settings.DATA_PATH.resolve()
    facade.plan_highway_route(rich(refs), store)
    store.nh_distance_graph.data[0] = float("nan")
    with pytest.raises(DataIntegrityError):
        facade.plan_highway_route(rich(refs), store)


def test_legacy_precision_and_nonfinite_rejection(synthetic):
    store, refs = synthetic
    result = facade.plan_highway_route(rich(refs), store)
    precise = result.model_copy(update={"distance_km": 65.16, "drive_time_hours": 1.234})
    with patch.object(RoutingService, "calculate_highway_route", return_value=precise):
        envelope = facade.legacy_highway_route(legacy(refs), store)
    assert envelope["distance_km"] == 65.2
    assert envelope["drive_time_hours"] == 1.23
    invalid = result.model_copy(update={"drive_time_hours": float("nan")})
    with patch.object(RoutingService, "calculate_highway_route", return_value=invalid):
        with pytest.raises(ValueError, match="Non-finite"):
            facade.legacy_highway_route(legacy(refs), store)


@pytest.mark.parametrize("surface", ["rich", "legacy"])
def test_engine_model_validation_is_data_integrity(synthetic, surface):
    from server.models.phase1 import RoutePoint
    from server.services.error_contract import route_error_contract
    from server.services.exceptions import DataIntegrityError

    store, refs = synthetic

    def invalid_calculation(*args, **kwargs):
        return RoutePoint(latitude=999, longitude=0)

    with patch.object(RoutingService, "calculate_highway_route", side_effect=invalid_calculation):
        with pytest.raises(DataIntegrityError) as caught:
            if surface == "rich":
                facade.plan_highway_route(rich(refs), store)
            else:
                facade.legacy_highway_route(legacy(refs), store)
    assert route_error_contract(caught.value)["status"] == 503


def test_stored_invalid_station_code_is_data_integrity(synthetic):
    import pandas as pd

    from server.services.exceptions import DataIntegrityError

    store, refs = synthetic
    store.rail_stations_df = pd.DataFrame([dict(
        station_name="Synthetic validation station", station_code="TEST_BAD_CODE", district_code=0,
        latitude=refs[0].latitude, longitude=refs[0].longitude)])
    request = HighwayRoutePlanRequest(
        origin=dict(type="hub", hub_type="rail_station", code="TEST_BAD_CODE"), destination=refs[1])
    with pytest.raises(DataIntegrityError):
        facade.plan_highway_route(request, store)


def test_invalid_calculated_envelope_is_not_client_validation(synthetic):
    from server.services.error_contract import route_error_contract
    from server.services.exceptions import DataIntegrityError

    store, refs = synthetic
    result = facade.plan_highway_route(rich(refs), store).model_copy(update={"distance_km": -1})
    with patch.object(RoutingService, "calculate_highway_route", return_value=result):
        with pytest.raises(DataIntegrityError) as caught:
            facade.legacy_highway_route(legacy(refs), store)
    assert route_error_contract(caught.value)["status"] == 503


def test_committed_corridor_metadata_and_legacy_parity():
    from server.dependencies import get_data_store

    store = get_data_store()
    request = HighwayRoutePlanRequest.model_validate(dict(
        origin=dict(type="district", district_code=521),
        destination=dict(type="hub", hub_type="port", name="JNPT")))
    result = facade.plan_highway_route(request, store)
    assert result.route_geometry is None
    assert result.metadata.data_version == facade.metadata_for_store(store).data_version
    assert result.metadata.road_network_vintage is not None
    refs = [CoordinateLocation(latitude=p.latitude, longitude=p.longitude)
            for p in (result.origin, result.destination)]
    envelope = facade.legacy_highway_route(legacy(refs), store)
    assert envelope["distance_km"] == round(result.distance_km, 1)
    assert envelope["drive_time_hours"] == round(result.drive_time_hours, 2)
    assert envelope["route_quality"] == result.route_quality.model_dump(mode="json")
    json.dumps(envelope, allow_nan=False)
