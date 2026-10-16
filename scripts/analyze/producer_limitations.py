"""Source-condition and limitation sidecar (schema 1) bound to a producer manifest.

The builder reads the actual artifact headers/hashes and the declared district
GeoJSON and population CSV under a source root. It has no writer: callers persist
the returned plain-JSON dict with ``producer_manifest.atomic_write_manifest``.
The validator reads only the sidecar (strict JSON) plus, optionally, the source
root; artifact facts come from an already validated manifest.

Disclosures describe retained upstream rows. They do not repair, reassign or
deduplicate any identifier, assert a cause, or claim population accuracy.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
import pandas as pd

from scripts.analyze.isochrone_coverage import coverage_issues
from scripts.analyze.producer_io import sha256_file
from scripts.analyze.producer_manifest import (
    RUN_ID_PATTERN, SHA, VINTAGE_STATEMENT, REQUIRED_ARTIFACTS, _metadata, private_path_issues, read_manifest)
from scripts.audit.identity_contract import (
    IdentityError, cross_state_codes, label_key, same_state_label_codes, strict_code, validate_identities)

SCHEMA_VERSION = 1
SIDECAR_FILENAME = "producer_limitations.json"
CLASSES = ("identity", "population", "flag", "population_independent")
IDENTITY_COLUMNS = ("state", "district", "district_code")
DISTRICT_SOURCE = "administrative/india_districts_lgd.geojson"
POPULATION_SOURCE = "demographic/district_population_estimates.csv"
OVERLAP_TOLERANCE = 1e-9
TOP_LEVEL_KEYS = {"schema_version", "run_id", "artifact_sha256", "column_classification",
                  "source_identity_disclosures", "oracle_independence", "resource_measurement_method", "limitations"}

LIMITATIONS = [
    "No navigation or door-to-door claim.",
    "No official tariff.",
    "No rail routing.",
    "No claim of national or district population accuracy; conservation of totals is not accuracy.",
    "No cause is asserted for source conditions and no official identifier is corrected.",
    VINTAGE_STATEMENT,
    "Isochrones are approximate coverage.",
    "The graph oracle is a bounded sample.",
    "Live default gravity parameters 0.85/1.65 differ from batch parameters 1.2/1.5.",
]
# Schema 2 adds the isochrone_coverage section; schema 1 sidecars stay valid and unchanged.
SCHEMA_VERSION_V2 = 2
TOP_LEVEL_KEYS_V2 = TOP_LEVEL_KEYS | {"isochrone_coverage"}
COVERAGE_LIMITATION = ("Isochrone catchments are limited to the road-graph component of the snapped vertex; "
                       "component-bounded hubs are listed in isochrone_coverage.")
LIMITATIONS_V2 = [*LIMITATIONS, COVERAGE_LIMITATION]
PORTS_SOURCE = "logistics_hubs/ports.csv"
MMLPS_SOURCE = "logistics_hubs/mmlps.csv"
ORACLE_INDEPENDENCE = {
    "producer_independent": True,
    "shared_components": [
        "highway_pair_kernel",
        "graph_builder",
        "freight_cost_core",
        "producer_io.district_population (W0 policy)",
        "port_hinterland_model.build_port_hinterland (stored-output consistency, not independent model validation)",
    ],
    "sample_rows_max": 12,
    "iso_valid_hubs_per_type": 2,
}
RESOURCE_METHODS = {
    "windows_peak_working_set": {"elapsed": "perf_counter_seconds_generate_stage4",
                                 "peak_memory": "GetProcessMemoryInfo.PeakWorkingSetSize",
                                 "unit": "bytes", "scope": "whole_process_high_water"},
    "linux_ru_maxrss": "resource.ru_maxrss_kib_times_1024",
}
NOTES = {
    "cross_state_shared_code": "Upstream source condition: one district code under more than one state; rows and codes are preserved literally; cause not determined.",
    "same_state_label_multiple_codes": "Retained rows; overlapping geometries can make row-count or population-weighted aggregation double-represent the area; Kerala/Alappuzha/598 is a separate cross-state code condition; cause not determined.",
}
CROSS, SAME = "cross_state_shared_code", "same_state_label_multiple_codes"
_ROW_KEYS = {"source_row", "state", "district", "district_code", "pop_2011", "population_method"}
_FRACTIONS = ("fraction_of_first", "fraction_of_second")


def classify_column(column):
    if column in IDENTITY_COLUMNS:
        return "identity"
    return {"pop_2011": "population", "is_island": "flag"}.get(column, "population_independent")


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _fraction(value):
    """Overlap fractions are JSON floats (0.0 and 1.0 included), never integers or bools."""
    return type(value) is float and math.isfinite(value)


def _exact(actual, expected):
    """Structural equality with exact types (no 1 == True or 12 == 12.0 confusion)."""
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_exact(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(_exact(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _population_index(frame):
    if not {"state", "district", "district_code", "pop_2011"} <= set(frame):
        raise ValueError("Missing population identity columns")
    index = {}
    for record in frame.to_dict("records"):
        if type(record["state"]) is not str or type(record["district"]) is not str:
            continue
        try:
            code = strict_code(record["district_code"])
        except IdentityError:
            continue
        if code is not None:
            index.setdefault((record["state"], record["district"], code), []).append(record)
    return index


def _row(source_row, record, population):
    code = strict_code(record["district_code"])
    matches = population.get((record["state"], record["district"], code), [])
    pop = method = None
    if len(matches) == 1 and _finite(matches[0]["pop_2011"]):
        pop = matches[0]["pop_2011"]
        method = matches[0].get("method")
        method = method if type(method) is str else None
    return {"source_row": source_row, "state": record["state"], "district": record["district"],
            "district_code": code, "pop_2011": pop, "population_method": method}


def _overlaps(geometries, rows):
    result = []
    for position, first in enumerate(rows):
        for second in rows[position + 1:]:
            a, b = geometries[first], geometries[second]
            if any(g is None or g.is_empty or not g.is_valid or not g.area > 0 for g in (a, b)):
                raise ValueError("Unable to measure geometry overlap")
            intersection = a.intersection(b).area
            fractions = [min(1.0, round(intersection / g.area, 10)) for g in (a, b)]
            result.append({"source_rows": [first, second], "fraction_of_first": fractions[0],
                           "fraction_of_second": fractions[1]})
    return result


def _source_disclosures(source_root):
    """Generic disclosures from the declared sources; identities are validated first."""
    root = Path(source_root)
    districts = gpd.read_file(root / DISTRICT_SOURCE)
    if not set(IDENTITY_COLUMNS) <= set(districts):
        raise ValueError("Missing primary district identity")
    records = districts[list(IDENTITY_COLUMNS)].to_dict("records")
    validate_identities(records)
    cross, same = cross_state_codes(records), same_state_label_codes(records)
    population = _population_index(pd.read_csv(root / POPULATION_SOURCE))
    entries = [{"kind": CROSS, "code": code, "rows": [_row(i, records[i], population) for i in cross[code]],
                "note": NOTES[CROSS]} for code in sorted(cross)]
    if same:
        geometries = list(districts.to_crs(7755).geometry)
    for key in sorted(same):
        rows = same[key]
        entries.append({"kind": SAME, "state": records[rows[0]]["state"], "district": records[rows[0]]["district"],
                        "rows": [_row(i, records[i], population) for i in rows],
                        "overlaps": _overlaps(geometries, rows), "note": NOTES[SAME]})
    return entries


def build_limitations(source_root, artifact_paths, run_id, resource_method, *, isochrone_coverage=None) -> dict:
    """Plain-JSON sidecar document from actual files; raises on any unusable input.

    Without ``isochrone_coverage`` the document is exactly schema 1. A coverage
    section (checked by ``coverage_issues``) produces schema 2.
    """
    coverage = None
    if isochrone_coverage is not None:
        issues = coverage_issues(isochrone_coverage, None)
        if issues:
            raise ValueError("Invalid isochrone_coverage: " + "; ".join(issues[:5]))
        coverage = json.loads(json.dumps(isochrone_coverage, allow_nan=False))
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Invalid run_id")
    if resource_method not in RESOURCE_METHODS:
        raise ValueError("Unsupported resource measurement method")
    if set(artifact_paths) != set(REQUIRED_ARTIFACTS):
        raise ValueError("Exactly seven required artifacts must be supplied")
    hashes, classification = {}, {}
    for name in REQUIRED_ARTIFACTS:
        hashes[name] = sha256_file(artifact_paths[name])
        facts = _metadata(artifact_paths[name])
        classification[name] = ("population_independent" if facts["type"] == "geojson"
                                else [[column, classify_column(column)] for column in facts["columns"]])
    method = RESOURCE_METHODS[resource_method]
    document = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "artifact_sha256": hashes,
                "column_classification": classification,
                "source_identity_disclosures": _source_disclosures(source_root),
                "oracle_independence": {**ORACLE_INDEPENDENCE, "shared_components": list(ORACLE_INDEPENDENCE["shared_components"])},
                "resource_measurement_method": dict(method) if isinstance(method, dict) else method,
                "limitations": list(LIMITATIONS)}
    if coverage is None:
        return document
    ordered = {}
    for key, value in document.items():
        ordered[key] = value
        if key == "source_identity_disclosures":
            ordered["isochrone_coverage"] = coverage
    ordered["schema_version"], ordered["limitations"] = SCHEMA_VERSION_V2, list(LIMITATIONS_V2)
    return ordered


def _valid_row(row):
    return (isinstance(row, dict) and set(row) == _ROW_KEYS and type(row["source_row"]) is int and row["source_row"] >= 0
            and type(row["state"]) is str and type(row["district"]) is str
            and type(row["district_code"]) is int and row["district_code"] > 0
            and (row["pop_2011"] is None or _finite(row["pop_2011"]))
            and (row["population_method"] is None or type(row["population_method"]) is str))


def _valid_rows(rows):
    return (isinstance(rows, list) and len(rows) >= 2 and all(_valid_row(r) for r in rows)
            and all(a["source_row"] < b["source_row"] for a, b in zip(rows, rows[1:])))


def _row_keys(rows):
    """Normalized (state, district) per row via the frozen S1 label_key, or None if any label is unusable."""
    try:
        return [(label_key(r["state"]), label_key(r["district"])) for r in rows]
    except IdentityError:
        return None


def _valid_overlaps(overlaps, rows):
    wanted = [[a["source_row"], b["source_row"]] for i, a in enumerate(rows) for b in rows[i + 1:]]
    return (isinstance(overlaps, list) and len(overlaps) == len(wanted)
            and all(isinstance(o, dict) and set(o) == {"source_rows", *_FRACTIONS} and _exact(o["source_rows"], pair)
                    and all(_fraction(o[f]) and 0 <= o[f] <= 1 for f in _FRACTIONS)
                    for o, pair in zip(overlaps, wanted)))


def _valid_disclosures(entries):
    """Entry shape and order: cross-state by code, then same-state by (state, label) key."""
    if not isinstance(entries, list):
        return False
    last_code, last_key, seen_same = 0, None, False
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        if entry.get("kind") == CROSS:
            if (seen_same or set(entry) != {"kind", "code", "rows", "note"} or type(entry["code"]) is not int
                    or entry["code"] <= last_code or entry["note"] != NOTES[CROSS] or not _valid_rows(entry["rows"])
                    or any(r["district_code"] != entry["code"] for r in entry["rows"])):
                return False
            keys = _row_keys(entry["rows"])
            # A cross-state condition needs more than one distinct normalized state.
            if keys is None or len({state for state, _ in keys}) < 2:
                return False
            last_code = entry["code"]
        elif entry.get("kind") == SAME:
            if (set(entry) != {"kind", "state", "district", "rows", "overlaps", "note"} or entry["note"] != NOTES[SAME]
                    or not _valid_rows(entry["rows"]) or not _valid_overlaps(entry["overlaps"], entry["rows"])
                    or type(entry["state"]) is not str or type(entry["district"]) is not str
                    or (entry["state"], entry["district"]) != (entry["rows"][0]["state"], entry["rows"][0]["district"])):
                return False
            try:
                key = (label_key(entry["state"]), label_key(entry["district"]))
            except IdentityError:
                return False
            keys = _row_keys(entry["rows"])
            # One normalized state/district key shared by every row, carrying several distinct codes.
            if keys is None or set(keys) != {key} or len({r["district_code"] for r in entry["rows"]}) < 2:
                return False
            if last_key is not None and key <= last_key:
                return False
            last_key, seen_same = key, True
        else:
            return False
    return True


def _same_disclosures(actual, expected):
    """Exact equality except overlap fractions, compared within OVERLAP_TOLERANCE."""
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            (_fraction(actual[k]) and _fraction(expected[k]) and abs(actual[k] - expected[k]) <= OVERLAP_TOLERANCE)
            if k in _FRACTIONS else _same_disclosures(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(_same_disclosures(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _manifest_usable(manifest):
    if not isinstance(manifest, dict) or not isinstance(manifest.get("run_id"), str):
        return False
    entries = manifest.get("artifacts")
    return (isinstance(entries, dict) and set(entries) == set(REQUIRED_ARTIFACTS)
            and all(isinstance(e, dict) and isinstance(e.get("sha256"), str) for e in entries.values()))


def _manifest_thresholds(manifest):
    """Configured thresholds per hub type as canonical floats, or None when unusable."""
    try:
        configured = manifest["config"]["thresholds_hours"]
        result = {}
        for hub_type in ("major_port", "mmlp"):
            values = configured[hub_type]
            if (not isinstance(values, list) or not values
                    or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values)):
                return None
            result[hub_type] = [float(v) for v in values]
            if any(a >= b for a, b in zip(result[hub_type], result[hub_type][1:])):
                return None
        return result
    except (KeyError, TypeError, OverflowError, ValueError):
        return None


def _hub_names(source_root):
    """Literal major-port and MMLP names in source order."""
    root = Path(source_root)
    ports = pd.read_csv(root / PORTS_SOURCE, dtype=str, keep_default_na=False)
    mmlps = pd.read_csv(root / MMLPS_SOURCE, dtype=str, keep_default_na=False)
    if not {"name", "hub_type"} <= set(ports) or "name" not in mmlps:
        raise ValueError("Missing hub name columns")
    return {"major_port": ports.loc[ports["hub_type"] == "major_port", "name"].tolist(),
            "mmlp": mmlps["name"].tolist()}


def _coverage_names(coverage):
    return {hub_type: [e["hub_name"] for e in coverage["entries"] if e["hub_type"] == hub_type]
            for hub_type in ("major_port", "mmlp")}


def validate_limitations(path, manifest, *, source_root=None, require_coverage=False) -> list[str]:
    """Strict sidecar validation against exact constants and the manifest's artifact facts.

    Schema 1 (eight keys) and schema 2 (nine keys, with isochrone_coverage) are both
    recognised by their exact key sets. ``require_coverage`` rejects a schema 1 sidecar.
    """
    if not _manifest_usable(manifest):
        return ["limitations: manifest unavailable"]
    try:
        document = read_manifest(path)
    except (OSError, ValueError, TypeError):
        return ["limitations: unreadable or invalid strict JSON"]
    if not isinstance(document, dict) or set(document) not in (TOP_LEVEL_KEYS, TOP_LEVEL_KEYS_V2):
        return ["limitations: missing or unknown top-level keys"]
    version2 = set(document) == TOP_LEVEL_KEYS_V2
    expected_version, expected_limitations = ((SCHEMA_VERSION_V2, LIMITATIONS_V2) if version2
                                              else (SCHEMA_VERSION, LIMITATIONS))
    issues = []
    if type(document["schema_version"]) is not int or document["schema_version"] != expected_version:
        issues.append("limitations: unsupported schema")
    run_id = document["run_id"]
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id) or run_id != manifest["run_id"]:
        issues.append("limitations: run_id mismatch")
    entries = manifest["artifacts"]
    hashes = document["artifact_sha256"]
    if (not isinstance(hashes, dict) or set(hashes) != set(REQUIRED_ARTIFACTS)
            or any(not isinstance(v, str) or not SHA.fullmatch(v) or v != entries[k]["sha256"] for k, v in hashes.items())):
        issues.append("limitations: artifact hashes do not match the manifest")
    classes = document["column_classification"]
    expected = {}
    for name, entry in entries.items():
        columns = entry.get("columns")
        if entry.get("type") == "geojson":
            expected[name] = "population_independent"
        elif isinstance(columns, list) and all(isinstance(c, str) for c in columns):
            expected[name] = [[c, classify_column(c)] for c in columns]
    if len(expected) != len(entries) or not _exact(classes, expected):
        issues.append("limitations: column_classification must cover the manifest columns exactly")
    if not _exact(document["oracle_independence"], ORACLE_INDEPENDENCE):
        issues.append("limitations: oracle_independence is not the supported constant")
    if not any(_exact(document["resource_measurement_method"], m) for m in RESOURCE_METHODS.values()):
        issues.append("limitations: unsupported resource_measurement_method")
    if not _exact(document["limitations"], expected_limitations):
        issues.append("limitations: limitations list is not the supported constant")
    disclosures = document["source_identity_disclosures"]
    if not _valid_disclosures(disclosures):
        issues.append("limitations: invalid source_identity_disclosures")
    elif source_root is not None:
        try:
            recomputed = _source_disclosures(source_root)
        except Exception:  # Missing/malformed sources or unmeasurable geometry: never invent values.
            issues.append("limitations: source disclosures unavailable")
        else:
            if not _same_disclosures(disclosures, recomputed):
                issues.append("limitations: source_identity_disclosures differ from the declared sources")
    if version2:
        coverage = document["isochrone_coverage"]
        thresholds = _manifest_thresholds(manifest)
        if thresholds is None:
            issues.append("limitations: manifest thresholds unavailable")
        found = coverage_issues(coverage, thresholds)
        issues.extend(f"limitations: isochrone_coverage: {issue}" for issue in found)
        if source_root is not None and not coverage_issues(coverage, None):
            try:
                names = _hub_names(source_root)
            except Exception:  # Missing/malformed hub sources: never invent names.
                issues.append("limitations: hub sources unavailable")
            else:
                if names != _coverage_names(coverage):
                    issues.append("limitations: isochrone_coverage hubs differ from the declared hub sources")
    elif require_coverage:
        issues.append("limitations: isochrone_coverage required (schema 2)")
    issues.extend(private_path_issues(document, "limitations"))
    return issues
