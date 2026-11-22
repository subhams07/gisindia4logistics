"""Standardize Survey of India village boundaries published on the National Water Data Portal.

Source: "Village Boundary" dataset, National Water Data Portal (National Water
Informatics Centre, Ministry of Jal Shakti),
https://nwdp.nwic.gov.in/dataset/9bad17f2-9d88-428d-98ad-831ef01ae2e4 — one
GeoJSON zip per state (vb_soi_<ab>_geojson.zip), source agency "Survey of
India (SOI)", EPSG:7755, with LGD state/district codes, Census 2011 village
codes and village-level Census 2011 population.

It covers the nine states the SoI village portal does not publish (Arunachal
Pradesh, Assam, Himachal Pradesh, Jammu & Kashmir, Ladakh, Manipur, Meghalaya,
Mizoram, Nagaland). The portal's TLS certificate has expired, so the zips are
downloaded by hand into data/raw/nwdp_villages/ (gitignored); this script only
standardizes them. Treated as Government of India open data with attribution
(see docs/legal_compliance.md).

Output per state, same schema and geometry pipeline as
fetch_village_boundaries_soi.py plus population_2011 and households_2011:
    data/administrative/villages/<state>_soi_villages.geojson
    data/administrative/villages_parquet/<state>_soi_villages.parquet

Usage:
    python scripts/fetch/fetch_village_boundaries_nwdp.py            # the nine states
    python scripts/fetch/fetch_village_boundaries_nwdp.py --state Assam
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
import zipfile

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from standardize import DATA_DIR, write_geojson  # noqa: E402
from fetch_village_boundaries_soi import robust_prep  # noqa: E402

RAW_DIR = DATA_DIR / "raw" / "nwdp_villages"
NINE_STATES = {
    "ar": "Arunachal Pradesh", "as": "Assam", "hp": "Himachal Pradesh",
    "jk": "Jammu and Kashmir", "lk": "Ladakh", "ml": "Meghalaya",
    "mn": "Manipur", "mz": "Mizoram", "nl": "Nagaland",
}
NO_CODE = "999999"  # NWDP's village code for forest blocks and "Data Not Available" polygons
ATTRIBUTION = ("Village boundaries: Survey of India, via the National Water Data Portal "
               "(National Water Informatics Centre, Ministry of Jal Shakti), Government of India")


def code(series: pd.Series) -> pd.Series:
    """Zero-padded code strings ('024') -> '24'; blanks -> None (matches the SoI village files)."""
    num = pd.to_numeric(series.astype(str).str.strip(), errors="coerce")
    return num.map(lambda v: None if pd.isna(v) else str(int(v)))


def load_standardized(zip_path: pathlib.Path, state: str) -> gpd.GeoDataFrame:
    with zipfile.ZipFile(zip_path) as zf, tempfile.TemporaryDirectory() as td:
        member = next(n for n in zf.namelist() if n.lower().endswith(".geojson"))
        zf.extract(member, td)
        raw = gpd.read_file(pathlib.Path(td) / member)
    raw.columns = [c.strip() for c in raw.columns]  # many NWDP column names end in "\n"
    if set(raw.src_agency.unique()) != {"Survey of India (SOI)"}:
        raise SystemExit(f"{zip_path.name}: unexpected source agency {raw.src_agency.unique()}")
    village_code = raw.vlcode.astype(str).str.strip()
    category = raw.total_urban_rural.astype(str).str.strip()
    gdf = gpd.GeoDataFrame({
        "state": state,
        "state_code": code(raw.stcode),
        "district": raw.district.astype(str).str.strip(),
        "district_code": code(raw.dtcode),
        "sub_district": raw.subdistric.astype(str).str.strip().replace({"Data Not Available": None}),
        "sub_district_code": code(raw.sdcode).where(raw.sdcode.astype(str).str.strip() != "9999"),
        "village": raw.village.astype(str).str.strip(),
        "village_category": category.where(category.isin(["Rural", "Urban"])),
        "village_code": village_code.where(village_code != NO_CODE),
        "population_2011": pd.to_numeric(raw.total_population_village, errors="coerce").round().astype("Int64"),
        "households_2011": pd.to_numeric(raw.total_households, errors="coerce").round().astype("Int64"),
    }, geometry=raw.geometry, crs=raw.crs)
    return gdf.to_crs(4326)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", help="one of the nine states (default: all nine)")
    ap.add_argument("--simplify", type=float, default=0.0005, metavar="TOL",
                    help="simplification tolerance in degrees (default 0.0005 ~ 50 m, as for the SoI files)")
    args = ap.parse_args()
    todo = {ab: st for ab, st in NINE_STATES.items() if args.state in (None, st)}
    if not todo:
        raise SystemExit(f"--state must be one of: {', '.join(NINE_STATES.values())}")

    for ab, state in todo.items():
        zip_path = RAW_DIR / f"vb_soi_{ab}_geojson.zip"
        if not zip_path.exists():
            raise SystemExit(f"missing {zip_path}; download it from the NWDP Village Boundary dataset")
        gdf = load_standardized(zip_path, state)
        gdf.geometry = gdf.geometry.map(lambda g: robust_prep(g, args.simplify))
        empty = gdf.geometry.isna() | gdf.geometry.is_empty
        if empty.any():
            print(f"  {state}: dropping {int(empty.sum())} empty-geometry villages present in source")
            gdf = gdf[~empty]
        slug = state.lower().replace(" ", "_")
        path = DATA_DIR / "administrative" / "villages" / f"{slug}_soi_villages.geojson"
        fc = json.loads(gdf.to_json())
        fc["name"] = f"{slug}_soi_villages"
        fc["attribution"] = ATTRIBUTION
        write_geojson(path, fc)
        # validate what was written (rounding can break geometries) and mirror it to Parquet
        back = gpd.read_file(path)
        if not back.geometry.is_valid.all():
            raise SystemExit(f"{path.name}: invalid geometry after write")
        pq = DATA_DIR / "administrative" / "villages_parquet" / f"{slug}_soi_villages.parquet"
        back.to_parquet(pq, engine="pyarrow", compression="snappy", index=False)
        print(f"{state}: {len(back)} villages, {back.district.nunique()} districts, "
              f"{int(back.village_code.isna().sum())} without village code, "
              f"population {int(back.population_2011.sum()):,} -> {path.name} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
