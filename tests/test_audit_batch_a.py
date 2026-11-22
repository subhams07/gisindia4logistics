"""Checks for the 2026-11 audit batch A changes (data invariants and pure helpers)."""
import pathlib
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
needs_data = pytest.mark.skipif(not (DATA / "administrative" / "india_states_lgd.geojson").exists(),
                                reason="committed data not present")


def _script(sub, name):
    import importlib
    path = str(ROOT / "scripts" / sub)
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module(name)


@pytest.fixture
def audit():
    return _script("audit", "audit_all")


def _results(audit, function, *args):
    sink = []
    token = audit._RESULT_SINK.set(sink)
    try:
        function(*args)
    finally:
        audit._RESULT_SINK.reset(token)
    return sink


# --- India's official political map -------------------------------------------------------

@needs_data
@pytest.mark.parametrize("layer", ["india_states_lgd.geojson", "india_states.geojson"])
def test_official_map_reference_points(audit, layer):
    states = gpd.read_file(DATA / "administrative" / layer)
    results = _results(audit, audit._political_map_checks, layer, states)
    assert len(results) == 5 and all(r["status"] == "PASS" for r in results), results


def test_official_map_check_fails_when_a_point_moves(audit):
    from shapely.geometry import box
    wrong = gpd.GeoDataFrame({"state": ["Ladakh"]}, geometry=[box(60, 20, 100, 40)], crs=4326)
    results = _results(audit, audit._political_map_checks, "synthetic", wrong)
    by_place = {r["check"].split(": ")[1].split(" in ")[0]: r["status"] for r in results}
    assert by_place["Aksai Chin"] == by_place["Gilgit"] == "PASS"
    assert by_place["Muzaffarabad"] == by_place["Mirpur"] == by_place["Tawang"] == "FAIL"


# --- Habitations -------------------------------------------------------------------------

@needs_data
def test_soi_states_keep_village_unit():
    access = pd.read_csv(DATA / "analysis" / "sikkim_village_access.csv", usecols=["unit"])
    assert set(access["unit"]) == {"village"}


# --- Demographics validation gate --------------------------------------------------------

@needs_data
def test_demographics_gate_passes_on_committed_table_and_fails_when_perturbed():
    fd = _script("fetch", "fetch_demographics")
    df = pd.read_csv(DATA / "demographic" / "census2011_district_key_indicators.csv")
    assert all(fd.validation_checks(df).values())
    broken = df.copy()
    broken.loc[0, "Population"] += 1
    failed = {k for k, v in fd.validation_checks(broken).items() if not v}
    assert "population reconciles" in failed and "male+female == total" in failed
    assert not all(fd.validation_checks(df.iloc[1:]).values())


# --- Freight consistency ----------------------------------------------------------------

def _freight_rows(entity_type, code, group, value, fy="2023-24"):
    return {"fy": fy, "entity_type": entity_type, "entity_code": code, "commodity_group": group, "value": value}


def test_freight_consistency_flags_parts_that_do_not_add_up():
    ft = _script("fetch", "build_freight_tables")
    rail = pd.DataFrame([_freight_rows("national", "ALL_INDIA", "all", 100.0),
                         _freight_rows("national", "ALL_INDIA", "coal", 60.0),
                         _freight_rows("national", "ALL_INDIA", "other", 40.0),
                         _freight_rows("zone", "SR", "all", 70.0)])
    port = pd.DataFrame([_freight_rows("national", "ALL_MAJOR_PORTS", "all", 50.0),
                         _freight_rows("port", "INMAA", "all", 50.2)])
    issues = ft.consistency_issues(rail, port)
    assert len(issues) == 1 and issues[0].startswith("rail 2023-24 zones")


@needs_data
def test_committed_freight_tables_report_their_known_inconsistencies():
    ft = _script("fetch", "build_freight_tables")
    issues = ft.consistency_issues(pd.read_csv(DATA / "freight" / "rail_freight_annual.csv"),
                                   pd.read_csv(DATA / "freight" / "port_throughput_annual.csv"))
    assert any("rail 2023-24 zones" in i for i in issues) and any("ports 2022-23" in i for i in issues)


# --- Small helpers ----------------------------------------------------------------------

def test_toll_clusters_chain_within_radius():
    tp = _script("fetch", "fetch_toll_plazas")
    coords = np.array([[0, 0], [100, 0], [200, 0], [1000, 0], [1149, 0], [5000, 5000]], float)
    labels = tp.cluster_labels(coords, 150.0)
    assert labels[0] == labels[1] == labels[2] and labels[3] == labels[4]
    assert len(set(labels)) == 3
    assert list(tp.cluster_labels(np.array([[0.0, 0.0]]), 150.0)) == [0]


def test_gct_station_name_normalisation():
    gct = _script("fetch", "build_gct_terminals")
    assert gct.norm("Wadi Junction") == "wadi"
    assert gct.norm(" Barabani RS ") == "barabani"


def test_rail_zone_cleaning_handles_blank_and_placeholders():
    er = _script("fetch", "enrich_rail_stations")
    assert er.clean_zone("   ") is None and er.clean_zone("NA") is None and er.clean_zone(None) is None
    assert er.clean_zone("wc (jabalpur)") == "WCR" and er.clean_zone("ECOR") == "ECoR"
    assert er._parse('var x = 1; const data = [ {"code": "NDLS"} ];')["code"].tolist() == ["NDLS"]
    assert er._parse("no data here") is None


def test_twin_parity_check_detects_row_and_column_drift(audit, tmp_path):
    from shapely.geometry import Point
    villages = tmp_path / "villages"
    twins = tmp_path / "villages_parquet"
    villages.mkdir()
    twins.mkdir()
    frame = gpd.GeoDataFrame({"village": ["a", "b"]}, geometry=[Point(77, 20), Point(78, 21)], crs=4326)
    gj = villages / "x_soi_villages.geojson"
    frame.to_file(gj, driver="GeoJSON")
    frame.to_parquet(twins / "x_soi_villages.parquet", index=False)
    read = gpd.read_file(gj)
    assert [r["status"] for r in _results(audit, audit._twin_parity_check, "v", "x", read, gj)] == ["PASS"]
    frame.iloc[:1].assign(extra=1).to_parquet(twins / "x_soi_villages.parquet", index=False)
    assert [r["status"] for r in _results(audit, audit._twin_parity_check, "v", "x", read, gj)] == ["FAIL"]
    (twins / "x_soi_villages.parquet").unlink()
    assert [r["status"] for r in _results(audit, audit._twin_parity_check, "v", "x", read, gj)] == ["FAIL"]


@needs_data
def test_catalog_statuses_are_known_and_removed_files_are_not_listed():
    import yaml
    catalog = yaml.safe_load((ROOT / "catalog.yaml").read_text())["datasets"]
    assert {d["quality_status"] for d in catalog} <= {"verified", "community", "sample", "unverified"}
    deleted = {"data/administrative/india_subdistricts_lgd.geojson",
               "data/administrative/villages/pune_soi_villages.geojson"}
    assert not deleted & {d.get("path") for d in catalog}
    assert not any((DATA.parent / path).exists() for path in deleted)
