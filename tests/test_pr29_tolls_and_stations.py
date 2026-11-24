"""Unit tests for PR #29 toll-plaza stability, station-review regression checks, and audit hardening."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.audit import audit_all as audit
from scripts.clean import assign_station_states as ass
from scripts.fetch import fetch_toll_plazas as ftp


# --- fetch_toll_plazas -------------------------------------------------------------------

def test_select_representative_element_prefers_node_centroid_then_lowest_id():
    grp = pd.DataFrame([
        {"osm_type": "way", "osm_id": 10, "proj_x": 0.0, "proj_y": 0.0},
        {"osm_type": "node", "osm_id": 500, "proj_x": 10.0, "proj_y": 0.0},
        {"osm_type": "node", "osm_id": 200, "proj_x": 10.0, "proj_y": 0.0},
        {"osm_type": "node", "osm_id": 999, "proj_x": 50.0, "proj_y": 0.0},
    ])
    rep = ftp._select_representative_element(grp)
    # Centroid x = 17.5 -> nodes at x=10.0 tie for closest node (way at 0 is ignored vs node); lowest osm_id wins
    assert rep["osm_type"] == "node"
    assert int(rep["osm_id"]) == 200


def _sample_plaza(osm_id: int, state: str, name: str, lat: float, lon: float) -> dict:
    return {
        "osm_node_id": osm_id,
        "name": name,
        "toll_type": "national_highway",
        "nh_number": "NH 44",
        "state": state,
        "district": "TEST",
        "district_code": 1,
        "latitude": lat,
        "longitude": lon,
        "booth_count": 2,
        "source_url": f"https://www.openstreetmap.org/node/{osm_id}",
    }


def test_finalize_plazas_preserves_existing_toll_ids_across_refreshes(tmp_path):
    initial = pd.DataFrame([
        _sample_plaza(2002, "Maharashtra", "Beta Toll", 19.0, 73.0),
        _sample_plaza(1001, "Gujarat", "Alpha Toll", 22.0, 72.0),
    ])
    first = ftp._finalize_plazas_frame(initial)
    assert first[["toll_id", "osm_node_id"]].to_dict("records") == [
        {"toll_id": "TPL-0001", "osm_node_id": 1001},
        {"toll_id": "TPL-0002", "osm_node_id": 2002},
    ]

    csv_path = tmp_path / "toll_plazas.csv"
    first.to_csv(csv_path, index=False, lineterminator="\n")
    existing_ids = ftp._load_existing_toll_ids(csv_path)
    assert existing_ids == {1001: "TPL-0001", 2002: "TPL-0002"}

    # Insert a brand-new toll earlier in sort order (Andhra Pradesh): existing IDs must NOT shift
    refreshed = pd.DataFrame([
        _sample_plaza(3003, "Andhra Pradesh", "New Toll", 16.0, 80.0),
        _sample_plaza(2002, "Maharashtra", "Beta Toll", 19.0, 73.0),
        _sample_plaza(1001, "Gujarat", "Alpha Toll", 22.0, 72.0),
    ])
    second = ftp._finalize_plazas_frame(refreshed, existing_ids=existing_ids)
    by_osm = dict(zip(second["osm_node_id"], second["toll_id"]))
    assert by_osm == {1001: "TPL-0001", 2002: "TPL-0002", 3003: "TPL-0003"}


def test_load_plazas_from_cache_records_and_error_cases(tmp_path):
    good = tmp_path / "records.json"
    good.write_text(
        json.dumps({"records": [_sample_plaza(42, "Haryana", "Karnal Toll", 29.7, 76.9)]}),
        encoding="utf-8",
    )
    df = ftp.load_plazas_from_cache(good)
    assert list(df.columns) == ftp.OUTPUT_COLUMNS
    assert df.loc[0, "toll_id"] == "TPL-0001"
    assert int(df.loc[0, "osm_node_id"]) == 42

    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps({"records": [{"name": "Broken"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        ftp.load_plazas_from_cache(missing)

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"unexpected": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported cache format"):
        ftp.load_plazas_from_cache(bad)


# --- assign_station_states ---------------------------------------------------------------

def test_load_resolved_review_reads_only_non_empty_resolutions(tmp_path):
    review_csv = tmp_path / "review.csv"
    pd.DataFrame([
        {"station_code": "AAA", "station_name": "Alpha", "resolution": "label_ok_polygon_correct", "note": "ok"},
        {"station_code": "BBB", "station_name": "Beta", "resolution": "", "note": ""},
        {"station_code": "CCC", "station_name": "Gamma", "resolution": "   ", "note": ""},
    ]).to_csv(review_csv, index=False, lineterminator="\n")

    resolved_df, resolved_codes = ass._load_resolved_review(review_csv, ass.REVIEW_COLUMNS)
    assert list(resolved_df.columns) == ass.REVIEW_COLUMNS
    assert resolved_codes == {"AAA"}


def test_filter_unresolved_station_reviews_surfaces_hard_regressions():
    resolved_df = pd.DataFrame([
        {"station_code": "AAA", "resolution": "label_ok_polygon_correct"},
        {"station_code": "BBB", "resolution": "coords_fixed"},
    ])
    cat_resolved_df = pd.DataFrame([
        {"station_code": "CCC", "resolution": "coords_fixed"},
    ])
    live_review = [
        {"station_code": "AAA", "reason": "source state disagrees with the polygon"},
        {"station_code": "BBB", "reason": "outside every state polygon"},
        {"station_code": "CCC", "reason": "shares coordinates with another station"},
        {"station_code": "DDD", "reason": "source state disagrees with the polygon"},
    ]
    unresolved = ass.filter_unresolved_station_reviews(live_review, resolved_df, cat_resolved_df)
    assert [r["station_code"] for r in unresolved] == ["BBB", "CCC", "DDD"]


def test_filter_unresolved_category_reviews_catches_regressed_fixes_and_keeps_collision():
    resolved_df = pd.DataFrame([
        {"station_code": "TDK", "category_state": "Chhattisgarh", "polygon_state": "Andhra Pradesh",
         "resolution": "code_collision"},
        {"station_code": "ALPR", "category_state": "Andhra Pradesh", "polygon_state": "Telangana",
         "resolution": "label_fixed"},
    ])
    live = [
        {"station_code": "TDK", "category_state": "Chhattisgarh", "polygon_state": "Andhra Pradesh"},
        {"station_code": "ALPR", "category_state": "Andhra Pradesh", "polygon_state": "Telangana"},
    ]
    unresolved = ass.filter_unresolved_category_reviews(live, resolved_df)
    # TDK collision stays suppressed while unchanged; ALPR (previously label_fixed) must resurface
    assert [r["station_code"] for r in unresolved] == ["ALPR"]


# --- audit_all hardening -----------------------------------------------------------------

@pytest.fixture
def audit_results():
    results = []
    token = audit._RESULT_SINK.set(results)
    try:
        yield results
    finally:
        audit._RESULT_SINK.reset(token)


def test_audit_rail_international_blank_state_handles_mixed_nan_and_empty(audit_results, tmp_path):
    rail_dir = tmp_path / "rail"
    rail_dir.mkdir()
    pd.DataFrame([
        {"station_code": "DSN", "state": float("nan"), "latitude": 23.53, "longitude": 88.78},
        {"station_code": "DHCA", "state": "", "latitude": 23.82, "longitude": 90.40},
        {"station_code": "NDLS", "state": "Delhi", "latitude": 28.64, "longitude": 77.22},
    ]).to_csv(rail_dir / "railway_stations.csv", index=False)
    pd.DataFrame([
        {"station_code": "NDLS", "category": "NSG1"},
    ]).to_csv(rail_dir / "station_categories.csv", index=False)
    pd.DataFrame([
        {"name": "GCT1", "state": "Delhi", "latitude": 28.64, "longitude": 77.22},
    ]).to_csv(rail_dir / "freight_terminals.csv", index=False)

    audit.audit_rail(tmp_path)
    intl_check = next(r for r in audit_results if "DSN + DHCA" in r["check"])
    assert intl_check["status"] == "PASS"


def test_audit_roads_missing_toll_id_columns_fails_schema_without_attribute_error(audit_results, tmp_path):
    roads_dir = tmp_path / "roads"
    roads_dir.mkdir()
    pd.DataFrame([
        {"name": "Old Toll", "toll_type": "national_highway", "nh_number": "NH 44",
         "state": "Haryana", "district": "AMBALA", "district_code": 1,
         "latitude": 30.3, "longitude": 76.8, "booth_count": 2, "source_url": "https://www.openstreetmap.org"},
    ]).to_csv(roads_dir / "toll_plazas.csv", index=False)

    # Create a tiny valid NH geojson so audit_roads reaches toll_plazas cleanly
    import geopandas as gpd
    from shapely.geometry import LineString

    gpd.GeoDataFrame(
        {"nh": ["44"], "highway": ["trunk"]},
        geometry=[LineString([(77.0, 28.0), (77.1, 28.1)])],
        crs=4326,
    ).to_file(roads_dir / "india_nh_network.geojson", driver="GeoJSON")

    audit.audit_roads(tmp_path, fast=True)
    toll_checks = {r["check"]: r["status"] for r in audit_results if r["dataset"] == "toll_plazas"}
    assert toll_checks["schema"] == "FAIL"
    assert "toll_id unique, TPL-NNNN" not in toll_checks
