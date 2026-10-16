"""Isochrone coverage facts and the sidecar ``isochrone_coverage`` schema (N1a).

Catchments are limited to the road-graph component of the snapped vertex. This
module records, per hub, the snap distance, the size of that component and, per
threshold, the producer status and reachable-node count, then derives whether a
catchment is saturated or bounded by its component. It never reads artifacts,
never measures geometry and does not import the sidecar module.
"""
from __future__ import annotations

import math
from numbers import Real

import geopandas as gpd
import numpy as np
from shapely.geometry import Point

from scripts.analyze import highway_pair_kernel as kernel

PROJ = 7755
SNAP_RULE = "nearest physical NH vertex within 150 km, node-ID ties; catchment limited to that vertex's connected component"
STATUSES = ("invalid_coordinates", "no_highway_within_150km", "feeder_exceeds_threshold", "unsupported_threshold", "ok")
HUB_TYPES = ("major_port", "mmlp")
ENTRY_KEYS = ("hub_type", "hub_name", "hub_order", "thresholds_hours", "statuses", "reachable_node_counts",
              "snap_feeder_km", "snap_component_node_count", "ok_threshold_count", "saturated_across_ok_thresholds",
              "component_bounded_thresholds")
FEEDER_SPEED_KMH = 35.0
MAX_SNAP_M = kernel.MAX_FEEDER_DISTANCE_M
_FACT_KEYS = ("hub_type", "hub_name", "hub_order", "snap_feeder_km", "snap_component_node_count")
_UNSNAPPED = ("invalid_coordinates", "no_highway_within_150km")
_FEEDER_TOLERANCE_KM = 1e-9


def hub_point(row):
    """EPSG:7755 point, or None, under the producer's coordinate policy."""
    lon, lat = row.get("longitude"), row.get("latitude")
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, Real) or not math.isfinite(v) for v in (lon, lat)):
        return None
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        return None
    point = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(PROJ).iloc[0]
    if not np.isfinite([point.x, point.y]).all():
        return None
    return np.array([point.x, point.y])


def component_sizes(labels):
    """Node count per component label as plain ints; labels need not be dense."""
    values, counts = np.unique(labels, return_counts=True)
    return {int(value): int(count) for value, count in zip(values, counts)}


def _nearest(point, graph):
    indices, distances = kernel.radius_candidates(point, graph)
    candidates = [(float(distance), int(node)) for node, distance in zip(indices, distances)
                  if np.isfinite(distance) and 0 <= distance <= MAX_SNAP_M]
    return min(candidates) if candidates else None


def component_facts(graph, rows, hub_type):
    """Per-hub snap distance and snapped-component size from a validated graph namespace."""
    if hub_type not in HUB_TYPES:
        raise ValueError("Unsupported hub type")
    labels = graph.nh_comp_labels
    if (not isinstance(labels, np.ndarray) or labels.ndim != 1 or not np.issubdtype(labels.dtype, np.integer)):
        raise ValueError("Component labels must be a one-dimensional integer array")
    xy = graph.nh_node_xy
    if np.shape(xy) != (len(labels), 2) or graph.nh_tree.n != len(labels):
        raise ValueError("Component labels, node coordinates and tree are misaligned")
    names = []
    for row in rows:
        name = row.get("name")
        if type(name) is not str or not name.strip():
            raise ValueError("Hub names must be non-blank strings")
        names.append(name)
    sizes = component_sizes(labels)
    facts = []
    for order, (row, name) in enumerate(zip(rows, names)):
        point = hub_point(row)
        snap = _nearest(point, graph) if point is not None else None
        feeder_km = component = None
        if snap is not None:
            distance, node = snap
            feeder_km, component = distance / 1000.0, sizes[int(labels[node])]
        facts.append({"hub_type": hub_type, "hub_name": name, "hub_order": order,
                      "snap_feeder_km": feeder_km, "snap_component_node_count": component})
    return facts


def _is_int(value):
    return type(value) is int


def _is_float(value):
    return type(value) is float and math.isfinite(value)


def _name(value):
    return type(value) is str and bool(value.strip())


def _list_of(value, check):
    return isinstance(value, list) and all(check(v) for v in value)


def _thresholds_ok(entry):
    thresholds = entry["thresholds_hours"]
    return (_list_of(thresholds, _is_float) and bool(thresholds) and all(t > 0 for t in thresholds)
            and all(a < b for a, b in zip(thresholds, thresholds[1:])))


def _shape_issues(entry):
    """Typed structure of one entry; returns issues and whether semantic checks are meaningful."""
    issues = []
    if entry["hub_type"] not in HUB_TYPES:
        issues.append("unsupported hub_type")
    if not _name(entry["hub_name"]):
        issues.append("hub_name must be a non-blank string")
    if not _is_int(entry["hub_order"]) or entry["hub_order"] < 0:
        issues.append("hub_order must be a non-negative integer")
    thresholds_ok = _thresholds_ok(entry)
    if not thresholds_ok:
        issues.append("thresholds_hours must be finite positive strictly increasing floats")
    statuses, counts = entry["statuses"], entry["reachable_node_counts"]
    statuses_ok = _list_of(statuses, lambda s: type(s) is str and s in STATUSES)
    counts_ok = _list_of(counts, lambda c: _is_int(c) and c >= 0)
    if not statuses_ok:
        issues.append("statuses must be a list of supported statuses")
    if not counts_ok:
        issues.append("reachable_node_counts must be non-negative integers")
    aligned = (thresholds_ok and statuses_ok and counts_ok
               and len(entry["thresholds_hours"]) == len(statuses) == len(counts))
    if thresholds_ok and statuses_ok and counts_ok and not aligned:
        issues.append("thresholds, statuses and counts must have the same length")
    feeder, component = entry["snap_feeder_km"], entry["snap_component_node_count"]
    feeder_ok = feeder is None or (_is_float(feeder) and 0 <= feeder <= MAX_SNAP_M / 1000.0)
    component_ok = component is None or (_is_int(component) and component >= 1)
    if not feeder_ok:
        issues.append("snap_feeder_km must be null or a finite float within 0..150")
    if not component_ok:
        issues.append("snap_component_node_count must be null or a positive integer")
    if feeder_ok and component_ok and (feeder is None) != (component is None):
        issues.append("snap_feeder_km and snap_component_node_count must be null together")
    ok_count, saturated, bounded = (entry["ok_threshold_count"], entry["saturated_across_ok_thresholds"],
                                    entry["component_bounded_thresholds"])
    derived_ok = _is_int(ok_count) and ok_count >= 0 and (saturated is None or type(saturated) is bool) \
        and _list_of(bounded, _is_float)
    if not derived_ok:
        issues.append("derived fields must be typed ok_threshold_count, saturation and bounded thresholds")
    usable = (aligned and feeder_ok and component_ok and (feeder is None) == (component is None) and derived_ok)
    return issues, usable


def _semantic_issues(entry):
    issues = []
    thresholds, statuses, counts = entry["thresholds_hours"], entry["statuses"], entry["reachable_node_counts"]
    feeder, component = entry["snap_feeder_km"], entry["snap_component_node_count"]
    if feeder is None:
        if len(set(statuses)) != 1 or statuses[0] not in _UNSNAPPED:
            issues.append("unsnapped hub needs one identical invalid_coordinates or no_highway_within_150km status")
        if any(counts):
            issues.append("unsnapped hub must have zero reachable counts")
        expected = (0, None, [])
    else:
        feeder_hours = feeder / FEEDER_SPEED_KMH
        for threshold, status, count in zip(thresholds, statuses, counts):
            if feeder_hours > threshold:
                if status != "feeder_exceeds_threshold" or count != 0:
                    issues.append("threshold below feeder time must be feeder_exceeds_threshold with zero count")
            elif not 1 <= count <= component:
                issues.append("reachable count must be within 1..component size")
            elif status != ("ok" if count >= 3 else "unsupported_threshold"):
                issues.append("status disagrees with reachable count")
        if any(b < a for a, b in zip(counts, counts[1:])):
            issues.append("reachable counts must be non-decreasing")
        ok_counts = [c for s, c in zip(statuses, counts) if s == "ok"]
        expected = (len(ok_counts), len(set(ok_counts)) == 1 if len(ok_counts) >= 2 else None,
                    [t for t, s, c in zip(thresholds, statuses, counts)
                     if s in ("ok", "unsupported_threshold") and c == component])
    if (entry["ok_threshold_count"] != expected[0] or entry["saturated_across_ok_thresholds"] is not expected[1]
            or entry["component_bounded_thresholds"] != expected[2]):
        issues.append("derived ok count, saturation or bounded thresholds disagree with the entry")
    return issues


def _entry_issues(entry, expected_thresholds):
    if not isinstance(entry, dict) or set(entry) != set(ENTRY_KEYS):
        return ["entry must be an object with exactly the entry keys"]
    issues, usable = _shape_issues(entry)
    if usable:
        issues.extend(_semantic_issues(entry))
    if expected_thresholds is not None:
        wanted = expected_thresholds.get(entry["hub_type"]) if isinstance(entry["hub_type"], str) else None
        actual = entry["thresholds_hours"]
        if (not isinstance(wanted, (list, tuple)) or not isinstance(actual, list) or len(wanted) != len(actual)
                or not all(type(w) is float and type(a) is float and w == a for w, a in zip(wanted, actual))):
            issues.append("thresholds_hours differ from the configured thresholds")
    return issues


def _plain_json(value):
    """True only for exact plain JSON types; iterative, and rejects reference cycles (not shared acyclic containers)."""
    stack, active = [(value, False)], set()
    while stack:
        item, leaving = stack.pop()
        if leaving:
            active.discard(id(item))
            continue
        kind = type(item)
        if item is None or kind is bool or kind is int or kind is str:
            continue
        if kind is float:
            if not math.isfinite(item):
                return False
            continue
        if kind is list:
            children = item
        elif kind is dict:
            if any(type(key) is not str for key in item):
                return False
            children = item.values()
        else:
            return False
        if id(item) in active:
            return False
        active.add(id(item))
        stack.append((item, True))
        stack.extend((child, False) for child in children)
    return True


def _plain_thresholds(value):
    return (type(value) is dict and all(type(key) is str for key in value)
            and all(type(values) is list and all(type(t) is float and math.isfinite(t) for t in values)
                    for values in value.values()))


def coverage_issues(coverage, thresholds_by_type=None):
    """Every invariant violation as a plain message; never raises and never echoes values."""
    try:
        if not _plain_json(coverage):
            return ["isochrone_coverage must contain only plain JSON values"]
        if thresholds_by_type is not None and not _plain_thresholds(thresholds_by_type):
            return ["configured thresholds must be an object of plain float lists"]
        if not isinstance(coverage, dict) or set(coverage) != {"snap_rule", "entries"}:
            return ["isochrone_coverage must be an object with snap_rule and entries"]
        issues = []
        if coverage["snap_rule"] != SNAP_RULE:
            issues.append("snap_rule is not the supported constant")
        entries = coverage["entries"]
        if not isinstance(entries, list):
            return [*issues, "entries must be a list"]
        if thresholds_by_type is not None and not isinstance(thresholds_by_type, dict):
            return [*issues, "configured thresholds must be an object"]
        type_index, next_order = 0, 0
        names_seen = {hub_type: set() for hub_type in HUB_TYPES}
        for position, entry in enumerate(entries):
            issues.extend(f"entries[{position}]: {issue}" for issue in _entry_issues(entry, thresholds_by_type))
            if not isinstance(entry, dict):
                continue
            hub_type, order = entry.get("hub_type"), entry.get("hub_order")
            if hub_type in HUB_TYPES and _name(entry.get("hub_name")):
                key = entry["hub_name"].strip().casefold()
                if key in names_seen[hub_type]:
                    issues.append(f"entries[{position}]: hub_name duplicates an earlier hub of the same hub_type")
                names_seen[hub_type].add(key)
            if hub_type not in HUB_TYPES or not _is_int(order):
                continue
            index = HUB_TYPES.index(hub_type)
            if index < type_index:
                issues.append(f"entries[{position}]: entries must list major_port hubs before mmlp hubs")
                continue
            if index > type_index:
                type_index, next_order = index, 0
            if order != next_order:
                issues.append(f"entries[{position}]: hub_order must run 0..n-1 without gaps, duplicates or swaps")
            next_order = order + 1
        return issues
    except Exception:  # Defensive: malformed input is reported, never raised.
        return ["isochrone_coverage has an unusable structure"]


def _runs(document, hub_type):
    features = document.get("features") if isinstance(document, dict) else None
    if not isinstance(features, list):
        raise ValueError("Isochrone artifact must be a FeatureCollection")
    runs = []
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            raise ValueError("Isochrone feature properties are unavailable")
        if properties.get("hub_type") != hub_type:
            raise ValueError("Isochrone feature hub_type does not match its artifact")
        name = properties.get("hub_name")
        if not runs or runs[-1][0] != name:
            runs.append((name, []))
        runs[-1][1].append(properties)
    return runs


def _bind(hub_type, document, facts):
    runs = _runs(document, hub_type)
    if len(runs) != len(facts):
        raise ValueError("Isochrone hubs and component facts differ in count")
    entries = []
    for order, ((name, properties), fact) in enumerate(zip(runs, facts)):
        if not isinstance(fact, dict) or set(fact) != set(_FACT_KEYS):
            raise ValueError("Component fact has unsupported keys")
        if (fact["hub_type"] != hub_type or type(fact["hub_order"]) is not int or fact["hub_order"] != order
                or fact["hub_name"] != name):
            raise ValueError("Component fact does not bind to the artifact hub by type, order and name")
        feeder = fact["snap_feeder_km"]
        for item in properties:
            value = item.get("snap_feeder_km")
            if feeder is None or value is None:
                if value is not feeder:
                    raise ValueError("Artifact snap distance disagrees with the component fact")
            elif (isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value)
                  or abs(value - feeder) > _FEEDER_TOLERANCE_KM):
                raise ValueError("Artifact snap distance disagrees with the component fact")
        thresholds = []
        for item in properties:
            value = item.get("time_threshold_hours")
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError("Artifact threshold is not a number")
            thresholds.append(float(value))
        statuses = [item.get("status") for item in properties]
        counts = [item.get("reachable_node_count") for item in properties]
        component = fact["snap_component_node_count"]
        snapped = feeder is not None and component is not None
        ok_counts = [c for s, c in zip(statuses, counts) if s == "ok"]
        try:
            bounded = ([t for t, s, c in zip(thresholds, statuses, counts)
                        if s in ("ok", "unsupported_threshold") and c == component] if snapped else [])
        except TypeError:
            bounded = []
        entries.append({"hub_type": hub_type, "hub_name": name, "hub_order": order, "thresholds_hours": thresholds,
                        "statuses": statuses, "reachable_node_counts": counts,
                        "snap_feeder_km": None if feeder is None else properties[0].get("snap_feeder_km"),
                        "snap_component_node_count": component, "ok_threshold_count": len(ok_counts),
                        "saturated_across_ok_thresholds": len(set(ok_counts)) == 1 if len(ok_counts) >= 2 else None,
                        "component_bounded_thresholds": bounded})
    return entries


def coverage_entries(ports_document, mmlps_document, port_facts, mmlp_facts):
    """Schema-2 coverage from the two isochrone artifacts and their component facts; ValueError on any mismatch."""
    try:
        entries = _bind("major_port", ports_document, port_facts) + _bind("mmlp", mmlps_document, mmlp_facts)
    except (TypeError, KeyError, AttributeError, OverflowError) as error:
        raise ValueError("Isochrone artifact or component facts have an unusable shape") from error
    result = {"snap_rule": SNAP_RULE, "entries": entries}
    issues = coverage_issues(result, None)
    if issues:
        raise ValueError("Invalid isochrone coverage: " + "; ".join(issues[:5]))
    return result
