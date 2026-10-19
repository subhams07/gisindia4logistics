"""Deterministic pure-kernel and live-scalar parity; never load source datasets."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import weakref
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pytest
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import KDTree
from shapely.geometry import LineString

from scripts.clean.graph_builder import build_canonical_highway_graph, validate_graph_metadata
from server.models.phase1 import VehicleType
from server.services.exceptions import DataIntegrityError
from server.services.graph_snapshot import GRAPH_FIELDS, GraphSnapshot
from server.services.routing_service import RoutingService
from tests.test_routing_edge_cases import location


@pytest.fixture
def kernel():
    return importlib.import_module("scripts.analyze.highway_pair_kernel")


def fixture_graph(xy, edges, speeds=None, bridges=()):
    xy = np.asarray(xy, dtype=float)
    rows, cols, km, hours = [], [], [], []
    for i, (u, v) in enumerate(edges):
        distance = float(np.linalg.norm(xy[u] - xy[v]) / 1000)
        for a, b in ((u, v), (v, u)):
            rows.append(a)
            cols.append(b)
            km.append(distance)
            hours.append(distance / (70 if speeds is None else speeds[i]))
    shape = (len(xy), len(xy))
    time = csr_matrix((hours, (rows, cols)), shape=shape)
    distance = csr_matrix((km, (rows, cols)), shape=shape)
    marked = [(a, b) for u, v in bridges for a, b in ((u, v), (v, u))]
    mask = csr_matrix(([1] * len(marked),
                       ([a for a, b in marked], [b for a, b in marked])), shape=shape)
    graph = SimpleNamespace(nh_graph=time, nh_distance_graph=distance, nh_node_xy=xy,
                            nh_comp_labels=connected_components(time, directed=False)[1],
                            nh_tree=KDTree(xy), nh_bridge_mask=mask)
    validate_graph_metadata(*(getattr(graph, field) for field in GRAPH_FIELDS))
    return graph


def faster_detour():
    return fixture_graph([(0, 0), (1000, 0), (500, 600)],
                         [(0, 1), (0, 2), (2, 1)], [35, 90, 90])


@pytest.mark.parametrize("published", [False, True])
def test_fastest_predecessor_path_not_shortest_distance(kernel, published):
    graph = faster_detour()
    if published:
        graph = GraphSnapshot(*(getattr(graph, field) for field in GRAPH_FIELDS))
    metric = kernel.pair_network_metrics(graph, [(0, 1)])[0]
    expected_km = 2 * np.hypot(500, 600) / 1000
    assert metric.network_distance_km == pytest.approx(expected_km)
    assert metric.network_distance_km > dijkstra(graph.nh_distance_graph, indices=0)[1]
    assert metric.network_time_hours == pytest.approx(expected_km / 90)
    assert metric.synthetic_bridge_count == 0
    assert not hasattr(metric, "node_ids")


def test_duplicate_physical_and_bridge_builder_metrics(kernel):
    frame = gpd.GeoDataFrame({
        "geometry": [LineString([(0, 0), (300, 0)]),
                     LineString([(0, 0), (300, 0)]),
                     LineString([(600, 0), (900, 0)])],
        "highway": ["primary", "motorway", "trunk"]}, crs=7755)
    graph = SimpleNamespace(**dict(zip(GRAPH_FIELDS, build_canonical_highway_graph(frame))))
    assert graph.nh_distance_graph[0, 1] == .3
    assert graph.nh_graph[0, 1] == pytest.approx(.3 / 90)
    assert graph.nh_bridge_mask[0, 1] == 0
    metric = kernel.pair_network_metrics(graph, [(0, 3)])[0]
    assert metric.network_distance_km == pytest.approx(.9)
    assert metric.network_time_hours == pytest.approx(.3 / 90 + .3 / 35 + .3 / 70)
    assert metric.synthetic_bridge_count == 1
    assert metric.synthetic_bridge_distance_km == .3
    assert metric.maximum_synthetic_bridge_m == 300


def test_radius_threshold_inclusive(kernel):
    graph = fixture_graph([(150000, 0), (150000.001, 0)], [(0, 1)])
    ids, distances = kernel.radius_candidates(np.array([0., 0.]), graph)
    assert ids.tolist() == [0]
    assert distances.tolist() == [150000.]


def test_threshold_before_nearest_and_sparse_labels(kernel):
    labels = np.array([2**40, 7, 2**40, 7], dtype=np.int64)
    assert kernel.select_compatible_pair([0, 100000], [0, 1], [150001, 100000], [2, 3], labels) == (
        1, 3, 100000., 100000., 7)
    assert kernel.select_compatible_pair([150000], [0], [150000], [2], labels) == (
        0, 2, 150000., 150000., 2**40)
    assert kernel.select_compatible_pair([0], [0], [0], [1], labels) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 150000.001])
def test_invalid_or_over_threshold_distances_excluded(kernel, bad):
    assert kernel.nearest_per_component([bad, 10], [0, 1], np.array([0, 0])) == {0: (10., 1)}


@pytest.mark.parametrize("reverse", [False, True])
def test_stable_ties_and_small_components(kernel, reverse):
    # No component-size preference: two-node label 2**40 beats 200-node label 7.
    labels = np.array([2**40, 2**40] + [7] * 200)
    ids = list(range(202))
    if reverse:
        ids.reverse()
    distances = [100.] * len(ids)
    assert kernel.select_compatible_pair(distances, ids, distances, ids, labels) == (
        0, 0, 100., 100., 2**40)
    # The destination ID is the next tie-breaker after equal sums/origin IDs.
    assert kernel.select_compatible_pair([10, 20], [0, 2], [30, 20], [1, 3], labels) == (
        0, 1, 10., 30., 2**40)


def test_all_candidates_not_top_150(kernel):
    graph = fixture_graph([(i, 0) for i in range(151)] + [(0, 1000), (300000, 1000)], [(151, 152)])
    oi, od = kernel.radius_candidates(np.array([0., 0.]), graph)
    di, dd = kernel.radius_candidates(np.array([300000., 1000.]), graph)
    assert kernel.select_compatible_pair(od, oi, dd, di, graph.nh_comp_labels)[:2] == (151, 152)


@pytest.mark.parametrize("bad", [True, np.bool_(False), "0", 0.0, -1, 3])
def test_pair_ids_not_coerced(kernel, bad):
    with pytest.raises(DataIntegrityError, match="Invalid highway node index"):
        kernel.pair_network_metrics(faster_detour(), [(bad, 1)])


@pytest.mark.parametrize("bad", [True, "0", 0.0, -1, 2])
def test_snap_ids_not_coerced(kernel, bad):
    with pytest.raises(DataIntegrityError, match="Invalid highway node index"):
        kernel.select_compatible_pair([0], [bad], [0], [1], np.array([0, 0]))


@pytest.mark.parametrize("chain,message", [
    ([-9999, -9999, -9999], "Predecessor chain broken"),
    ([-9999, 2, 1], "Predecessor chain broken"),
    ([-9999, 9, 1], "Invalid predecessor index"),
    ([-9999, True, 1], "Invalid predecessor index"),
    ([-9999, "0", 1], "Invalid predecessor index"),
])
def test_corrupt_chains(kernel, chain, message):
    with pytest.raises(DataIntegrityError, match=message):
        kernel.reconstruct_path(0, 2, chain)


def test_reconstruct_scalar_parity(kernel):
    assert kernel.reconstruct_path(0, 2, np.array([-9999, 0, 1])) == [0, 1, 2]
    assert kernel.reconstruct_path(1, 1, [-9999, -9999]) == [1]


def test_repeated_and_disconnected_and_same_snap(kernel, monkeypatch):
    graph = fixture_graph([(0, 0), (1000, 0), (1000000, 0)], [(0, 1)])
    original = kernel.dijkstra
    calls = []
    def spy(*args, **kwargs):
        calls.append(list(kwargs["indices"]))
        return original(*args, **kwargs)
    monkeypatch.setattr(kernel, "dijkstra", spy)
    outputs = kernel.pair_network_metrics(graph, [(0, 1), (0, 1), (1, 1), (0, 2), (2, 2)])
    assert outputs[0] == outputs[1]
    assert outputs[2].network_time_hours == outputs[4].network_distance_km == 0
    assert outputs[3] is None
    assert calls == [[0]]
    calls.clear()
    assert kernel.pair_network_metrics(graph, []) == []
    kernel.pair_network_metrics(graph, [(0, 0), (2, 2), (0, 2)])
    assert calls == []


def test_same_component_unreachable_is_integrity(kernel):
    graph = fixture_graph([(0, 0), (1000, 0)], [])
    graph.nh_comp_labels[:] = 0  # deliberate post-validation corruption
    with pytest.raises(DataIntegrityError, match="Same-component destination is unreachable on the highway graph"):
        kernel.pair_network_metrics(graph, [(0, 1)])


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_invalid_distance_fails(kernel, bad):
    graph = faster_detour()
    graph.nh_distance_graph[0, 2] = bad
    with pytest.raises(DataIntegrityError, match="Invalid highway edge distance metadata"):
        kernel.pair_network_metrics(graph, [(0, 1)])


@pytest.mark.parametrize("bad", [-1, 2, float("nan"), float("inf")])
def test_bridge_corruption_fails(kernel, bad):
    graph = fixture_graph([(0, 0), (300, 0)], [(0, 1)], bridges=[(0, 1)])
    graph.nh_bridge_mask = graph.nh_bridge_mask.astype(float)
    graph.nh_bridge_mask[0, 1] = bad
    with pytest.raises(DataIntegrityError, match="Invalid highway edge distance/bridge metadata"):
        kernel.pair_network_metrics(graph, [(0, 1)])


def test_bridge_over_threshold_fails(kernel):
    graph = faster_detour()
    graph.nh_bridge_mask = graph.nh_graph.copy()
    graph.nh_bridge_mask.data[:] = 1
    with pytest.raises(DataIntegrityError, match="Invalid highway edge distance/bridge metadata"):
        kernel.pair_network_metrics(graph, [(0, 1)])


@pytest.mark.parametrize("chunk", [0, 33, -1, True, "2", 1.5])
def test_invalid_chunk_size(kernel, chunk):
    with pytest.raises(ValueError, match="chunk_size"):
        kernel.pair_network_metrics(faster_detour(), [(0, 1)], chunk_size=chunk)


@pytest.mark.parametrize("chunk_size", [1, 7, 32])
def test_real_dijkstra_allocation_bound_and_release(kernel, monkeypatch, chunk_size):
    graph = fixture_graph([(i * 1000, 0) for i in range(70)], [(i, i + 1) for i in range(69)])
    original = kernel.dijkstra
    allocations, prior = [], []
    def spy(*args, **kwargs):
        assert all(ref() is None for ref in prior), "previous chunk buffers retained"
        assert args[0] is graph.nh_graph  # never distance Dijkstra
        assert kwargs["return_predecessors"] is True
        assert kwargs["directed"] is False
        result = original(*args, **kwargs)
        allocations.append(result[0].shape)
        assert result[0].shape[0] <= chunk_size <= 32
        prior[:] = [weakref.ref(array) for array in result]
        return result
    monkeypatch.setattr(kernel, "dijkstra", spy)
    pairs = [(u, 69) for u in range(69)] + [(0, 69), (0, 1)]
    outputs = kernel.pair_network_metrics(graph, pairs, chunk_size=chunk_size)
    assert len(outputs) == len(pairs)
    assert sum(shape[0] for shape in allocations) == 69
    assert max(shape[0] for shape in allocations) <= chunk_size
    assert all(ref() is None for ref in prior)
    for (u, v), metric in zip(pairs, outputs):
        assert metric.network_distance_km == v - u
        assert metric.network_time_hours == pytest.approx((v - u) / 70)


@pytest.mark.parametrize("seed", [1728, 51])
def test_seeded_live_scalar_pair_and_feeder_parity(kernel, seed):
    rng = np.random.default_rng(seed)
    service = RoutingService()
    base = np.array(service.to_proj.transform(78, 22))
    xy = base + np.column_stack((np.arange(10) * 1000, rng.uniform(-100, 100, 10)))
    graph = fixture_graph(xy, [(i, i + 1) for i in range(9)] + [(0, 9)],
                          list(rng.uniform(35, 90, 9)) + [35])
    for _ in range(15):
        a, b = rng.choice(10, 2, replace=False)
        origin = location(service, xy[a] + (0, 130))
        destination = location(service, xy[b] + (0, -170))
        op = np.array(service.to_proj.transform(origin.longitude, origin.latitude))
        dp = np.array(service.to_proj.transform(destination.longitude, destination.latitude))
        oi, od = kernel.radius_candidates(op, graph)
        di, dd = kernel.radius_candidates(dp, graph)
        selected = kernel.select_compatible_pair(od, oi, dd, di, graph.nh_comp_labels)
        assert selected == service._find_compatible_component_pair(od, oi, dd, di, graph.nh_comp_labels)
        u, v, om, dm, _ = selected
        metric = kernel.pair_network_metrics(graph, [(u, v)])[0]
        # Independent scalar predecessor walk, not delegated helpers, is the oracle.
        times, predecessors = dijkstra(graph.nh_graph, directed=False, indices=u, return_predecessors=True)
        curr, distance = v, 0.
        while curr != u:
            prev = predecessors[curr]
            distance += float(graph.nh_distance_graph[prev, curr])
            curr = prev
        assert metric.network_time_hours == times[v]
        assert metric.network_distance_km == pytest.approx(distance)
        for vehicle, factor in ((VehicleType.LMV, 1.), (VehicleType.MAV_20T, .8)):
            result, path = service.calculate_highway_route_with_path(
                origin, destination, vehicle_type=vehicle, geometry_detail="none", store=graph)
            assert path.node_ids[0] == u and path.node_ids[-1] == v
            assert result.network_distance_km == round(metric.network_distance_km, 2)
            assert result.distance_km == round(metric.network_distance_km + (om + dm) / 1000, 2)
            assert result.drive_time_hours == round(
                metric.network_time_hours / factor + (om / 1000 + dm / 1000) / (35 * factor), 2)


def test_pure_import_and_execution_no_server_or_store_initialization():
    root = Path(__file__).resolve().parents[1]
    code = '''
import sys
from pathlib import Path
from types import SimpleNamespace
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
class NoServer:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'server' or fullname.startswith('server.'):
            raise AssertionError('Pure kernel imported server/config/store')
sys.meta_path.insert(0, NoServer())
from scripts.analyze import highway_pair_kernel as kernel
from scripts.clean.graph_builder import validate_graph_metadata
import numpy as np
from scipy.sparse import csr_matrix
from scipy.spatial import KDTree
assert Path(kernel.__file__).resolve() == root / 'scripts/analyze/highway_pair_kernel.py'
xy = np.array([[0., 0.], [300., 0.]])
time = csr_matrix(([.3/70, .3/70], ([0, 1], [1, 0])), shape=(2, 2))
dist = time * 70
labels, tree, mask = np.array([0, 0]), KDTree(xy), csr_matrix((2, 2))
validate_graph_metadata(time, dist, xy, labels, tree, mask)
graph = SimpleNamespace(nh_graph=time, nh_distance_graph=dist, nh_node_xy=xy,
                        nh_comp_labels=labels, nh_tree=tree, nh_bridge_mask=mask)
ids, ds = kernel.radius_candidates(xy[0], graph)
assert kernel.select_compatible_pair(ds, ids, ds, ids, labels)[:2] == (0, 0)
assert kernel.pair_network_metrics(graph, [(0, 1)])[0].network_distance_km == .3
'''
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, "-B", "-c", code, str(root)], env=env,
                            cwd=root, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_dijkstra_corrupt_predecessor_fails(kernel, monkeypatch):
    graph = faster_detour()
    original = kernel.dijkstra
    def corrupt(*args, **kwargs):
        hours, predecessors = original(*args, **kwargs)
        predecessors[0, 1] = 1
        return hours, predecessors
    monkeypatch.setattr(kernel, "dijkstra", corrupt)
    with pytest.raises(DataIntegrityError, match="Predecessor chain broken"):
        kernel.pair_network_metrics(graph, [(0, 1)])


def test_helper_delegation_keeps_instance_patchability(kernel, monkeypatch):
    service = RoutingService()
    graph = faster_detour()
    sentinel = ([0], np.array([0.]))
    monkeypatch.setattr(kernel, "radius_candidates", lambda *a, **k: sentinel)
    assert service._radius_candidates([0, 0], graph) is sentinel
    monkeypatch.setattr(kernel, "select_compatible_pair", lambda *a, **k: "pair")
    assert service._find_compatible_component_pair([], [], [], [], []) == "pair"
    monkeypatch.setattr(kernel, "reconstruct_path", lambda *a: [7])
    assert service._reconstruct_path(0, 1, []) == [7]
    monkeypatch.setattr(kernel, "analyze_path_edges", lambda *a: (1, 2, 3, 4))
    assert service._analyze_path_edges([], graph) == (1, 2, 3, 4)
