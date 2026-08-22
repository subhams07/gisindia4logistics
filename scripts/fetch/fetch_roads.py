"""Fetch road networks classified by Indian road type for a state or district.

Data source: OpenStreetMap via the Overpass API (ODbL license). For state- or
nation-wide extracts, prefer the Geofabrik India PBF
(https://download.geofabrik.de/asia/india.html) and clip with osmium — this
script uses Overpass, which suits district-sized queries.

Usage:
    python scripts/fetch/fetch_roads.py --district Pune --state Maharashtra
    python scripts/fetch/fetch_roads.py --bbox 73.5,17.9,74.5,18.6 --name pune
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import (classify_osm_road, district_bbox, DATA_DIR,  # noqa: E402
                         http_session, write_geojson)

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Road types fetched; service streets excluded to keep sizes manageable
HIGHWAY_TYPES = [
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link",
    "unclassified", "residential",
]


def overpass_query(bbox: tuple, timeout_s: int = 180, highways: list[str] | None = None) -> dict:
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    types = highways or HIGHWAY_TYPES
    query = f"""
    [out:json][timeout:{timeout_s}];
    (
      way["highway"~"^({'|'.join(types)})$"]({south},{west},{north},{east});
    );
    out body geom;
    """
    last_err = None
    sess = http_session()
    for url in OVERPASS_URLS:
        for attempt in range(3):  # Overpass rate-limits/502s transiently
            try:
                r = sess.post(url, data={"data": query}, timeout=timeout_s + 30)
                if r.status_code == 429:
                    time.sleep(30 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last_err = e
                time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"All Overpass mirrors failed: {last_err}")


def to_geojson(data: dict, name: str) -> dict:
    features = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        tags = el.get("tags", {})
        road_class = classify_osm_road(tags.get("highway"), tags.get("ref"))
        coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
        if len(coords) < 2:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "osm_id": el["id"],
                "name": tags.get("name"),
                "ref": tags.get("ref"),
                "highway": tags.get("highway"),
                "road_class": road_class,
                "lanes": tags.get("lanes"),
                "surface": tags.get("surface"),
                "oneway": tags.get("oneway"),
            },
        })
    return {"type": "FeatureCollection",
            "name": name,
            "features": features}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--district", help="district name (requires committed boundaries)")
    ap.add_argument("--state", help="state name to disambiguate district")
    ap.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat (alternative to district)")
    ap.add_argument("--name", default="roads", help="output file base name")
    ap.add_argument("--output", default=str(DATA_DIR / "roads"),
                    help="output directory")
    ap.add_argument("--classes",
                    default="motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link,tertiary,tertiary_link",
                    help="comma-separated OSM highway types (add unclassified,residential for full network)")
    ap.add_argument("--simplify", type=float, default=0.0, metavar="TOL",
                    help="simplify lines by tolerance in degrees (0.0005 ~ 50 m) to shrink output")
    args = ap.parse_args()

    if args.bbox:
        bbox = tuple(float(x) for x in args.bbox.split(","))
    elif args.district:
        bbox = district_bbox(args.district, args.state)
    else:
        ap.error("one of --district or --bbox is required")

    highways = [c.strip() for c in args.classes.split(",") if c.strip()]
    print(f"Querying Overpass for bbox {bbox} ...")
    data = overpass_query(bbox, highways=highways)
    fc = to_geojson(data, args.name)
    if args.simplify > 0:
        import geopandas as gpd
        gdf = gpd.GeoDataFrame.from_features(fc, crs=4326)
        gdf.geometry = gdf.geometry.simplify(args.simplify)
        fc = json.loads(gdf.to_json())
    out = pathlib.Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.name}_roads.geojson"
    write_geojson(path, fc)
    classes = {}
    for f in fc["features"]:
        classes[f["properties"]["road_class"]] = classes.get(f["properties"]["road_class"], 0) + 1
    print(f"Wrote {len(fc['features'])} road segments -> {path}")
    print("By class:", dict(sorted(classes.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
