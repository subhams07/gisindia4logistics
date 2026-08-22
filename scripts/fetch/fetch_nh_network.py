"""Fetch the National Highway network of India (numbered NH routes) from OSM.

Extracts ways carrying ref tags like NH44, NH48 (plus old-style NH4 etc.)
across the country, classifies each segment by NH number, and writes a
committed national layer. Uses the India bounding box in one Overpass query;
falls back to per-state queries if the server rejects the big query.

Source: OpenStreetMap (ODbL).

Usage:
    python scripts/fetch/fetch_nh_network.py            # whole India
    python scripts/fetch/fetch_nh_network.py --state Maharashtra
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR, load_states, http_session, write_geojson  # noqa: E402

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
INDIA_BBOX = (6.0, 68.0, 37.5, 97.5)  # minlat, minlon, maxlat, maxlon
OUT = DATA_DIR / "roads" / "india_nh_network.geojson"


def overpass(query: str, timeout_s: int = 600) -> dict:
    import time
    last_err = None
    sess = http_session()
    for url in OVERPASS_URLS:
        for attempt in range(3):
            try:
                r = sess.post(url, data={"data": query}, timeout=timeout_s + 60)
                if r.status_code in (429, 502, 503):
                    time.sleep(20 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last_err = e
                time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"Overpass failed: {last_err}")


def bbox_query(bbox, timeout_s=600):
    minlat, minlon, maxlat, maxlon = bbox
    return f"""
    [out:json][timeout:{timeout_s}];
    (
      way["highway"~"^(motorway|trunk|primary)$"]["ref"~"NH",i]({minlat},{minlon},{maxlat},{maxlon});
    );
    out body geom;
    """


def to_features(data: dict) -> list[dict]:
    features = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        tags = el.get("tags", {})
        refs = [r.strip() for r in re.split(r"[;,]", str(tags.get("ref", ""))) if "NH" in r.upper()]
        nh_numbers = sorted({re.sub(r"^NH", "", r, flags=re.I).strip() for r in refs})
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
                "nh": ";".join(nh_numbers),
                "highway": tags.get("highway"),
            },
        })
    return features


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", help="restrict to one state (uses committed state boundaries)")
    ap.add_argument("--simplify", type=float, default=0.0005, metavar="TOL",
                    help="line simplification tolerance, degrees (0.0005 ~ 50 m; 0 disables)")
    args = ap.parse_args()

    if args.state:
        states = load_states()
        row = states[states["state"].str.lower() == args.state.lower()]
        if not len(row):
            raise SystemExit(f"state not found: {args.state}")
        bbox = tuple(row.total_bounds[i] for i in (1, 0, 3, 2))
        bboxes = [bbox]
        label = args.state.lower().replace(" ", "_")
    else:
        bboxes = [INDIA_BBOX]
        label = "india"

    all_features = []
    for bbox in bboxes:
        print(f"Querying Overpass for NH refs in {bbox} ...")
        try:
            data = overpass(bbox_query(bbox))
        except RuntimeError:
            if args.state:
                raise
            print("India-wide query failed; falling back to per-state queries ...")
            states = load_states()
            for _, row in states.iterrows():
                b = row.geometry.bounds  # (minx, miny, maxx, maxy)
                bbox_s = (b[1], b[0], b[3], b[2])
                print(f"  state query: {row['state']} {bbox_s}")
                try:
                    all_features.extend(to_features(overpass(bbox_query(bbox_s, 300))))
                except RuntimeError as e:
                    print(f"  WARN {row['state']}: {e}")
            data = None
        if data:
            all_features.extend(to_features(data))

    # dedupe ways (state fallback overlaps borders)
    seen = {}
    for f in all_features:
        seen[f["properties"]["osm_id"]] = f
    features = list(seen.values())
    print(f"NH segments: {len(features)} | distinct NH numbers: "
          f"{len({n for f in features for n in f['properties']['nh'].split(';') if n})}")

    if args.simplify > 0:
        import geopandas as gpd
        gdf = gpd.GeoDataFrame.from_features(features, crs=4326)
        gdf.geometry = gdf.geometry.simplify(args.simplify).make_valid()
        features = json.loads(gdf.to_json())["features"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fc = {"type": "FeatureCollection", "name": f"{label}_nh_network",
          "features": features,
          "attribution": "(c) OpenStreetMap contributors (ODbL)"}
    write_geojson(OUT if not args.state else OUT.with_name(f"{label}_nh_network.geojson"), fc)
    print(f"Wrote -> {OUT if not args.state else OUT.with_name(f'{label}_nh_network.geojson')}")


if __name__ == "__main__":
    main()
