"""Fetch railway lines (and optionally stations) for a region from OpenStreetMap.

Rail LINE geometry via Overpass (usage=rail / railway=rail). Station points are
committed in data/rail/railway_stations.csv (DataMeet, 2016); this script can
also refresh station points for the queried region from OSM.

Source: OpenStreetMap, ODbL license.

Usage:
    python scripts/fetch/fetch_rail.py --district Pune --state Maharashtra
    python scripts/fetch/fetch_rail.py --bbox 73.5,17.9,74.5,18.6 --name pune --stations
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import district_bbox, DATA_DIR, http_session  # noqa: E402

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def overpass(query: str, timeout_s: int = 180) -> dict:
    last_err = None
    sess = http_session()
    for url in OVERPASS_URLS:
        try:
            r = sess.post(url, data={"data": query}, timeout=timeout_s + 30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last_err = e
    raise RuntimeError(f"All Overpass mirrors failed: {last_err}")


def lines_geojson(data: dict, name: str) -> dict:
    features = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        tags = el.get("tags", {})
        coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
        if len(coords) < 2:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "osm_id": el["id"],
                "name": tags.get("name"),
                "railway": tags.get("railway"),
                "usage": tags.get("usage", "main"),
                "electrified": tags.get("electrified"),
                "gauge": tags.get("gauge"),
                "maxspeed": tags.get("maxspeed"),
            },
        })
    return {"type": "FeatureCollection", "name": name, "features": features}


def stations_geojson(data: dict, name: str) -> dict:
    features = []
    for el in data.get("elements", []):
        if el.get("type") != "node":
            continue
        tags = el.get("tags", {})
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]},
            "properties": {
                "osm_id": el["id"], "name": tags.get("name"),
                "railway": tags.get("railway"),
            },
        })
    return {"type": "FeatureCollection", "name": name, "features": features}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--district")
    ap.add_argument("--state")
    ap.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat")
    ap.add_argument("--name", default="rail")
    ap.add_argument("--stations", action="store_true",
                    help="also fetch station/halt nodes from OSM")
    ap.add_argument("--output", default=str(DATA_DIR / "rail"))
    args = ap.parse_args()

    if args.bbox:
        bbox = tuple(float(x) for x in args.bbox.split(","))
    elif args.district:
        bbox = district_bbox(args.district, args.state)
    else:
        ap.error("one of --district or --bbox is required")
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    out = pathlib.Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Querying Overpass for rail lines in {bbox} ...")
    q_lines = f"""
    [out:json][timeout:180];
    ( way["railway"="rail"]["usage"!~"^(component|freight-dismantled)$"]({south},{west},{north},{east}); );
    out body geom;
    """
    fc = lines_geojson(overpass(q_lines), args.name)
    p = out / f"{args.name}_rail_lines.geojson"
    p.write_text(json.dumps(fc))
    print(f"Wrote {len(fc['features'])} rail line segments -> {p}")

    if args.stations:
        print("Fetching station nodes ...")
        q_st = f"""
        [out:json][timeout:180];
        ( node["railway"~"^(station|halt)$"]({south},{west},{north},{east}); );
        out body;
        """
        fc_s = stations_geojson(overpass(q_st), args.name)
        p_s = out / f"{args.name}_rail_stations_osm.geojson"
        p_s.write_text(json.dumps(fc_s))
        print(f"Wrote {len(fc_s['features'])} station nodes -> {p_s}")


if __name__ == "__main__":
    main()
