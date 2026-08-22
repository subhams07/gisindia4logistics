"""Fetch sub-district (taluka/tehsil) and village boundaries from OpenStreetMap.

India-wide taluka/village boundaries total gigabytes and are NOT committed to
this repo (see docs/sources.md for official alternatives — Bhuvan, SoI).

IMPORTANT — OSM admin_level numbering VARIES BY STATE in India:
  4 = state/UT (everywhere)
  Maharashtra/Karnataka/Tamil Nadu etc.: 5 = district, 6 = taluka/tehsil
  some states: 6 = district, 7 = tehsil/block
  10 = village (polygon coverage in India is currently near-zero; villages are
      mostly mapped as place nodes, not boundaries)

This script auto-detects the district/taluka levels for your bbox and lets you
override with --admin-level. Village fetch works only where OSM has polygon
relations (very sparse today).

Usage:
    python scripts/fetch/fetch_village_boundaries.py --district Pune --state Maharashtra --level taluka
    python scripts/fetch/fetch_village_boundaries.py --district Pune --state Maharashtra --level village
    python scripts/fetch/fetch_village_boundaries.py --bbox ... --level taluka --admin-level 6

For exact topology at scale, use the Geofabrik PBF + osmium instead:
    osmium extract --bbox=... india-latest.osm.pbf -o region.osm.pbf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import requests
from shapely.geometry import Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR, district_bbox, http_session, write_geojson  # noqa: E402

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

DEFAULT_LEVELS = {"division": [5], "district": [5, 6], "taluka": [6, 7], "village": [10]}


def overpass(query: str, timeout_s: int = 300) -> dict:
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
                time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"All Overpass mirrors failed: {last_err}")


def stitch_rings(segments: list[list[list[float]]]) -> list[list[list[float]]]:
    """Join unordered way segments into closed rings by matching endpoints."""
    remaining = [list(s) for s in segments]
    rings: list[list[list[float]]] = []
    while remaining:
        ring = remaining.pop(0)
        changed = True
        while changed and ring[0] != ring[-1]:
            changed = False
            for i, seg in enumerate(remaining):
                if seg[0] == ring[-1]:
                    ring.extend(seg[1:]); remaining.pop(i); changed = True; break
                if seg[-1] == ring[-1]:
                    ring.extend(list(reversed(seg))[1:]); remaining.pop(i); changed = True; break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
        # unclosed fragments are dropped (usually shared-border dupes)
    return rings


def relations_to_features(data: dict) -> list[dict]:
    """Assemble relation member ways into polygon features (outer + inner rings)."""
    features = []
    for el in data.get("elements", []):
        if el.get("type") != "relation":
            continue
        outers, inners = [], []
        for m in el.get("members", []):
            if m.get("type") != "way" or "geometry" not in m:
                continue
            coords = [[round(p["lon"], 7), round(p["lat"], 7)] for p in m["geometry"]]
            if len(coords) >= 2:
                (inners if m.get("role") == "inner" else outers).append(coords)
        outer_polys = [Polygon(r) for r in stitch_rings(outers) if Polygon(r).is_valid]
        inner_polys = [Polygon(r) for r in stitch_rings(inners) if Polygon(r).is_valid]
        if not outer_polys:
            continue
        geom = unary_union(outer_polys)
        for hole in inner_polys:
            try:
                geom = geom.difference(hole)
            except Exception:
                pass
        tags = el.get("tags", {})
        features.append({
            "type": "Feature",
            "geometry": json.loads(json.dumps(geom.__geo_interface__)),
            "properties": {
                "osm_id": el["id"], "name": tags.get("name"),
                "admin_level": tags.get("admin_level"),
                "osm_boundary_type": tags.get("border_type") or tags.get("type"),
            },
        })
    return features


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--district", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--level", choices=list(DEFAULT_LEVELS), default="taluka")
    ap.add_argument("--admin-level", type=int,
                    help="override OSM admin_level (e.g. 6 for Maharashtra talukas)")
    ap.add_argument("--output", default=str(DATA_DIR / "administrative"))
    args = ap.parse_args()

    bbox = district_bbox(args.district, args.state)
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    levels = [args.admin_level] if args.admin_level else DEFAULT_LEVELS[args.level]

    all_features = []
    for level in levels:
        print(f"Fetching admin_level={level} ({args.level}) for {args.district}, {args.state} bbox={bbox}")
        query = f"""
        [out:json][timeout:300];
        (
          relation["boundary"="administrative"]["admin_level"="{level}"]({south},{west},{north},{east});
        );
        out body geom;
        """
        feats = relations_to_features(overpass(query))
        print(f"  level {level}: {len(feats)} polygon relations")
        all_features.extend(feats)

    # keep only features mostly inside the district (bbox brings neighbors whose
    # borders merely touch/clip the district polygon)
    import geopandas as gpd
    districts = gpd.read_file(DATA_DIR / "administrative" / "india_districts.geojson")
    dmask = (districts["district"].str.lower() == args.district.lower()) & \
            (districts["state"].str.lower() == args.state.lower())
    district_geom = districts[dmask].iloc[0].geometry
    gdf = gpd.GeoDataFrame.from_features(all_features, crs=4326)
    gdf = gdf[gdf.geometry.apply(
        lambda g: g.intersection(district_geom).area / g.area > 0.5
        if g is not None and g.area else False)].copy()

    # dedupe by name across probed levels (keep the smaller admin_level)
    gdf = gdf.sort_values("admin_level").drop_duplicates(subset=["name"], keep="first")

    out = pathlib.Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.district.lower().replace(' ', '_')}_{args.level}.geojson"
    write_geojson(path, {"type": "FeatureCollection",
                         "name": f"{args.district}_{args.level}",
                         "features": json.loads(gdf.to_json())["features"]})
    print(f"Wrote {len(gdf)} {args.level} boundaries -> {path}")
    if not len(gdf):
        print("No polygons found — OSM coverage for this level/region is likely missing.")
        print("Probe available levels first (see docstring) or use official sources.")


if __name__ == "__main__":
    main()
