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
import os
import tempfile
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
CACHE_VERSION = 6


def load_highway_source(data_dir: Path) -> Optional[Tuple[gpd.GeoDataFrame, Path]]:
    """Read the caller's preferred highway source and project to EPSG:7755.

    GeoJSON is selected only when parquet is absent. Read/projection failures
    propagate; neither source files nor default roots, stores or caches are changed.
    """
    nh_pq = data_dir / "roads" / "india_nh_network.parquet"
    nh_geojson = data_dir / "roads" / "india_nh_network.geojson"
    if nh_pq.exists():
        return gpd.read_parquet(nh_pq).to_crs(PROJ_EPSG), nh_pq
    if nh_geojson.exists():
        return gpd.read_file(nh_geojson).to_crs(PROJ_EPSG), nh_geojson
    return None


def validate_graph_metadata(graph_time, graph_dist, coords, labels, tree, bridge_mask):
    """Reject inconsistent metrics/topology rather than inventing route diagnostics."""
    n = len(coords)
    if (np.shape(coords) != (n, 2) or n == 0 or not np.isfinite(coords).all()
            or np.shape(labels) != (n,) or not np.issubdtype(labels.dtype, np.integer)
            or np.any(labels < 0) or tree.n != n or not np.array_equal(tree.data, coords)):
        raise ValueError("Invalid node/component/tree metadata")
    for matrix in (graph_time, graph_dist, bridge_mask):
        if not sp.isspmatrix_csr(matrix) or matrix.shape != (n, n):
            raise ValueError("Invalid sparse graph metadata")
        matrix.check_format(full_check=True)
        if not matrix.has_canonical_format or not np.isfinite(matrix.data).all():
            raise ValueError("Invalid sparse values metadata")
        if (matrix != matrix.T).nnz:
            raise ValueError("Asymmetric graph metadata")
    if (np.any(graph_time.data <= 0) or np.any(graph_dist.data <= 0)
            or np.any(bridge_mask.data != 1)
            or not np.array_equal(graph_time.indptr, graph_dist.indptr)
            or not np.array_equal(graph_time.indices, graph_dist.indices)
            or np.any(graph_time.diagonal() != 0)
            or bridge_mask.multiply(graph_time != 0).nnz != bridge_mask.nnz):
        raise ValueError("Invalid edge distance/bridge metadata")
    rows = np.repeat(np.arange(n), np.diff(graph_dist.indptr))
    lengths_km = np.linalg.norm(coords[rows] - coords[graph_dist.indices], axis=1) / 1000
    if not np.allclose(graph_dist.data, lengths_km, rtol=1e-9, atol=1e-9):
        raise ValueError("Edge distance metadata disagrees with projected vertices")
    if np.any(bridge_mask.multiply(graph_dist).data * 1000 > BRIDGE_MAX_METERS + 1e-6):
        raise ValueError("Synthetic bridge metadata exceeds junction threshold")
    actual = connected_components(graph_time, directed=False)[1]
    # Component IDs need not be canonical, but the partition must match exactly.
    pairs = np.unique(np.column_stack((actual, labels)), axis=0)
    if len(pairs) != len(np.unique(actual)) or len(pairs) != len(np.unique(labels)):
        raise ValueError("Incorrect connected component metadata")


def snap_pt(x: float, y: float, grid: float = SNAP_GRID_METERS) -> Tuple[float, float]:
    """Snap coordinates to a 15m grid for topological junction alignment."""
    return (round(x / grid) * grid, round(y / grid) * grid)


def compute_gdf_content_hash(nh_gdf: gpd.GeoDataFrame) -> str:
    """Computes a deterministic SHA-256 hash of highway geometry and road attributes."""
    h = hashlib.sha256()
    cols_to_hash = [c for c in ["highway", "ref", "osm_id"] if c in nh_gdf.columns]
    for c in cols_to_hash:
        vals = [str(x) for x in nh_gdf[c]]
        h.update("\x1f".join(vals).encode("utf-8"))
    if "geometry" in nh_gdf:
        try:
            for wkb in nh_gdf.geometry.to_wkb():
                if wkb is not None:
                    h.update(wkb)
        except Exception:
            h.update(np.asarray(nh_gdf.total_bounds).tobytes())
            h.update(np.asarray(nh_gdf.geometry.length.values).tobytes())
    return h.hexdigest()


def compute_graph_fingerprint(nh_gdf: gpd.GeoDataFrame) -> Dict[str, Any]:
    """Computes a structural, parameter, and content fingerprint for cache validation."""
    speed_hash = hashlib.sha256(json.dumps(SPEEDS, sort_keys=True).encode()).hexdigest()
    content_hash = compute_gdf_content_hash(nh_gdf)
    return {
        "cache_version": CACHE_VERSION,
        "row_count": int(len(nh_gdf)),
        "crs": str(nh_gdf.crs),
        "snap_grid_meters": float(SNAP_GRID_METERS),
        "bridge_max_meters": float(BRIDGE_MAX_METERS),
        "bridge_speed": float(BRIDGE_SPEED),
        "default_speed": float(DEFAULT_SPEED),
        "speed_hash": speed_hash,
        "source_content_sha256": content_hash,
    }


def build_canonical_highway_graph(
    nh_gdf: gpd.GeoDataFrame,
) -> Tuple[sp.csr_matrix, sp.csr_matrix, np.ndarray, np.ndarray, KDTree, sp.csr_matrix]:
    """
    Constructs the canonical National Highway routing graph in EPSG:7755.

    Returns:
        graph_time: csr_matrix weighted by transit duration (hours)
        graph_dist: csr_matrix weighted by road distance (km)
        coords_arr: (N, 2) numpy array of vertex coordinates in EPSG:7755
        labels: (N,) numpy array of connected component IDs
        tree: KDTree built on coords_arr
        bridge_mask: (N, N) csr_matrix with 1 for synthetic junction bridge edges, 0 for physical edges
    """
    if nh_gdf.crs is None or nh_gdf.crs.to_epsg() != PROJ_EPSG:
        raise ValueError("Highway graph requires EPSG:7755 metre coordinates")
    node_map: Dict[Tuple[float, float], int] = {}
    node_coords: List[Tuple[float, float]] = []

    def get_node(x: float, y: float) -> int:
        if not np.isfinite([x, y]).all():
            raise ValueError("Highway coordinates must be finite")
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

    if not node_coords:
        raise ValueError("Highway graph has no nodes")
    coords_arr = np.array(node_coords, dtype=np.float64)
    endpoint_indices = list(linestring_endpoints)
    endpoint_coords = coords_arr[endpoint_indices]

    bridge_edges_set: set = set()
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
                        bridge_edges_set.add(edge)

    N = len(node_coords)
    rows = [e[0] for e in edges_dict]
    cols = [e[1] for e in edges_dict]
    time_data = [v[0] for v in edges_dict.values()]
    dist_data = [v[1] / 1000.0 for v in edges_dict.values()]
    bridge_edges = sorted(bridge_edges_set)

    graph_time = sp.csr_matrix((time_data, (rows, cols)), shape=(N, N), dtype=np.float64)
    graph_dist = sp.csr_matrix((dist_data, (rows, cols)), shape=(N, N), dtype=np.float64)
    bridge_mask = sp.csr_matrix(
        (np.ones(len(bridge_edges), dtype=np.int8),
         ([e[0] for e in bridge_edges], [e[1] for e in bridge_edges])), shape=(N, N))

    _, labels = connected_components(graph_time, directed=False)
    tree = KDTree(coords_arr)
    validate_graph_metadata(graph_time, graph_dist, coords_arr, labels, tree, bridge_mask)

    return graph_time, graph_dist, coords_arr, labels, tree, bridge_mask


def load_or_build_cached_graph(
    nh_gdf: gpd.GeoDataFrame,
    cache_dir: Optional[Path] = None,
) -> Tuple[sp.csr_matrix, sp.csr_matrix, np.ndarray, np.ndarray, KDTree, sp.csr_matrix]:
    """Loads precomputed graph from disk cache with fingerprint validation or builds and caches it."""
    if nh_gdf.crs is None or nh_gdf.crs.to_epsg() != PROJ_EPSG:
        raise ValueError("Highway graph requires EPSG:7755 metre coordinates")
    expected_meta = compute_graph_fingerprint(nh_gdf)

    if cache_dir is not None:
        cache_file = cache_dir / "canonical_nh_graph.npz"
        if cache_file.exists():
            try:
                with np.load(cache_file, allow_pickle=False) as data:
                    meta_json = str(data["metadata"][0]) if "metadata" in data else "{}"
                    stored_meta = json.loads(meta_json)
                    if stored_meta == expected_meta and "bridge_data" in data:
                        N = int(data["N"])
                        graph_time = sp.csr_matrix(
                            (data["time_data"], data["time_indices"], data["time_indptr"]), shape=(N, N)
                        )
                        graph_dist = sp.csr_matrix(
                            (data["dist_data"], data["dist_indices"], data["dist_indptr"]), shape=(N, N)
                        )
                        bridge_mask = sp.csr_matrix(
                            (data["bridge_data"], data["bridge_indices"], data["bridge_indptr"]), shape=(N, N)
                        )
                        coords_arr = np.copy(data["coords_arr"])
                        labels = np.copy(data["labels"])
                        tree = KDTree(coords_arr)
                        validate_graph_metadata(graph_time, graph_dist, coords_arr, labels, tree, bridge_mask)
                        return graph_time, graph_dist, coords_arr, labels, tree, bridge_mask
                    else:
                        LOGGER.info("Graph cache fingerprint mismatch; rebuilding canonical highway graph.")
            except Exception as e:
                LOGGER.warning("Could not load graph cache (%s); rebuilding.", e)

    # Build graph
    graph_time, graph_dist, coords_arr, labels, tree, bridge_mask = build_canonical_highway_graph(nh_gdf)

    # Save cache safely using allow_pickle=False compatible string array
    if cache_dir is not None:
        temporary = None
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / "canonical_nh_graph.npz"
            # Same-directory temporary file keeps replacement atomic; closed first
            # for Windows compatibility. Readers only ever see complete archives.
            with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".npz", delete=False) as handle:
                temporary = Path(handle.name)
            np.savez_compressed(
                temporary,
                N=graph_time.shape[0],
                time_data=graph_time.data,
                time_indices=graph_time.indices,
                time_indptr=graph_time.indptr,
                dist_data=graph_dist.data,
                dist_indices=graph_dist.indices,
                dist_indptr=graph_dist.indptr,
                bridge_data=bridge_mask.data,
                bridge_indices=bridge_mask.indices,
                bridge_indptr=bridge_mask.indptr,
                coords_arr=coords_arr,
                labels=labels,
                metadata=np.array([json.dumps(expected_meta)], dtype=np.str_),
            )
            os.replace(temporary, cache_file)
        except (OSError, PermissionError) as e:
            LOGGER.warning("Could not write graph cache to %s (%s); running in-memory.", cache_dir, e)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as e:
                    LOGGER.warning("Could not remove incomplete cache %s (%s).", temporary, e)

    return graph_time, graph_dist, coords_arr, labels, tree, bridge_mask
