"""Approximate highway catchments, NOT door-to-door point reachability.

LMV factor 1.0; closest NH vertex within 150 km on a supported component (at
least kernel.MIN_SUPPORTED_COMPONENT_NODES nodes); access at 35 km/h.
Only the source component participates. EPSG:7755 convex hull, 8 km buffer,
500 m topology-preserving simplification, then WGS84. No navigation claims.
"""
from __future__ import annotations

import argparse
import math
from numbers import Real
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import MultiPoint, Point

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.analyze import highway_pair_kernel as kernel
from scripts.analyze import producer_io
from scripts.clean import graph_builder as builder
from scripts.clean.standardize import DATA_DIR, REPO_ROOT

PROJ = 7755
COVERAGE_METHOD = "approximate_highway_catchment"
COVERAGE_ASSUMPTIONS = (
    f"LMV factor 1.0; closest NH snap within 150 km on a supported component (>= {kernel.MIN_SUPPORTED_COMPONENT_NODES} nodes); source feeder at 35 km/h; "
    "single-source component; projected convex hull + 8 km buffer, simplify 500 m; "
    "approximation, not navigation or door-to-door point reachability"
)


def _thresholds(values):
    result = []
    for value in values:
        if (isinstance(value, (bool, np.bool_)) or not isinstance(value, Real)
                or not math.isfinite(value) or value <= 0):
            raise ValueError("Thresholds must be finite positive hours")
        result.append(float(value))
    if not result or len(set(result)) != len(result):
        raise ValueError("Thresholds must be nonempty and unique")
    return tuple(result)


def _nearest_source(point, graph, sizes=None):
    """Closest supported-component node, inclusive 150 km, stable node-ID ties.

    Returns (node, feeder km, component node count) or None.
    """
    indices, distances = kernel.radius_candidates(point, graph)
    if sizes is None:
        sizes = kernel.component_sizes(graph.nh_comp_labels)
    snap = kernel.nearest_supported(distances, indices, graph.nh_comp_labels, sizes)
    if snap is None:
        return None
    distance, node, size = snap
    return node, distance / 1000.0, size


def _coordinate(row):
    lon, lat = row.get("longitude"), row.get("latitude")
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, Real)
           or not math.isfinite(v) for v in (lon, lat)):
        return None
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        return None
    point = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(PROJ).iloc[0]
    if not np.isfinite([point.x, point.y]).all():
        return None
    return np.array([point.x, point.y])


def _hub_features(frame, thresholds, hub_type, graph, chunk_size):
    features, jobs = [], []
    sizes = kernel.component_sizes(graph.nh_comp_labels)
    for row in frame.to_dict("records"):
        point = _coordinate(row)
        snap = _nearest_source(point, graph, sizes) if point is not None else None
        status = "invalid_coordinates" if point is None else "no_highway_within_150km"
        node, feeder_km, component = snap if snap is not None else (None, None, None)
        feeder_hours = feeder_km / 35.0 if snap is not None else None
        slots = []
        for threshold in thresholds:
            feature = dict(row)
            feature.update(hub_name=row.get("name"), hub_type=hub_type,
                time_threshold_hours=threshold,
                label=f"{threshold:g} Hour Approximate Highway Catchment",
                status=status, reachable_node_count=0, snap_feeder_km=feeder_km,
                snap_component_node_count=component,
                feeder_time_hours=feeder_hours, coverage_method=COVERAGE_METHOD,
                coverage_assumptions=COVERAGE_ASSUMPTIONS, geometry=None)
            if snap is not None:
                feature["status"] = ("feeder_exceeds_threshold" if feeder_hours > threshold
                                     else "unsupported_threshold")
            slots.append(len(features))
            features.append(feature)
        if snap is not None and feeder_hours <= max(thresholds):
            jobs.append((node, feeder_hours, slots))

    for start in range(0, len(jobs), chunk_size):
        chunk = jobs[start:start + chunk_size]
        # No graph copy, distance search or national all-sources allocation.
        limit = max(max(thresholds) - feeder for _, feeder, _ in chunk)
        distances = dijkstra(graph.nh_graph, directed=False,
                             indices=[node for node, _, _ in chunk], limit=limit)
        for network_hours, (_, feeder_hours, slots) in zip(distances, chunk):
            for threshold, slot in zip(thresholds, slots):
                if feeder_hours > threshold:
                    continue
                mask = network_hours + feeder_hours <= threshold
                count = int(np.count_nonzero(mask))
                feature = features[slot]
                feature["reachable_node_count"] = count
                if count < 3:
                    continue
                polygon = MultiPoint(graph.nh_node_xy[mask]).convex_hull.buffer(8000.0)
                polygon = polygon.simplify(500.0, preserve_topology=True)
                if polygon.is_empty or not polygon.is_valid or polygon.geom_type not in {"Polygon", "MultiPolygon"}:
                    raise ValueError("Invalid approximate highway catchment geometry")
                feature.update(status="ok", geometry=polygon)
        del distances, network_hours
    columns = list(dict.fromkeys([*frame.columns, "hub_name", "hub_type", "time_threshold_hours",
        "label", "status", "reachable_node_count", "snap_feeder_km", "snap_component_node_count", "feeder_time_hours",
        "coverage_method", "coverage_assumptions", "geometry"]))
    result = gpd.GeoDataFrame(features, columns=columns, geometry="geometry", crs=PROJ).to_crs(4326)
    result["snap_component_node_count"] = result["snap_component_node_count"].astype("Int64")
    okay = result[result.status == "ok"]
    if not okay.geometry.is_valid.all() or not okay.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("Invalid WGS84 approximate highway catchment geometry")
    return result


def build_isochrones(*, data_dir, cache_dir, thresholds_ports=(4, 8, 12, 24),
                     thresholds_mmlps=(4, 8, 12), chunk_size=32):
    """Build source-count-derived features without publishing derivatives.

    Missing/corrupt selected inputs fail; no repository fallback. The canonical
    builder alone may populate the supplied separately writable cache directory.
    Unsupported thresholds retain identity, actual count and null geometry.
    """
    if (isinstance(chunk_size, (bool, np.bool_)) or not isinstance(chunk_size, (int, np.integer))
            or not 1 <= chunk_size <= 32):
        raise ValueError("chunk_size must be an integer from 1 to 32")
    thresholds_ports, thresholds_mmlps = _thresholds(thresholds_ports), _thresholds(thresholds_mmlps)
    root = Path(data_dir)
    source = builder.load_highway_source(root)
    if source is None:
        raise FileNotFoundError(f"No national highway source in {root}")
    nh, selected = source
    ports_path, mmlps_path = root / "logistics_hubs/ports.csv", root / "logistics_hubs/mmlps.csv"
    ports, mmlps = pd.read_csv(ports_path), pd.read_csv(mmlps_path)
    if "hub_type" not in ports.columns:
        raise ValueError("Missing ports hub_type")
    for frame in (ports, mmlps):
        if not {"name", "latitude", "longitude"}.issubset(frame.columns):
            raise ValueError("Missing hub source columns")
    ports = ports[ports.hub_type == "major_port"]
    for frame in (ports, mmlps):
        names = frame["name"].tolist()
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("Invalid stored hub identity")
        keys = [name.strip().casefold() for name in names]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate stored hub identity")
    values = builder.load_or_build_cached_graph(nh, cache_dir=Path(cache_dir) if cache_dir is not None else None)
    builder.validate_graph_metadata(*values)
    # Exact six shared GraphFields, retaining original sparse matrices/arrays.
    graph = SimpleNamespace(nh_graph=values[0], nh_distance_graph=values[1], nh_node_xy=values[2],
        nh_comp_labels=values[3], nh_tree=values[4], nh_bridge_mask=values[5])
    ports_gdf = _hub_features(ports, thresholds_ports, "major_port", graph, chunk_size)
    mmlps_gdf = _hub_features(mmlps, thresholds_mmlps, "mmlp", graph, chunk_size)
    provenance = dict(coverage_method=COVERAGE_METHOD, max_snap_km=150.0, feeder_speed_kmh=35.0,
        min_supported_component_nodes=kernel.MIN_SUPPORTED_COMPONENT_NODES,
        buffer_m=8000.0, simplify_m=500.0, projection="EPSG:7755", vehicle="LMV", vehicle_factor=1.0,
        component_policy="single_source_component", routing_objective="undirected_network_time",
        reachability_policy="network_plus_source_feeder_within_threshold; fewer_than_3_nodes_unsupported",
        navigation_claim=False, door_to_door_point_reachability=False,
        thresholds_ports=list(thresholds_ports), thresholds_mmlps=list(thresholds_mmlps),
        source_ports=len(ports), source_mmlps=len(mmlps), graph_fingerprint=builder.compute_graph_fingerprint(nh),
        sources={str(path): producer_io.sha256_file(path) for path in (selected, ports_path, mmlps_path)})
    return ports_gdf, mmlps_gdf, provenance


def write_isochrones(ports, mmlps, *, ports_path, mmlps_path):
    """Atomic per file; not a two-file transaction."""
    producer_io.atomic_write_geojson(ports, ports_path)
    producer_io.atomic_write_geojson(mmlps, mmlps_path)


def generate_isochrones(*, data_dir=None, cache_dir=None, output_dir=None,
                        thresholds_ports=(4, 8, 12, 24), thresholds_mmlps=(4, 8, 12), chunk_size=32):
    """Default path policy without importing server/settings or reading .env."""
    root = Path(data_dir) if data_dir is not None else Path(os.environ.get("GIS4LOGISTICS_DATA_PATH", DATA_DIR))
    outputs = Path(os.environ.get("GIS4LOGISTICS_OUTPUT_PATH", REPO_ROOT / "outputs"))
    cache = Path(cache_dir) if cache_dir is not None else Path(os.environ.get("GIS4LOGISTICS_CACHE_PATH", outputs / "cache"))
    destination = Path(output_dir) if output_dir is not None else root / "analysis"
    result = build_isochrones(data_dir=root, cache_dir=cache, thresholds_ports=thresholds_ports,
                             thresholds_mmlps=thresholds_mmlps, chunk_size=chunk_size)
    write_isochrones(result[0], result[1], ports_path=destination / "major_ports_isochrones.geojson",
                     mmlps_path=destination / "mmlp_isochrones.geojson")
    return result


def generate_hub_isochrones(**kwargs):
    """Historical zero-argument entry point."""
    return generate_isochrones(**kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    return generate_isochrones(data_dir=args.data_dir, cache_dir=args.cache_dir, output_dir=args.output_dir)


if __name__ == "__main__":
    main()