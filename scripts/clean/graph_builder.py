"""
scripts/clean/graph_builder.py
Unified, canonical Highway Graph Builder and Serializer for GISIndia4Logistics.
Used identically by live server (server.dependencies.DataStore) and analytical engines.
"""

from pathlib import Path
from typing import Tuple, Dict, Optional, List, Any
import hashlib
import json
import logging
import numpy as np
import scipy.sparse as sp
from scipy.spatial import KDTree
from scipy.sparse.csgraph import connected_components
import geopandas as gpd

LOGGER = logging.getLogger(__name__)

PROJ_EPSG = 7755
SPEEDS = {"motorway": 90.0, "trunk": 70.0, "primary": 55.0}
DEFAULT_SPEED = 50.0
BRIDGE_SPEED = 35.0
BRIDGE_MAX_METERS = 350.0
SNAP_GRID_METERS = 15.0
CACHE_VERSION = 3


def snap_pt(x: float, y: float, grid: float = SNAP_GRID_METERS) -> Tuple[float, float]:
    """Snap coordinates to a 15m grid for topological junction alignment."""
    return (round(x / grid) * grid, round(y / grid) * grid)


def compute_graph_fingerprint(nh_gdf: gpd.GeoDataFrame) -> Dict[str, Any]:
    """Computes a structural and parameter fingerprint for cache validation."""
    speed_hash = hashlib.sha256(json.dumps(SPEEDS, sort_keys=True).encode()).hexdigest()[:16]
    return {
        "cache_version": CACHE_VERSION,
        "row_count": int(len(nh_gdf)),
        "crs": str(nh_gdf.crs),
        "snap_grid_meters": float(SNAP_GRID_METERS),
        "bridge_max_meters": float(BRIDGE_MAX_METERS),
        "bridge_speed": float(BRIDGE_SPEED),
        "default_speed": float(DEFAULT_SPEED),
        "speed_hash": speed_hash,
    }


def build_canonical_highway_graph(
    nh_gdf: gpd.GeoDataFrame,
) -> Tuple[sp.csr_matrix, sp.csr_matrix, np.ndarray, np.ndarray, KDTree]:
    """
    Constructs the canonical National Highway routing graph in EPSG:7755.

    Returns:
        graph_time: csr_matrix weighted by transit duration (hours)
        graph_dist: csr_matrix weighted by road distance (km)
        coords_arr: (N, 2) numpy array of vertex coordinates in EPSG:7755
        labels: (N,) numpy array of connected component IDs
        tree: KDTree built on coords_arr
    """
    node_map: Dict[Tuple[float, float], int] = {}
    node_coords: List[Tuple[float, float]] = []

    def get_node(x: float, y: float) -> int:
        k = snap_pt(x, y)
        if k not in node_map:
            nid = len(node_coords)
            node_map[k] = nid
            node_coords.append(k)
            return nid
        return node_map[k]

    edges_dict: Dict[Tuple[int, int], Tuple[float, float]] = {}
    linestring_endpoints: set = set()

    for _, row in nh_gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        spd = SPEEDS.get(str(row.get("highway")).lower(), DEFAULT_SPEED)
        lines = [geom] if geom.geom_type == "LineString" else (geom.geoms if geom.geom_type == "MultiLineString" else [])

        for line in lines:
            coords = list(line.coords)
            if len(coords) < 2:
                continue

            u_first = get_node(coords[0][0], coords[0][1])
            u_last = get_node(coords[-1][0], coords[-1][1])
            linestring_endpoints.add(u_first)
            linestring_endpoints.add(u_last)

            for i in range(len(coords) - 1):
                u = get_node(coords[i][0], coords[i][1])
                v = get_node(coords[i + 1][0], coords[i + 1][1])
                if u != v:
                    d_m = float(np.hypot(node_coords[u][0] - node_coords[v][0], node_coords[u][1] - node_coords[v][1]))
                    t_hrs = (d_m / 1000.0) / spd

                    for edge in [(u, v), (v, u)]:
                        if edge not in edges_dict or t_hrs < edges_dict[edge][0]:
                            edges_dict[edge] = (t_hrs, d_m)

    coords_arr = np.array(node_coords, dtype=np.float64)
    endpoint_indices = list(linestring_endpoints)
    endpoint_coords = coords_arr[endpoint_indices]

    if len(endpoint_coords) > 0:
        ep_tree = KDTree(endpoint_coords)
        ep_pairs = ep_tree.query_pairs(r=BRIDGE_MAX_METERS)
        for i_ep, j_ep in ep_pairs:
            u = endpoint_indices[i_ep]
            v = endpoint_indices[j_ep]
            d_m = float(np.hypot(coords_arr[u][0] - coords_arr[v][0], coords_arr[u][1] - coords_arr[v][1]))
            if d_m > 0:
                t_hrs = (d_m / 1000.0) / BRIDGE_SPEED
                for edge in [(u, v), (v, u)]:
                    if edge not in edges_dict:
                        edges_dict[edge] = (t_hrs, d_m)

    N = len(node_coords)
    rows = [e[0] for e in edges_dict]
    cols = [e[1] for e in edges_dict]
    time_data = [v[0] for v in edges_dict.values()]
    dist_data = [v[1] / 1000.0 for v in edges_dict.values()]

    graph_time = sp.csr_matrix((time_data, (rows, cols)), shape=(N, N), dtype=np.float64)
    graph_dist = sp.csr_matrix((dist_data, (rows, cols)), shape=(N, N), dtype=np.float64)

    _, labels = connected_components(graph_time, directed=False)
    tree = KDTree(coords_arr)

    return graph_time, graph_dist, coords_arr, labels, tree


def load_or_build_cached_graph(
    nh_gdf: gpd.GeoDataFrame,
    cache_dir: Optional[Path] = None,
) -> Tuple[sp.csr_matrix, sp.csr_matrix, np.ndarray, np.ndarray, KDTree]:
    """Loads precomputed graph from disk cache with fingerprint validation or builds and caches it."""
    expected_meta = compute_graph_fingerprint(nh_gdf)

    if cache_dir is not None:
        cache_file = cache_dir / "canonical_nh_graph.npz"
        if cache_file.exists():
            try:
                data = np.load(cache_file, allow_pickle=True)
                # Verify fingerprint metadata
                meta_json = str(data.get("metadata", "{}"))
                stored_meta = json.loads(meta_json)
                if stored_meta == expected_meta:
                    N = int(data["N"])
                    graph_time = sp.csr_matrix(
                        (data["time_data"], data["time_indices"], data["time_indptr"]), shape=(N, N)
                    )
                    graph_dist = sp.csr_matrix(
                        (data["dist_data"], data["dist_indices"], data["dist_indptr"]), shape=(N, N)
                    )
                    coords_arr = data["coords_arr"]
                    labels = data["labels"]
                    tree = KDTree(coords_arr)
                    return graph_time, graph_dist, coords_arr, labels, tree
                else:
                    LOGGER.info("Graph cache fingerprint mismatch; rebuilding canonical highway graph.")
            except Exception as e:
                LOGGER.warning("Could not load graph cache (%s); rebuilding.", e)

    # Build graph
    graph_time, graph_dist, coords_arr, labels, tree = build_canonical_highway_graph(nh_gdf)

    # Save cache safely
    if cache_dir is not None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / "canonical_nh_graph.npz"
            np.savez_compressed(
                cache_file,
                N=graph_time.shape[0],
                time_data=graph_time.data,
                time_indices=graph_time.indices,
                time_indptr=graph_time.indptr,
                dist_data=graph_dist.data,
                dist_indices=graph_dist.indices,
                dist_indptr=graph_dist.indptr,
                coords_arr=coords_arr,
                labels=labels,
                metadata=json.dumps(expected_meta),
            )
        except (OSError, PermissionError) as e:
            LOGGER.warning("Could not write graph cache to %s (%s); running in-memory.", cache_dir, e)

    return graph_time, graph_dist, coords_arr, labels, tree
