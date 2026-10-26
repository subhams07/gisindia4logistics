"""Spatial district assignment, summaries, population allocation and village maps."""
import importlib
import pathlib
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon, box

ROOT = pathlib.Path(__file__).resolve().parents[1]
for sub in ("scripts/clean", "scripts/analyze"):
    if str(ROOT / sub) not in sys.path:
        sys.path.insert(0, str(ROOT / sub))

import district_assignment as da  # noqa: E402
import nearest_facility as nf  # noqa: E402

DATA = ROOT / "data"


def layer():
    """Synthetic state 'Testland' (A, B with a ~1 km gap, null-code NAZ, overlapping D5/D6) + 'Elseland'."""
    c_shape = Polygon([(0, 4), (2, 4), (2, 6), (1.8, 6), (1.8, 4.2), (0, 4.2)])  # centroid outside the shell
    rows = [
        ("Testland", "A", 1, box(0, 0, 2, 2)),
        ("Testland", "B", 2, box(2.01, 0, 4, 2)),
        ("Testland", "NAZ", None, box(0, 2.5, 2, 3.5)),
        ("Testland", "D5", 5, box(10, 0, 12, 2)),
        ("Testland", "D6", 6, box(11, 0, 13, 2)),
        ("Testland", "CSHAPE", 7, c_shape),
        ("Elseland", "E", 9, box(2.002, 0.5, 2.008, 1.0)),  # sits in the gap between A and B
    ]
    return gpd.GeoDataFrame(
        {"state": [r[0] for r in rows], "district": [r[1] for r in rows],
         "district_code": pd.array([r[2] for r in rows], dtype="Int64")},
        geometry=[r[3] for r in rows], crs=4326)


def assign(points, **kw):
    return da.assign_current_district("Testland", gpd.GeoSeries(points, crs=4326), districts=layer(), **kw)


def test_inside_edge_other_state_null_code_and_far():
    r = assign([Point(1, 1), Point(2.004, 0.2), Point(2.005, 0.75), Point(1, 3)])
    assert [r.loc[0, "district"], r.loc[1, "district"], r.loc[3, "district"]] == ["A", "A", "NAZ"]
    assert r.loc[0, "district_assignment"] == "within" and r.loc[0, "district_assignment_km"] == 0
    # edge: in the gap -> nearest polygon of the same state, small positive distance
    assert r.loc[1, "district_assignment"] == "nearest_in_state"
    assert 0 < r.loc[1, "district_assignment_km"] < 2
    # inside ANOTHER state's polygon only: still resolved inside Testland
    assert r.loc[2, "district_assignment"] == "nearest_in_state" and r.loc[2, "district"] in {"A", "B"}
    # null-code polygon keeps a null code and its name
    assert r.loc[3, "district"] == "NAZ" and pd.isna(r.loc[3, "district_code"])
    assert str(r["district_code"].dtype) == "Int64"
    with pytest.raises(ValueError, match="farther than"):
        assign([Point(50, 50)])


def test_multiple_hits_prefer_source_code_else_layer_order():
    pt = [Point(11.5, 1)]  # inside both D5 and D6
    assert assign(pt).loc[0, "district"] == "D5"
    assert assign(pt, source_codes=[6]).loc[0, "district"] == "D6"
    assert assign(pt, source_codes=[999]).loc[0, "district"] == "D5"


def test_polygon_uses_representative_point_and_index_alignment():
    c_shape_village = box(0.2, 4.05, 0.4, 4.15)  # inside the thin arm of the C
    geoms = gpd.GeoSeries([box(0.5, 0.5, 0.7, 0.7), c_shape_village], index=[10, 20], crs=4326)
    r = da.assign_current_district("Testland", geoms, districts=layer())
    assert list(r.index) == [10, 20]
    assert r["district"].tolist() == ["A", "CSHAPE"]


def test_unknown_state_and_empty_geometry_rejected():
    with pytest.raises(ValueError, match="No district polygons"):
        da.assign_current_district("Nowhere", gpd.GeoSeries([Point(0, 0)], crs=4326), districts=layer())
    with pytest.raises(ValueError, match="null or empty"):
        assign([Point()])


def test_reassign_frame_flags_placeholder_and_is_idempotent():
    df = pd.DataFrame({
        "village": ["v1", da.PLACEHOLDER_NAME], "district": ["OLD", "OLD2"], "district_code": [99, None],
        "village_code": ["1", da.PLACEHOLDER_NAME], "dist_x_km": [1.5, 2.5]})
    geoms = [Point(1, 1), Point(3, 1)]
    once = da.reassign_frame(df, "Testland", geoms, districts=layer())
    cols = list(once.columns)
    i = cols.index("district_code")
    assert cols[i + 1:i + 6] == da.NEW_COLUMNS
    assert once["source_district"].tolist() == ["OLD", "OLD2"]
    assert once["exclude_from_district_stats"].tolist() == [False, True]
    assert once["dist_x_km"].tolist() == [1.5, 2.5]
    twice = da.reassign_frame(once, "Testland", geoms, districts=layer())
    pd.testing.assert_frame_equal(once, twice)


def test_summarize_districts_exact_keys_exclusion_and_no_fuzzy():
    base = pd.DataFrame({
        "district": ["A", "A", "NAZ", "A"], "district_code": pd.array([1, 1, None, 1], dtype="Int64"),
        "dist_rail_station_km": [1.0, 3.0, 9.0, 500.0],
        "exclude_from_district_stats": [False, False, False, True]})
    est = pd.DataFrame({"state": ["Testland", "Testland"], "district": ["A", "NAZ"],
                        "district_code": [1, None], "pop_2011": [100, 10]})
    s = nf.summarize_districts(base, "Testland", [5.0], est)
    d = s[s.district != "__STATE__"]
    assert d["district_code"].notna().sum() == 1 and len(d) == 2
    a = d[d.district == "A"].iloc[0]
    assert a.villages == 2 and a.rail_station_mean_km == 2.0 and a.census2011_population == 100
    assert d[d.district == "NAZ"].iloc[0].census2011_population == 10  # exact name for the null-code polygon
    st = s[s.district == "__STATE__"].iloc[0]
    assert st.villages == 3 and st.census2011_population == 110
    # a close-but-not-equal name must NOT be matched fuzzily
    est2 = est.assign(district=["AA", "NAZ"])
    est2["district_code"] = [2, None]
    with pytest.raises(ValueError, match="no population estimate"):
        nf.summarize_districts(base, "Testland", [5.0], est2)


# ---------------------------------------------------------------- real data (fast)
needs_data = pytest.mark.skipif(not (DATA / "analysis" / "india_district_access_summary.csv").exists(),
                                reason="repository data not available")


@needs_data
def test_national_summary_keys_and_population_join():
    roll = pd.read_csv(DATA / "analysis" / "india_district_access_summary.csv")
    d = roll[roll.district != "__STATE__"]
    assert len(roll) == 816 and len(d) == 780 and (roll.district == "__STATE__").sum() == 36
    coded = d.dropna(subset=["district_code"])
    assert not coded.duplicated(["state", "district_code"]).any()
    assert not d[d.district_code.isna()].duplicated(["state", "district"]).any()
    polys = gpd.read_parquet(DATA / "administrative" / "india_districts_lgd.parquet")
    est = pd.read_csv(DATA / "demographic" / "district_population_estimates.csv")
    key = lambda df: set(zip(df.state, pd.to_numeric(df.district_code, errors="coerce").fillna(-1).astype(int),
                             np.where(pd.to_numeric(df.district_code, errors="coerce").isna(), df.district, "")))
    assert key(polys) == key(est) == key(d)
    assert d.census2011_population.notna().all()


@needs_data
def test_reassignment_preserves_distances_and_matches_committed_table():
    sys.path.insert(0, str(ROOT / "scripts" / "analyze"))
    import reassign_village_districts as rv
    path = DATA / "analysis" / "sikkim_village_access.csv"
    table = rv.read_text_table(path)
    geo = rv.load_geometry("sikkim")
    assert (rv.norm_code(geo["village_code"]).to_numpy() == rv.norm_code(table["village_code"]).to_numpy()).all()
    again = da.reassign_frame(table, "Sikkim", geo.geometry)
    dist_cols = [c for c in table.columns if c.startswith(("dist_", "nearest_"))]
    assert again[dist_cols].astype(str).equals(table[dist_cols].astype(str))
    assert again["district"].notna().all() and again["exclude_from_district_stats"].sum() == 0
    assert (again["district_assignment_km"] <= 2).all()


@needs_data
def test_village_counts_are_state_scoped_and_placeholder_free():
    import allocate_population as ap
    counts = ap.village_counts()
    assert counts[("telangana", 762)] == 343  # Mulugu: not Sakti's 471 villages
    assert ("assam", 766) not in counts       # Tamulpur has no SoI village polygons
    assert counts[("goa", 793)] == 100        # Kushavati
    assert all(k[0] == k[0].lower() for k in counts)
    est = pd.read_csv(DATA / "demographic" / "district_population_estimates.csv").set_index(["state", "district"])
    assert est.loc[("Telangana", "MULUGU"), "pop_2011"] < 1_200_000
    assert est.loc[("Assam", "TAMULPUR"), "pop_2011"] < 1_000_000
    assert est.loc[("Delhi", "SHAHDARA"), "pop_2011"] < 2_000_000


@needs_data
@pytest.mark.parametrize("state,district,code", [("Goa", "KUSHAVATI", 793), ("Odisha", "ANUGUL", 384),
                                                 ("Maharashtra", "Nashik", 516), ("Haryana", "Ambala", 70)])
def test_plot_villages_selects_by_corrected_district(state, district, code):
    from scripts.analyze.plot_villages import load_district_villages_gdf
    gdf = load_district_villages_gdf(state, district)
    acc = pd.read_csv(DATA / "analysis" / f"{state.lower().replace(' ', '_')}_village_access.csv",
                      usecols=["district_code"])
    assert len(gdf) == int((acc.district_code == code).sum()) > 0
    assert set(gdf["district_code"]) == {code} and gdf.geometry.notna().all()
    assert len(load_district_villages_gdf(state, str(code))) == len(gdf)
    with pytest.raises(ValueError, match="not found"):
        load_district_villages_gdf(state, "NO SUCH DISTRICT")
