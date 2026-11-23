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
