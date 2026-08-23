"""fetch_toll_plazas.py — Fetch, cluster, and attribute nationwide Toll Plazas in India.

Queries OpenStreetMap Overpass for barrier=toll_booth within India, clusters
multi-lane booths (DBSCAN 150m), intersects with LGD districts, and snaps
to the National Highway network to determine the highway number.

Outputs:
  data/roads/toll_plazas.csv (~1,500 clustered Toll Plazas)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

from scripts.clean.standardize import DATA_DIR, http_session

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_QUERY = """[out:json][timeout:120];
(
  node["barrier"="toll_booth"](6.5,68.0,37.5,97.5);
  way["barrier"="toll_booth"](6.5,68.0,37.5,97.5);
);
out center;
"""


def fetch_raw_toll_booths() -> list[dict]:
    print("Fetching barrier=toll_booth from OSM Overpass ...")
    session = http_session()
    resp = session.post(OVERPASS_URL, data={"data": OVERPASS_QUERY}, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    elements = data.get("elements", [])
    print(f"  Retrieved {len(elements)} raw toll booth elements from Overpass")
    return elements


def main() -> None:
    raw_elements = fetch_raw_toll_booths()
    if not raw_elements:
        print("ERROR: No toll booth elements returned.")
        sys.exit(1)

    pts = []
    for e in raw_elements:
        lat = e.get("lat") or e.get("center", {}).get("lat")
        lon = e.get("lon") or e.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        tags = e.get("tags", {})
        name = tags.get("name") or tags.get("name:en") or tags.get("description")
        ref = tags.get("ref")
        pts.append({
            "osm_id": e.get("id"),
            "raw_name": name,
            "ref": ref,
            "latitude": float(lat),
            "longitude": float(lon),
        })

    gdf = gpd.GeoDataFrame(
        pts,
        geometry=gpd.points_from_xy([p["longitude"] for p in pts], [p["latitude"] for p in pts]),
        crs=4326,
    )

    # 1. Spatially clip to India boundary & attach District / State attributes
    districts = gpd.read_file(DATA_DIR / "administrative" / "india_districts_lgd.geojson")
    print("Spatially joining toll booths to LGD districts ...")
    gdf_in = gpd.sjoin(gdf, districts[["state", "district", "district_code", "geometry"]], how="inner", predicate="within")
    print(f"  {len(gdf_in)} toll booths strictly within India LGD districts")

    # 2. Cluster multi-lane toll booths within 150 meters in projected CRS (EPSG:7755)
    gdf_proj = gdf_in.to_crs(7755)
    coords = np.column_stack([gdf_proj.geometry.x, gdf_proj.geometry.y])
    db = DBSCAN(eps=150, min_samples=1).fit(coords)
    gdf_in["cluster"] = db.labels_

    # 3. Aggregate clusters into single discrete Toll Plazas
    plazas = []
    for _, grp in gdf_in.groupby("cluster"):
        named_rows = grp[grp["raw_name"].notna()]
        name = named_rows["raw_name"].iloc[0] if len(named_rows) else None
        
        ref_rows = grp[grp["ref"].notna()]
        ref = ref_rows["ref"].iloc[0] if len(ref_rows) else None

        rep_lat = grp["latitude"].mean()
        rep_lon = grp["longitude"].mean()
        state = grp["state"].iloc[0]
        district = grp["district"].iloc[0]
        district_code = grp["district_code"].iloc[0]

        if not name:
            name = f"{district} Toll Plaza" if district else "Toll Plaza"

        plazas.append({
            "name": name,
            "toll_type": "national_highway" if ref and "NH" in str(ref).upper() else "highway_toll",
            "nh_number": ref,
            "state": state,
            "district": district,
            "district_code": district_code,
            "latitude": round(rep_lat, 6),
            "longitude": round(rep_lon, 6),
            "booth_count": len(grp),
            "source_url": "https://www.openstreetmap.org",
        })

    plazas_df = pd.DataFrame(plazas)

    # 4. Snap to National Highway network to enrich missing nh_number
    nh_path = DATA_DIR / "roads" / "india_nh_network.geojson"
    if nh_path.exists():
        print("Snapping Toll Plazas to National Highway network to determine NH numbers ...")
        nh = gpd.read_file(nh_path).to_crs(7755)
        p_gdf = gpd.GeoDataFrame(
            plazas_df,
            geometry=gpd.points_from_xy(plazas_df["longitude"], plazas_df["latitude"]),
            crs=4326,
        ).to_crs(7755)
        
        joined_nh = gpd.sjoin_nearest(p_gdf, nh[["nh", "name", "highway", "geometry"]], how="left", distance_col="nh_dist_m")
        joined_nh = joined_nh[~joined_nh.index.duplicated(keep="first")]

        for idx, row in joined_nh.iterrows():
            if pd.isna(plazas_df.at[idx, "nh_number"]) and pd.notna(row["nh"]):
                plazas_df.at[idx, "nh_number"] = f"NH {row['nh']}"
            if row["nh_dist_m"] < 1000 and row["highway"] == "motorway":
                plazas_df.at[idx, "toll_type"] = "expressway_toll"
            elif row["nh_dist_m"] < 1000:
                plazas_df.at[idx, "toll_type"] = "national_highway"

    # Sort and clean
    plazas_df = plazas_df.sort_values(["state", "district", "name"]).reset_index(drop=True)

    out_csv = DATA_DIR / "roads" / "toll_plazas.csv"
    plazas_df.to_csv(out_csv, index=False)
    print(f"\nWrote {len(plazas_df)} clustered Toll Plazas -> {out_csv}")
    print(f"Toll types distribution:")
    print(plazas_df["toll_type"].value_counts())
    print(f"Top states by toll plaza count:")
    print(plazas_df["state"].value_counts().head(10))


if __name__ == "__main__":
    main()
