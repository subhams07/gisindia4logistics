"""Enrich railway stations and station categories with 2024 classification data.

Extracts official 2024 station categories, operational zones, divisions,
annual passenger footfall, and earnings from Indian Railways master data
(via railway-stations-classification.pages.dev / Railway Board disclosures).

Enriches:
1. data/rail/station_categories.csv (5,938 stations with category, zone, division, passengers, revenue)
2. data/rail/railway_stations.csv (fills missing zones, increasing zone coverage from ~48% to ~80%)

The base table, data/rail/railway_stations.csv, was converted from DataMeet's
railways stations.json (2016 vintage); no script in this repository rebuilds it.
This script only fills its zone and division columns. It is safe to re-run:
existing zone and division values are kept and only gaps are filled.

Usage:
    python scripts/fetch/enrich_rail_stations.py [--source-file saved_page.html]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR, http_session

SOURCE_URL = "https://railway-stations-classification.pages.dev/"
CAT_PATH = DATA_DIR / "rail" / "station_categories.csv"
STATIONS_PATH = DATA_DIR / "rail" / "railway_stations.csv"

ZONE_FIXES = {
    "WC": "WCR",
    "ECOR": "ECoR",
    "NA": None,
    "?": None,
}


def clean_zone(z: str | float | None) -> str | None:
    if z is None or pd.isna(z):
        return None
    parts = str(z).strip().split()
    if not parts:
        return None
    z = parts[0].upper()
    return ZONE_FIXES.get(z, z)


def _parse(text: str) -> pd.DataFrame | None:
    m = re.search(r"const data\s*=\s*(\[\s*\{.*?\}\s*\]);", text, re.DOTALL)
    return pd.DataFrame(json.loads(m.group(1))) if m else None


def fetch_or_load_data(source_file: pathlib.Path | None = None) -> pd.DataFrame:
    if source_file is not None:
        df = _parse(source_file.read_text(encoding="utf-8"))
        if df is None:
            raise RuntimeError(f"No station data array found in {source_file}")
        return df
    try:
        session = http_session()
        resp = session.get(SOURCE_URL, timeout=15)
        text = resp.text
    except Exception as e:
        print(f"Network fetch failed ({e}); checking local cache...")
        text = None

    if text:
        df = _parse(text)
        if df is not None:
            return df

    raise RuntimeError("Could not fetch the station classification page; save it in a browser "
                       "and pass --source-file.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-file", type=pathlib.Path,
                    help="saved copy of the classification page (used instead of fetching it)")
    args = ap.parse_args()
    print("Loading 2024 Indian Railway classification data...")
    raw = fetch_or_load_data(args.source_file)
    print(f"Loaded {len(raw)} records from source")

    # Format station categories dataset
    df = raw.copy()
    # "string" keeps missing values missing on pandas 2 and 3 (astype(str) gives 'nan' on 2)
    df["station_code"] = df["code"].astype("string").str.strip().str.upper()
    df["station_name"] = df["station"].astype("string").str.strip()
    df["category"] = df["new"].astype("string").str.replace(" ", "").str.replace("-", "").str.upper()
    df["zone"] = df["zone"].map(clean_zone)
    df["division"] = df["division"].astype("string").str.strip().str.upper().replace({"NA": None, "NONE": None})
    df["state"] = df["state"].astype("string").str.strip()
    df["total_passengers"] = pd.to_numeric(df["total_pax"], errors="coerce")
    df["total_revenue"] = pd.to_numeric(df["total_rev"], errors="coerce")
    df["source_url"] = SOURCE_URL

    cat_cols = [
        "station_code", "station_name", "category", "zone", "division",
        "state", "total_passengers", "total_revenue", "source_url"
    ]
    categories = df[cat_cols].drop_duplicates(subset=["station_code"]).sort_values("station_code")
    categories.to_csv(CAT_PATH, index=False)
    print(f"Wrote {len(categories)} stations -> {CAT_PATH}")
    print(f"  NSG1 stations: {(categories['category'] == 'NSG1').sum()}")
    print(f"  Zones populated: {categories['zone'].notna().sum()}/{len(categories)}")

    # Enrich main railway_stations.csv
    print(f"\nEnriching {STATIONS_PATH} ...")
    st = pd.read_csv(STATIONS_PATH)
    st["code_norm"] = st["station_code"].astype("string").str.strip().str.upper()
    cat_map = categories.set_index("station_code")

    # Fill missing or placeholder zones
    st_zones = st["zone"].map(clean_zone)
    filled_zones = st_zones.fillna(st["code_norm"].map(cat_map["zone"].to_dict()))
    st["zone"] = filled_zones

    # Fill missing divisions; keep existing values so re-runs are idempotent
    div_map = cat_map["division"].dropna().to_dict()
    existing = st["division"] if "division" in st.columns else pd.Series(pd.NA, index=st.index)
    st["division"] = existing.where(existing.notna(), st["code_norm"].map(div_map))

    # Clean and save
    st = st.drop(columns=["code_norm"])
    cols = ["station_name", "station_code", "zone", "division", "state", "address", "latitude", "longitude"]
    st = st[cols]
    st.to_csv(STATIONS_PATH, index=False)
    print(f"Wrote {len(st)} stations -> {STATIONS_PATH}")
    print(f"  Stations with valid zone: {st['zone'].notna().sum()}/{len(st)} ({(st['zone'].notna().mean()*100):.1f}%)")
    print(f"  Stations with division: {st['division'].notna().sum()}/{len(st)}")


if __name__ == "__main__":
    main()
