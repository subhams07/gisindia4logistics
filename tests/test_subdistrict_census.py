"""Unit tests for the Census 2011 Sub-District PCA & Table A-01 dataset."""
import pathlib

import pandas as pd
import pytest

from scripts.fetch import fetch_subdistrict_census2011 as fsc

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SUBDIST_CSV = DATA / "demographic" / "census2011_subdistrict_key_indicators.csv"
DIST_CSV = DATA / "demographic" / "census2011_district_key_indicators.csv"

needs_data = pytest.mark.skipif(
    not (SUBDIST_CSV.exists() and DIST_CSV.exists()),
    reason="committed Census 2011 CSVs not present",
)


@pytest.fixture(scope="module")
def committed_tables():
    return fsc.read_subdistrict_csv(SUBDIST_CSV), pd.read_csv(DIST_CSV)


@needs_data
def test_committed_subdistrict_csv_format_and_keys(committed_tables):
    df, _ = committed_tables
    raw = SUBDIST_CSV.read_bytes()
    assert b"\r" not in raw, "CSV must use pure LF line endings"
    assert list(df.columns) == fsc.OUTPUT_COLUMNS
    assert len(df) == 5988
    assert (df["census_state_code"].str.len() == 2).all()
    assert (df["census_district_code"].str.len() == 3).all()
    assert (df["census_subdistrict_code"].str.len() == 5).all()
    assert int(df["is_non_subdistrict_area"].sum()) == 30
    assert (df["is_non_subdistrict_area"] == (df["census_subdistrict_code"] == "99999")).all()


@needs_data
def test_validation_checks_pass_on_committed_table(committed_tables):
    df, dist_df = committed_tables
    checks = fsc.validation_checks(df, dist_df)
    assert all(checks.values()), {k: v for k, v in checks.items() if not v}


@needs_data
def test_validation_fails_on_dropped_row(committed_tables):
    df, dist_df = committed_tables
    checks = fsc.validation_checks(df.iloc[1:].copy(), dist_df)
    assert not checks["subdistricts == 5988"]
    assert not checks["population == 1,210,854,977"]
    assert not checks["640/640 districts match district table"]


@needs_data
def test_validation_fails_on_population_plus_one(committed_tables):
    df, dist_df = committed_tables
    broken = df.copy()
    broken.loc[0, "population"] += 1
    checks = fsc.validation_checks(broken, dist_df)
    assert not checks["population == 1,210,854,977"]
    assert not checks["male + female == population"]
    assert not checks["rural + urban == population"]
    assert not checks["640/640 districts match district table"]


@needs_data
def test_validation_fails_on_male_female_mismatch(committed_tables):
    df, dist_df = committed_tables
    broken = df.copy()
    broken.loc[0, "male"] += 1
    checks = fsc.validation_checks(broken, dist_df)
    assert not checks["male + female == population"]
    assert checks["population == 1,210,854,977"]


@needs_data
def test_validation_fails_on_rural_urban_mismatch(committed_tables):
    df, dist_df = committed_tables
    broken = df.copy()
    broken.loc[0, "rural_population"] += 1
    checks = fsc.validation_checks(broken, dist_df)
    assert not checks["rural + urban == population"]
    assert checks["population == 1,210,854,977"]


@needs_data
def test_validation_fails_on_duplicate_composite_key(committed_tables):
    df, dist_df = committed_tables
    broken = df.copy()
    for col in fsc.COMPOSITE_KEY:
        broken.loc[1, col] = broken.loc[0, col]
    checks = fsc.validation_checks(broken, dist_df)
    assert not checks["composite key unique"]


@needs_data
def test_validation_fails_on_district_reconciliation_shift(committed_tables):
    df, dist_df = committed_tables
    broken = df.copy()
    # Pick two rows in different districts and shift 1 person between them
    idx_a = 0
    idx_b = int(broken.index[broken["census_district_code"] != broken.loc[idx_a, "census_district_code"]][0])
    for col in ("population", "male", "rural_population", "literates", "non_workers"):
        broken.loc[idx_a, col] += 1
        broken.loc[idx_b, col] -= 1
    checks = fsc.validation_checks(broken, dist_df)
    assert checks["population == 1,210,854,977"]
    assert checks["male + female == population"]
    assert checks["rural + urban == population"]
    assert not checks["640/640 districts match district table"]
