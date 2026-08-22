"""Standardize the PAN INDIA State/District/Sub-district boundary dataset.

Input: LGD-coded shapefiles (national LCC projection) provided by the repo
maintainer — schema matches Survey of India's administrative boundary
database (STATE_LGD / DIST_LGD / SUBDIS_LGD columns). Source archive:
data/raw/panindia/State_District_Subdistrict_PAN INDIA.rar
(not committed; place the extracted folder under data/raw/panindia/)

Outputs (committed, simplified):
    data/administrative/india_states_lgd.geojson        (~36 features, current)
    data/administrative/india_districts_lgd.geojson     (~800, current districts)
    data/administrative/india_subdistricts_lgd.geojson  (~6,600 talukas/tehsils)

These are CURRENT administrative units with LGD codes — they complement the
Census-2011-vintage district file (india_districts.geojson) used for joining
Census tables. 'DISPUTED' inter-state slivers are kept out of the states layer
and flagged in district/subdistrict layers via `remarks`.

Usage:
    python scripts/clean/process_panindia_boundaries.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import geopandas as gpd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from standardize import DATA_DIR, clean_state_name, write_geojson  # noqa: E402

SRC = DATA_DIR / "raw" / "panindia" / "State_District_Subdistrict_PAN INDIA"
OUT = DATA_DIR / "administrative"

SCHEMAS = {
    "states": {
        "path": SRC / "State Boundary" / "State Boundary.shp",
        "rename": {"STATE": "state"},
        "keep": ["state", "geometry"],
    },
    "districts": {
        "path": SRC / "District_Subdistrict_PAN INDIA" / "District Boundary.shp",
        "rename": {"STATE_UT": "state", "STATE_LGD": "state_code",
                   "DISTRICT": "district", "DIST_LGD": "district_code",
                   "REMARKS": "remarks"},
        "keep": ["state", "state_code", "district", "district_code", "remarks", "geometry"],
    },
    "subdistricts": {
        "path": SRC / "District_Subdistrict_PAN INDIA" / "Sub_district Boundary.shp",
        "rename": {"STATE_UT": "state", "STATE_LGD": "state_code",
                   "DISTRICT": "district", "DIST_LGD": "district_code",
                   "SUB_DIST": "sub_district", "SUBDIS_LGD": "sub_district_code",
                   "SUBDIS_TYP": "sub_district_type", "REMARKS": "remarks"},
        "keep": ["state", "state_code", "district", "district_code", "sub_district",
                 "sub_district_code", "sub_district_type", "remarks", "geometry"],
    },
}

# per-layer simplification tolerance (degrees) tuned to keep files < 10 MB
SIMPLIFY = {"states": 0.001, "districts": 0.0012, "subdistricts": 0.0012}

# provider's export mangled some name characters (legacy-font artifact):
# '<' stands for 'a', '>' for 'A', '#' for 'u' (e.g. BR>HMAUR=BRAHMAUR,
# Dh<rw<d=DHARWAD, Bengal#ru=BENGALURU) — verified on samples across states
NAME_CHAR_FIXES = {"<": "a", ">": "A", "#": "u"}


def fix_mojibake(s):
    import pandas as pd
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return s
    s = str(s)
    for bad, good in NAME_CHAR_FIXES.items():
        s = s.replace(bad, good)
    return s


def process(layer: str) -> gpd.GeoDataFrame:
    cfg = SCHEMAS[layer]
    gdf = gpd.read_file(cfg["path"]).rename(columns=cfg["rename"])
    gdf = gdf.to_crs(4326)

    # drop inter-state disputed slivers: named in the states layer, and in
    # district/subdistrict layers as rows with no state/district and a
    # REMARKS like "DISPUTED (RAJSTHAN & GUJRAT)"
    if layer == "states":
        n_disputed = int(gdf["state"].str.contains("DISPUTED", na=False).sum())
        gdf = gdf[~gdf["state"].str.contains("DISPUTED", na=False)]
    else:
        rem = gdf["remarks"].astype(str)
        n_disputed = int((gdf["state"].isna() & rem.str.contains("DISPUTED", na=False)).sum())
        gdf = gdf[gdf["state"].notna()]
    gdf["state"] = gdf["state"].map(clean_state_name).str.title()
    n_unmapped = int(gdf["state"].isna().sum())
    assert n_unmapped == 0, f"[{layer}] unmapped state names remain"

    for col in ("district", "sub_district"):
        if col in gdf:
            gdf[col] = gdf[col].map(fix_mojibake)
    if "remarks" in gdf:
        gdf["remarks"] = gdf["remarks"].map(fix_mojibake)

    # PoK districts (Mirpur, Muzaffarabad) carry "NOT AVAILABLE" LGD codes
    for col in ("district_code", "sub_district_code"):
        if col in gdf:
            gdf[col] = gdf[col].mask(
                gdf[col].astype(str).str.upper().eq("NOT AVAILABLE"))

    print(f"[{layer}] raw={len(gdf) + n_disputed} disputed_dropped={n_disputed} kept={len(gdf)}")
    gdf.geometry = gdf.geometry.simplify(SIMPLIFY[layer])
    gdf = gdf[cfg["keep"]].sort_values(
        [c for c in ("state", "district", "sub_district") if c in gdf.columns]).reset_index(drop=True)
    return gdf


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for layer, fname in (("states", "india_states_lgd.geojson"),
                         ("districts", "india_districts_lgd.geojson"),
                         ("subdistricts", "india_subdistricts_lgd.gpkg")):
        gdf = process(layer)
        key = {"states": "state", "districts": "district_code",
               "subdistricts": "sub_district_code"}[layer]
        dup = int(gdf[key].duplicated().sum()) if key in gdf else 0
        if fname.endswith(".gpkg"):
            path = OUT / fname
            gdf.to_file(path, layer="subdistricts", driver="GPKG")  # large layer -> Git LFS
        else:
            fc = json.loads(gdf.to_json())
            fc["name"] = fname.replace(".geojson", "")
            fc["attribution"] = ("Administrative boundaries (LGD-coded, current): "
                                 "PAN INDIA dataset via repo maintainer; "
                                 "schema consistent with Survey of India / LGD")
            path = OUT / fname
            write_geojson(path, fc)
        print(f"[{layer}] wrote {len(gdf)} features, dup {key}={dup}, "
              f"valid_geom={bool(gdf.geometry.is_valid.all())}, "
              f"{path.stat().st_size/1e6:.1f} MB -> {path}")


if __name__ == "__main__":
    main()
