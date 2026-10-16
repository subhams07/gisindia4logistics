"""Canonical LMV analytical matrix; closest-compatible snaps, fastest-time paths.

This is not navigation or global door-to-door optimization. Build is separate
from single-file atomic publication; a multi-artifact promotion belongs upstream.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse.csgraph import dijkstra

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.analyze import highway_pair_kernel as kernel
from scripts.analyze.producer_io import (
    atomic_write_csv, atomic_write_parquet, district_population, is_island_state,
    sha256_file,
)
from scripts.clean.graph_builder import (
    DEFAULT_SPEED as DEFAULT_SPEED, SPEEDS as SPEEDS, compute_graph_fingerprint, load_highway_source,
    load_or_build_cached_graph, validate_graph_metadata,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
PROJ = 7755
ACCESS_SPEED = 35.0
ISLAND_DISTRICT_NAMES = {"nicobars", "north and middle andaman", "south andamans", "lakshadweep"}
HUB_FILES = {
    "rail_station": ("rail/railway_stations.csv", "station_name"),
    "freight_terminal": ("rail/freight_terminals.csv", "name"),
    "port": ("logistics_hubs/ports.csv", "name"),
    "icd": ("logistics_hubs/icds.csv", "name"),
    "mmlp": ("logistics_hubs/mmlps.csv", "name"),
    "air_cargo": ("logistics_hubs/air_cargo.csv", "name"),
    "icp": ("logistics_hubs/icps.csv", "name"),
    "toll_plaza": ("roads/toll_plazas.csv", "name"),
}
KINDS = (*HUB_FILES, "expressway")
IDENTITY_COLUMNS = ["state", "district", "district_code"]
SUMMARY_COLUMNS = IDENTITY_COLUMNS + [
    "pop_2011", "is_island", "highway_access_dist_km", "highway_access_time_min",
] + [column for kind in KINDS for column in (
    f"nearest_{kind}_name", f"{kind}_straight_km", f"{kind}_road_distance_km",
    f"{kind}_drive_time_hours", f"{kind}_drive_time_min",
)]


def _point_candidates(point, graph):
    """Nearest node per component, inclusive 150 km, deterministic node ties."""
    if not np.isfinite(point).all():
        return {}
    ids, distances = kernel.radius_candidates(point, graph)
    valid = np.isfinite(distances) & (distances >= 0) & (distances <= kernel.MAX_FEEDER_DISTANCE_M)
    ids, distances = ids[valid], distances[valid]
    labels = graph.nh_comp_labels[ids]
    order = np.lexsort((ids, distances, labels))
    labels, ids, distances = labels[order], ids[order], distances[order]
    first = np.r_[True, labels[1:] != labels[:-1]] if len(labels) else np.array([], dtype=bool)
    return {int(c): (float(d), int(n)) for c, d, n in zip(labels[first], distances[first], ids[first])}


def _hub_components(candidates):
    """Sparse inverted component index; never allocate by maximum label."""
    rows = {}
    for hub, components in enumerate(candidates):
        for comp, (distance, node) in components.items():
            rows.setdefault(comp, []).append((hub, distance, node))
    return {c: (np.array([r[0] for r in entries], dtype=int),
                np.array([r[1] for r in entries], dtype=float),
                np.array([r[2] for r in entries], dtype=int)) for c, entries in rows.items()}


def _compatible_hubs(origin, components, count):
    """Vectorized scalar-kernel objective (o metres + h metres, o ID, h ID, label)."""
    u = np.full(count, -1, dtype=int)
    v = np.full(count, -1, dtype=int)
    od = np.full(count, np.inf)
    hd = np.full(count, np.inf)
    labels = np.full(count, -1, dtype=np.int64)
    total = np.full(count, np.inf)
    for comp, (o_distance, o_node) in origin.items():
        if comp not in components:
            continue
        hubs, distances, nodes = components[comp]
        sums = o_distance + distances
        equal = sums == total[hubs]
        better = (sums < total[hubs]) | (equal & (
            (o_node < u[hubs]) | ((o_node == u[hubs]) & (
                (nodes < v[hubs]) | ((nodes == v[hubs]) & (comp < labels[hubs]))))))
        slots = hubs[better]
        u[slots], v[slots] = o_node, nodes[better]
        od[slots], hd[slots], labels[slots] = o_distance, distances[better], comp
        total[slots] = sums[better]
    return u, v, od, hd, labels


def _selected_pair(selected, slot):
    u, v, od, hd, labels = selected
    if u[slot] < 0:
        return None
    return int(u[slot]), int(v[slot]), float(od[slot]), float(hd[slot]), int(labels[slot])


def _port_key(name):
    return name.replace("Port of ", "").replace(" Port", "").replace(" ", "_").lower()


def _district_points(districts):
    """Project once, representative points once; invalid geometry retains its row."""
    projected = districts.to_crs(PROJ)
    points = np.full((len(projected), 2), np.nan)
    valid = projected.geometry.notna() & ~projected.geometry.is_empty & projected.geometry.is_valid
    representatives = projected.loc[valid].geometry.representative_point()
    points[np.flatnonzero(valid)] = np.column_stack((representatives.x, representatives.y))
    return points


def _load_hubs(root, nh, sources):
    hubs, major_ports, invalid = [], [], {}
    for kind, (relative, name_col) in HUB_FILES.items():
        path = root / relative
        invalid[kind] = 0
        if not path.exists():
            continue
        sources[relative] = sha256_file(path)
        frame = pd.read_csv(path)
        required = {name_col, "latitude", "longitude"}
        if kind == "port":
            required.add("hub_type")
        if not required.issubset(frame.columns):
            raise ValueError(f"Missing hub source columns: {path}")
        lat = pd.to_numeric(frame.latitude, errors="coerce")
        lon = pd.to_numeric(frame.longitude, errors="coerce")
        valid = np.isfinite(lat) & np.isfinite(lon) & lat.between(-90, 90) & lon.between(-180, 180)
        points = gpd.GeoSeries(gpd.points_from_xy(lon[valid], lat[valid]), crs=4326).to_crs(PROJ)
        xy = {int(i): (float(p.x), float(p.y)) for i, p in zip(frame.index[valid], points)}
        for source_order, row in frame.iterrows():
            point = xy.get(source_order, (np.nan, np.nan))
            usable = bool(np.isfinite(point).all())
            if not usable:
                invalid[kind] += 1
            slot = len(hubs)
            hubs.append({"kind": kind, "name": row[name_col], "source_order": int(source_order),
                         "point": np.asarray(point), "valid": usable})
            # Invalid major ports still have authoritative output columns.
            if kind == "port" and row.hub_type == "major_port":
                if not isinstance(row[name_col], str):
                    raise ValueError("Invalid major port identity")
                major_ports.append((slot, _port_key(row[name_col])))
    invalid["expressway"] = 0
    if "highway" in nh:
        for source_order, (_, row) in enumerate(nh.loc[nh.highway == "motorway"].iterrows()):
            geom = row.geometry
            if geom is None or geom.is_empty or not geom.is_valid:
                invalid["expressway"] += 1
                continue
            point = geom.interpolate(0.5, normalized=True)
            name = row.get("name")
            if pd.isna(name):
                ref = row.get("nh")
                name = "NE " + str(ref) if pd.notna(ref) else "Expressway"
            usable = bool(np.isfinite([point.x, point.y]).all())
            if not usable:
                invalid["expressway"] += 1
                continue
            hubs.append({"kind": "expressway", "name": name, "source_order": source_order,
                         "point": np.array([point.x, point.y]), "valid": True})
    keys = [key for _, key in major_ports]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate legacy major-port key")
    return hubs, major_ports, invalid


def build_travel_outputs(*, data_dir, cache_dir, chunk_size=32):
    """Return coherent unpromoted summary/matrix frames and run provenance.

    Pass 1 time-only Dijkstra retains just selected hub target scalars per district
    chunk, not a national node matrix. Pass 2 walks time predecessors only for
    category winners and fixed major-port pairs, using the accepted pure kernel.
    """
    if isinstance(chunk_size, (bool, np.bool_)) or not isinstance(chunk_size, (int, np.integer)) or not 1 <= chunk_size <= 32:
        raise ValueError("chunk_size must be an integer from 1 to 32")
    started = time.perf_counter()
    root = Path(data_dir)
    district_path = root / "administrative/india_districts_lgd.geojson"
    if not district_path.is_file():
        raise FileNotFoundError(district_path)
    loaded = load_highway_source(root)
    if loaded is None:
        raise FileNotFoundError(f"No highway source in {root / 'roads'}")
    nh, source_path = loaded
    districts = gpd.read_file(district_path)
    if not set(IDENTITY_COLUMNS).issubset(districts.columns):
        raise ValueError("Missing district identity columns")
    points = _district_points(districts)
    sources = {str(source_path.relative_to(root)).replace("\\", "/"): sha256_file(source_path),
               "administrative/india_districts_lgd.geojson": sha256_file(district_path)}
    population_path = root / "demographic/district_population_estimates.csv"
    if population_path.exists():
        population = pd.read_csv(population_path)
        sources["demographic/district_population_estimates.csv"] = sha256_file(population_path)
    else:
        population = pd.DataFrame(columns=IDENTITY_COLUMNS + ["pop_2011"])
    populations = district_population(districts, population)
    hubs, major_ports, invalid_hubs = _load_hubs(root, nh, sources)
    fields = load_or_build_cached_graph(nh, cache_dir=Path(cache_dir))
    validate_graph_metadata(*fields)
    graph = SimpleNamespace(**dict(zip(("nh_graph", "nh_distance_graph", "nh_node_xy",
                                       "nh_comp_labels", "nh_tree", "nh_bridge_mask"), fields)))
    origin_candidates = [_point_candidates(p, graph) for p in points]
    hub_candidates = [_point_candidates(h["point"], graph) for h in hubs]
    components = _hub_components(hub_candidates)
    hub_points = np.asarray([h["point"] for h in hubs], dtype=float).reshape(-1, 2)
    kind_slots = {kind: np.array([i for i, h in enumerate(hubs) if h["kind"] == kind], dtype=int)
                  for kind in KINDS}
    matrix_columns = IDENTITY_COLUMNS + [c for _, key in major_ports for c in (
        f"drive_hours_to_{key}", f"road_km_to_{key}")]
    rows, matrix_rows = [], []
    for pos, identity in enumerate(districts[IDENTITY_COLUMNS].to_dict("records")):
        row = dict.fromkeys(SUMMARY_COLUMNS)
        row.update(identity)
        row["pop_2011"] = populations.iloc[pos]
        row["is_island"] = is_island_state(identity["state"])
        if np.isfinite(points[pos]).all():
            access = float(graph.nh_tree.query(points[pos])[0]) / 1000.0
            row["highway_access_dist_km"] = round(access, 1)
            row["highway_access_time_min"] = round(access / ACCESS_SPEED * 60, 1)
        rows.append(row)
        matrix_row = dict.fromkeys(matrix_columns)
        matrix_row.update(identity)
        matrix_rows.append(matrix_row)
    diagnostics = []
    for start in range(0, len(rows), chunk_size):
        stop = min(start + chunk_size, len(rows))
        selected, network, grouped = {}, {}, {}
        for pos in range(start, stop):
            selection = _compatible_hubs(origin_candidates[pos], components, len(hubs))
            selected[pos] = selection
            network[pos] = np.full(len(hubs), np.inf)
            u, v = selection[:2]
            for source in np.unique(u[u >= 0]):
                slots = np.flatnonzero(u == source)
                grouped.setdefault(int(source), []).append((pos, slots, v[slots]))
        source_nodes = list(grouped)
        for offset in range(0, len(source_nodes), chunk_size):
            batch = source_nodes[offset:offset + chunk_size]
            hours = dijkstra(graph.nh_graph, directed=False, indices=batch)
            for j, source in enumerate(batch):
                for pos, slots, targets in grouped[source]:
                    network[pos][slots] = hours[j, targets]
            del hours
        requests, locations = [], []
        for pos in range(start, stop):
            u, v, od, hd, _ = selected[pos]
            compatible = u >= 0
            if not np.isfinite(network[pos][compatible]).all():
                raise kernel._integrity("Same-component destination is unreachable on the highway graph")
            totals = network[pos] + od / 1000.0 / ACCESS_SPEED + hd / 1000.0 / ACCESS_SPEED
            same_point = np.all(hub_points == points[pos], axis=1)
            totals[same_point & compatible] = 0.0
            winners = []
            for kind, slots in kind_slots.items():
                if len(slots):
                    slot = int(slots[np.argmin(totals[slots])])
                    if np.isfinite(totals[slot]):
                        winners.append((slot, kind, None))
            winners.extend((slot, None, key) for slot, key in major_ports if compatible[slot])
            for slot, kind, key in winners:
                requests.append((int(u[slot]), int(v[slot])))
                locations.append((pos, slot, kind, key, float(totals[slot]), bool(same_point[slot])))
        metrics = kernel.pair_network_metrics(graph, requests, chunk_size=chunk_size)
        for (pos, slot, kind, key, total, zero), metric in zip(locations, metrics):
            if metric is None:
                raise kernel._integrity("Selected compatible pair has no network metrics")
            if metric.network_time_hours != network[pos][slot]:
                raise kernel._integrity("Pass 1/2 highway network time mismatch")
            od, hd = selected[pos][2:4]
            distance = 0.0 if zero else metric.network_distance_km + od[slot] / 1000.0 + hd[slot] / 1000.0
            if kind is not None:
                row = rows[pos]
                row[f"nearest_{kind}_name"] = hubs[slot]["name"]
                row[f"{kind}_straight_km"] = round(float(np.linalg.norm(points[pos] - hubs[slot]["point"])) / 1000, 1)
                row[f"{kind}_road_distance_km"] = round(float(distance), 1)
                row[f"{kind}_drive_time_hours"] = round(total, 2)
                row[f"{kind}_drive_time_min"] = round(total * 60.0, 1)
            else:
                matrix_rows[pos][f"drive_hours_to_{key}"] = round(total, 2)
                matrix_rows[pos][f"road_km_to_{key}"] = round(float(distance), 1)
            diagnostics.append({"district_row": pos, "kind": kind, "port_key": key,
                                "hub_source_order": hubs[slot]["source_order"],
                                "synthetic_bridge_count": metric.synthetic_bridge_count,
                                "synthetic_bridge_distance_km": metric.synthetic_bridge_distance_km,
                                "maximum_synthetic_bridge_m": metric.maximum_synthetic_bridge_m})
    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    port_matrix = pd.DataFrame(matrix_rows, columns=matrix_columns)
    # CSV/parquet identity and missingness must survive uncoded primary rows.
    for frame in (summary, port_matrix):
        frame["district_code"] = frame["district_code"].astype("float64")
        for column in frame.columns:
            if column not in IDENTITY_COLUMNS and not column.startswith("nearest_") and column != "is_island":
                frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
    provenance = {
        "data_dir": str(root.resolve()), "cache_dir": str(Path(cache_dir).resolve()),
        "sources_sha256": sources, "graph_fingerprint": compute_graph_fingerprint(nh),
        "objective": "closest-compatible (feeder_sum_m, origin_node, hub_node, component); then minimum LMV total hours per category, source row order ties",
        "path_metric": "distance and bridge diagnostics on fastest-time predecessor path",
        "lmv_factor": 1.0, "feeder_speed_kmh": ACCESS_SPEED, "candidate_radius_m": 150000.0,
        "units": {"distance": "km", "time": "hours", "minutes": "minutes"},
        "null_policy": "no compatible candidate or invalid geometry/coordinates => null; corrupt graph => error",
        "island_policy": "geographic state identity; no distance-based suppression",
        "district_rows": len(summary), "invalid_district_geometries": int((~np.isfinite(points).all(axis=1)).sum()),
        "invalid_hub_coordinates": invalid_hubs, "winner_and_port_diagnostics": diagnostics,
        "chunk_size": int(chunk_size), "elapsed_seconds": time.perf_counter() - started,
    }
    return summary, port_matrix, provenance


def write_travel_outputs(summary_df, port_matrix_df, *, summary_csv, summary_parquet, port_matrix_csv):
    """Individual atomic writers, not a three-file transaction or promotion."""
    atomic_write_csv(summary_df, summary_csv)
    atomic_write_parquet(summary_df, summary_parquet)
    atomic_write_csv(port_matrix_df, port_matrix_csv)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--cache-dir", type=Path, default=PROJECT_ROOT / "outputs/cache")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir is not None else args.data_dir / "analysis"
    summary, port_matrix, provenance = build_travel_outputs(data_dir=args.data_dir, cache_dir=args.cache_dir)
    write_travel_outputs(summary, port_matrix,
                         summary_csv=output_dir / "nh_district_travel_time_summary.csv",
                         summary_parquet=output_dir / "nh_district_travel_time_summary.parquet",
                         port_matrix_csv=output_dir / "nh_district_port_matrix.csv")
    print(f"Wrote {len(summary)} district rows; elapsed {provenance['elapsed_seconds']:.2f}s")


if __name__ == "__main__":
    main()
