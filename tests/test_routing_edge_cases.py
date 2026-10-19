"""Exact projected synthetic regressions; no source data or display-based analysis."""
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import KDTree
from shapely.geometry import LineString

from server.models.phase1 import ResolvedLocation
from server.services.exceptions import DataIntegrityError, RouteNotAvailableError
from server.services.routing_service import RoutingService


@pytest.fixture
def service():
    return RoutingService()


def location(service, xy):
    lon, lat = service.to_wgs84.transform(*xy)
    return ResolvedLocation(type="coordinate", canonical_name="synthetic", latitude=lat,
                            longitude=lon, source_dataset="test", match_method="coordinate")


def graph(xy, edges, bridges=()):
    xy = np.asarray(xy, dtype=float)
    rows, cols, distances = [], [], []
    for u, v in edges:
        for a, b in [(u, v), (v, u)]:
            rows.append(a)
            cols.append(b)
            distances.append(np.linalg.norm(xy[u] - xy[v]) / 1000)
    dist = csr_matrix((distances, (rows, cols)), shape=(len(xy), len(xy)))
    time = dist / 70
    br = [(a, b) for u, v in bridges for a, b in [(u, v), (v, u)]]
    mask = csr_matrix(([1] * len(br), ([a for a, b in br], [b for a, b in br])), shape=time.shape)
    return SimpleNamespace(nh_graph=time, nh_distance_graph=dist, nh_bridge_mask=mask,
                           nh_node_xy=xy, nh_tree=KDTree(xy),
                           nh_comp_labels=connected_components(time, directed=False)[1], main_comp_label=999)


def test_threshold_before_min_sum(service):
    pair = service._find_compatible_component_pair(
        np.array([0., 100000.]), np.array([0, 1]),
        np.array([150001., 100000.]), np.array([2, 3]), np.array([0, 1, 0, 1]))
    assert pair == (1, 3, 100000., 100000., 1)


def test_dense_nodes_do_not_hide_compatible_component(service):
    base = np.array(service.to_proj.transform(78, 22))
    xy = [base + (i, 0) for i in range(151)] + [base + (0, 1000), base + (300000, 1000)]
    store = graph(xy, [(151, 152)])
    result = service.calculate_highway_route(location(service, base), location(service, xy[-1]), store=store)
    assert result.route_quality.connected_component == store.nh_comp_labels[151]
    assert result.network_distance_km == 300
    assert result.origin_access_distance_km == 1


def test_threshold_valid_alternative_end_to_end(service):
    base = np.array(service.to_proj.transform(78, 22))
    xy = [base + (x, 0) for x in (0, 169999, 100000, 220000)]
    store = graph(xy, [(0, 1), (2, 3)])
    result = service.calculate_highway_route(
        location(service, base), location(service, base + (320000, 0)), store=store)
    assert result.route_quality.connected_component == store.nh_comp_labels[2]
    assert result.origin_access_distance_km == result.destination_access_distance_km == 100
    assert result.network_distance_km == 120
    assert result.route_quality.quality == "low_confidence"


@pytest.mark.parametrize("detail,include", [("none", True), ("full", False), ("simplified", False)])
def test_geometry_suppression(service, detail, include):
    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [(0, 1)])
    result = service.calculate_highway_route(location(service, base), location(service, base + (1000, 0)),
                                             geometry_detail=detail, include_route_geometry=include, store=store)
    assert result.route_geometry is None
    assert not result.route_quality.geometry_simplified
    assert result.route_quality.geometry_tolerance_m is None


def test_unsupported_detail(service):
    with pytest.raises(ValueError, match="geometry_detail"):
        service.calculate_highway_route(None, None, geometry_detail="bad")


@pytest.mark.parametrize("isolated", [True, False])
def test_same_point_truthful_snap(service, isolated):
    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [] if isolated else [(0, 1)])
    loc = location(service, base + (0, 200))
    result = service.calculate_highway_route(loc, loc, store=store)
    assert result.route_geometry is None
    assert result.distance_km == result.drive_time_hours == 0
    assert result.route_quality.connected_component == 0
    assert result.route_quality.origin_snap_distance_km == .2
    assert result.origin_access_distance_km == 0
    assert result.origin_snapped != result.origin
    assert any("Zero movement" in w for w in result.route_quality.warnings)
    if isolated:
        assert result.route_quality.quality == "low_confidence"
        assert any("isolated" in w for w in result.route_quality.warnings)


@pytest.mark.parametrize("bridge", [True, False])
def test_exact_bridge_and_internal_path(service, bridge):
    base = np.array(service.to_proj.transform(78, 22))
    xy = [base, base + (1000, 0), base + (1300, 0), base + (2300, 0)]
    store = graph(xy, [(0, 1), (1, 2), (2, 3)], [(1, 2)] if bridge else [])
    result, path = service.calculate_highway_route_with_path(
        location(service, base + (-100, 0)), location(service, base + (2400, 0)),
        geometry_detail="none", store=store)
    q = result.route_quality
    assert q.synthetic_bridge_count == int(bridge)
    assert q.synthetic_bridge_distance_km == (.3 if bridge else 0)
    assert q.maximum_synthetic_bridge_m == (300 if bridge else 0)
    assert path.node_ids == (0, 1, 2, 3)
    assert path.edge_kinds == ("physical", "bridge" if bridge else "physical", "physical")
    assert np.array_equal(path.projected_coordinates, xy)
    assert len(path.origin_feeder) == len(path.destination_feeder) == 2
    assert all(type(value) is float for feeder in (path.origin_feeder, path.destination_feeder)
               for point in feeder for value in point)
    assert result.route_geometry is None


def test_metric_simplification_and_endpoints(service):
    base = np.array(service.to_proj.transform(78, 22))
    xy = [base, base + (1000, 40), base + (2000, 0), base + (3000, 80), base + (4000, 0)]
    store = graph(xy, [(i, i + 1) for i in range(4)])
    origin, dest = location(service, xy[0]), location(service, xy[-1])
    full = service.calculate_highway_route(origin, dest, geometry_detail="full", store=store)
    simple = service.calculate_highway_route(origin, dest, store=store)
    coords = simple.route_geometry["coordinates"]
    assert len(coords) == 4
    assert coords[0] == full.route_geometry["coordinates"][0] == [round(origin.longitude, 5), round(origin.latitude, 5)]
    assert coords[-1] == full.route_geometry["coordinates"][-1]
    projected = [service.to_proj.transform(*p) for p in coords]
    # Five decimal degree output adds < 1m rounding error at this fixture latitude.
    assert LineString(xy).hausdorff_distance(LineString(projected)) <= 51
    assert simple.route_quality.geometry_tolerance_m == 50


def test_invalid_metadata_rejected(service):
    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [(0, 1)])
    store.nh_comp_labels[:] = [0, 1]
    with pytest.raises(DataIntegrityError, match="metadata"):
        service.calculate_highway_route(location(service, base), location(service, base), store=store)


def test_same_point_remote_rejected(service):
    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [(0, 1)])
    loc = location(service, base + (0, 150001))
    with pytest.raises(RouteNotAvailableError, match="snap limit"):
        service.calculate_highway_route(loc, loc, store=store)


@pytest.mark.parametrize("latitude,longitude", [(float("nan"), 78), (22, float("inf")), (91, 78)])
def test_same_point_invalid_coordinates(service, latitude, longitude):
    from server.services.exceptions import LocationNotFoundError

    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [(0, 1)])
    loc = location(service, base).model_copy(update={"latitude": latitude, "longitude": longitude})
    with pytest.raises(LocationNotFoundError):
        service.calculate_highway_route(loc, loc, store=store)


def test_small_regional_component_is_not_rejected(service):
    from shapely.geometry import shape

    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [(0, 1)])
    result, path = service.calculate_highway_route_with_path(
        location(service, base), location(service, base + (1000, 0)), store=store)
    assert result.route_quality.connected_component == 0 != store.main_comp_label
    assert shape(result.route_geometry).is_valid
    assert path.crs_epsg == 7755


@pytest.mark.parametrize("field", ["nh_distance_graph", "nh_bridge_mask", "nh_tree"])
def test_missing_metadata_rejected(service, field):
    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [(0, 1)])
    setattr(store, field, None)
    with pytest.raises(DataIntegrityError, match="metadata"):
        service.calculate_highway_route(location(service, base), location(service, base), store=store)


@pytest.mark.parametrize("isolated", [False, True])
def test_distinct_endpoints_same_snap_are_feeder_only(service, monkeypatch, isolated):
    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [] if isolated else [(0, 1)])
    origin = location(service, base + (0, 20000))
    destination = location(service, base + (100, 20000))

    def no_dijkstra(*args, **kwargs):
        pytest.fail("Same-snap endpoints must not run Dijkstra")

    monkeypatch.setattr("server.services.routing_service.dijkstra", no_dijkstra)
    result, path = service.calculate_highway_route_with_path(
        origin, destination, geometry_detail="full", store=store)
    assert path.node_ids == (0,)
    assert path.edge_kinds == ()
    assert result.network_distance_km == 0
    # Preserve the minimum-snap objective: ~40km modelled feeders for 100m separation,
    # not an invented routable direct path. Disclose the pathology instead.
    assert result.distance_km == 40
    assert result.drive_time_hours > 0
    assert result.route_quality.quality == "low_confidence"
    assert any("no network traversal" in w and "feeder-only via snap node" in w
               for w in result.route_quality.warnings)
    assert any("Feeder sum exceeds 3x" in w for w in result.route_quality.warnings)
    assert len(result.route_geometry["coordinates"]) == 3
    assert all(type(value) is float for feeder in (path.origin_feeder, path.destination_feeder)
               for point in feeder for value in point)


@pytest.mark.parametrize("height,expected", [(1.0, False), (1.5, False), (1.5001, True)])
def test_feeder_excess_warning_strict_ratio_boundary(service, monkeypatch, height, expected):
    # Identity projection isolates the strict >3x boundary from CRS round-trip error.
    # Endpoint separation is 2m; feeder sum is 4m, 6m, or 6.0004m respectively.
    identity = SimpleNamespace(transform=lambda x, y: (x, y))
    monkeypatch.setattr(service, "to_proj", identity)
    monkeypatch.setattr(service, "to_wgs84", identity)
    store = graph([(0, -height), (2, -height)], [(0, 1)])
    origin = ResolvedLocation(type="coordinate", canonical_name="origin", latitude=height,
                              longitude=0, source_dataset="test", match_method="coordinate")
    destination = origin.model_copy(update={"longitude": 2, "canonical_name": "destination"})
    result, path = service.calculate_highway_route_with_path(origin, destination, store=store)
    assert path.node_ids == (0, 1)
    assert result.route_quality.quality == "network_exact"
    warnings = [w for w in result.route_quality.warnings if "Feeder sum exceeds 3x" in w]
    assert bool(warnings) is expected
    if expected:
        assert warnings == [
            ("Feeder sum exceeds 3x direct projected endpoint distance; straight-line feeders "
             "may dominate this estimate. Direct separation is diagnostic only, not a routable alternative.")
        ]


def test_zero_movement_skips_dijkstra_and_feeder_excess_warning(service, monkeypatch):
    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [(0, 1)])
    loc = location(service, base + (0, 20000))

    def no_dijkstra(*args, **kwargs):
        pytest.fail("Zero movement must not run Dijkstra")

    monkeypatch.setattr("server.services.routing_service.dijkstra", no_dijkstra)
    result, path = service.calculate_highway_route_with_path(loc, loc, store=store)
    assert path.origin_feeder == path.destination_feeder == ()
    assert not any("Feeder sum exceeds" in w for w in result.route_quality.warnings)


def test_exact_150km_radius_is_inclusive(service):
    store = graph([(150000, 0), (150000.001, 0)], [(0, 1)])
    indices, distances = service._radius_candidates(np.array([0., 0.]), store)
    assert indices.tolist() == [0]
    assert distances.tolist() == [150000.]
    assert service._find_compatible_component_pair(
        distances, indices, distances, indices, store.nh_comp_labels
    ) == (0, 0, 150000., 150000., 0)


@pytest.mark.parametrize("reverse", [False, True])
def test_snap_ties_are_stable_across_candidate_order(service, reverse):
    indices = np.array([3, 2, 1, 0]) if reverse else np.array([0, 1, 2, 3])
    # Both components tie on total distance; lowest origin node wins. Within each
    # component equal-distance candidates resolve to the lowest node ID as well.
    pair = service._find_compatible_component_pair(
        np.full(4, 100.), indices, np.full(4, 100.), indices, np.array([7, 7, 2, 2]))
    assert pair == (0, 0, 100., 100., 7)


@pytest.mark.parametrize("corruption", [
    "time_asymmetry", "distance_asymmetry", "bridge_asymmetry", "tree",
    "structure", "shape", "negative_labels", "bridge_over_350m",
])
def test_metadata_corruptions_rejected_before_routing(service, corruption):
    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0), base + (2000, 0)], [(0, 1), (1, 2)])
    if corruption == "time_asymmetry":
        store.nh_graph.data[0] *= 2
    elif corruption == "distance_asymmetry":
        store.nh_distance_graph.data[0] *= 2
    elif corruption == "bridge_asymmetry":
        store.nh_bridge_mask = csr_matrix(([1], ([0], [1])), shape=(3, 3))
    elif corruption == "tree":
        store.nh_tree = KDTree(store.nh_node_xy + (1, 0))
    elif corruption == "structure":
        # Symmetric, positive distances but a different edge topology than time.
        store.nh_distance_graph = graph(store.nh_node_xy, [(0, 2)]).nh_distance_graph
    elif corruption == "shape":
        store.nh_distance_graph = csr_matrix((2, 2))
    elif corruption == "negative_labels":
        store.nh_comp_labels[:] = -1
    else:
        store.nh_bridge_mask = graph(store.nh_node_xy, [(0, 1), (1, 2)], [(0, 1)]).nh_bridge_mask
    # Even a zero-movement request must not bypass validation.
    with pytest.raises(DataIntegrityError, match="metadata"):
        service.calculate_highway_route(location(service, base), location(service, base), store=store)


def test_mutated_graph_is_revalidated_not_memoized(service):
    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [(0, 1)])
    origin, destination = location(service, base), location(service, base + (1000, 0))
    service.calculate_highway_route(origin, destination, store=store)
    store.nh_comp_labels[:] = -1
    with pytest.raises(DataIntegrityError, match="metadata"):
        service.calculate_highway_route(origin, destination, store=store)


def test_model_limitations_are_explicit(service):
    base = np.array(service.to_proj.transform(78, 22))
    store = graph([base, base + (1000, 0)], [(0, 1)])
    result = service.calculate_highway_route(
        location(service, base), location(service, base + (1000, 0)), store=store)
    limitations = " ".join(result.metadata.limitations)
    assert "15m grid" in limitations and "grade-separated" in limitations
    assert "all-endpoint" in limitations and "350m" in limitations and "heuristic" in limitations
    assert "undirected" in limitations and "one-way" in limitations
    assert "network_exact" in limitations and "25km" in limitations and "physical ground truth" in limitations
