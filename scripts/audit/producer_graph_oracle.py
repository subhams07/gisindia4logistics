"""Bounded source-grounded graph oracles, independent of analytical producers.

Pure expectation functions accept a validated six-field graph capture. The I/O
adapter returns check records (not reports), never invokes producers or rewrites
artifacts. Only the canonical builder may populate the explicitly supplied cache.
Catchments are approximate coverage, not navigation or point reachability.
"""
import math
from numbers import Real
import os
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import MultiPoint, Point, shape
from shapely.ops import transform

from scripts.analyze import highway_pair_kernel as kernel
from scripts.analyze.producer_io import is_island_state
from scripts.analyze.producer_manifest import MANIFEST_FILENAME, read_manifest, validate_config
from scripts.audit.identity_contract import IdentityError, collision_rows, exact_identities_equal, identity_key, validate_identities
from scripts.clean import graph_builder as builder

FIELDS = ("nh_graph", "nh_distance_graph", "nh_node_xy", "nh_comp_labels", "nh_tree", "nh_bridge_mask")
IDENTITY = ["state", "district", "district_code"]
NE_STATES = {"assam", "arunachal pradesh", "manipur", "meghalaya", "mizoram", "nagaland", "sikkim", "tripura"}
OBJECTIVE = "closest-compatible scalar snaps; minimum LMV total hours; source-order ties; time-predecessor distance"


def mandatory_sample_indices(frame):
    """Existing strata (Pune, first NE, first island, first uncoded), then every collision row.

    Collision rows are permissive and never raise; strict validation is the caller's job.
    """
    chosen = []
    masks = [frame.district.astype(str).str.strip().str.casefold() == "pune",
             frame.state.astype(str).str.strip().str.casefold().isin(NE_STATES),
             frame.state.map(is_island_state), frame.district_code.isna()]
    for mask in masks:
        indices = np.flatnonzero(mask.to_numpy())
        if len(indices) and int(indices[0]) not in chosen:
            chosen.append(int(indices[0]))
    for index in collision_rows(frame[IDENTITY].to_dict("records")):
        if index not in chosen:
            chosen.append(index)
    return chosen


def sample_district_indices(frame):
    """Source indices, not guessed LGD codes; mandatory strata and collisions, then code strides."""
    chosen = mandatory_sample_indices(frame)[:12]
    numeric = pd.to_numeric(frame.district_code, errors="raise")
    ordered = sorted(np.flatnonzero(numeric.notna()), key=lambda i: (numeric.iloc[i], int(i)))
    if ordered:
        for position in np.linspace(0, len(ordered) - 1, min(12, len(ordered)), dtype=int):
            index = int(ordered[position])
            if index not in chosen and len(chosen) < 12:
                chosen.append(index)
    # All-uncoded tiny fixtures still exercise more than the first source row.
    for index in range(len(frame)):
        if len(chosen) >= min(12, len(frame)):
            break
        if index not in chosen:
            chosen.append(index)
    return chosen


def district_points(frame):
    projected = frame.to_crs(7755)
    points = np.full((len(frame), 2), np.nan)
    for index, geometry in enumerate(projected.geometry):
        if geometry is not None and not geometry.is_empty and geometry.is_valid:
            point = geometry.representative_point()
            if np.isfinite([point.x, point.y]).all():
                points[index] = (point.x, point.y)
    return points


def coordinate(row):
    values = [row.get("longitude"), row.get("latitude")]
    if any(isinstance(v, (bool, np.bool_)) for v in values):
        return None
    lon, lat = pd.to_numeric(pd.Series(values), errors="coerce")
    if not np.isfinite([lon, lat]).all() or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    point = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(7755).iloc[0]
    return np.array([point.x, point.y]) if np.isfinite([point.x, point.y]).all() else None


def port_key(name):
    return name.replace("Port of ", "").replace(" Port", "").replace(" ", "_").lower()


def _candidates(point, graph):
    if point is None or not np.isfinite(point).all():
        return np.array([], dtype=int), np.array([], dtype=float)
    ids, distances = kernel.radius_candidates(point, graph)
    # Exercise scalar sparse-label minima, never the producer's vector index.
    best = kernel.nearest_per_component(distances, ids, graph.nh_comp_labels)
    return np.array([v[1] for v in best.values()], dtype=int), np.array([v[0] for v in best.values()])


def route_expectations(graph, points, hubs, *, chunk_size=32):
    """Every sampled district x port/terminal pair, bounded source Dijkstra.

    Each record contains rounded category values and all major-port matrix cells.
    No all-rail scan; no path logs or all-origins x nodes retained allocation.
    """
    if type(chunk_size) is not int or not 1 <= chunk_size <= 32:
        raise ValueError("Unsupported oracle chunk size")
    hc = [_candidates(h["point"], graph) for h in hubs]
    requests, jobs = [], []
    for index, point in enumerate(points):
        oi, od = _candidates(point, graph)
        for slot, (hi, hd) in enumerate(hc):
            pair = kernel.select_compatible_pair(od, oi, hd, hi, graph.nh_comp_labels)
            if pair is not None:
                requests.append(pair[:2])
                jobs.append((index, slot, pair))
    metrics = kernel.pair_network_metrics(graph, requests, chunk_size=chunk_size)
    by_row = [{} for _ in points]
    for (index, slot, pair), metric in zip(jobs, metrics):
        if metric is None:
            raise ValueError("Compatible scalar pair missing metrics")
        point, hub = points[index], hubs[slot]
        zero = np.array_equal(point, hub["point"])
        # Producer's sequential feeder order (origin then hub); pooling changes rounding.
        hours = 0. if zero else metric.network_time_hours + pair[2] / 1000. / 35. + pair[3] / 1000. / 35.
        km = 0. if zero else metric.network_distance_km + pair[2] / 1000. + pair[3] / 1000.
        if not np.isfinite([hours, km]).all():
            raise ValueError("Nonfinite route oracle metrics")
        by_row[index][slot] = (hours, km, float(np.linalg.norm(point - hub["point"])) / 1000.)
    outputs = []
    for values in by_row:
        result = {"matrix": {}}
        for kind in ("port", "freight_terminal"):
            columns = [f"nearest_{kind}_name", f"{kind}_straight_km", f"{kind}_road_distance_km", f"{kind}_drive_time_hours", f"{kind}_drive_time_min"]
            expected = dict.fromkeys(columns)
            valid = [slot for slot in values if hubs[slot]["kind"] == kind]
            if valid:
                slot = min(valid, key=lambda s: (values[s][0], hubs[s]["source_order"]))
                hours, km, straight = values[slot]
                expected.update(zip(columns, [hubs[slot]["name"], round(straight, 1), round(km, 1), round(hours, 2), round(hours * 60., 1)]))
            result[kind] = expected
        for slot, hub in enumerate(hubs):
            if hub.get("major"):
                key = port_key(hub["name"])
                hours, km = values[slot][:2] if slot in values else (None, None)
                result["matrix"].update({f"drive_hours_to_{key}": round(hours, 2) if hours is not None else None,
                    f"road_km_to_{key}": round(km, 1) if km is not None else None})
        outputs.append(result)
    return outputs


def iso_expectations(graph, point, thresholds, *, buffer_m=8000., simplify_m=500.):
    """Independent supported-component nearest scalar snap and single-source TIME Dijkstra."""
    snap = None
    if point is not None:
        ids, distances = kernel.radius_candidates(point, graph)
        values, counts = np.unique(graph.nh_comp_labels, return_counts=True)
        sizes = dict(zip(values.tolist(), counts.tolist()))
        candidates = [(float(d), int(i)) for i, d in zip(ids, distances) if np.isfinite(d) and 0 <= d <= 150000.
                      and sizes[int(graph.nh_comp_labels[int(i)])] >= ORACLE_MIN_SUPPORTED_COMPONENT_NODES]
        if candidates:
            snap = min(candidates)
    feeder = snap[0] / 1000. if snap is not None else None
    feeder_hours = feeder / 35. if feeder is not None else None
    network = None
    if snap is not None and feeder_hours <= max(thresholds):
        network = dijkstra(graph.nh_graph, directed=False, indices=snap[1], limit=max(thresholds) - feeder_hours + 1e-9)
    results = []
    for threshold in thresholds:
        status = "invalid_coordinates" if point is None else "no_highway_within_150km"
        count, geometry = 0, None
        if snap is not None:
            status = "feeder_exceeds_threshold" if feeder_hours > threshold else "unsupported_threshold"
            if feeder_hours <= threshold:
                mask = np.isfinite(network) & (network + feeder_hours <= threshold)
                count = int(np.count_nonzero(mask))
                if count >= 3:
                    geometry = MultiPoint(graph.nh_node_xy[mask]).convex_hull.buffer(buffer_m).simplify(simplify_m, preserve_topology=True)
                    if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                        raise ValueError("Invalid independent catchment geometry")
                    status = "ok"
        results.append(dict(time_threshold_hours=threshold, status=status, reachable_node_count=count,
            snap_feeder_km=feeder, feeder_time_hours=feeder_hours, geometry=geometry))
    return results


def _equal(actual, expected):
    if expected is None or (not isinstance(expected, str) and pd.isna(expected)):
        return bool(pd.isna(actual))
    if isinstance(expected, str):
        return isinstance(actual, str) and actual == expected
    if isinstance(actual, (bool, np.bool_)):
        return False
    return isinstance(actual, (int, float, np.number)) and np.isfinite(actual) and abs(float(actual) - expected) <= 1e-9


def _values_equal(row, expected):
    return all(column in row and _equal(row[column], value) for column, value in expected.items())


def _identity(row):
    """Qualified key: (coded, state, code) or (uncoded, state, district); raises IdentityError."""
    return identity_key(row)


def _identities_aligned(frame, records):
    try:
        return set(IDENTITY) <= set(frame) and exact_identities_equal(frame[IDENTITY].to_dict("records"), records)
    except IdentityError:
        return False


def _record(dataset, name, ok, detail=""):
    return dict(dataset=dataset, check=name, ok=bool(ok), detail=detail)


def _route_checks(source_root, artifact_root, graph, checks=None):
    """Append records to `checks` as they are produced so a later failure cannot discard them."""
    checks = [] if checks is None else checks
    districts = gpd.read_file(source_root / "administrative/india_districts_lgd.geojson")
    if not set(IDENTITY) <= set(districts):
        raise ValueError("Missing primary district identity")
    # The entire declared source is validated before any sampling; IdentityError fails the section.
    records = districts[IDENTITY].to_dict("records")
    identities = validate_identities(records)
    # Sampling and mandatory coverage precede every fallible hub/route/artifact step.
    indices = sample_district_indices(districts)
    mandatory = mandatory_sample_indices(districts)
    if len(mandatory) > 12:
        checks.append(_record("route_oracle", "mandatory collision coverage within 12-row bound", False,
            f"mandatory_rows={len(mandatory)}; omitted={len(mandatory) - 12}; truncated bounded sample retained"))
    hubs = []
    for kind, relative in (("port", "logistics_hubs/ports.csv"), ("freight_terminal", "rail/freight_terminals.csv")):
        frame = pd.read_csv(source_root / relative)
        if not {"name", "latitude", "longitude"} <= set(frame) or (kind == "port" and "hub_type" not in frame):
            raise ValueError("Missing actual source hub schema")
        for order, row in enumerate(frame.to_dict("records")):
            if not isinstance(row["name"], str) or not row["name"].strip():
                raise ValueError("Invalid source hub name")
            hubs.append(dict(kind=kind, name=row["name"], source_order=order, point=coordinate(row), major=kind == "port" and row["hub_type"] == "major_port"))
    keys = [port_key(h["name"]) for h in hubs if h["major"]]
    if len(keys) != len(set(keys)) or not keys:
        raise ValueError("Missing or duplicate major port identities")
    points = district_points(districts)[indices]
    expected = route_expectations(graph, points, hubs)
    checks.append(_record("route_oracle", "fixed source-derived sample/objective", bool(indices), f"sample_ids={[identities[i] for i in indices]}; rows={indices}; limit=12; chunk<=32; {OBJECTIVE}"))
    # Read lazily so checks from earlier artifacts survive a later artifact read failure.
    frames = [("summary CSV", lambda: pd.read_csv(artifact_root / "nh_district_travel_time_summary.csv")),
              ("summary parquet", lambda: pd.read_parquet(artifact_root / "nh_district_travel_time_summary.parquet")),
              ("matrix", lambda: pd.read_csv(artifact_root / "nh_district_port_matrix.csv"))]
    for label, load in frames:
        frame = load()
        aligned = _identities_aligned(frame, records)
        checks.append(_record("route_oracle", f"{label}: exact source identities including null codes", aligned))
        if not aligned:
            continue
        for index, values in zip(indices, expected):
            row = frame.iloc[index]
            for kind in (["matrix"] if label == "matrix" else ["port", "freight_terminal"]):
                wanted = values[kind]
                checks.append(_record("route_oracle", f"{label} row {index} {kind}: scalar rounded values/nulls", _values_equal(row, wanted), f"id={identities[index]}; expected={wanted}"))
    return checks


def _iso_checks(source_root, artifact_root, graph, thresholds, buffer_m, simplify_m, checks=None):
    """Append records to `checks` as produced; earlier diagnostics survive later failures."""
    checks = [] if checks is None else checks
    back = Transformer.from_crs(4326, 7755, always_xy=True).transform
    for source, output, kind in (("ports.csv", "major_ports_isochrones.geojson", "major_port"), ("mmlps.csv", "mmlp_isochrones.geojson", "mmlp")):
        frame = pd.read_csv(source_root / "logistics_hubs" / source)
        if not {"name", "latitude", "longitude"} <= set(frame):
            raise ValueError("Missing source isochrone hub schema")
        if kind == "major_port":
            frame = frame[frame.hub_type == kind]
        rows = frame.to_dict("records")
        names = [row["name"] for row in rows]
        if not rows or len(set(names)) != len(names):
            raise ValueError("Missing or duplicate source isochrone identity")
        features = read_manifest(artifact_root / output)["features"]
        actual_ids = [(f.get("properties", {}).get("hub_name"), f.get("properties", {}).get("hub_type"), f.get("properties", {}).get("time_threshold_hours")) for f in features]
        wanted_ids = [(name, kind, t) for name in names for t in thresholds[kind]]
        aligned = actual_ids == wanted_ids
        checks.append(_record("iso_oracle", f"{kind}: all source/threshold identities", aligned, f"source_hubs={len(rows)}; features={len(features)}"))
        if not aligned:
            continue
        selected, valid, exceptional = [], 0, set()
        for index, row in enumerate(rows):
            point = coordinate(row)
            if point is None:
                category = "invalid_coordinates"
            else:
                ids, _ = kernel.radius_candidates(point, graph)
                category = "valid" if len(ids) else "no_highway_within_150km"
            if category == "valid" and valid < 2:
                selected.append((index, point))
                valid += 1
            elif category != "valid" and category not in exceptional:
                selected.append((index, point))
                exceptional.add(category)
        checks.append(_record("iso_oracle", f"{kind}: bounded independent probe sample", valid == 2,
            f"source_rows={[i for i, _ in selected]}; hubs={[names[i] for i, _ in selected]}; two valid snaps plus present invalid/no-snap strata; single-source Dijkstra"))
        for index, point in selected:
            expected = iso_expectations(graph, point, thresholds[kind], buffer_m=buffer_m, simplify_m=simplify_m)
            for offset, wanted in enumerate(expected):
                feature = features[index * len(thresholds[kind]) + offset]
                properties = feature.get("properties", {})
                scalar = {k: v for k, v in wanted.items() if k != "geometry"}
                scalar["coverage_method"] = "approximate_highway_catchment"
                checks.append(_record("iso_oracle", f"{kind} {names[index]} {wanted['time_threshold_hours']:g}h: count/status/feeder", _values_equal(properties, scalar), f"expected={scalar}"))
                wanted_geom, geometry = wanted["geometry"], feature.get("geometry")
                okay, detail = geometry is None and wanted_geom is None, "expected=null"
                if wanted_geom is not None and geometry is not None:
                    actual = transform(back, shape(geometry))
                    if actual.is_valid and not actual.is_empty and actual.geom_type in {"Polygon", "MultiPolygon"}:
                        hausdorff = actual.hausdorff_distance(wanted_geom)
                        difference = actual.symmetric_difference(wanted_geom).area
                        # A 0.1 m boundary perturbation envelope, plus Hausdorff.
                        area_limit = .1 * wanted_geom.length + np.pi * .1 ** 2
                        okay = hausdorff <= .1 and difference <= area_limit
                        detail = f"expected_area_m2={round(wanted_geom.area, 1)}; actual_area_m2={round(actual.area, 1)}; hausdorff_m={hausdorff:.6g}; symmetric_difference_m2={difference:.6g}; boundary_tolerance_m=0.1"
                checks.append(_record("iso_oracle", f"{kind} {names[index]} {wanted['time_threshold_hours']:g}h: projected coverage geometry/null", okay, detail))
    return checks


# --- Category and highway-access oracle (S4) ---------------------------------------------------
# Producer-independent (own source mapping, own selection/rounding, own time Dijkstra and KD tree) but it
# shares the accepted highway_pair_kernel and graph builder; it does not route over a rail network.
CATEGORY_SOURCES = {
    "rail_station": ("rail/railway_stations.csv", "station_name"),
    "icd": ("logistics_hubs/icds.csv", "name"),
    "mmlp": ("logistics_hubs/mmlps.csv", "name"),
    "air_cargo": ("logistics_hubs/air_cargo.csv", "name"),
    "icp": ("logistics_hubs/icps.csv", "name"),
    "toll_plaza": ("roads/toll_plazas.csv", "name"),
}
CATEGORY_KINDS = (*CATEGORY_SOURCES, "expressway")
ACCESS_COLUMNS = ("highway_access_dist_km", "highway_access_time_min")


def _category_columns(kind):
    return [f"nearest_{kind}_name", f"{kind}_straight_km", f"{kind}_road_distance_km", f"{kind}_drive_time_hours", f"{kind}_drive_time_min"]


def _expressway_hubs(nh):
    """Motorway-row midpoints from the selected projected highway frame, in source order.

    Invalid/empty geometries are skipped (their enumeration index is kept); the name is the existing
    name, else "NE " + nh, else "Expressway".
    """
    hubs = []
    if "highway" not in nh:
        return hubs
    for order, (_, row) in enumerate(nh.loc[nh.highway == "motorway"].iterrows()):
        geometry = row.geometry
        if not hasattr(geometry, "is_empty") or geometry.is_empty or not geometry.is_valid:  # Missing (None/NaN), empty or invalid.
            continue
        point = geometry.interpolate(0.5, normalized=True)
        if not np.isfinite([point.x, point.y]).all():
            continue
        name = row.get("name")
        if pd.isna(name):
            ref = row.get("nh")
            name = "NE " + str(ref) if pd.notna(ref) else "Expressway"
        hubs.append(dict(kind="expressway", name=name, source_order=order, point=np.array([point.x, point.y])))
    return hubs


def _category_hubs(source_root, nh):
    """{kind: hubs | None}; None means the source file is absent (all-null expectations)."""
    hubs = {}
    for kind, (relative, name_column) in CATEGORY_SOURCES.items():
        path = Path(source_root) / relative
        if not path.exists():
            hubs[kind] = None
            continue
        frame = pd.read_csv(path)
        if not {name_column, "latitude", "longitude"} <= set(frame):
            raise ValueError("Missing actual category hub source schema")
        # Literal names in source order; unusable coordinates stay as null candidates.
        hubs[kind] = [dict(kind=kind, name=row[name_column], source_order=order, point=coordinate(row))
                      for order, row in enumerate(frame.to_dict("records"))]
    hubs["expressway"] = _expressway_hubs(nh)
    return hubs


def category_expectations(graph, points, hubs_by_kind, *, chunk_size=32):
    """Per sampled point: rounded nearest-hub values for each category plus highway access.

    Every (origin, hub) pair uses the scalar kernel selection; one TIME-only Dijkstra per <=32 unique origin
    nodes keeps only per-pair target scalars; the winner per (origin, category) is chosen on unrounded
    sequential-feeder time with source-order ties, and path metrics are requested for winners only.
    """
    if type(chunk_size) is not int or not 1 <= chunk_size <= 32:
        raise ValueError("Unsupported oracle chunk size")
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    origins = [_candidates(point, graph) for point in points]
    plan = []
    for kind in CATEGORY_KINDS:
        hubs = hubs_by_kind.get(kind) or []
        candidates = [_candidates(h["point"], graph) for h in hubs]
        for index, (origin_ids, origin_distances) in enumerate(origins):
            for slot, (hub_ids, hub_distances) in enumerate(candidates):
                pair = kernel.select_compatible_pair(origin_distances, origin_ids, hub_distances, hub_ids, graph.nh_comp_labels)
                if pair is not None:
                    plan.append((index, kind, slot, pair))
    network = [None] * len(plan)
    grouped = {}
    for number, (_, _, _, pair) in enumerate(plan):
        grouped.setdefault(pair[0], []).append(number)
    sources = list(grouped)
    for offset in range(0, len(sources), chunk_size):
        batch = sources[offset:offset + chunk_size]
        hours = dijkstra(graph.nh_graph, directed=False, indices=batch)
        for row, source in enumerate(batch):
            for number in grouped[source]:
                network[number] = float(hours[row, plan[number][3][1]])  # A Python scalar, never a view.
        del hours
    best = {}
    for number, (index, kind, slot, pair) in enumerate(plan):
        if not np.isfinite(network[number]):
            raise ValueError("Same-component destination is unreachable on the highway graph")
        hub = hubs_by_kind[kind][slot]
        zero = np.array_equal(points[index], hub["point"])
        total = 0. if zero else network[number] + pair[2] / 1000. / 35. + pair[3] / 1000. / 35.
        if not np.isfinite(total):
            raise ValueError("Nonfinite category oracle metrics")
        # Raw time first, then the hub's declared source_order; the input slot only breaks equal declared orders.
        key = (total, hub["source_order"], slot)
        if (index, kind) not in best or key < best[(index, kind)][0]:
            best[(index, kind)] = (key, number, zero)
    outputs = [{kind: dict.fromkeys(_category_columns(kind)) for kind in CATEGORY_KINDS} | {"access": dict.fromkeys(ACCESS_COLUMNS)} for _ in points]
    winners = list(best.items())
    metrics = kernel.pair_network_metrics(graph, [plan[number][3][:2] for _, (_, number, _) in winners], chunk_size=chunk_size)
    for ((index, kind), ((total, _, slot), number, zero)), metric in zip(winners, metrics):
        if metric is None:
            raise ValueError("Selected compatible pair has no network metrics")
        if metric.network_time_hours != network[number]:
            raise ValueError("Time-only and path-metric highway network times differ")
        pair, hub = plan[number][3], hubs_by_kind[kind][slot]
        distance = 0. if zero else metric.network_distance_km + pair[2] / 1000. + pair[3] / 1000.
        columns = _category_columns(kind)
        outputs[index][kind].update(zip(columns, [hub["name"], round(float(np.linalg.norm(points[index] - hub["point"])) / 1000, 1),
                                                  round(float(distance), 1), round(total, 2), round(total * 60., 1)]))
    tree = cKDTree(graph.nh_node_xy)  # The oracle's own tree, not graph.nh_tree.
    for index, point in enumerate(points):
        if np.isfinite(point).all():
            raw = float(tree.query(point)[0]) / 1000.
            outputs[index]["access"].update(zip(ACCESS_COLUMNS, [round(raw, 1), round(raw / 35. * 60, 1)]))  # Independent roundings.
    return outputs


def _category_checks(source_root, artifact_root, graph, nh, checks=None):
    """Append records as produced; the fixed sample and S1 identity contract are shared with the route section."""
    checks = [] if checks is None else checks
    districts = gpd.read_file(source_root / "administrative/india_districts_lgd.geojson")
    if not set(IDENTITY) <= set(districts):
        raise ValueError("Missing primary district identity")
    records = districts[IDENTITY].to_dict("records")
    try:
        identities = validate_identities(records)
    except IdentityError as exc:
        # The route section already reports the identity failure; this one is blocked, not crashed.
        checks.append(_record("category_oracle", "source identity invalid: category oracle blocked", False, type(exc).__name__))
        return checks
    indices = sample_district_indices(districts)
    mandatory = mandatory_sample_indices(districts)
    if len(mandatory) > 12:
        # STOP: the route section already reports the overflow and continues with the truncated sample; the
        # category section records its own FAIL and does no further work on an incomplete mandatory sample.
        checks.append(_record("category_oracle", "category sample: mandatory collision coverage within 12-row bound", False,
            f"mandatory_rows={len(mandatory)}; omitted={len(mandatory) - 12}; category checks not run"))
        return checks
    hubs_by_kind = _category_hubs(source_root, nh)
    expected = category_expectations(graph, district_points(districts)[indices], hubs_by_kind)
    absent = [kind for kind in CATEGORY_KINDS if hubs_by_kind[kind] is None]
    checks.append(_record("category_oracle", "fixed source-derived sample/objective", bool(indices),
        f"sample_ids={[identities[i] for i in indices]}; rows={indices}; limit=12; chunk<=32; absent_sources={absent}; {OBJECTIVE}; "
        "producer-independent but shares the accepted highway_pair_kernel and graph builder; no rail network routing"))
    frames = [("summary CSV", lambda: pd.read_csv(artifact_root / "nh_district_travel_time_summary.csv")),
              ("summary parquet", lambda: pd.read_parquet(artifact_root / "nh_district_travel_time_summary.parquet"))]
    for label, load in frames:
        frame = load()
        aligned = _identities_aligned(frame, records)
        # Source-identity alignment is a shared route/category contract, reported under the identity dataset.
        checks.append(_record("route_oracle", f"category sample: {label}: exact source identities including null codes", aligned))
        if not aligned:
            continue
        for index, values in zip(indices, expected):
            row = frame.iloc[index]
            for kind in (*CATEGORY_KINDS, "access"):
                detail = f"id={identities[index]}; expected={values[kind]}" + ("; source absent: all-null expectation" if kind in absent else "")
                checks.append(_record("category_oracle", f"{label} row {index} {kind}: scalar rounded values/nulls", _values_equal(row, values[kind]), detail))
    return checks


# --- Isochrone coverage oracle (N1c) -------------------------------------------------------------
# Independent of the coverage helper, the sidecar module and every producer: its own strict coordinate predicate,
# its own snap selection and one np.unique partition map. It shares the accepted kernel candidate enumeration, the
# tree and the builder, so it is NOT independent of defects in those. It certifies only that the declared hub
# identities match the source identities and that each declared snap pair matches an independent computation;
# snap rule, statuses, thresholds, derived fields, geometry and provenance stay with the generic validator.
# Independent literal expectation of the supported-component floor; a test asserts it equals the kernel's.
ORACLE_MIN_SUPPORTED_COMPONENT_NODES = 1000
COVERAGE_SIDECAR = "producer_limitations.json"
COVERAGE_AVAILABILITY = "coverage sidecar availability"
COVERAGE_LEGACY = "not applicable: legacy coverage unavailable"
COVERAGE_FINAL = "coverage hub set equals source order"
COVERAGE_KEYS = ("hub_type", "hub_order", "hub_name", "snap_feeder_km", "snap_component_node_count")


def _strict_coordinate(row):
    """EPSG:7755 point, or None: real non-bool finite numbers inside the geographic ranges, finite projection."""
    lon, lat = row.get("longitude"), row.get("latitude")
    for value in (lon, lat):
        try:
            if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not math.isfinite(value):
                return None
        except OverflowError:
            return None
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        return None
    point = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(7755).iloc[0]
    return np.array([point.x, point.y]) if np.isfinite([point.x, point.y]).all() else None


def _coverage_snap(point, graph, sizes=None):
    """(distance_m, node) of the closest vertex within 150 km (inclusive) on a supported component, node-ID ties, else None."""
    if point is None:
        return None
    ids, distances = kernel.radius_candidates(point, graph)
    sizes = _coverage_component_sizes(graph.nh_comp_labels) if sizes is None else sizes
    candidates = [(float(d), int(i)) for i, d in zip(ids, distances) if np.isfinite(d) and 0 <= d <= 150000.0
                  and sizes[int(graph.nh_comp_labels[int(i)])] >= ORACLE_MIN_SUPPORTED_COMPONENT_NODES]
    return min(candidates) if candidates else None


def _coverage_component_sizes(labels):
    """Plain-int node count per component label from one partition map; labels may be sparse or huge."""
    values, counts = np.unique(labels, return_counts=True)
    return {int(value): int(count) for value, count in zip(values, counts)}


def _coverage_expected(source_root, graph):
    """(identity sequence, {identity: None | (feeder_km, component_size)}) from every actual source hub."""
    root = Path(source_root) / "logistics_hubs"
    ports, mmlps = pd.read_csv(root / "ports.csv"), pd.read_csv(root / "mmlps.csv")
    if not {"hub_type", "name", "latitude", "longitude"} <= set(ports) or not {"name", "latitude", "longitude"} <= set(mmlps):
        raise ValueError("Missing coverage source hub schema")
    sizes = _coverage_component_sizes(graph.nh_comp_labels)
    sequence, wanted = [], {}
    for hub_type, frame in (("major_port", ports[ports["hub_type"] == "major_port"]), ("mmlp", mmlps)):
        if frame.empty:
            raise ValueError("No source hubs for a coverage hub type")
        seen = set()
        for order, row in enumerate(frame.to_dict("records")):
            name = row["name"]
            if type(name) is not str or not name.strip():
                raise ValueError("Invalid coverage source hub name")
            key = name.strip().casefold()
            if key in seen:
                raise ValueError("Duplicate coverage source hub name")
            seen.add(key)
            snap = _coverage_snap(_strict_coordinate(row), graph, sizes)
            identity = (hub_type, order, name)
            wanted[identity] = None if snap is None else (snap[0] / 1000.0, sizes[int(graph.nh_comp_labels[snap[1]])])
            sequence.append(identity)
    return sequence, wanted


def _coverage_identity(entry):
    """The declared (hub_type, hub_order, hub_name); raises ValueError when it cannot be formed from the entry."""
    if not isinstance(entry, dict) or any(key not in entry for key in COVERAGE_KEYS):
        raise ValueError("Unusable coverage entry")
    hub_type, order, name = entry["hub_type"], entry["hub_order"], entry["hub_name"]
    if type(hub_type) is not str or type(order) is not int or type(name) is not str or not name.strip():
        raise ValueError("Unusable coverage entry identity")
    return hub_type, order, name


def _coverage_pair_matches(entry, wanted):
    feeder, component = entry["snap_feeder_km"], entry["snap_component_node_count"]
    if wanted is None:
        return feeder is None and component is None
    return (type(feeder) is float and math.isfinite(feeder) and feeder >= 0 and abs(feeder - wanted[0]) <= 1e-9
            and type(component) is int and component >= 1 and component == wanted[1])


def _coverage_checks(source_root, artifact_root, graph, checks=None):
    """Append records as produced; a crash keeps its earlier records and never becomes a legacy result."""
    checks = [] if checks is None else checks
    path = Path(artifact_root) / COVERAGE_SIDECAR
    if not os.path.lexists(path):  # Only true absence is legacy; any present path is read (and may crash).
        checks.append(_record("coverage_oracle", COVERAGE_AVAILABILITY, True, COVERAGE_LEGACY))
        return checks
    document = read_manifest(path)
    version = document.get("schema_version") if isinstance(document, dict) else None
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported coverage sidecar schema")
    if version == 1:  # Discriminator only: a version-1 document is not validated here.
        checks.append(_record("coverage_oracle", COVERAGE_AVAILABILITY, True, COVERAGE_LEGACY))
        return checks
    checks.append(_record("coverage_oracle", COVERAGE_AVAILABILITY, True, "version 2 declaration present"))
    coverage = document.get("isochrone_coverage")
    entries = coverage.get("entries") if isinstance(coverage, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Unusable coverage entries")
    sequence, wanted = _coverage_expected(source_root, graph)
    declared = []
    for entry in entries:
        identity = _coverage_identity(entry)
        declared.append(identity)
        known = identity in wanted
        ok = known and _coverage_pair_matches(entry, wanted[identity])
        checks.append(_record("coverage_oracle", f"{identity[0]} {identity[1]} {identity[2]}: independent snap feeder and component cardinality", ok,
                              "" if known else "identity not among the source hubs"))
    checks.append(_record("coverage_oracle", COVERAGE_FINAL, declared == sequence, f"declared={len(declared)}; source={len(sequence)}"))
    return checks


def graph_oracle_checks(source_root, artifact_root, cache_dir):
    """Read actual roots, validate configuration/capture, return PASS/FAIL records.

    Errors propagate to the runner wrapper and become FAIL, never legacy WARN.
    Legacy artifacts use documented canonical defaults; present manifests must
    supply supported exact graph settings and threshold lists.
    """
    source_root, artifact_root, cache_dir = map(Path, (source_root, artifact_root, cache_dir))
    manifest_path = artifact_root / MANIFEST_FILENAME
    thresholds = {"major_port": [4., 8., 12., 24.], "mmlp": [4., 8., 12.]}
    buffer_m, simplify_m = 8000., 500.
    manifest = None
    if manifest_path.exists():
        manifest = read_manifest(manifest_path)
        issues = validate_config(manifest.get("config"))
        if issues:
            return [_record("graph_oracle", "supported explicit graph oracle configuration", False, "; ".join(issues))]
        thresholds = manifest["config"]["thresholds_hours"]
        buffer_m, simplify_m = manifest["config"]["buffer_m"], manifest["config"]["simplify_m"]
    source = builder.load_highway_source(source_root)
    if source is None:
        raise FileNotFoundError("Missing highway source")
    nh, selected = source
    values = builder.load_or_build_cached_graph(nh, cache_dir=cache_dir)
    builder.validate_graph_metadata(*values)
    graph = SimpleNamespace(**dict(zip(FIELDS, values)))
    checks = [_record("graph_oracle", "validated six-field graph capture", True, f"nodes={graph.nh_graph.shape[0]}; directed_edges={graph.nh_graph.nnz}; selected_source={selected.relative_to(source_root).as_posix()}")]
    if manifest is not None:
        actual = dict(fingerprint=builder.compute_graph_fingerprint(nh), version=builder.CACHE_VERSION,
            node_count=graph.nh_graph.shape[0], edge_count=graph.nh_graph.nnz, selected_source=selected.relative_to(source_root).as_posix())
        checks.append(_record("graph_oracle", "manifest matches actual canonical graph", manifest["graph"] == actual))
    # Independent guarded sections: a crash in one still lets the other report.
    # Each section appends to its own list, so earlier records survive its later crash
    # and neither section sees the other's records.
    for dataset, section in (("route_oracle", lambda local: _route_checks(source_root, artifact_root, graph, local)),
                             ("category_oracle", lambda local: _category_checks(source_root, artifact_root, graph, nh, local)),
                             ("iso_oracle", lambda local: _iso_checks(source_root, artifact_root, graph, thresholds, buffer_m, simplify_m, local)),
                             ("coverage_oracle", lambda local: _coverage_checks(source_root, artifact_root, graph, local))):
        local = []
        try:
            section(local)
        except Exception as exc:
            local.append(_record(dataset, "section crashed", False, type(exc).__name__))
        checks += local
    return checks
