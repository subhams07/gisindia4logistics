"""End-to-end demo: build a district logistics GeoPackage and overview map.

Combines, for one district:
  - district boundary (committed)
  - road network by class (Overpass/OSM)
  - rail lines + stations (Overpass/OSM + committed station table)
  - logistics hubs (committed)
  - Census 2011 indicators (committed)

Outputs:
  examples/output/<district>_logistics.gpkg   (all layers)
  examples/output/<district>_logistics_map.png

Usage:
    python scripts/make_demo.py --district Pune --state Maharashtra
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "fetch"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "clean"))
from fetch_roads import overpass_query, to_geojson  # noqa: E402
from fetch_rail import overpass, lines_geojson  # noqa: E402
from standardize import DATA_DIR, REPO_ROOT, points_from_csv  # noqa: E402

OUT_DIR = REPO_ROOT / "examples" / "output"

ROAD_STYLE = {
    "NH": dict(color="#d62728", linewidth=1.6, zorder=5),
    "SH": dict(color="#ff7f0e", linewidth=1.1, zorder=4),
    "MDR": dict(color="#bcbd22", linewidth=0.7, zorder=3),
    "ODR": dict(color="#7f7f7f", linewidth=0.4, zorder=2),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--district", default="Pune")
    ap.add_argument("--state", default="Maharashtra")
    args = ap.parse_args()

    districts = gpd.read_file(DATA_DIR / "administrative" / "india_districts_lgd.geojson")
    mask = (districts["district"].str.lower() == args.district.lower()) & \
           (districts["state"].str.lower() == args.state.lower())
    if not mask.any():
        raise SystemExit(f"District not found: {args.district}, {args.state}")
    district = districts[mask]
    bbox = tuple(district.total_bounds)
    print(f"{args.district}, {args.state}: bbox={bbox}")

    # --- roads (OSM) ---
    roads_fc = to_geojson(overpass_query(bbox), args.district)
    roads = gpd.GeoDataFrame.from_features(roads_fc, crs=4326)

    # --- rail lines (OSM) ---
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    q = f'''[out:json][timeout:180];
    ( way["railway"="rail"]({south},{west},{north},{east}); );
    out body geom;'''
    rail = gpd.GeoDataFrame.from_features(lines_geojson(overpass(q), args.district), crs=4326)

    # --- stations (committed table, filtered by bbox) ---
    st = pd.read_csv(DATA_DIR / "rail" / "railway_stations.csv")
    st = st[st["longitude"].between(bbox[0], bbox[2]) & st["latitude"].between(bbox[1], bbox[3])]
    stations = points_from_csv(st)

    # --- hubs (committed tables, filtered by bbox) ---
    hub_frames = []
    for f in ("ports.csv", "icds.csv", "icps.csv", "air_cargo.csv"):
        p = DATA_DIR / "logistics_hubs" / f
        df = pd.read_csv(p)
        df = df[df["longitude"].between(bbox[0], bbox[2]) & df["latitude"].between(bbox[1], bbox[3])]
        hub_frames.append(df)
    hubs = points_from_csv(pd.concat(hub_frames, ignore_index=True))

    # --- census row ---
    census = pd.read_csv(DATA_DIR / "demographic" / "census2011_district_key_indicators.csv")
    crow = census[(census["district"].str.lower() == args.district.lower())]
    if len(crow):
        r = crow.iloc[0]
        print(f"Census 2011: population {r.Population:,}, literate {r.Literate:,}")

    # --- write geopackage ---
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gpkg = OUT_DIR / f"{args.district.lower().replace(' ', '_')}_logistics.gpkg"
    district.to_file(gpkg, layer="district_boundary", driver="GPKG")
    roads.to_file(gpkg, layer="roads", driver="GPKG")
    rail.to_file(gpkg, layer="rail_lines", driver="GPKG")
    stations.to_file(gpkg, layer="rail_stations", driver="GPKG")
    if len(hubs):
        hubs.to_file(gpkg, layer="logistics_hubs", driver="GPKG")
    print(f"Wrote layers -> {gpkg}")

    # --- map ---
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 11))
    district.boundary.plot(ax=ax, color="#2c3e50", linewidth=1.2)
    for cls, style in ROAD_STYLE.items():
        sub = roads[roads["road_class"] == cls]
        if len(sub):
            sub.plot(ax=ax, label=f"Road {cls}", **style)
    if len(rail):
        rail.plot(ax=ax, color="#17a2b8", linewidth=0.8, linestyle="--", label="Railway")
    if len(stations):
        stations.plot(ax=ax, color="k", markersize=3, marker="s", label="Stations")
    if len(hubs):
        hubs.plot(ax=ax, color="#8e44ad", markersize=40, marker="^",
                  edgecolor="w", label="Logistics hubs")
    ax.set_title(f"{args.district} district — logistics layers (OSM + committed datasets)")
    ax.legend(loc="lower left", fontsize=8, framealpha=0.9)
    ax.set_axis_off()
    fig.text(0.99, 0.01,
             "Boundaries indicative (DataMeet/OSM) — not authoritative "
             "depictions; © OpenStreetMap contributors (ODbL)",
             ha="right", fontsize=6, color="#555555")
    png = OUT_DIR / f"{args.district.lower().replace(' ', '_')}_logistics_map.png"
    fig.savefig(png, dpi=150, bbox_inches="tight")
    print(f"Wrote map -> {png}")


if __name__ == "__main__":
    main()
