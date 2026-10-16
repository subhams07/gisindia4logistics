"""
tests/test_routing_service.py
Comprehensive unit, regression, and corridor benchmark tests for canonical RoutingService.
"""

import re

import pytest

from server.dependencies import DataStore
from server.models.phase1 import (
    DistrictLocation,
    HighwayRouteResult,
    HubLocation,
    ResolvedLocation,
    RouteQuality,
    VehicleType,
)
from server.services.exceptions import RouteNotAvailableError
from server.services.routing_service import RoutingService


@pytest.fixture(scope="module")
def store():
    """Initializes and returns the singleton DataStore instance with loaded highway graph."""
    return DataStore.get_instance()


@pytest.fixture(scope="module")
def routing_service():
    """Returns the singleton RoutingService instance."""
    return RoutingService.get_instance()


def test_pune_to_jnpt_benchmark_corridor(routing_service, store):
    """
    Validates the Pune -> JNPT short-haul freight corridor benchmark.
    Expected distance: ~120 - 170 km; Drive time: 2.0 - 4.0 hrs for MAV_20T.
    """
    orig = DistrictLocation(district="Pune", state="Maharashtra")
    dest = HubLocation(hub_type="port", name="JNPT")

    result = routing_service.calculate_highway_route(
        origin=orig,
        destination=dest,
        vehicle_type=VehicleType.MAV_20T,
        include_route_geometry=True,
        geometry_detail="simplified",
        store=store,
    )

    assert isinstance(result, HighwayRouteResult)
    assert 110.0 <= result.distance_km <= 180.0
    assert 2.0 <= result.drive_time_hours <= 4.5
    assert re.match(r"^\d+ hrs \d+ min$", result.drive_time_formatted)

    # Route quality & diagnostics
    assert isinstance(result.route_quality, RouteQuality)
    assert result.route_quality.quality in ("network_exact", "modelled_connectivity")
    assert result.route_quality.origin_snap_distance_km <= 15.0
    assert result.route_quality.destination_snap_distance_km <= 15.0
    assert result.route_quality.geometry_simplified is True

    # Geometry integrity
    assert result.route_geometry is not None
    assert result.route_geometry["type"] == "LineString"
    coords = result.route_geometry["coordinates"]
    assert len(coords) >= 10

    # Strict coordinate order: [[lon, lat], ...]
    for lon, lat in coords:
        assert 72.0 <= lon <= 75.0, f"Longitude {lon} out of expected Maharashtra bounds"
        assert 18.0 <= lat <= 20.0, f"Latitude {lat} out of expected Maharashtra bounds"

    # Endpoints conservation
    assert coords[0] == [round(result.origin.longitude, 5), round(result.origin.latitude, 5)]
    assert coords[-1] == [round(result.destination.longitude, 5), round(result.destination.latitude, 5)]


def test_delhi_to_jaipur_model_corridor(routing_service, store):
    """
    Existing model regression bounds, not independently verified corridor truth.

    The ~379km detour is explained in docs/routing_review.md; bounds are unchanged.
    """
    orig = HubLocation(hub_type="rail_station", code="NDLS")
    dest = DistrictLocation(district="Jaipur", state="Rajasthan")

    result = routing_service.calculate_highway_route(
        origin=orig,
        destination=dest,
        vehicle_type=VehicleType.TRUCK_2AXLE,
        include_route_geometry=True,
        geometry_detail="simplified",
        store=store,
    )

    assert 240.0 <= result.distance_km <= 420.0
    assert 4.0 <= result.drive_time_hours <= 8.0
    assert result.network_distance_km > 220.0


def test_vehicle_speed_scaling(routing_service, store):
    """
    Verifies that commercial vehicle class factors proportionally scale driving duration.
    LMV < LCV < 2_AXLE_TRUCK < MAV_20T < 7_AXLE_OVERSIZED.
    """
    orig = DistrictLocation(district="Pune", state="Maharashtra")
    dest = DistrictLocation(district="Nashik", state="Maharashtra")

    res_lmv = routing_service.calculate_highway_route(orig, dest, vehicle_type=VehicleType.LMV, store=store)
    res_lcv = routing_service.calculate_highway_route(orig, dest, vehicle_type=VehicleType.LCV, store=store)
    res_truck = routing_service.calculate_highway_route(orig, dest, vehicle_type=VehicleType.TRUCK_2AXLE, store=store)
    res_mav = routing_service.calculate_highway_route(orig, dest, vehicle_type=VehicleType.MAV_20T, store=store)
    res_heavy = routing_service.calculate_highway_route(orig, dest, vehicle_type=VehicleType.OVERSIZED_7AXLE, store=store)

    assert res_lmv.drive_time_hours < res_lcv.drive_time_hours
    assert res_lcv.drive_time_hours < res_truck.drive_time_hours
    assert res_truck.drive_time_hours < res_mav.drive_time_hours
    assert res_mav.drive_time_hours < res_heavy.drive_time_hours
    # Distance remains topological invariant
    assert res_lmv.distance_km == res_mav.distance_km == res_heavy.distance_km


def test_geometry_simplification_preserves_endpoints(routing_service, store):
    """
    Verifies that Douglas-Peucker simplification reduces coordinate count while conserving exact endpoints.
    """
    orig = DistrictLocation(district="Pune", state="Maharashtra")
    dest = HubLocation(hub_type="port", name="JNPT")

    res_full = routing_service.calculate_highway_route(
        orig, dest, include_route_geometry=True, geometry_detail="full", store=store
    )
    res_simp = routing_service.calculate_highway_route(
        orig, dest, include_route_geometry=True, geometry_detail="simplified", store=store
    )

    coords_full = res_full.route_geometry["coordinates"]
    coords_simp = res_simp.route_geometry["coordinates"]

    assert len(coords_simp) <= len(coords_full)
    assert coords_simp[0] == coords_full[0]
    assert coords_simp[-1] == coords_full[-1]
    assert res_simp.route_quality.geometry_simplified is True
    assert res_full.route_quality.geometry_simplified is False


def test_routing_to_disconnected_island_raises_route_not_available(routing_service, store):
    """
    Verifies that routing between mainland and an isolated island without road connectivity raises RouteNotAvailableError.
    """
    orig = DistrictLocation(district="Pune", state="Maharashtra")
    dest = DistrictLocation(district="South Andamans", state="Andaman And Nicobar Islands")

    with pytest.raises(RouteNotAvailableError) as exc_info:
        routing_service.calculate_highway_route(orig, dest, store=store)

    err_msg = str(exc_info.value)
    assert "No continuous highway network route available" in err_msg or "too remote" in err_msg


def test_routing_with_resolved_locations_directly(routing_service, store):
    """
    Verifies that pre-resolved ResolvedLocation instances can be passed directly to avoid duplicate resolution.
    """
    resolved_orig = ResolvedLocation(
        type="coordinate",
        canonical_name="Custom Origin",
        latitude=18.5204,
        longitude=73.8567,
        source_dataset="user",
        match_method="coordinate",
    )
    resolved_dest = ResolvedLocation(
        type="coordinate",
        canonical_name="Custom Destination",
        latitude=18.9444,
        longitude=72.8369,
        source_dataset="user",
        match_method="coordinate",
    )

    result = routing_service.calculate_highway_route(
        origin=resolved_orig,
        destination=resolved_dest,
        vehicle_type=VehicleType.LMV,
        store=store,
    )

    assert result.origin.canonical_name == "Custom Origin"
    assert result.destination.canonical_name == "Custom Destination"
    assert result.distance_km > 50.0


def test_origin_equals_destination_route(routing_service, store):
    """
    Verifies that routing to the exact same origin and destination produces a 0-distance route cleanly.
    """
    loc = DistrictLocation(district="Pune", state="Maharashtra")
    result = routing_service.calculate_highway_route(
        origin=loc,
        destination=loc,
        store=store,
    )
    assert result.distance_km == 0.0
    assert result.drive_time_hours == 0.0
    assert result.drive_time_formatted == "0 hrs 0 min"
    assert result.route_quality.quality == "network_exact"
    assert result.route_geometry is None
    assert any("Zero movement" in w for w in result.route_quality.warnings)


def test_synthetic_bridge_disclosure_metadata(routing_service, store):
    """
    Verifies that synthetic bridge diagnostics, component ID, and response metadata are fully populated.
    """
    orig = DistrictLocation(district="Indore", state="Madhya Pradesh")
    dest = HubLocation(hub_type="port", name="Kandla")

    result = routing_service.calculate_highway_route(
        origin=orig,
        destination=dest,
        vehicle_type=VehicleType.MAV_20T,
        store=store,
    )

    q = result.route_quality
    assert q.connected_component >= 0
    assert q.synthetic_bridge_count >= 0
    assert q.synthetic_bridge_distance_km >= 0.0
    assert q.maximum_synthetic_bridge_m >= 0.0
    assert q.quality in ("network_exact", "modelled_connectivity", "low_confidence")

    # Response Metadata
    assert result.metadata.api_version == "1.0.0"
    assert result.metadata.road_network_vintage is not None
    assert "National Highway" in result.metadata.limitations[0] or "National Highway" in result.metadata.limitations[1]


@pytest.mark.parametrize("origin,destination", [
    (DistrictLocation(district="Pune", state="Maharashtra"), HubLocation(hub_type="port", name="JNPT")),
    (HubLocation(hub_type="rail_station", code="NDLS"), DistrictLocation(district="Jaipur", state="Rajasthan")),
])
def test_real_corridor_metric_hausdorff(routing_service, store, origin, destination):
    from shapely.geometry import LineString, shape

    full, path = routing_service.calculate_highway_route_with_path(
        origin, destination, geometry_detail="full", store=store)
    simple = routing_service.calculate_highway_route(origin, destination, store=store)
    assert shape(full.route_geometry).is_valid
    assert shape(simple.route_geometry).is_valid
    full_xy = [routing_service.to_proj.transform(*p) for p in full.route_geometry["coordinates"]]
    simple_xy = [routing_service.to_proj.transform(*p) for p in simple.route_geometry["coordinates"]]
    # The 50m metric bound plus conservative allowance for both outputs' 5dp rounding.
    error_m = LineString(full_xy).hausdorff_distance(LineString(simple_xy))
    assert error_m <= 52, error_m
    assert full.route_geometry["coordinates"][0] == simple.route_geometry["coordinates"][0]
    assert full.route_geometry["coordinates"][-1] == simple.route_geometry["coordinates"][-1]
    assert len(path.projected_coordinates) == len(path.node_ids)
    assert len(path.edge_kinds) == len(path.node_ids) - 1
    print(f"{full.origin.canonical_name} -> {full.destination.canonical_name}: "
          f"{full.distance_km}km, {full.drive_time_hours}h, Hausdorff {error_m:.3f}m, "
          f"bridges {full.route_quality.synthetic_bridge_count}, "
          f"{len(full_xy)} -> {len(simple_xy)} display vertices")


def test_real_graph_warm_validation_count_and_latency(routing_service, store):
    """Three paired warm runs; timings are evidence, not a throughput benchmark."""
    from statistics import median
    from time import perf_counter
    from types import SimpleNamespace
    from unittest.mock import patch

    from scipy.sparse import csr_matrix
    from scripts.clean.graph_builder import validate_graph_metadata

    snapshot = store.get_highway_snapshot()
    raw = SimpleNamespace(nh_graph=csr_matrix(snapshot.nh_graph, copy=True),
                          nh_distance_graph=csr_matrix(snapshot.nh_distance_graph, copy=True),
                          nh_bridge_mask=csr_matrix(snapshot.nh_bridge_mask, copy=True),
                          nh_node_xy=snapshot.nh_node_xy, nh_comp_labels=snapshot.nh_comp_labels,
                          nh_tree=snapshot.nh_tree)
    origin = routing_service.resolver.resolve(DistrictLocation(district='Pune', state='Maharashtra'), store)
    destination = routing_service.resolver.resolve(HubLocation(hub_type='port', name='JNPT'), store)
    routing_service.calculate_highway_route(origin, destination, store=store)
    before, after = [], []
    with patch('scripts.clean.graph_builder.validate_graph_metadata', wraps=validate_graph_metadata) as validation:
        for _ in range(3):
            start = perf_counter()
            old = routing_service.calculate_highway_route(origin, destination, store=raw)
            before.append(perf_counter() - start)
            start = perf_counter()
            new = routing_service.calculate_highway_route(origin, destination, store=store)
            after.append(perf_counter() - start)
            exclude = {'metadata': {'generated_at_utc'}}
            assert old.model_dump(exclude=exclude) == new.model_dump(exclude=exclude)
        assert validation.call_count == 3
        baseline_calls = validation.call_count
        validation.reset_mock()
        for _ in range(3):
            routing_service.calculate_highway_route(origin, destination, store=store)
        assert validation.call_count == 0
        print(f'National graph {len(snapshot.nh_node_xy)} nodes / {snapshot.nh_graph.nnz} directed edges; '
              f'validation-every-call warm3={before}, median={median(before):.6f}s, validations={baseline_calls}; '
              f'published warm3={after}, median={median(after):.6f}s, validations={validation.call_count}; '
              f'distance={new.distance_km}km, bridges={new.route_quality.synthetic_bridge_count}')
