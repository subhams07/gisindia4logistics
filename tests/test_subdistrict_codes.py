"""Unit and integration tests for SoI ABDB sub-district code preservation (Audit Item #18)."""
from __future__ import annotations

import pathlib
import re

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import box

import scripts.audit.audit_all as audit_all
import scripts.clean.process_panindia_boundaries as panindia
import scripts.clean.restore_subdistrict_codes as restore

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SUB_GPKG = DATA / "administrative" / "india_subdistricts_lgd.gpkg"
SUB_PARQUET = DATA / "administrative" / "india_subdistricts_lgd.parquet"
SUB_ACCESS_CSV = DATA / "analysis" / "india_subdistrict_access.csv"

needs_data = pytest.mark.skipif(
    not (SUB_GPKG.exists() and SUB_PARQUET.exists() and SUB_ACCESS_CSV.exists()),
    reason="committed subdistrict layers or access CSV not present",
)


# --- Unit tests: normalize_sub_district_code ---------------------------------------------

@pytest.mark.parametrize(
    "raw_value,expected",
    [
        ("00285", "285"),
        ("00000", "0"),
        (" 938 ", "938"),
        ("06479", "6479"),
        (285, "285"),
        (285.0, "285"),
        ("285.0", "285"),
        ("00285.0", "285"),
        ("00285N001", "00285N001"),
        ("00288N002", "00288N002"),
        ("00356N001", "00356N001"),
        (" 07314NOO1\r\n", "07314NOO1"),
        ("05010N004", "05010N004"),
        ("NOT AVAILABLE", None),
        ("NOT AVAILABLE\r\n", None),
        ("not available", None),
        (" Not Available ", None),
        ("NEW MANDAL", None),
        ("new mandal", None),
        ("", None),
        ("   \t\n", None),
        (None, None),
        (float("nan"), None),
        (pd.NA, None),
    ],
)
def test_normalize_sub_district_code(raw_value, expected):
    assert panindia.normalize_sub_district_code(raw_value) == expected


def test_match_subdistrict_codes_tiebreaks_by_geometry_and_rejects_unmatched():
    committed = gpd.GeoDataFrame(
        {
            "state": ["Uttarakhand", "Uttarakhand"],
            "abdb_district_code": [57, 57],
            "sub_district": ["DUPNAME", "DUPNAME"],
        },
        geometry=[box(79.0, 30.0, 79.5, 30.5), box(79.5, 30.0, 80.0, 30.5)],
        crs=4326,
    )
    raw = gpd.GeoDataFrame(
        {
            "STATE_UT": ["UTTARAKHAND", "UTTARAKHAND"],
            "DISTRICT": ["CHAMOLI", "CHAMOLI"],
            "DIST_LGD": ["057", "057"],
            "SUB_DIST": ["DUPNAME", "DUPNAME"],
            "SUBDIS_LGD": ["00285N002", "00285N001"],
        },
        geometry=[box(79.5, 30.0, 80.0, 30.5), box(79.0, 30.0, 79.5, 30.5)],
        crs=4326,
    )
    matched = restore.match_subdistrict_codes(committed, raw)
    assert matched.tolist() == ["00285N001", "00285N002"]

    missing_raw = raw.iloc[:1].copy()
    with pytest.raises(ValueError, match="only 1 raw rows"):
        restore.match_subdistrict_codes(committed, missing_raw)


def test_restore_subdistrict_codes_writes_atomically_and_preserves_wkb(tmp_path):
    admin = tmp_path / "administrative"
    admin.mkdir()
    committed = gpd.GeoDataFrame(
        {
            "state": ["Uttarakhand", "Telangana"],
            "state_code": [5, 36],
            "district": ["CHAMOLI", "ADILABAD"],
            "district_code": [57, 501],
            "abdb_district_code": [57, 532],
            "sub_district": ["GHAT", "SATHNALA"],
            "sub_district_code": pd.Series(["285", "7314"], dtype="string"),
        },
        geometry=[box(79.0, 30.0, 79.5, 30.5), box(78.5, 19.5, 79.0, 20.0)],
        crs=4326,
    )
    gpkg_path = admin / "india_subdistricts_lgd.gpkg"
    pq_path = admin / "india_subdistricts_lgd.parquet"
    committed.to_file(gpkg_path, layer="subdistricts", driver="GPKG")
    committed.to_parquet(pq_path, engine="pyarrow", compression="snappy", index=False)

    raw_shp = tmp_path / "Sub_district Boundary.shp"
    raw = gpd.GeoDataFrame(
        {
            "STATE_UT": ["UTTARAKHAND", "TELANGANA"],
            "DISTRICT": ["CHAMOLI", "ADILABAD"],
            "DIST_LGD": ["057", "532"],
            "SUB_DIST": ["GHAT", "SATHNALA"],
            "SUBDIS_LGD": ["00285N001", "07314NOO1"],
        },
        geometry=committed.geometry,
        crs=4326,
    )
    raw.to_file(raw_shp)

    summary = restore.restore_subdistrict_codes(raw_shp, data_dir=tmp_path)
    assert summary["alphanumeric_codes"] == 2
    assert summary["within_state_dup_rows"] == 0
    assert not gpkg_path.with_name(f".{gpkg_path.stem}.tmp{gpkg_path.suffix}").exists()
    assert not pq_path.with_name(f".{pq_path.stem}.tmp{pq_path.suffix}").exists()
    assert gpd.read_file(gpkg_path)["sub_district_code"].tolist() == ["00285N001", "07314NOO1"]
    assert gpd.read_parquet(pq_path)["sub_district_code"].tolist() == ["00285N001", "07314NOO1"]


# --- Integration tests: committed layers & access CSV ------------------------------------

@needs_data
def test_committed_subdistrict_layers_preserve_alphanumeric_codes_and_parity():
    gpkg = gpd.read_file(SUB_GPKG)
    parq = gpd.read_parquet(SUB_PARQUET)

    assert len(gpkg) == 6636
    assert len(parq) == 6636
    assert list(gpkg.columns) == list(parq.columns)
    assert (gpkg.geometry.to_wkb() == parq.geometry.to_wkb()).all()

    pq_field = pq.read_schema(SUB_PARQUET).field("sub_district_code")
    assert pa.types.is_string(pq_field.type) or pa.types.is_large_string(pq_field.type)

    g_codes = gpkg["sub_district_code"].astype("string")
    p_codes = parq["sub_district_code"].astype("string")
    assert ((g_codes == p_codes) | (g_codes.isna() & p_codes.isna())).all()

    coded = gpkg.dropna(subset=["sub_district_code"]).copy()
    coded_str = coded["sub_district_code"].astype("string")
    assert len(coded) == 6557
    assert int(gpkg["sub_district_code"].isna().sum()) == 79
    assert int(coded_str.nunique()) == 6554

    alnum = coded_str[coded_str.str.contains(r"[^0-9]", regex=True)]
    assert len(alnum) == 342
    n_pattern = alnum[alnum.str.fullmatch(r"\d+N\d+")]
    assert len(n_pattern) == 341
    assert set(alnum) - set(n_pattern) == {"07314NOO1"}

    # Zero within-state duplicates; national duplicates strictly the 3 documented raw collisions
    assert int(coded.assign(_code=coded_str).duplicated(["state", "_code"], keep=False).sum()) == 0
    nat_dups = set(coded_str[coded_str.duplicated(keep=False)].tolist())
    assert nat_dups == set(audit_all.EXPECTED_SUBDISTRICT_CROSS_STATE_DUP_CODES)
    assert nat_dups == {"3606", "6148", "6479"}
    assert int(coded_str.duplicated(keep=False).sum()) == 6

    # Key carveout spot-checks: Ghat, Dewal, Nandprayag no longer colliding with Kalka, Sathnala
    by_code = coded.set_index(coded_str)
    assert by_code.loc["00285N001", "sub_district"] == "GHAT"
    assert by_code.loc["00288N002", "sub_district"] == "DEWAL"
    assert by_code.loc["00356N001", "sub_district"] == "NANDPRAYAG"
    assert by_code.loc["00356N001", "state"] == "Uttarakhand"
    assert by_code.loc["356", "sub_district"] == "Kalka"
    assert by_code.loc["356", "state"] == "Haryana"
    assert by_code.loc["07314NOO1", "sub_district"] == "SATHNALA"
    assert by_code.loc["07314NOO1", "state"] == "Telangana"

    # Sangareddy Nizampet ("NEW MANDAL" in raw ABDB) is null
    niz = gpkg[
        gpkg["state"].eq("Telangana")
        & gpkg["district"].eq("SANGAREDDY")
        & gpkg["sub_district"].eq("NIZAMPET")
    ]
    assert len(niz) == 1
    assert pd.isna(niz["sub_district_code"].iloc[0])


@needs_data
def test_committed_subdistrict_access_csv_matches_gpkg_and_is_pure_lf():
    raw_bytes = SUB_ACCESS_CSV.read_bytes()
    assert b"\r" not in raw_bytes
    assert b"<NA>" not in raw_bytes

    csv_df = pd.read_csv(SUB_ACCESS_CSV, dtype=str, keep_default_na=False)
    gpkg = gpd.read_file(SUB_GPKG)

    assert len(csv_df) == 6636
    expected_codes = gpkg["sub_district_code"].astype("string").fillna("")
    assert (csv_df["sub_district_code"] == expected_codes).all()
    assert int((csv_df["sub_district_code"] == "").sum()) == 79
    assert int(csv_df["sub_district_code"].map(lambda s: bool(re.search(r"[^0-9]", s))).sum()) == 342
    assert "00285N001" in set(csv_df["sub_district_code"])
    assert "00356N001" in set(csv_df["sub_district_code"])
    assert "07314NOO1" in set(csv_df["sub_district_code"])
