"""fetch_border_habitations.py — Ingest and standardize settlement & village points for the 9 Border/NE States.

Covers the 9 states/UTs not published in SoI village boundary zips:
  1. Arunachal Pradesh
  2. Assam
  3. Himachal Pradesh
  4. Jammu and Kashmir
  5. Ladakh
  6. Manipur
  7. Meghalaya
  8. Mizoram
  9. Nagaland

Queries OpenStreetMap Overpass for geocoded settlement place nodes
(village, hamlet, town, isolated_dwelling), spatially joins them with
the 781 LGD districts layer, standardizes the schema, and saves GeoJSONs
in data/administrative/villages/{state_slug}_habitations.geojson.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

import geopandas as gpd
import pandas as pd

from scripts.clean.standardize import DATA_DIR, http_session

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

BORDER_STATES = [
    "Arunachal Pradesh",
    "Assam",
    "Himachal Pradesh",
    "Jammu and Kashmir",
    "Ladakh",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
]


def fetch_state_places(state_name: str, state_geom) -> gpd.GeoDataFrame:
    session = http_session()
    minx, miny, maxx, maxy = state_geom.bounds
    # Add buffer to bbox
    buf = 0.05
    minx, miny, maxx, maxy = minx - buf, miny - buf, maxx + buf, maxy + buf

    query = f"""[out:json][timeout:90];
(
  node["place"~"village|hamlet|town|isolated_dwelling"]({miny:.4f},{minx:.4f},{maxy:.4f},{maxx:.4f});
);
out body;
"""
    for attempt in range(4):
        try:
            resp = session.post(OVERPASS_URL, data={"data": query}, timeout=100)
            resp.raise_for_status()
            data = resp.json()
            elems = data.get("elements", [])
            break
        except Exception as e:
            if attempt == 3:
                print(f"  ERROR fetching {state_name}: {e}")
                return gpd.GeoDataFrame()
            time.sleep(10 * (attempt + 1))

    pts = []
    for e in elems:
        lat, lon = e.get("lat"), e.get("lon")
        if lat is None or lon is None:
            continue
        tags = e.get("tags", {})
        name = tags.get("name") or tags.get("name:en") or tags.get("name:hi") or tags.get("alt_name")
        place = tags.get("place", "village")
        if not name:
            name = f"Unnamed {place.capitalize()}"
        
        pts.append({
            "osm_id": str(e.get("id")),
            "village": name,
            "place_type": place,
            "village_category": "Urban" if place == "town" else "Rural",
            "latitude": float(lat),
            "longitude": float(lon),
        })

    if not pts:
        return gpd.GeoDataFrame()

    gdf = gpd.GeoDataFrame(
        pts,
        geometry=gpd.points_from_xy([p["longitude"] for p in pts], [p["latitude"] for p in pts]),
        crs=4326,
    )
    # Filter strictly within state polygon
    gdf_in = gdf[gdf.geometry.within(state_geom)].copy()
    return gdf_in


def main() -> None:
    states_gdf = gpd.read_file(DATA_DIR / "administrative" / "india_states_lgd.geojson")
    districts_gdf = gpd.read_file(DATA_DIR / "administrative" / "india_districts_lgd.geojson")

    out_dir = DATA_DIR / "administrative" / "villages"
    out_dir.mkdir(parents=True, exist_ok=True)

    total_habitations = 0

    for st in BORDER_STATES:
        st_row = states_gdf[states_gdf["state"].str.lower() == st.lower()]
        if not len(st_row):
            print(f"State not found: {st}")
            continue
        
        st_geom = st_row.iloc[0].geometry
        state_code = st_row.iloc[0].get("state_code", None)
        slug = st.lower().replace(" ", "_")

        print(f"\nProcessing {st} ...")
        gdf_places = fetch_state_places(st, st_geom)
        print(f"  Retrieved {len(gdf_places)} settlement points within state boundary")

        if not len(gdf_places):
            continue

        # Spatial join with LGD districts to assign district & district_code
        st_districts = districts_gdf[districts_gdf["state"].str.lower() == st.lower()]
        joined = gpd.sjoin(
            gdf_places,
            st_districts[["district", "district_code", "geometry"]],
            how="left",
            predicate="within",
        )
        # In case of border slivers, nearest join fallback
        unmatched = joined["district"].isna()
        if unmatched.any():
            unmatched_pts = gdf_places[unmatched].copy()
            near_j = gpd.sjoin_nearest(unmatched_pts, st_districts[["district", "district_code", "geometry"]], how="left")
            near_j = near_j[~near_j.index.duplicated(keep="first")]
            joined.loc[unmatched, "district"] = near_j["district"].values
            joined.loc[unmatched, "district_code"] = near_j["district_code"].values

        joined["state"] = st
        joined["state_code"] = state_code
        joined["id"] = range(1, len(joined) + 1)
        joined["village_code"] = joined["osm_id"]
        joined["sub_district"] = None
        joined["sub_district_code"] = None
        joined["sub_district_type"] = None

        cols = [
            "id", "state", "state_code", "district", "district_code",
            "sub_district", "sub_district_code", "sub_district_type",
            "village", "village_category", "village_code", "geometry"
        ]
        final_gdf = joined[cols].copy()

        out_file = out_dir / f"{slug}_habitations.geojson"
        final_gdf.to_file(out_file, driver="GeoJSON")
        print(f"  Wrote {len(final_gdf)} habitations -> {out_file}")
        total_habitations += len(final_gdf)

    print(f"\n=== Completed Border Habitations Ingestion: {total_habitations} total settlements across 9 states ===")


if __name__ == "__main__":
    main()
