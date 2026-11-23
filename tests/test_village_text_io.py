"""Village codes and text survive reading on pandas 2 and 3 (explicit dtypes, no default NA tokens)."""
import importlib.util
from pathlib import Path

import pandas as pd

from scripts.analyze.village_access_io import read_village_access_csv

ROOT = Path(__file__).resolve().parents[1]


def _normaliser():
    spec = importlib.util.spec_from_file_location("normalise", ROOT / "scripts/clean/normalize_village_properties.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codes_keep_leading_zeros_and_text_is_not_parsed_as_missing(tmp_path):
    path = tmp_path / "x_village_access.csv"
    path.write_text("village,district,district_code,village_code,source_district_code,nearest_port,dist_port_km\n"
                    "NAN,Jaipur,110,071955,99,Mumbai Port,10.5\n"
                    "None,Jaipur,110,,99,Mumbai Port,\n", encoding="utf-8")
    frame = read_village_access_csv(path)
    assert frame.village_code.tolist()[0] == "071955"
    assert pd.isna(frame.village_code.iloc[1])
    assert frame.village.tolist() == ["NAN", "None"]  # real names, not missing values
    assert str(frame.district_code.dtype) == "Int64" and frame.district_code.tolist() == [110, 110]
    assert frame.dist_port_km.dtype == "float64" and pd.isna(frame.dist_port_km.iloc[1])


def test_committed_zero_padded_codes_survive_the_api_reader():
    raw = pd.read_csv(ROOT / "data/analysis/himachal_pradesh_village_access.csv", dtype=str, keep_default_na=False)
    padded = raw.village_code.str.match(r"^0\d").sum()
    assert padded > 0
    frame = read_village_access_csv(ROOT / "data/analysis/himachal_pradesh_village_access.csv")
    assert frame.village_code.fillna("").tolist() == raw.village_code.tolist()


def test_not_available_markers_become_null_but_similar_names_stay():
    module = _normaliser()
    props = {"state": "PUNJAB", "village": "NONE", "village_code": "Not available", "village_category": "NA",
             "sub_district_code": "<Null>", "district": " Tarn  Taran\r\n", "district_code": 37}
    out = module.normalise_properties(props, "Punjab")
    assert out == {"state": "Punjab", "village": "NONE", "village_code": None, "village_category": None,
                   "sub_district_code": None, "district": "Tarn Taran", "district_code": "37"}
    assert list(out) == list(props)
