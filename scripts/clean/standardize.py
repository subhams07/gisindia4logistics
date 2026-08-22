"""Shared standardization utilities for GIS4Logistics datasets."""
from __future__ import annotations

import json
import pathlib
import re

import geopandas as gpd
import pandas as pd
import requests

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

USER_AGENT = "GIS4Logistics/0.1 (India logistics GIS data collection)"


def http_session() -> requests.Session:
    """Session with a UA header — several APIs (incl. Overpass) reject bare clients."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def write_geojson(path, feature_collection: dict, ndigits: int = 5) -> None:
    """Write a GeoJSON dict with coordinates rounded to `ndigits` decimals
    (~1 m at 5) — cuts file size 3-4x with no visible quality loss."""
    def round_coords(obj):
        if isinstance(obj, (list, tuple)):
            if obj and all(isinstance(v, (int, float)) for v in obj):
                return [round(v, ndigits) for v in obj]
            return [round_coords(v) for v in obj]
        return obj

    for f in feature_collection.get("features", []):
        geom = f.get("geometry")
        if geom and "coordinates" in geom:
            geom["coordinates"] = round_coords(geom["coordinates"])
    path.write_text(json.dumps(feature_collection, separators=(",", ":")))

ROAD_CLASS_MAP = {
    "motorway": "NH",
    "trunk": "NH",
    "primary": "SH",
    "secondary": "MDR",
    "tertiary": "ODR",
    "unclassified": "village_road",
    "residential": "street",
    "service": "service",
}

# LGD state codes (https://lgdirectory.gov.in) — canonical join key
LGD_STATE_CODES = {
    "andhra pradesh": 28, "arunachal pradesh": 12, "assam": 18, "bihar": 10,
    "chhattisgarh": 22, "goa": 30, "gujarat": 24, "haryana": 6,
    "himachal pradesh": 2, "jharkhand": 20, "karnataka": 29, "kerala": 32,
    "madhya pradesh": 23, "maharashtra": 27, "manipur": 14, "meghalaya": 17,
    "mizoram": 15, "nagaland": 13, "odisha": 21, "punjab": 3, "rajasthan": 8,
    "sikkim": 11, "tamil nadu": 33, "telangana": 36, "tripura": 16,
    "uttar pradesh": 9, "uttarakhand": 5, "west bengal": 19,
    "andaman and nicobar islands": 35, "chandigarh": 4,
    "dadra and nagar haveli and daman and diu": 38,
    "delhi": 7, "jammu and kashmir": 1, "ladakh": 37, "lakshadweep": 31,
    "puducherry": 34,
}

STATE_NAME_FIXES = {
    "orissa": "odisha",
    "uttaranchal": "uttarakhand",
    "nct of delhi": "delhi",
    "andaman & nicobar island": "andaman and nicobar islands",
    "andaman & nicobar islands": "andaman and nicobar islands",
    "dadra & nagar haveli": "dadra and nagar haveli and daman and diu",
    "dadra & nagar haveli & daman & diu": "dadra and nagar haveli and daman and diu",
    "dadra and nagar haveli": "dadra and nagar haveli and daman and diu",
    "jammu & kashmir": "jammu and kashmir",
    "pondicherry": "puducherry",
    "daman and diu": "dadra and nagar haveli and daman and diu",
    "daman & diu": "dadra and nagar haveli and daman and diu",
    "dadra & nagar haveli and daman & diu": "dadra and nagar haveli and daman and diu",
    "arunanchal pradesh": "arunachal pradesh",
    "dadara & nagar havelli": "dadra and nagar haveli and daman and diu",
    "andaman & nicobar": "andaman and nicobar islands",
    "telangana ": "telangana",
}


def clean_state_name(name: str) -> str | None:
    """Normalize a state name to LGD spelling; return None if unknown."""
    if name is None:
        return None
    key = str(name).strip().lower()
    key = STATE_NAME_FIXES.get(key, key)
    return key.title() if key in LGD_STATE_CODES else None


def to_wgs84(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)
    return gdf


def classify_osm_road(highway: str | None, ref: str | None) -> str | None:
    """Map OSM highway tag (+ ref) to Indian road classification."""
    if ref:
        ref = str(ref).upper()
        if re.search(r"\bNH", ref):
            return "NH"
        if re.search(r"\bSH\b|\bMH\b|\bKA\b|\bGJ\b|\bRJ\b|\bUP\b|\bMP\b|\bAP\b|\bTS\b|\bTN\b|\bWB\b", ref):
            return "SH"
    if not highway:
        return None
    highway = re.sub(r"_link$", "", str(highway))
    return ROAD_CLASS_MAP.get(highway)


def load_states() -> gpd.GeoDataFrame:
    p = DATA_DIR / "administrative" / "india_states_lgd.geojson"
    return gpd.read_file(p if p.exists() else DATA_DIR / "administrative" / "india_states.geojson")


def load_districts() -> gpd.GeoDataFrame:
    """Current LGD-coded districts when available (780), else Census-2011 file."""
    p = DATA_DIR / "administrative" / "india_districts_lgd.geojson"
    return gpd.read_file(p if p.exists() else DATA_DIR / "administrative" / "india_districts.geojson")


def district_bbox(district: str, state: str | None = None) -> tuple:
    """Return (minx, miny, maxx, maxy) for a district, optionally filtered by state."""
    gdf = load_districts()
    mask = gdf["district"].str.lower() == district.lower()
    if state:
        mask &= gdf["state"].str.lower() == state.lower()
    if not mask.any():
        raise ValueError(f"District not found: {district} (state={state})")
    return tuple(gdf[mask].total_bounds)


def points_from_csv(df: pd.DataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["longitude"], df["latitude"]), crs=4326
    )
