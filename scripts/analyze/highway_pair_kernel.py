"""Pure bounded highway pair calculations over an already captured graph.

The caller supplies a published GraphSnapshot or validated raw nh_* fields and
keeps that capture coherent for the call. No source/config/store/cache loading,
graph publication, vehicle adjustment, feeder time, rounding or producer I/O.
"""
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from scipy.sparse.csgraph import dijkstra

MAX_FEEDER_DISTANCE_M = 150000.0
MAX_SOURCE_CHUNK = 32


def _integrity(message):
    # Lazy import avoids services package initialization (and its routing import)
    # when analytical callers import/use this pure module on valid captures.
    from server.services.exceptions import DataIntegrityError

    return DataIntegrityError(message)


def _node_id(value, size, message="Invalid highway node index"):
    if (isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, np.integer)) or not 0 <= value < size):
        raise _integrity(message)
    return int(value)


def radius_candidates(point, graph, *, max_distance_m=MAX_FEEDER_DISTANCE_M):
    """Return (node IDs, projected metre distances) for all nodes in the radius.

    point is an EPSG:7755 two-vector; graph supplies nh_tree and nh_node_xy.
    Candidate order is the tree's order, as in live routing; selection resolves
    ties separately. The inclusive default radius is 150000 metres.
    """
    idx = np.asarray(graph.nh_tree.query_ball_point(point, max_distance_m), dtype=int)
    distances = np.linalg.norm(graph.nh_node_xy[idx] - point, axis=1)
    return idx, distances


def nearest_per_component(distances, indices, comp_labels, *,
                          max_distance_m=MAX_FEEDER_DISTANCE_M):
    """Return {component: (metres, node ID)} after inclusive threshold filtering.

    Parallel distance/ID iterables follow the live zip behavior. Nonfinite,
    negative or over-threshold distances are excluded BEFORE choosing a minimum.
    Equal distances prefer the smaller node ID; sparse labels use dicts, never
    allocation proportional to the maximum label. IDs must be integers, not
    bools or numeric strings. No component-size or mainland preference.
    """
    best = {}
    for distance, idx in zip(distances, indices):
        if not np.isfinite(distance) or not 0 <= distance <= max_distance_m:
            continue
        idx = _node_id(idx, len(comp_labels))
        comp = int(comp_labels[idx])
        value = (float(distance), idx)
        if comp not in best or value < best[comp]:
            best[comp] = value
    return best


MIN_SUPPORTED_COMPONENT_NODES = 1000


def component_sizes(comp_labels):
    """Node count per component label as plain ints; labels may be sparse."""
    values, counts = np.unique(comp_labels, return_counts=True)
    return {int(value): int(count) for value, count in zip(values, counts)}


def nearest_supported(distances, indices, comp_labels, sizes, *,
                      min_nodes=None,
                      max_distance_m=MAX_FEEDER_DISTANCE_M):
    """Return (metres, node ID, component size) of the closest supported-component node, or None.

    Single-source analogue of closest-compatible snapping: the candidate set is
    nearest_per_component (same threshold filter and node-ID ties) restricted to
    components of at least min_nodes nodes, so a hub is never snapped to a tiny
    disconnected fragment when a supported component lies within the limit.
    Ties across components prefer the smaller node ID, then the smaller label.
    """
    min_nodes = MIN_SUPPORTED_COMPONENT_NODES if min_nodes is None else min_nodes
    per_component = nearest_per_component(distances, indices, comp_labels, max_distance_m=max_distance_m)
    supported = [(distance, node, comp) for comp, (distance, node) in per_component.items()
                 if sizes[comp] >= min_nodes]
    if not supported:
        return None
    distance, node, comp = min(supported, key=lambda item: (item[0], item[1], item[2]))
    return distance, node, sizes[comp]


def select_compatible_pair(dists_orig, idxs_orig, dists_dest, idxs_dest, comp_labels, *,
                           max_distance_m=MAX_FEEDER_DISTANCE_M):
    """Return (u, v, origin metres, destination metres, label), or None.

    Both endpoints must have a threshold-valid node on the same component.
    Selection minimizes (sum of feeders, origin ID, destination ID, label).
    This is closest-compatible snapping, NOT global door-to-door optimization.
    None is valid no-compatible-pair unreachability, handled by the caller.
    """
    o = nearest_per_component(dists_orig, idxs_orig, comp_labels, max_distance_m=max_distance_m)
    d = nearest_per_component(dists_dest, idxs_dest, comp_labels, max_distance_m=max_distance_m)
    common = o.keys() & d.keys()
    if not common:
        return None
    comp = min(common, key=lambda c: (o[c][0] + d[c][0], o[c][1], d[c][1], c))
    return o[comp][1], d[comp][1], o[comp][0], d[comp][0], comp


def reconstruct_path(orig_node_idx, dest_node_idx, predecessors):
    """Return one scalar time-Dijkstra predecessor path as a list of node IDs.

    predecessors is the row for orig_node_idx, with SciPy's -9999 sentinel.
    Broken/cyclic/out-of-range/noninteger chains raise the existing domain error;
    no per-node chain cache is constructed. Same-snap returns the checked node.
    """
    message = "Invalid predecessor index during shortest path reconstruction"
    orig_node_idx = _node_id(orig_node_idx, len(predecessors), message)
    dest_node_idx = _node_id(dest_node_idx, len(predecessors), message)
    path, visited = [], set()
    curr = dest_node_idx
    while curr != -9999:
        curr = _node_id(curr, len(predecessors), message)
        if curr in visited:
            break
        visited.add(curr)
        path.append(curr)
        if curr == orig_node_idx:
            return path[::-1]
        curr = predecessors[curr]
    raise _integrity("Predecessor chain broken during shortest path reconstruction")


def analyze_path_edges(path_nodes, graph):
    """Return (network km, bridge count, bridge km, maximum bridge metres).

    Sum distance metadata on the supplied time-predecessor path, not a second
    shortest-distance path. A singleton has zero metrics. Coherence was validated
    at capture; defensive checks reject corrupt traversed distance/bridge values.
    """
    total, count, bridges, maximum = 0.0, 0, 0.0, 0.0
    for u, v in pairwise(path_nodes):
        u = _node_id(u, graph.nh_distance_graph.shape[0])
        v = _node_id(v, graph.nh_distance_graph.shape[0])
        km = float(graph.nh_distance_graph[u, v])
        if not np.isfinite(km) or km <= 0:
            raise _integrity("Invalid highway edge distance metadata")
        total += km
        bridge = graph.nh_bridge_mask[u, v]
        if (not np.isfinite(bridge) or bridge not in (0, 1)
                or (bridge and km * 1000 > 350.0 + 1e-6)):
            raise _integrity("Invalid highway edge distance/bridge metadata")
        if bridge:
            count += 1
            bridges += km
            maximum = max(maximum, km * 1000)
    if not np.isfinite([total, bridges, maximum]).all():
        raise _integrity("Invalid highway edge distance metadata")
    return total, count, bridges, maximum


@dataclass(frozen=True)
class PairNetworkMetrics:
    """Unrounded network-only values; no paths, feeders or vehicle adjustments."""

    network_time_hours: float
    network_distance_km: float
    synthetic_bridge_count: int
    synthetic_bridge_distance_km: float
    maximum_synthetic_bridge_m: float


def pair_network_metrics(graph, pairs, *, chunk_size=MAX_SOURCE_CHUNK):
    """Return a list of PairNetworkMetrics | None in input pair order.

    graph is an already validated coherent nh_* capture (snapshot or raw fixture),
    never a default datastore. pairs is an iterable of integer (u, v) node IDs,
    usually produced by select_compatible_pair. Repetitions are preserved. Cross-
    component pairs yield None: the caller handles valid unreachability. Same-
    snap pairs yield zero without Dijkstra. Invalid IDs (including bool/string),
    same-component unreachable nodes, broken predecessor chains or corrupt
    traversed metadata fail as DataIntegrityError, not missing-route nulls.

    Group requests by source; run UNDIRECTED TIME Dijkstra in source chunks of
    1..32. Distances and bridge metrics come from each target's SAME predecessor
    path. No distance Dijkstra, all-origins x all-nodes allocation or cached
    per-node path chains. Working space is O(chunk_size * nodes + pairs + one
    path); buffers are released before the next chunk. Only pair scalar outputs
    are retained. The caller adds feeders/vehicle factors and published rounding.
    """
    if (isinstance(chunk_size, (bool, np.bool_))
            or not isinstance(chunk_size, (int, np.integer))
            or not 1 <= chunk_size <= MAX_SOURCE_CHUNK):
        raise ValueError("chunk_size must be an integer from 1 to 32")
    outputs, grouped = [], {}
    n = graph.nh_graph.shape[0]
    for u, v in pairs:
        u, v = _node_id(u, n), _node_id(v, n)
        slot = len(outputs)
        outputs.append(None)
        if graph.nh_comp_labels[u] != graph.nh_comp_labels[v]:
            continue
        if u == v:
            outputs[slot] = PairNetworkMetrics(0.0, 0.0, 0, 0.0, 0.0)
        else:
            grouped.setdefault(u, []).append((slot, v))
    sources = list(grouped)
    for offset in range(0, len(sources), chunk_size):
        chunk = sources[offset:offset + chunk_size]
        hours, predecessors = dijkstra(graph.nh_graph, directed=False, indices=chunk,
                                       return_predecessors=True)
        for row, u in enumerate(chunk):
            for slot, v in grouped[u]:
                raw_hours = float(hours[row, v])
                if not np.isfinite(raw_hours):
                    raise _integrity("Same-component destination is unreachable on the highway graph")
                if raw_hours < 0:
                    raise _integrity("Invalid highway graph metadata")
                nodes = reconstruct_path(u, v, predecessors[row])
                km, count, bridges, maximum = analyze_path_edges(nodes, graph)
                outputs[slot] = PairNetworkMetrics(raw_hours, km, count, bridges, maximum)
                del nodes
        del hours, predecessors
    return outputs
