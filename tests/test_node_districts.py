"""Unit tests for LGD district assignment on economic/agri node CSVs and audit checks."""
from __future__ import annotations

import importlib
import pathlib

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
HUBS = DATA / "logistics_hubs"
LGD_DISTRICTS_PARQUET = DATA / "administrative" / "india_districts_lgd.parquet"

needs_data = pytest.mark.skipif(
    not LGD_DISTRICTS_PARQUET.exists(),
    reason="committed LGD district parquet not present",
)


def _script(sub: str, name: str):
    """Import a script via the scripts namespace package (no sys.path edits)."""
    return importlib.import_module(f"scripts.{sub}.{name}")


@pytest.fixture(scope="module")
def assign_mod():
    return _script("clean", "assign_node_districts")


@pytest.fixture(scope="module")
def audit_mod():
    return _script("audit", "audit_all")


@pytest.fixture(scope="module")
def synthetic_raw_districts() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "state": ["StateA", "StateB"],
            "district": ["DIST_ONE", "DIST_TWO"],
            "district_code": [101, 202],
        },
        geometry=[box(75.0, 20.0, 76.0, 21.0), box(76.0, 20.0, 77.0, 21.0)],
        crs=4326,
    )


@pytest.fixture(scope="module")
def synthetic_poly_districts(synthetic_raw_districts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return synthetic_raw_districts.rename(
        columns={"state": "poly_state", "district": "poly_district", "district_code": "poly_code"}
    )


@pytest.fixture(scope="module")
def committed_districts() -> gpd.GeoDataFrame:
    return gpd.read_parquet(
        LGD_DISTRICTS_PARQUET,
        columns=["state", "district", "district_code", "geometry"],
    )


# --- assign_table / assign_file ----------------------------------------------------------

def test_assign_table_sets_lgd_district_and_code_and_is_idempotent(assign_mod, synthetic_poly_districts):
    raw = pd.DataFrame(
        [
            {"name": "Node 1", "state": "StateA", "district": "Old Name", "latitude": "20.5", "longitude": "75.5"},
            {"name": "Node 2", "state": "StateB", "district": "Stale", "latitude": "20.5", "longitude": "76.5"},
        ]
    )
    first = assign_mod.assign_table(raw, synthetic_poly_districts, label="test")

    assert "district_code" not in raw.columns  # input DataFrame not mutated
    assert list(first.columns) == ["name", "state", "district", "district_code", "latitude", "longitude"]
    assert first["district"].tolist() == ["DIST_ONE", "DIST_TWO"]
    assert first["district_code"].tolist() == ["101", "202"]

    second = assign_mod.assign_table(first, synthetic_poly_districts, label="test")
    pd.testing.assert_frame_equal(first, second)


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_assign_file_updates_in_place_preserves_eol_and_is_idempotent(
    assign_mod, synthetic_poly_districts, tmp_path, eol
):
    csv_path = tmp_path / "nodes.csv"
    df = pd.DataFrame(
        [{"name": "Node 1", "state": "StateA", "district": "Legacy", "latitude": "20.5", "longitude": "75.5"}]
    )
    df.to_csv(csv_path, index=False, lineterminator=eol)

    count = assign_mod.assign_file(csv_path, districts=synthetic_poly_districts)
    assert count == 1
    after_first = csv_path.read_bytes()
    assert (b"\r\n" in after_first) is (eol == "\r\n")

    parsed = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    assert parsed.loc[0, "district"] == "DIST_ONE"
    assert parsed.loc[0, "district_code"] == "101"

    assign_mod.assign_file(csv_path, districts=synthetic_poly_districts)
    assert csv_path.read_bytes() == after_first


@pytest.mark.parametrize(
    "row,match",
    [
        # Point lies in StateB polygon while declaring StateA
        ({"name": "WrongState", "state": "StateA", "district": "X", "latitude": "20.5", "longitude": "76.5"}, "outside their own state"),
        # Point outside all polygons
        ({"name": "Ocean", "state": "StateA", "district": "X", "latitude": "10.0", "longitude": "70.0"}, "outside their own state"),
        # Blank coordinates
        ({"name": "BlankLat", "state": "StateA", "district": "X", "latitude": "", "longitude": "75.5"}, "missing/invalid coordinates"),
        ({"name": "BlankLon", "state": "StateA", "district": "X", "latitude": "20.5", "longitude": ""}, "missing/invalid coordinates"),
        # Non-numeric coordinates
        ({"name": "BadLat", "state": "StateA", "district": "X", "latitude": "not_a_num", "longitude": "75.5"}, "missing/invalid coordinates"),
        ({"name": "BadLon", "state": "StateA", "district": "X", "latitude": "20.5", "longitude": "N/A"}, "missing/invalid coordinates"),
    ],
)
def test_assign_table_rejects_bad_coords_and_out_of_state(assign_mod, synthetic_poly_districts, row, match):
    with pytest.raises(ValueError, match=match):
        assign_mod.assign_table(pd.DataFrame([row]), synthetic_poly_districts, label="bad")


# --- _node_district_mismatches -----------------------------------------------------------

def test_node_district_mismatches_detects_all_failure_modes(audit_mod, synthetic_raw_districts):
    valid = pd.DataFrame(
        [
            {
                "name": "OkNode",
                "state": "StateA",
                "district": "DIST_ONE",
                "district_code": "101",
                "latitude": "20.5",
                "longitude": "75.5",
            }
        ]
    )
    assert audit_mod._node_district_mismatches(valid, DATA, districts=synthetic_raw_districts) == []

    # 1. Missing district_code column
    no_code = valid.drop(columns=["district_code"])
    assert audit_mod._node_district_mismatches(no_code, DATA, districts=synthetic_raw_districts) == [
        "no district_code column"
    ]

    # 2. Wrong district name
    wrong_name = valid.assign(district="WRONG_DIST")
    res = audit_mod._node_district_mismatches(wrong_name, DATA, districts=synthetic_raw_districts)
    assert len(res) == 1 and "OkNode" in res[0] and "WRONG_DIST" in res[0]

    # 3. Wrong district_code
    wrong_code = valid.assign(district_code="999")
    res = audit_mod._node_district_mismatches(wrong_code, DATA, districts=synthetic_raw_districts)
    assert len(res) == 1 and "OkNode" in res[0] and "999" in res[0]

    # 4. Out-of-state point (inside StateB polygon but declaring StateA)
    out_of_state = valid.assign(longitude="76.5")
    res = audit_mod._node_district_mismatches(out_of_state, DATA, districts=synthetic_raw_districts)
    assert len(res) == 1 and "OkNode" in res[0] and "StateB" in res[0]

    # 5. Outside all polygons
    outside_all = valid.assign(latitude="10.0", longitude="70.0")
    res = audit_mod._node_district_mismatches(outside_all, DATA, districts=synthetic_raw_districts)
    assert len(res) == 1 and "OkNode" in res[0]

    # 6. Invalid coordinates (blank and non-numeric)
    for bad_df in (valid.assign(latitude=""), valid.assign(longitude="abc")):
        res = audit_mod._node_district_mismatches(bad_df, DATA, districts=synthetic_raw_districts)
        assert res == ["OkNode: invalid coordinates"]


@needs_data
@pytest.mark.parametrize(
    "stem,expected_rows",
    [
        ("industrial_nodes", 20),
        ("pm_mitra_parks", 7),
        ("cold_chain_storages", 15),
        ("enam_mandis", 16),
    ],
)
def test_committed_node_csvs_pass_district_audit(audit_mod, committed_districts, stem, expected_rows):
    path = HUBS / f"{stem}.csv"
    table = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert len(table) == expected_rows
    assert b"\r" not in path.read_bytes()
    assert audit_mod._node_district_mismatches(table, DATA, districts=committed_districts) == []


# --- Builders integrate assign_file directly ---------------------------------------------

@needs_data
def test_build_industrial_nodes_passes_audit_directly(audit_mod, committed_districts, tmp_path):
    mod = _script("fetch", "build_industrial_nodes")
    mod.build_industrial_nodes(out_dir=tmp_path, data_dir=DATA)

    for stem, expected_rows in (("industrial_nodes", 20), ("pm_mitra_parks", 7)):
        path = tmp_path / f"{stem}.csv"
        assert path.exists()
        assert b"\r" not in path.read_bytes()
        table = pd.read_csv(path, dtype=str, keep_default_na=False)
        assert len(table) == expected_rows
        assert audit_mod._node_district_mismatches(table, DATA, districts=committed_districts) == []

    nodes = pd.read_csv(tmp_path / "industrial_nodes.csv", dtype=str, keep_default_na=False).set_index("name")
    prayagraj = nodes.loc["Integrated Manufacturing Cluster (IMC) Prayagraj"]
    assert prayagraj["district"] == "PRAYAGRAJ"
    assert prayagraj["district_code"] == "120"
    assert prayagraj["area_acres"] == "352"
    assert nodes.loc["Orvakal Industrial Node", "corridor"] == "HBIC"


@needs_data
def test_build_agri_passes_audit_directly(audit_mod, committed_districts, tmp_path):
    mod = _script("fetch", "build_agri_logistics")
    mod.build_agri(out_dir=tmp_path, data_dir=DATA)

    for stem, expected_rows in (("cold_chain_storages", 15), ("enam_mandis", 16)):
        path = tmp_path / f"{stem}.csv"
        assert path.exists()
        assert b"\r" not in path.read_bytes()
        table = pd.read_csv(path, dtype=str, keep_default_na=False)
        assert len(table) == expected_rows
        assert audit_mod._node_district_mismatches(table, DATA, districts=committed_districts) == []

    cold = pd.read_csv(tmp_path / "cold_chain_storages.csv", dtype=str, keep_default_na=False).set_index("name")
    assert cold.loc["Bengaluru Rural Cold Hub (Hosakote)", "district"] == "Bengaluru (Rural)"
    assert cold.loc["Bengaluru Rural Cold Hub (Hosakote)", "district_code"] == "526"
