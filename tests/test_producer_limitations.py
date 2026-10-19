"""Synthetic sidecar (schema 1) builder/validator regressions: tmp_path sources only."""
import ast
import copy
from dataclasses import asdict
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from scripts.analyze import producer_limitations as lim
from scripts.analyze import producer_manifest as pm
from scripts.analyze.freight_cost_core import FreightCostParameters
from scripts.analyze.producer_io import sha256_file
from scripts.audit import identity_contract

TOP_ORDER = ["schema_version", "run_id", "artifact_sha256", "column_classification", "source_identity_disclosures",
             "oracle_independence", "resource_measurement_method", "limitations"]


def configuration():
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA
    return {"units": {"distance": "km", "time": "hours", "cost": "INR/tonne"},
            "objectives": {"hub_selection": "minimum_lmv_time", "snap": "closest_compatible", "distance": "time_predecessor_path"},
            "speeds": {"motorway": 90., "trunk": 70., "primary": 55., "default": 50.},
            "feeder_speed_kmh": 35., "snap_limit_km": 150., "lmv_factor": 1.,
            "chunk_size": 32, "thresholds_hours": {"major_port": [4., 8., 12., 24.], "mmlp": [4., 8., 12.]}, "buffer_m": 8000., "simplify_m": 500.,
            "alpha": .85, "beta": 1.65, "capacities": {name: cap for name, cap in PORT_MATRIX_DATA.values()},
            "freight_parameters": asdict(FreightCostParameters())}


def write_sources(source, rows, geometries, population):
    (source / "administrative").mkdir(parents=True, exist_ok=True)
    (source / "demographic").mkdir(parents=True, exist_ok=True)
    frame = gpd.GeoDataFrame(pd.DataFrame(rows, columns=["state", "district", "district_code"]), geometry=geometries, crs=4326)
    frame.to_file(source / lim.DISTRICT_SOURCE, driver="GeoJSON")
    pd.DataFrame(population, columns=["state", "district", "district_code", "pop_2011", "method"]).to_csv(source / lim.POPULATION_SOURCE, index=False)


ROWS = [("Kerala", "Alappuzha", 598), ("Maharashtra", "DHULE", 598), ("Maharashtra", "DHULE", 498), ("Assam", "Other", 7)]
SQUARES = [box(76., 9., 76.1, 9.1), box(74., 21., 74.1, 21.1), box(74.01, 21., 74.11, 21.1), box(92., 26., 92.1, 26.1)]
POPULATION = [("Kerala", "Alappuzha", 598, 1000., "census"), ("Maharashtra", "DHULE", 598, 2000., "estimate"),
              ("Maharashtra", "DHULE", 498, 3000., None), ("Assam", "Other", 7, 4000., "census")]


def write_artifacts(root):
    root.mkdir(parents=True, exist_ok=True)
    identity = {"state": ["A", "B"], "district": ["x", "y"], "district_code": [1., 2.]}
    tables = {
        "nh_district_travel_time_summary.csv": {**identity, "pop_2011": [1., 2.], "is_island": [False, True], "nearest_port_name": ["P", "Q"], "port_drive_time_hours": [1., None]},
        "nh_district_port_matrix.csv": {**identity, "drive_hours_to_mumbai": [1., 2.], "road_km_to_mumbai": [3., 4.]},
        "district_freight_modal_split.csv": {**identity, "pop_2011": [1., 2.], "is_island": [False, True], "target_port": ["P", "Q"], "optimal_mode": ["Road", "Rail"]},
        "district_port_hinterland_catchment.csv": {**identity, "pop_2011": [1., 2.], "is_island": [False, True], "primary_port": ["P", "Q"], "mumbai_share": [.5, .5]},
    }
    for name, columns in tables.items():
        pd.DataFrame(columns).to_csv(root / name, index=False)
    pd.DataFrame(tables["nh_district_travel_time_summary.csv"]).to_parquet(root / "nh_district_travel_time_summary.parquet", index=False)
    for name in ("major_ports_isochrones.geojson", "mmlp_isochrones.geojson"):
        (root / name).write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    return {name: root / name for name in pm.REQUIRED_ARTIFACTS}


class World:
    def __init__(self, tmp_path, rows=ROWS, geometries=SQUARES, population=POPULATION, run_id="synthetic-run-1"):
        self.source, self.analysis = tmp_path / "source", tmp_path / "analysis"
        write_sources(self.source, rows, geometries, population)
        self.artifacts = write_artifacts(self.analysis)
        self.run_id = run_id
        self.manifest = pm.build_manifest(run_id, self.artifacts, {},
            {"fingerprint": "synthetic", "version": 1, "node_count": 2, "edge_count": 2, "selected_source": "roads/test.parquet"},
            configuration(), {}, {"elapsed_seconds": None, "peak_memory_bytes": None})
        self.document = lim.build_limitations(self.source, self.artifacts, run_id, "windows_peak_working_set")
        self.path = tmp_path / "producer_limitations.json"

    def write(self, document=None):
        self.path.write_text(json.dumps(self.document if document is None else document, indent=1), encoding="utf-8")
        return self.path

    def issues(self, document=None, source=False):
        self.write(document)
        return lim.validate_limitations(self.path, self.manifest, source_root=self.source if source else None)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_constants_exported_and_obsolete_sections_removed(world):
    for name in ("LIMITATIONS", "ORACLE_INDEPENDENCE", "RESOURCE_METHODS", "NOTES", "CLASSES"):
        assert hasattr(lim, name)
    assert lim.CLASSES == ("identity", "population", "flag", "population_independent")
    assert set(lim.RESOURCE_METHODS) == {"windows_peak_working_set", "linux_ru_maxrss"}
    assert list(world.document) == TOP_ORDER
    assert "population_sensitive_outputs" not in world.document and "route_only_outputs" not in world.document
    assert lim.LIMITATIONS[4].startswith("No cause is asserted") and any("0.85/1.65" in s and "1.2/1.5" in s for s in lim.LIMITATIONS)


def test_document_is_plain_json_without_local_paths(world, tmp_path):
    text = json.dumps(world.document, allow_nan=False)
    assert json.loads(text) == world.document
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "/") not in text
    assert world.document["run_id"] == "synthetic-run-1" and world.document["schema_version"] == 1


def test_hashes_and_ordered_column_classification_from_actual_headers(world):
    for name, path in world.artifacts.items():
        assert world.document["artifact_sha256"][name] == sha256_file(path) == world.manifest["artifacts"][name]["sha256"]
    classes = world.document["column_classification"]
    assert classes["major_ports_isochrones.geojson"] == classes["mmlp_isochrones.geojson"] == "population_independent"
    summary = classes["nh_district_travel_time_summary.csv"]
    assert summary == [["state", "identity"], ["district", "identity"], ["district_code", "identity"], ["pop_2011", "population"],
                       ["is_island", "flag"], ["nearest_port_name", "population_independent"], ["port_drive_time_hours", "population_independent"]]
    assert classes["nh_district_travel_time_summary.parquet"] == summary
    assert [c for _, c in classes["nh_district_port_matrix.csv"]] == ["identity"] * 3 + ["population_independent"] * 2


def test_generic_disclosures_literal_ordered_and_population_copied_verbatim(world):
    cross, same = world.document["source_identity_disclosures"]
    assert cross["kind"] == "cross_state_shared_code" and cross["code"] == 598 and cross["note"] == lim.NOTES["cross_state_shared_code"]
    assert [(r["source_row"], r["state"], r["district"], r["district_code"], r["pop_2011"], r["population_method"]) for r in cross["rows"]] == [
        (0, "Kerala", "Alappuzha", 598, 1000., "census"), (1, "Maharashtra", "DHULE", 598, 2000., "estimate")]
    assert same["kind"] == "same_state_label_multiple_codes" and (same["state"], same["district"]) == ("Maharashtra", "DHULE")
    assert [(r["source_row"], r["district_code"], r["pop_2011"], r["population_method"]) for r in same["rows"]] == [(1, 598, 2000., "estimate"), (2, 498, 3000., None)]
    projected = gpd.GeoDataFrame(geometry=SQUARES, crs=4326).to_crs(7755).geometry
    inter = projected[1].intersection(projected[2]).area
    assert same["overlaps"] == [{"source_rows": [1, 2], "fraction_of_first": round(inter / projected[1].area, 10),
                                 "fraction_of_second": round(inter / projected[2].area, 10)}]
    assert 0.85 < same["overlaps"][0]["fraction_of_first"] < 0.95
    assert same["note"] == lim.NOTES["same_state_label_multiple_codes"]


def test_disclosures_are_generic_not_hardcoded_to_a_code(tmp_path):
    rows = [("Goa", "A", 77), ("Sikkim", "B", 77), ("Sikkim", "C", 12), ("Goa", "D", 5), ("Goa", "D", 6), ("Goa", "E", None)]
    geoms = [box(70 + i, 10, 70.1 + i, 10.1) for i in range(6)]
    geoms[3], geoms[4] = box(80, 10, 80.1, 10.1), box(80.05, 10, 80.15, 10.1)
    population = [("Goa", "A", 77, 10., None), ("Sikkim", "B", 77, 20., None)]
    entries = World(tmp_path, rows, geoms, population).document["source_identity_disclosures"]
    assert [(e["kind"], e.get("code")) for e in entries] == [("cross_state_shared_code", 77), ("same_state_label_multiple_codes", None)]
    assert [r["source_row"] for r in entries[0]["rows"]] == [0, 1] and (entries[1]["state"], entries[1]["district"]) == ("Goa", "D")
    assert [r["pop_2011"] for r in entries[1]["rows"]] == [None, None]  # Unmatched populations are null, not invented.


def test_no_condition_gives_empty_disclosures(tmp_path):
    entries = World(tmp_path, ROWS[:1] + ROWS[3:], [SQUARES[0], SQUARES[3]], POPULATION[:1] + POPULATION[3:]).document["source_identity_disclosures"]
    assert entries == []


@pytest.mark.parametrize("population,expected", [
    ([("Maharashtra", "DHULE", 498, 3000., "a"), ("Maharashtra", "DHULE", 498, 3001., "b")], (None, None)),  # Not unique.
    ([("maharashtra", "DHULE", 498, 3000., "a")], (None, None)),  # Labels are literal, not normalized.
    ([("Maharashtra", "DHULE", 498, float("nan"), "a")], (None, None)),
    ([("Maharashtra", "DHULE", 498, float("inf"), "a")], (None, None)),
    ([("Maharashtra", "DHULE", 498, 3000., "a")], (3000., "a")),
])
def test_population_requires_unique_exact_literal_tuple(tmp_path, population, expected):
    entries = World(tmp_path, population=population).document["source_identity_disclosures"]
    row = next(r for e in entries if e["kind"] == "same_state_label_multiple_codes" for r in e["rows"] if r["district_code"] == 498)
    assert (row["pop_2011"], row["population_method"]) == expected


def test_valid_sidecar_validates_with_and_without_source_root(world):
    assert world.issues() == [] and world.issues(source=True) == []


def test_unusable_manifest_and_unreadable_file(world, tmp_path):
    world.write()
    assert lim.validate_limitations(world.path, None) == ["limitations: manifest unavailable"]
    assert lim.validate_limitations(world.path, {"run_id": "x", "artifacts": {}}) == ["limitations: manifest unavailable"]
    assert lim.validate_limitations(tmp_path / "missing.json", world.manifest) == ["limitations: unreadable or invalid strict JSON"]
    for text in ('{"schema_version":1,"schema_version":1}', '{"x":NaN}', '{"x":Infinity}', "[]", "not json"):
        world.path.write_text(text)
        assert lim.validate_limitations(world.path, world.manifest) in (["limitations: unreadable or invalid strict JSON"], ["limitations: missing or unknown top-level keys"])
        assert lim.validate_limitations(world.path, world.manifest)


def mutate(path_keys, value):
    def apply(doc):
        target = doc
        for key in path_keys[:-1]:
            target = target[key]
        target[path_keys[-1]] = value
    return apply


SUMMARY = "nh_district_travel_time_summary.csv"


@pytest.mark.parametrize("name,apply", [
    ("schema_two", mutate(["schema_version"], 2)),
    ("schema_bool", mutate(["schema_version"], True)),
    ("schema_str", mutate(["schema_version"], "1")),
    ("run_id_other", mutate(["run_id"], "other-run")),
    ("run_id_invalid", mutate(["run_id"], "bad run")),
    ("hash_flipped", mutate(["artifact_sha256", SUMMARY], "0" * 64)),
    ("hash_nonhex", mutate(["artifact_sha256", SUMMARY], "Z" * 64)),
    ("hash_type", mutate(["artifact_sha256", SUMMARY], 5)),
    ("hash_missing", lambda d: d["artifact_sha256"].pop(SUMMARY)),
    ("hash_extra", mutate(["artifact_sha256", "other.csv"], "0" * 64)),
    ("unknown_key", mutate(["population_sensitive_outputs"], ["pop_2011"])),
    ("route_only_key", mutate(["route_only_outputs"], [])),
    ("missing_key", lambda d: d.pop("limitations")),
    ("limitations_changed", lambda d: d["limitations"].__setitem__(0, "Navigation is supported.")),
    ("limitations_reordered", lambda d: d["limitations"].reverse()),
    ("limitations_extra", lambda d: d["limitations"].append("extra")),
    ("limitations_dropped", lambda d: d["limitations"].pop()),
    ("limitations_type", mutate(["limitations"], "text")),
    ("limitations_element_type", lambda d: d["limitations"].__setitem__(1, 7)),
    ("oracle_flag", mutate(["oracle_independence", "producer_independent"], False)),
    ("oracle_rows", mutate(["oracle_independence", "sample_rows_max"], 13)),
    ("oracle_float", mutate(["oracle_independence", "sample_rows_max"], 12.0)),
    ("oracle_bool", mutate(["oracle_independence", "iso_valid_hubs_per_type"], True)),
    ("oracle_components_dropped", lambda d: d["oracle_independence"]["shared_components"].pop()),
    ("oracle_components_reordered", lambda d: d["oracle_independence"]["shared_components"].reverse()),
    ("oracle_extra_key", mutate(["oracle_independence", "extra"], 1)),
    ("resource_none", mutate(["resource_measurement_method"], None)),
    ("resource_text", mutate(["resource_measurement_method"], "perf_counter")),
    ("resource_dict_changed", mutate(["resource_measurement_method", "unit"], "kib")),
    ("resource_dict_extra", mutate(["resource_measurement_method", "extra"], 1)),
    ("resource_dict_missing", lambda d: d["resource_measurement_method"].pop("scope")),
    ("class_pop_independent", lambda d: d["column_classification"][SUMMARY].__setitem__(3, ["pop_2011", "population_independent"])),
    ("class_identity_misclassified", lambda d: d["column_classification"][SUMMARY].__setitem__(0, ["state", "population"])),
    ("class_flag_misclassified", lambda d: d["column_classification"][SUMMARY].__setitem__(4, ["is_island", "population_independent"])),
    ("class_unknown_class", lambda d: d["column_classification"][SUMMARY].__setitem__(5, ["nearest_port_name", "trusted"])),
    ("class_missing_column", lambda d: d["column_classification"][SUMMARY].pop()),
    ("class_extra_column", lambda d: d["column_classification"][SUMMARY].append(["extra", "population_independent"])),
    ("class_reordered", lambda d: d["column_classification"][SUMMARY].reverse()),
    ("class_triple", lambda d: d["column_classification"][SUMMARY].__setitem__(0, ["state", "identity", "x"])),
    ("class_dict_instead_of_list", mutate(["column_classification", SUMMARY], {"state": "identity"})),
    ("class_geojson_as_list", mutate(["column_classification", "mmlp_isochrones.geojson"], [["x", "identity"]])),
    ("class_geojson_wrong", mutate(["column_classification", "mmlp_isochrones.geojson"], "population")),
    ("class_artifact_missing", lambda d: d["column_classification"].pop("mmlp_isochrones.geojson")),
    ("class_artifact_extra", mutate(["column_classification", "other.csv"], [])),
])
def test_tamper_each_semantic_field_fails_even_with_matching_run_hashes_and_disclosures(world, name, apply):
    document = copy.deepcopy(world.document)
    apply(document)
    assert world.issues(document), name
    assert world.issues(document, source=True), name


def first(document, kind):
    return next(e for e in document["source_identity_disclosures"] if e["kind"] == kind)


@pytest.mark.parametrize("name,apply", [
    ("extra_key", lambda d: first(d, "cross_state_shared_code").__setitem__("extra", 1)),
    ("note_changed", lambda d: first(d, "cross_state_shared_code").__setitem__("note", "cause is a typo")),
    ("code_bool", lambda d: first(d, "cross_state_shared_code").__setitem__("code", True)),
    ("code_wrong_type", lambda d: first(d, "cross_state_shared_code").__setitem__("code", "598")),
    ("unknown_kind", lambda d: first(d, "cross_state_shared_code").__setitem__("kind", "other")),
    ("rows_single", lambda d: first(d, "cross_state_shared_code").__setitem__("rows", first(d, "cross_state_shared_code")["rows"][:1])),
    ("rows_not_ascending", lambda d: first(d, "cross_state_shared_code")["rows"].reverse()),
    ("row_extra_key", lambda d: first(d, "cross_state_shared_code")["rows"][0].__setitem__("x", 1)),
    ("row_pop_string", lambda d: first(d, "cross_state_shared_code")["rows"][0].__setitem__("pop_2011", "1000")),
    ("row_pop_bool", lambda d: first(d, "cross_state_shared_code")["rows"][0].__setitem__("pop_2011", True)),
    ("row_method_type", lambda d: first(d, "cross_state_shared_code")["rows"][0].__setitem__("population_method", 3)),
    ("row_code_mismatch", lambda d: first(d, "cross_state_shared_code")["rows"][0].__setitem__("district_code", 5)),
    ("row_source_row_bool", lambda d: first(d, "cross_state_shared_code")["rows"][0].__setitem__("source_row", False)),
    ("row_label_type", lambda d: first(d, "cross_state_shared_code")["rows"][0].__setitem__("district", None)),
    ("same_extra_key", lambda d: first(d, "same_state_label_multiple_codes").__setitem__("extra", 1)),
    ("same_label_mismatch", lambda d: first(d, "same_state_label_multiple_codes").__setitem__("district", "Dhule")),
    ("same_overlap_missing", lambda d: first(d, "same_state_label_multiple_codes").__setitem__("overlaps", [])),
    ("same_overlap_wrong_pair", lambda d: first(d, "same_state_label_multiple_codes")["overlaps"][0].__setitem__("source_rows", [2, 1])),
    ("same_overlap_fraction_high", lambda d: first(d, "same_state_label_multiple_codes")["overlaps"][0].__setitem__("fraction_of_first", 1.5)),
    ("same_overlap_fraction_negative", lambda d: first(d, "same_state_label_multiple_codes")["overlaps"][0].__setitem__("fraction_of_second", -.1)),
    ("same_overlap_fraction_string", lambda d: first(d, "same_state_label_multiple_codes")["overlaps"][0].__setitem__("fraction_of_first", "0.9")),
    ("same_overlap_extra_key", lambda d: first(d, "same_state_label_multiple_codes")["overlaps"][0].__setitem__("x", 0)),
    ("order_swapped", lambda d: d["source_identity_disclosures"].reverse()),
    ("duplicate_entry", lambda d: d["source_identity_disclosures"].append(copy.deepcopy(d["source_identity_disclosures"][0]))),
    ("not_a_list", lambda d: d.__setitem__("source_identity_disclosures", {})),
    ("entry_not_dict", lambda d: d["source_identity_disclosures"].append("x")),
])
def test_disclosure_entry_shape_tamper_fails_without_needing_source_root(world, name, apply):
    document = copy.deepcopy(world.document)
    apply(document)
    assert world.issues(document), name


def rows_of(document, kind):
    return first(document, kind)["rows"]


@pytest.mark.parametrize("name,apply", [
    ("cross_same_state_literal", lambda d: rows_of(d, "cross_state_shared_code")[1].__setitem__("state", "Kerala")),
    ("cross_same_state_case_and_space", lambda d: rows_of(d, "cross_state_shared_code")[1].__setitem__("state", "  KERALA ")),
    ("cross_blank_state_on_later_row", lambda d: rows_of(d, "cross_state_shared_code")[1].__setitem__("state", "   ")),
    ("cross_blank_district_on_later_row", lambda d: rows_of(d, "cross_state_shared_code")[1].__setitem__("district", "")),
    ("cross_blank_district_first_row", lambda d: rows_of(d, "cross_state_shared_code")[0].__setitem__("district", "  ")),
    ("same_different_states", lambda d: rows_of(d, "same_state_label_multiple_codes")[1].__setitem__("state", "Assam")),
    ("same_different_districts", lambda d: rows_of(d, "same_state_label_multiple_codes")[1].__setitem__("district", "Other")),
    ("same_one_code_only", lambda d: rows_of(d, "same_state_label_multiple_codes")[1].__setitem__("district_code", 598)),
    ("same_blank_state_on_later_row", lambda d: rows_of(d, "same_state_label_multiple_codes")[1].__setitem__("state", " ")),
    ("same_blank_district_on_later_row", lambda d: rows_of(d, "same_state_label_multiple_codes")[1].__setitem__("district", "")),
    ("same_entry_labels_blank", lambda d: first(d, "same_state_label_multiple_codes").update(state=" ", district=" ")),
    ("same_entry_key_differs_from_rows", lambda d: [r.update(state="Assam") for r in rows_of(d, "same_state_label_multiple_codes")]
        and first(d, "same_state_label_multiple_codes").__setitem__("state", "Maharashtra")),
])
def test_disclosure_semantic_contradictions_fail_fast_and_grounded(world, name, apply):
    document = copy.deepcopy(world.document)
    apply(document)
    assert world.issues(document), name  # Fast: no source_root.
    assert world.issues(document, source=True), name


def test_normalized_equal_labels_pass_without_rewriting_literals(world):
    document = copy.deepcopy(world.document)
    same = first(document, "same_state_label_multiple_codes")
    same["rows"][1]["state"], same["rows"][1]["district"] = " MAHARASHTRA", "dhule "
    assert world.issues(document) == []  # One normalized key; literals are kept as given.
    assert same["rows"][0]["state"] == "Maharashtra" and same["rows"][1]["district"] == "dhule "
    assert world.issues(document, source=True)  # Grounded recompute still compares literals and fails.
    cross = first(document, "cross_state_shared_code")
    assert {r["state"] for r in cross["rows"]} == {"Kerala", "Maharashtra"}


def test_shape_valid_but_ungrounded_disclosure_fails_only_with_source_root(world):
    document = copy.deepcopy(world.document)
    first(document, "cross_state_shared_code")["rows"][0]["pop_2011"] = 1001.
    assert world.issues(document) == []
    assert world.issues(document, source=True) == ["limitations: source_identity_disclosures differ from the declared sources"]
    for change in (lambda d: first(d, "cross_state_shared_code")["rows"][0].__setitem__("population_method", "other"),
                   lambda d: first(d, "cross_state_shared_code")["rows"][1].__setitem__("pop_2011", None),
                   lambda d: d["source_identity_disclosures"].pop()):
        document = copy.deepcopy(world.document)
        change(document)
        assert world.issues(document, source=True), "ungrounded disclosure accepted"


BOUNDARY_GEOMETRIES = {"identical": ([box(0, 0, 1, 1), box(0, 0, 1, 1)], 1.0), "disjoint": ([box(0, 0, 1, 1), box(5, 5, 6, 6)], 0.0)}


@pytest.mark.parametrize("kind", sorted(BOUNDARY_GEOMETRIES))
@pytest.mark.parametrize("source", [False, True], ids=["fast", "source-grounded"])
def test_overlap_fractions_must_be_json_floats_not_integers(tmp_path, kind, source):
    geometries, boundary = BOUNDARY_GEOMETRIES[kind]
    world = World(tmp_path, [("Goa", "D", 5), ("Goa", "D", 6)], geometries, [])
    overlap = first(world.document, "same_state_label_multiple_codes")["overlaps"][0]
    assert overlap["fraction_of_first"] == overlap["fraction_of_second"] == boundary
    assert type(overlap["fraction_of_first"]) is float and type(overlap["fraction_of_second"]) is float
    assert world.issues(source=source) == []  # Float 0.0/1.0 control passes with bindings unchanged.
    assert '"fraction_of_first": ' + repr(boundary) in world.path.read_text()
    for key in ("fraction_of_first", "fraction_of_second"):
        document = copy.deepcopy(world.document)
        first(document, "same_state_label_multiple_codes")["overlaps"][0][key] = int(boundary)
        assert world.issues(document, source=source), (key, boundary)


@pytest.mark.parametrize("value", [True, False, "1.0", "0", None, float("nan"), float("inf"), -0.5, 1.5])
@pytest.mark.parametrize("source", [False, True], ids=["fast", "source-grounded"])
def test_overlap_fraction_non_float_or_out_of_range_still_rejected(world, value, source):
    document = copy.deepcopy(world.document)
    first(document, "same_state_label_multiple_codes")["overlaps"][0]["fraction_of_first"] = value
    world.path.write_text(json.dumps(document, allow_nan=True))
    # NaN/inf are not strict JSON; the reader rejects them before the shape checks.
    assert lim.validate_limitations(world.path, world.manifest, source_root=world.source if source else None)


def test_overlap_fraction_tolerance_is_1e9(world):
    document = copy.deepcopy(world.document)
    overlap = first(document, "same_state_label_multiple_codes")["overlaps"][0]
    original = overlap["fraction_of_first"]
    overlap["fraction_of_first"] = original + 5e-10
    assert world.issues(document, source=True) == []
    overlap["fraction_of_first"] = original + 2e-9
    assert world.issues(document, source=True) == ["limitations: source_identity_disclosures differ from the declared sources"]


def test_source_changes_after_build_are_detected(world):
    pd.DataFrame(POPULATION[:3] + [("Assam", "Other", 7, 4001., "census")],
                 columns=["state", "district", "district_code", "pop_2011", "method"]).to_csv(world.source / lim.POPULATION_SOURCE, index=False)
    assert world.issues() == []
    # A population row that no disclosure uses does not change the disclosures.
    assert world.issues(source=True) == []
    # Change one disclosed population.
    pd.DataFrame([POPULATION[0][:3] + (1.5, "census")] + POPULATION[1:], columns=["state", "district", "district_code", "pop_2011", "method"]).to_csv(world.source / lim.POPULATION_SOURCE, index=False)
    assert world.issues(source=True) == ["limitations: source_identity_disclosures differ from the declared sources"]
    (world.source / lim.POPULATION_SOURCE).unlink()
    assert world.issues(source=True) == ["limitations: source disclosures unavailable"]
    (world.source / lim.DISTRICT_SOURCE).unlink()
    assert world.issues(source=True) == ["limitations: source disclosures unavailable"]


def test_privacy_scan_flags_local_paths_without_echo(world, tmp_path):
    document = copy.deepcopy(world.document)
    first(document, "cross_state_shared_code")["rows"][0]["district"] = "C:\\Users\\someone\\file"
    issues = world.issues(document)
    assert "limitations: absolute or private path-like value present" in issues
    assert not any("someone" in i for i in issues)
    document = copy.deepcopy(world.document)
    document["column_classification"]["/home/x/other.csv"] = []
    assert any("private" in i for i in world.issues(document))


def test_builder_rejects_invalid_inputs(world, tmp_path):
    args = (world.source, world.artifacts)
    for run_id in ("", "bad run", "x" * 65, None, "C:\\Users\\x"):
        with pytest.raises(ValueError):
            lim.build_limitations(*args, run_id, "windows_peak_working_set")
    for method in ("unknown", None, {"elapsed": "x"}):
        with pytest.raises((ValueError, TypeError)):
            lim.build_limitations(*args, "run", method)
    short = dict(world.artifacts)
    short.pop(SUMMARY)
    with pytest.raises(ValueError):
        lim.build_limitations(world.source, short, "run", "linux_ru_maxrss")
    assert lim.build_limitations(*args, "run", "linux_ru_maxrss")["resource_measurement_method"] == "resource.ru_maxrss_kib_times_1024"


@pytest.mark.parametrize("missing", [lim.DISTRICT_SOURCE, lim.POPULATION_SOURCE])
def test_builder_fails_on_missing_declared_source(world, missing):
    (world.source / missing).unlink()
    with pytest.raises(Exception):
        lim.build_limitations(world.source, world.artifacts, "run", "linux_ru_maxrss")


@pytest.mark.parametrize("rows", [
    [("Goa", "A", 5), ("Goa", "B", 5)],  # Same-state duplicate code is a source error, not a disclosure.
    [("Goa", "A", 1.5), ("Goa", "B", 6)],
    [("Goa", " ", 5), ("Goa", "B", 6)],
])
def test_builder_validates_all_identities_first(tmp_path, rows):
    # Typed code rejection (bool, string, oversized) is covered by the frozen S1 contract tests.
    with pytest.raises(ValueError):
        World(tmp_path, rows, [box(0, 0, 1, 1), box(2, 2, 3, 3)], [])


@pytest.mark.parametrize("bad", ["empty", "invalid"])
def test_unmeasurable_overlap_never_invents_fractions_from_sources(tmp_path, bad):
    geometry = {"empty": Polygon(), "invalid": Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])}[bad]
    rows = [("Goa", "D", 5), ("Goa", "D", 6)]
    with pytest.raises(ValueError, match="overlap"):
        World(tmp_path, rows, [box(0, 0, 1, 1), geometry], [])


@pytest.mark.parametrize("bad", [None, Polygon(), Polygon([(0, 0), (1, 0), (2, 0)]), Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])])
def test_overlap_measurement_rejects_missing_empty_degenerate_or_invalid_geometry(bad):
    with pytest.raises(ValueError, match="overlap"):
        lim._overlaps([box(0, 0, 1, 1), bad], [0, 1])
    assert lim._overlaps([box(0, 0, 2, 1), box(1, 0, 3, 1)], [0, 1]) == [
        {"source_rows": [0, 1], "fraction_of_first": 0.5, "fraction_of_second": 0.5}]


def test_uses_frozen_s1_helpers_without_copies():
    tree = ast.parse(Path(lim.__file__).read_text())
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "scripts.audit.identity_contract" for alias in node.names}
    assert {"cross_state_codes", "same_state_label_codes", "validate_identities", "strict_code", "label_key", "IdentityError"} <= imported
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert not defined & {"strict_code", "label_key", "identity_key", "validate_identities", "cross_state_codes", "same_state_label_codes", "collision_rows"}
    assert lim.cross_state_codes is identity_contract.cross_state_codes


def test_no_ones_nan_or_numpy_values_leak_into_document(world):
    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                assert type(k) is str
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        else:
            assert type(value) in (str, int, float, bool, type(None)) and not isinstance(value, (np.generic,))
    walk(world.document)


# ---- Sidecar schema 2 (isochrone_coverage), N1a additions: exclusive V2_/v2_ names, existing globals only ----

V2_ORDER = ["schema_version", "run_id", "artifact_sha256", "column_classification", "source_identity_disclosures",
            "isochrone_coverage", "oracle_independence", "resource_measurement_method", "limitations"]
V2_STALE = "limitations: isochrone_coverage required (schema 2)"
V2_COVERAGE_LIMITATION = ("Isochrone catchments are limited to the road-graph component of the snapped vertex, the closest "
                          "vertex within 150 km on a supported component (at least 1000 nodes); hubs with none are unsnapped; "
                          "component-bounded hubs are listed in isochrone_coverage.")


def v2_entry(hub_type, name, order, thresholds, statuses, counts, feeder, component, ok, saturated, bounded):
    return {"hub_type": hub_type, "hub_name": name, "hub_order": order, "thresholds_hours": thresholds, "statuses": statuses,
            "reachable_node_counts": counts, "snap_feeder_km": feeder, "snap_component_node_count": component,
            "ok_threshold_count": ok, "saturated_across_ok_thresholds": saturated, "component_bounded_thresholds": bounded}


def v2_coverage():
    return {"snap_rule": "closest NH vertex within 150 km on a supported connected component (at least 1000 nodes), node-ID ties; hubs with no supported-component vertex within 150 km are unsnapped; catchment limited to that vertex's connected component",
            "entries": [
                v2_entry("major_port", "Alpha", 0, [4., 8., 12., 24.], ["ok"] * 4, [5, 20, 40, 40], 10., 40, 4, False, [12., 24.]),
                v2_entry("major_port", "Beta", 1, [4., 8., 12., 24.], ["invalid_coordinates"] * 4, [0] * 4, None, None, 0, None, []),
                v2_entry("mmlp", "Mm1", 0, [4., 8., 12.], ["feeder_exceeds_threshold", "unsupported_threshold", "ok"], [0, 2, 7],
                         150., 7, 1, None, [12.])]}


def v2_write_hubs(source, ports=("Alpha", "Beta"), mmlps=("Mm1",)):
    (source / "logistics_hubs").mkdir(parents=True, exist_ok=True)
    rows = [(name, "major_port") for name in ports]
    rows.insert(1, ("Minor", "minor_port"))  # A non-major port between the two majors must be filtered out.
    pd.DataFrame(rows, columns=["name", "hub_type"]).to_csv(source / lim.PORTS_SOURCE, index=False)
    pd.DataFrame({"name": list(mmlps)}).to_csv(source / lim.MMLPS_SOURCE, index=False)


class V2World(World):
    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        v2_write_hubs(self.source)
        self.v1_document = self.document
        self.document = lim.build_limitations(self.source, self.artifacts, self.run_id, "windows_peak_working_set",
                                              isochrone_coverage=v2_coverage())

    def check(self, document=None, manifest=None, **kwargs):
        self.write(document)
        return lim.validate_limitations(self.path, self.manifest if manifest is None else manifest, **kwargs)


@pytest.fixture
def v2world(tmp_path):
    return V2World(tmp_path)


def v2_manifest_with(world, thresholds):
    manifest = copy.deepcopy(world.manifest)
    manifest["config"]["thresholds_hours"] = thresholds
    return manifest


def test_v2_constants_exact_and_v1_constants_unchanged():
    assert lim.SCHEMA_VERSION == 1 and lim.SCHEMA_VERSION_V2 == 2 and type(lim.SCHEMA_VERSION_V2) is int
    assert lim.TOP_LEVEL_KEYS_V2 == lim.TOP_LEVEL_KEYS | {"isochrone_coverage"} and len(lim.TOP_LEVEL_KEYS) == 8
    assert lim.COVERAGE_LIMITATION == V2_COVERAGE_LIMITATION
    assert lim.LIMITATIONS_V2 == [*lim.LIMITATIONS, V2_COVERAGE_LIMITATION] and len(lim.LIMITATIONS) == 9 and len(lim.LIMITATIONS_V2) == 10
    assert lim.PORTS_SOURCE == "logistics_hubs/ports.csv" and lim.MMLPS_SOURCE == "logistics_hubs/mmlps.csv"
    assert lim.DISTRICT_SOURCE == "administrative/india_districts_lgd.geojson"
    assert lim.POPULATION_SOURCE == "demographic/district_population_estimates.csv"


def test_v2_module_does_not_create_an_import_cycle():
    import scripts.analyze.isochrone_coverage as coverage_module
    assert not hasattr(coverage_module, "producer_limitations")
    assert "producer_limitations" not in Path(coverage_module.__file__).read_text(encoding="utf-8")


def test_v1_build_is_unchanged_without_or_with_explicit_none(world):
    explicit = lim.build_limitations(world.source, world.artifacts, world.run_id, "windows_peak_working_set", isochrone_coverage=None)
    assert explicit == world.document and list(explicit) == TOP_ORDER and explicit["schema_version"] == 1
    assert "isochrone_coverage" not in explicit and explicit["limitations"] == lim.LIMITATIONS
    assert world.issues(explicit) == [] and world.issues(explicit, source=True) == []


def test_v2_build_has_ordered_keys_schema_two_and_v2_limitations(v2world):
    document = v2world.document
    assert list(document) == V2_ORDER and type(document["schema_version"]) is int and document["schema_version"] == 2
    assert document["isochrone_coverage"] == v2_coverage() and document["limitations"] == lim.LIMITATIONS_V2
    for key in ("run_id", "artifact_sha256", "column_classification", "source_identity_disclosures", "oracle_independence",
                "resource_measurement_method"):
        assert document[key] == v2world.v1_document[key], key
    assert json.loads(json.dumps(document, allow_nan=False)) == document


def test_v2_build_copies_coverage_and_leaves_the_input_untouched(v2world):
    coverage = v2_coverage()
    document = lim.build_limitations(v2world.source, v2world.artifacts, "run", "linux_ru_maxrss", isochrone_coverage=coverage)
    assert coverage == v2_coverage()
    document["isochrone_coverage"]["entries"][0]["hub_name"] = "Changed"
    assert coverage["entries"][0]["hub_name"] == "Alpha"


@pytest.mark.parametrize("change", [
    lambda c: c.__setitem__("snap_rule", "other"), lambda c: c["entries"][0].__setitem__("ok_threshold_count", 1),
    lambda c: c["entries"][0]["reachable_node_counts"].__setitem__(0, float("nan")),
    lambda c: c["entries"][0]["thresholds_hours"].__setitem__(0, float("inf")),
    lambda c: c["entries"].reverse(), lambda c: c.pop("entries"),
])
def test_v2_build_refuses_invalid_coverage(v2world, change):
    coverage = v2_coverage()
    change(coverage)
    with pytest.raises(ValueError):
        lim.build_limitations(v2world.source, v2world.artifacts, "run", "linux_ru_maxrss", isochrone_coverage=coverage)
    for bad in ([], "x", 5):
        with pytest.raises(ValueError):
            lim.build_limitations(v2world.source, v2world.artifacts, "run", "linux_ru_maxrss", isochrone_coverage=bad)


def test_v2_validates_in_default_strict_and_source_modes(v2world):
    assert v2world.check() == []
    assert v2world.check(require_coverage=True) == []
    assert v2world.check(source_root=v2world.source) == []
    assert v2world.check(source_root=v2world.source, require_coverage=True) == []


def test_require_coverage_rejects_a_valid_v1_sidecar_only_in_strict_mode(world):
    assert world.issues() == [] and world.issues(source=True) == []
    world.write()
    assert V2_STALE in lim.validate_limitations(world.path, world.manifest, require_coverage=True)
    assert V2_STALE in lim.validate_limitations(world.path, world.manifest, source_root=world.source, require_coverage=True)
    assert lim.validate_limitations(world.path, world.manifest, require_coverage=False) == []


def test_v1_source_mode_does_not_require_hub_csvs(world):
    assert not (world.source / "logistics_hubs").exists()
    assert world.issues(source=True) == []


def test_v1_schema_two_tamper_remains_unsupported_schema_and_v1_key_tamper_unchanged(world):
    document = copy.deepcopy(world.document)
    document["schema_version"] = 2
    assert "limitations: unsupported schema" in world.issues(document)
    document = copy.deepcopy(world.document)
    document["route_only_outputs"] = []
    assert world.issues(document) == ["limitations: missing or unknown top-level keys"]
    document = copy.deepcopy(world.document)
    document["isochrone_coverage"] = v2_coverage()  # The nine-key set is judged as schema 2, so schema 1 and v1 limitations fail.
    assert world.issues(document) == ["limitations: unsupported schema", "limitations: limitations list is not the supported constant"]


def test_v2_with_v1_limitations_list_fails(v2world):
    document = copy.deepcopy(v2world.document)
    document["limitations"] = list(lim.LIMITATIONS)
    assert "limitations: limitations list is not the supported constant" in v2world.check(document)
    document["limitations"] = list(lim.LIMITATIONS_V2)
    assert v2world.check(document) == []
    document["limitations"].reverse()
    assert v2world.check(document)


@pytest.mark.parametrize("version", [1, True, False, "2", 2.0, None, 3, 0])
def test_v2_schema_version_must_be_int_two(v2world, version):
    document = copy.deepcopy(v2world.document)
    document["schema_version"] = version
    assert "limitations: unsupported schema" in v2world.check(document)


@pytest.mark.parametrize("name,apply", [
    ("v2_missing_coverage", lambda d: d.pop("isochrone_coverage")),
    ("v2_missing_other", lambda d: d.pop("limitations")),
    ("v2_extra_key", lambda d: d.__setitem__("extra", 1)),
    ("v2_unknown_replaces_known", lambda d: (d.pop("oracle_independence"), d.__setitem__("other", {}))),
])
def test_v2_unknown_or_missing_top_level_keys(v2world, name, apply):
    document = copy.deepcopy(v2world.document)
    apply(document)
    expected = ["limitations: missing or unknown top-level keys"]
    if name == "v2_missing_coverage":
        expected = []  # The remaining keys are exactly v1, so the v1 path judges them (schema 2 is unsupported there).
    issues = v2world.check(document)
    assert issues, name
    if expected:
        assert issues == expected


def test_v2_document_with_coverage_removed_is_judged_as_v1(v2world):
    document = copy.deepcopy(v2world.document)
    document.pop("isochrone_coverage")
    issues = v2world.check(document)
    assert "limitations: unsupported schema" in issues and "limitations: limitations list is not the supported constant" in issues


@pytest.mark.parametrize("name,apply", [
    ("snap_rule", lambda c: c.__setitem__("snap_rule", "x")),
    ("status_flip", lambda c: c["entries"][0]["statuses"].__setitem__(0, "unsupported_threshold")),
    ("derived_ok_count", lambda c: c["entries"][0].__setitem__("ok_threshold_count", 3)),
    ("derived_saturation", lambda c: c["entries"][0].__setitem__("saturated_across_ok_thresholds", True)),
    ("derived_bounded", lambda c: c["entries"][0].__setitem__("component_bounded_thresholds", [24.])),
    ("unsnapped_feeder", lambda c: c["entries"][1].__setitem__("snap_feeder_km", 1.)),
    ("order_gap", lambda c: c["entries"][1].__setitem__("hub_order", 2)),
    ("order_swap", lambda c: c["entries"].insert(0, c["entries"].pop(1))),
    ("count_bool", lambda c: c["entries"][0]["reachable_node_counts"].__setitem__(0, True)),
    ("count_huge", lambda c: c["entries"][0]["reachable_node_counts"].__setitem__(3, 10 ** 400)),
    ("feeder_nan_in_json", lambda c: c["entries"][0].__setitem__("snap_feeder_km", float("nan"))),
    ("thresholds_int", lambda c: c["entries"][0]["thresholds_hours"].__setitem__(0, 4)),
    ("entries_not_list", lambda c: c.__setitem__("entries", None)),
    ("coverage_not_dict", lambda c: c.clear()),
])
def test_v2_coverage_tamper_is_reported_with_the_coverage_prefix_and_never_raises(v2world, name, apply):
    document = copy.deepcopy(v2world.document)
    apply(document["isochrone_coverage"])
    v2world.path.write_text(json.dumps(document, indent=1, allow_nan=True), encoding="utf-8")
    for kwargs in ({}, {"source_root": v2world.source}, {"require_coverage": True}):
        issues = lim.validate_limitations(v2world.path, v2world.manifest, **kwargs)
        if name == "feeder_nan_in_json":
            assert issues  # Strict JSON reading rejects NaN before any coverage check.
        else:
            assert any(i.startswith("limitations: isochrone_coverage: ") for i in issues), (name, issues)


def test_v2_coverage_not_a_dict_does_not_raise(v2world):
    for value in (None, [], "x", 5, True):
        document = copy.deepcopy(v2world.document)
        document["isochrone_coverage"] = value
        assert any(i.startswith("limitations: isochrone_coverage: ") for i in v2world.check(document)), value


def test_v2_thresholds_come_from_the_manifest_config_and_integers_are_canonicalized(v2world):
    assert v2world.check(manifest=v2_manifest_with(v2world, {"major_port": [4, 8, 12, 24], "mmlp": [4, 8, 12]})) == []
    assert v2world.check(manifest=v2_manifest_with(v2world, {"major_port": [4., 8., 12., 24.], "mmlp": [4, 8., 12]})) == []
    for other in ({"major_port": [4., 8., 12., 24.], "mmlp": [4., 8.]}, {"major_port": [4., 8., 12.], "mmlp": [4., 8., 12.]},
                  {"major_port": [4., 8., 12., 25.], "mmlp": [4., 8., 12.]}):
        issues = v2world.check(manifest=v2_manifest_with(v2world, other))
        assert any(i.startswith("limitations: isochrone_coverage: ") for i in issues), other


@pytest.mark.parametrize("thresholds", [
    None, [], "4", {"major_port": [4., 8., 12., 24.]}, {"mmlp": [4., 8., 12.]},
    {"major_port": [float("nan"), 8., 12., 24.], "mmlp": [4., 8., 12.]}, {"major_port": [4., 8., 12., float("inf")], "mmlp": [4., 8., 12.]},
    {"major_port": [True, 8., 12., 24.], "mmlp": [4., 8., 12.]}, {"major_port": [], "mmlp": [4., 8., 12.]},
    {"major_port": "4,8", "mmlp": [4., 8., 12.]}, {"major_port": [4., 8., 12., 24.], "mmlp": [0., 8., 12.]},
    {"major_port": [4., 4., 12., 24.], "mmlp": [4., 8., 12.]}, {"major_port": [4., 8., 12., 10 ** 400], "mmlp": [4., 8., 12.]},
])
def test_v2_unusable_manifest_thresholds_are_reported_without_raising(v2world, thresholds):
    assert "limitations: manifest thresholds unavailable" in v2world.check(manifest=v2_manifest_with(v2world, thresholds))


def test_v2_missing_manifest_config_is_reported_but_v1_does_not_read_config(v2world, world):
    manifest = copy.deepcopy(v2world.manifest)
    manifest.pop("config")
    assert "limitations: manifest thresholds unavailable" in v2world.check(manifest=manifest)
    broken = copy.deepcopy(world.manifest)
    broken.pop("config")
    world.write()
    assert lim.validate_limitations(world.path, broken) == []  # v1 behavior is unchanged: no thresholds are consulted.


def test_v2_hub_sources_compare_literal_names_in_order_with_major_ports_only(v2world):
    assert v2world.check(source_root=v2world.source) == []
    for ports, mmlps in ((["Beta", "Alpha"], ["Mm1"]), (["Alpha"], ["Mm1"]), (["Alpha", "Beta", "Gamma"], ["Mm1"]),
                         (["Alpha", "Alpha"], ["Mm1"]), (["alpha", "Beta"], ["Mm1"]), (["Alpha", "Beta"], ["Mm1", "Mm2"]),
                         (["Alpha", "Beta"], []), (["Alpha", "Beta"], ["MM1"]), (["Alpha", "Beta"], ["Mm1 "])):
        v2_write_hubs(v2world.source, ports, mmlps)
        assert v2world.check(source_root=v2world.source), (ports, mmlps)
        assert v2world.check() == [], "sources are only read in source mode"
    v2_write_hubs(v2world.source)
    assert v2world.check(source_root=v2world.source) == []


def test_v2_hub_sources_missing_or_malformed_fail_with_the_sources_unavailable_issue(v2world):
    (v2world.source / lim.MMLPS_SOURCE).unlink()
    assert "limitations: hub sources unavailable" in v2world.check(source_root=v2world.source)
    v2_write_hubs(v2world.source)
    (v2world.source / lim.PORTS_SOURCE).unlink()
    assert "limitations: hub sources unavailable" in v2world.check(source_root=v2world.source)
    v2_write_hubs(v2world.source)
    (v2world.source / lim.PORTS_SOURCE).write_text("name\nAlpha\n", encoding="utf-8")  # hub_type column absent.
    assert "limitations: hub sources unavailable" in v2world.check(source_root=v2world.source)
    v2_write_hubs(v2world.source)
    (v2world.source / lim.MMLPS_SOURCE).write_text("other\nMm1\n", encoding="utf-8")  # name column absent.
    assert "limitations: hub sources unavailable" in v2world.check(source_root=v2world.source)
    v2_write_hubs(v2world.source)
    (v2world.source / lim.PORTS_SOURCE).write_bytes(b"")
    assert "limitations: hub sources unavailable" in v2world.check(source_root=v2world.source)
    v2_write_hubs(v2world.source)
    assert v2world.check(source_root=v2world.source) == []


def test_v2_source_mode_still_reads_district_and_population_sources(v2world):
    (v2world.source / lim.POPULATION_SOURCE).unlink()
    assert "limitations: source disclosures unavailable" in v2world.check(source_root=v2world.source)


def test_v2_hub_csvs_are_not_needed_by_the_builder_and_v1_sources_pass_without_them(tmp_path):
    plain = World(tmp_path)
    assert not (plain.source / "logistics_hubs").exists()
    assert plain.issues(source=True) == []
    plain.document = lim.build_limitations(plain.source, plain.artifacts, plain.run_id, "windows_peak_working_set",
                                           isochrone_coverage=v2_coverage())
    assert plain.issues() == []  # Builder succeeds and the default validation never needs hub CSVs.
    assert "limitations: hub sources unavailable" in lim.validate_limitations(plain.path, plain.manifest, source_root=plain.source)


def test_v2_private_paths_in_coverage_are_caught_without_echo(v2world):
    document = copy.deepcopy(v2world.document)
    document["isochrone_coverage"]["entries"][0]["hub_name"] = "C:\\Users\\someone\\hub"
    issues = v2world.check(document)
    assert "limitations: absolute or private path-like value present" in issues
    assert not any("someone" in i for i in issues)


def test_v2_validator_reads_only_the_sidecar_and_optional_sources(v2world, monkeypatch):
    opened = []
    original = pd.read_csv
    monkeypatch.setattr(pd, "read_csv", lambda path, *a, **k: (opened.append(Path(path).name), original(path, *a, **k))[1])
    assert v2world.check() == [] and opened == []
    assert v2world.check(source_root=v2world.source) == []
    assert not set(opened) & set(pm.REQUIRED_ARTIFACTS)  # Artifacts are never read; only declared source tables.


def test_v2_document_plain_json_types_only(v2world):
    def walk(value):
        if isinstance(value, dict):
            assert all(type(k) is str for k in value)
            [walk(v) for v in value.values()]
        elif isinstance(value, list):
            [walk(v) for v in value]
        else:
            assert type(value) in (str, int, float, bool, type(None)) and not isinstance(value, np.generic)
    walk(v2world.document)
    assert str(v2world.path.parent) not in json.dumps(v2world.document)


def test_old_v1_unit_cases_stay_exact_after_the_v2_extension(world):
    assert list(world.document) == TOP_ORDER and world.document["limitations"] == lim.LIMITATIONS
    assert world.issues() == [] and world.issues(source=True) == []
    document = copy.deepcopy(world.document)
    document["limitations"] = list(lim.LIMITATIONS_V2)
    assert "limitations: limitations list is not the supported constant" in world.issues(document)


@pytest.fixture(autouse=True, scope="module")
def _tiny_synthetic_graphs_are_supported():
    """Synthetic fixtures are far below the production component-size floor, so this module runs with
    the floor at 1; supported-component snapping is covered in tests/test_isochrone_producer.py and
    tests/test_supported_component_floor.py."""
    from scripts.analyze import highway_pair_kernel as _kernel
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_kernel, "MIN_SUPPORTED_COMPONENT_NODES", 1)
        from scripts.audit import producer_graph_oracle as _oracle
        patch.setattr(_oracle, "ORACLE_MIN_SUPPORTED_COMPONENT_NODES", 1)
        yield
