"""Download and standardize India state/district boundaries from DataMeet maps.

Source: https://github.com/datameet/maps (CC BY 4.0).
States layer is current (36 states/UTs); district layer is Census 2011 vintage
(640 districts after dropping one placeholder feature), which joins cleanly to
the Census 2011 demographic tables in this repo.

Outputs (committed, simplified to ~100 m tolerance to stay under 10 MB):
    data/administrative/india_states.geojson
    data/administrative/india_districts.geojson
    data/administrative/census2011_lgd_crosswalk.csv

Usage:
    python scripts/fetch/fetch_admin_boundaries.py [--keep-raw]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import geopandas as gpd
import pandas as pd
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import LGD_STATE_CODES, clean_state_name, DATA_DIR, REPO_ROOT  # noqa: E402

DATA_URL = "https://github.com/datameet/maps/archive/refs/heads/master.zip"
RAW_DIR = DATA_DIR / "raw" / "admin"
OUT_DIR = DATA_DIR / "administrative"
SIMPLIFY_TOL = 0.001  # degrees, ~100 m

DISTRICT_NAME_FIXES = {
    "nicobar": "nicobars", "marigaon": "morigaon", "chamrajnagar": "chamarajanagar",
    "bauda": "baudh", "lawangtlai": "lawngtlai", "garhchiroli": "gadchiroli",
    "nagappattinam": "nagapattinam", "virudunagar": "virudhunagar",
    "kansiram nagar": "kanshiram nagar", "maharajganj": "mahrajganj",
    "siddharth nagar": "siddharthnagar", "ri bhoi": "ribhoi",
    "pashchim medinipur": "paschim medinipur", "puducherry": "puducherry",
}


def clean_district_name(name: str) -> str:
    """Normalize spelling variants so boundary names join with Census tables."""
    if name is None:
        return None
    n = re.sub(r"\s+", " ", str(name).strip().lower())
    n = DISTRICT_NAME_FIXES.get(n, n)
    n = n.replace(" & ", " and ")
    n = re.sub(r"\s*\([^)]*\)", "", n)  # drop parenthetical: "saran (chhapra)" -> "saran"
    return n.strip()


def download() -> pathlib.Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / "maps-master.zip"
    if not dest.exists():
        print(f"Downloading {DATA_URL} (~230 MB) ...")
        r = requests.get(DATA_URL, timeout=600)
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest


def write_geojson(path: pathlib.Path, gdf: gpd.GeoDataFrame, ndigits: int = 5) -> None:
    """Write GeoJSON with coordinates rounded to `ndigits` (~1 m at 5 decimals)
    — geopandas' writer emits 15 decimals, which bloats file size 3-4x."""
    import json

    def round_coords(obj):
        if isinstance(obj, (list, tuple)):
            if obj and all(isinstance(v, (int, float)) for v in obj):
                return [round(v, ndigits) for v in obj]
            return [round_coords(v) for v in obj]
        return obj

    fc = json.loads(gdf.to_json())
    for f in fc["features"]:
        f["geometry"]["coordinates"] = round_coords(f["geometry"]["coordinates"])
    path.write_text(json.dumps(fc, separators=(",", ":")))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep-raw", action="store_true",
                    help="keep the downloaded zip (default: delete after processing)")
    args = ap.parse_args()

    zpath = download()
    zp = zpath.resolve().as_posix()
    states = gpd.read_file(f"zip://{zp}!maps-master/States/Admin2.shp")
    districts = gpd.read_file(f"zip://{zp}!maps-master/Districts/Census_2011/2011_Dist.shp")

    # --- states ---
    states["state"] = states["ST_NM"].map(clean_state_name).str.title()
    assert states["state"].notna().all(), states.loc[states["state"].isna(), "ST_NM"]
    states["state_code"] = states["state"].str.lower().map(LGD_STATE_CODES)
    states = states[["state", "state_code", "geometry"]].sort_values("state")
    states = states.to_crs(4326).simplify(SIMPLIFY_TOL) if states.crs != 4326 else states
    states.geometry = states.geometry.simplify(SIMPLIFY_TOL)

    # --- districts ---
    n_raw = len(districts)
    districts = districts[districts["DISTRICT"].str.upper() != "DATA NOT AVAILABLE"].copy()
    districts["state"] = districts["ST_NM"].map(clean_state_name).str.title()
    assert districts["state"].notna().all(), districts.loc[districts["state"].isna(), "ST_NM"]
    districts["state_code"] = districts["state"].str.lower().map(LGD_STATE_CODES)
    districts["district"] = districts["DISTRICT"].str.title().map(
        lambda x: DISTRICT_NAME_FIXES.get(x.lower(), x).title() if x else x)
    districts["district_key"] = districts["DISTRICT"].map(clean_district_name)
    districts["census_state_code"] = districts["ST_CEN_CD"]
    districts["census_district_code"] = districts["DT_CEN_CD"]
    districts.geometry = districts.geometry.simplify(SIMPLIFY_TOL)
    districts = districts[["state", "state_code", "district", "district_key",
                           "census_state_code", "census_district_code", "geometry"]]
    districts = districts.sort_values(["state", "district"])

    # --- validation ---
    checks = {
        "states == 36": len(states) == 36,
        "districts == 640 (2011)": len(districts) == 640,
        "dropped placeholder features": n_raw - len(districts),
        "all geometries valid": states.geometry.is_valid.all() and districts.geometry.is_valid.all(),
        "no duplicate state/district": not districts.duplicated(["state", "district_key"]).any(),
    }
    for k, v in checks.items():
        print(f"  {k}: {v}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_geojson(OUT_DIR / "india_states.geojson", states)
    write_geojson(OUT_DIR / "india_districts.geojson", districts)

    # crosswalk: (state, district_key) -> LGD state code + census codes
    xw = districts.drop(columns="geometry")
    xw.to_csv(OUT_DIR / "census2011_lgd_crosswalk.csv", index=False)

    for f in ("india_states.geojson", "india_districts.geojson"):
        size = (OUT_DIR / f).stat().st_size / 1e6
        print(f"  wrote {f} ({size:.1f} MB)")

    if not args.keep_raw and zpath.exists():
        zpath.unlink()


if __name__ == "__main__":
    main()
