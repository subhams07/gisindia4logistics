"""patch_boundary_names.py — Apply the boundary name fixes to the committed layers.

process_panindia_boundaries.fix_mojibake now also decodes the provider's '@'
(u) and backslash (i) legacy-font characters, collapses repeated spaces and
writes "SOUTH 24 PARGANAS". The raw ABDB archive is not in the repository, so
this script applies the same fixes to the committed outputs:

- india_districts_lgd (.geojson properties only, .parquet): district, remarks
- india_subdistricts_lgd (.parquet, .gpkg): sub_district, remarks;
  `district` is taken from the district layer by (state, district_code)
  (the sub-district layer spelled 295 rows differently, e.g. AIZWAL, RAYGAD),
  falling back to the fixed source name for rows without a code;
  `sub_district_type` case is normalised (TEHSIL -> Tehsil)
- district_code_crosswalk.csv and district_population_estimates.csv: district

Idempotent. Usage: python scripts/clean/patch_boundary_names.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from process_panindia_boundaries import fix_mojibake  # noqa: E402
from standardize import DATA_DIR  # noqa: E402

ADMIN = DATA_DIR / "administrative"
SUBDISTRICT_TYPES = {"TEHSIL": "Tehsil"}


def _fix(value):
    return fix_mojibake(value) if isinstance(value, str) else value


def patch_districts() -> pd.DataFrame:
    path = ADMIN / "india_districts_lgd.geojson"
    fc = json.loads(path.read_bytes())
    for feature in fc["features"]:
        props = feature["properties"]
        props["district"], props["remarks"] = _fix(props["district"]), _fix(props.get("remarks"))
    path.write_text(json.dumps(fc, separators=(",", ":")))
    pq = ADMIN / "india_districts_lgd.parquet"
    districts = gpd.read_parquet(pq)
    districts["district"] = districts["district"].map(_fix)
    districts["remarks"] = districts["remarks"].map(_fix)
    districts.to_parquet(pq, engine="pyarrow", compression="snappy", index=False)
    return districts


def patch_subdistricts(districts: pd.DataFrame) -> None:
    names = {(s, int(c)): d for s, c, d in zip(districts.state, districts.district_code, districts.district) if pd.notna(c)}
    for path in (ADMIN / "india_subdistricts_lgd.parquet", ADMIN / "india_subdistricts_lgd.gpkg"):
        sub = gpd.read_parquet(path) if path.suffix == ".parquet" else gpd.read_file(path)
        sub["district"] = [names.get((s, int(c)), _fix(d)) if pd.notna(c) else _fix(d)
                           for s, c, d in zip(sub.state, sub.district_code, sub.district)]
        sub["sub_district"] = sub["sub_district"].map(_fix)
        sub["remarks"] = sub["remarks"].map(_fix)
        sub["sub_district_type"] = sub["sub_district_type"].replace(SUBDISTRICT_TYPES)
        if path.suffix == ".parquet":
            sub.to_parquet(path, engine="pyarrow", compression="snappy", index=False)
        else:
            sub.to_file(path, layer="subdistricts", driver="GPKG")


def patch_tables(districts: pd.DataFrame) -> None:
    names = {(s, int(c)): d for s, c, d in zip(districts.state, districts.district_code, districts.district) if pd.notna(c)}
    for path in (ADMIN / "district_code_crosswalk.csv", DATA_DIR / "demographic" / "district_population_estimates.csv"):
        table = pd.read_csv(path, dtype=str, keep_default_na=False)  # rewrite the names only
        table["district"] = [names.get((s, int(float(c))), _fix(d)) if c else _fix(d)
                             for s, c, d in zip(table.state, table.district_code, table.district)]
        table.to_csv(path, index=False)


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    districts = patch_districts()
    patch_subdistricts(districts)
    patch_tables(districts)
    print("patched: india_districts_lgd (.geojson/.parquet), india_subdistricts_lgd (.parquet/.gpkg), "
          "district_code_crosswalk.csv, district_population_estimates.csv")


if __name__ == "__main__":
    main()
