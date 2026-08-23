"""Enrich railway stations and station categories with 2024 classification data.

Extracts official 2024 station categories, operational zones, divisions,
annual passenger footfall, and earnings from Indian Railways master data
(via railway-stations-classification.pages.dev / Railway Board disclosures).

Enriches:
1. data/rail/station_categories.csv (5,938 stations with category, zone, division, passengers, revenue)
2. data/rail/railway_stations.csv (fills missing zones, increasing zone coverage from ~48% to ~80%)

Usage:
    python scripts/fetch/enrich_rail_stations.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import pandas as pd
import requests

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
    z = str(z).strip().split()[0].upper()
    return ZONE_FIXES.get(z, z)


def fetch_or_load_data() -> pd.DataFrame:
    try:
        session = http_session()
        resp = session.get(SOURCE_URL, timeout=15)
        text = resp.text
    except Exception as e:
        print(f"Network fetch failed ({e}); checking local cache...")
        text = None

    if text:
        m = re.search(r"const data\s*=\s*(\[\s*\{.*?\}\s*\]);", text, re.DOTALL)
        if m:
            data = json.loads(m.group(1))
            return pd.DataFrame(data)

    # Fallback to local steps content if available
    local_candidates = list(pathlib.Path.home().glob(".gemini/antigravity/brain/*/content.md")) + \
                       list(pathlib.Path.home().glob(".gemini/antigravity/brain/*/*/*/content.md"))
    for cand in local_candidates:
        try:
            c_text = cand.read_text(encoding="utf-8")
            m = re.search(r"const data\s*=\s*(\[\s*\{.*?\}\s*\]);", c_text, re.DOTALL)
            if m:
                print(f"Loaded cached data from {cand}")
                return pd.DataFrame(json.loads(m.group(1)))
        except Exception:
            continue

    raise RuntimeError("Could not fetch or locate station classification source data.")


def main() -> None:
    print("Loading 2024 Indian Railway classification data...")
    raw = fetch_or_load_data()
    print(f"Loaded {len(raw)} records from source")

    # Format station categories dataset
    df = raw.copy()
    df["station_code"] = df["code"].astype(str).str.strip().str.upper()
    df["station_name"] = df["station"].astype(str).str.strip()
    df["category"] = df["new"].astype(str).str.replace(" ", "").str.replace("-", "").str.upper()
    df["zone"] = df["zone"].map(clean_zone)
    df["division"] = df["division"].astype(str).str.strip().str.upper().replace({"NA": None, "NONE": None})
    df["state"] = df["state"].astype(str).str.strip()
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
    st["code_norm"] = st["station_code"].astype(str).str.strip().str.upper()
    cat_map = categories.set_index("station_code")

    # Fill missing or placeholder zones
    st_zones = st["zone"].map(clean_zone)
    filled_zones = st_zones.fillna(st["code_norm"].map(cat_map["zone"].to_dict()))
    st["zone"] = filled_zones

    # Add division if not present or fill it
    div_map = cat_map["division"].dropna().to_dict()
    st["division"] = st["code_norm"].map(div_map)

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
