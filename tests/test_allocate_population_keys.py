"""allocate_population keys: one identity per current district and per 2011 parent."""
import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location("allocate_population", ROOT / "scripts/analyze/allocate_population.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_uncoded_districts_get_their_own_key():
    ap = _module()
    keys = [ap.district_key("Delhi", "NAZUL", pd.NA), ap.district_key("Delhi", "SHAHDARA", pd.NA),
            ap.district_key("Jammu and Kashmir", "MIRPUR", None), ap.district_key("Jammu and Kashmir", "MUZAFFARABAD", None),
            ap.district_key("Delhi", "EAST", 78)]
    assert len(set(keys)) == 5
    assert keys[-1] == "code:78"


def test_every_current_district_has_a_distinct_key():
    ap = _module()
    districts = pd.read_parquet(ROOT / "data/administrative/india_districts_lgd.parquet",
                                columns=["state", "district", "district_code"])
    keys = [ap.district_key(s, d, c) for s, d, c in zip(districts.state, districts.district, districts.district_code)]
    assert len(set(keys)) == len(districts) == 780


def test_2011_parents_with_the_same_name_in_two_states_stay_separate():
    ap = _module()
    parents = ap.load_parents()
    assert parents.p_key.is_unique and len(parents) == 640
    for state, name in [("delhi", "east"), ("sikkim", "east"), ("bihar", "aurangabad"), ("maharashtra", "aurangabad")]:
        assert f"{state}|{name}" in set(parents.p_key)


def test_sikkim_estimates_follow_their_2011_parents():
    """Same-name pooling with Delhi's East/North/South/West gave Mangan 12,481 (North Sikkim: 43,709)."""
    est = pd.read_csv(ROOT / "data/demographic/district_population_estimates.csv").set_index(["state", "district"]).pop_2011
    sikkim = est.loc["Sikkim"]
    assert abs(sikkim.sum() - 610577) <= 2  # Census 2011 total; districts are rounded after scaling
    assert abs(sikkim["MANGAN"] - 43709) / 43709 < 0.1
    assert abs(sikkim["GANGTOK"] + sikkim["PAKYONG"] - 283583) / 283583 < 0.05


def test_state_groups_cover_the_2011_and_current_names():
    ap = _module()
    assert ap.group_key("Telangana") == ap.group_key("Andhra Pradesh") == "andhra pradesh"
    assert ap.group_key("Ladakh") == "jammu and kashmir"
    # The Census table already uses the current DNH&DD name; it must land in the same group
    assert ap.group_key("Dadra and Nagar Haveli and Daman and Diu") == ap.group_key("Daman and Diu") == "dadra group"
    assert ap.group_key("Kerala") == "kerala"


def test_blend_rules():
    ap = _module()
    df = pd.DataFrame({"is_exact": [True, False, False, False],
                       "pop_area_share": [100.0, 100.0, 100.0, 100.0],
                       "pop_vill_share": [900.0, 300.0, 0.0, None]})
    out = ap.blend_estimates(df)
    assert out.method.tolist() == ["census2011", "blended", "area_share", "area_share"]
    assert out.pop_2011.tolist() == [100.0, 200.0, 100.0, 100.0]


def test_scaling_is_joint_per_group_and_matches_each_target():
    ap = _module()
    df = pd.DataFrame({"state": ["Andhra Pradesh", "Telangana", "Dadra and Nagar Haveli and Daman and Diu", "Kerala", "Goa"],
                       "pop_2011": [60.0, 40.0, 50.0, 30.0, 10.0]})
    targets = pd.Series({"andhra pradesh": 200.0, "dadra and nagar haveli and daman and diu": 75.0, "kerala": 30.0})
    out = ap.scale_to_state_totals(df, targets).set_index("state").pop_2011
    assert out["Andhra Pradesh"] + out["Telangana"] == 200.0 and out["Andhra Pradesh"] == 120.0  # shared target, shares kept
    assert out["Dadra and Nagar Haveli and Daman and Diu"] == 75.0  # was left unscaled before the fix
    assert out["Kerala"] == 30.0 and out["Goa"] == 10.0  # no target: unchanged


def test_committed_estimates_match_the_census_total():
    est = pd.read_csv(ROOT / "data/demographic/district_population_estimates.csv")
    census = pd.read_csv(ROOT / "data/demographic/census2011_district_key_indicators.csv")
    assert abs(est.pop_2011.sum() - census.Population.sum()) <= 10  # rounding only
    dnh = est[est.state == "Dadra and Nagar Haveli and Daman and Diu"].pop_2011.sum()
    assert dnh == census[census.state == "Dadra and Nagar Haveli and Daman and Diu"].Population.sum()
