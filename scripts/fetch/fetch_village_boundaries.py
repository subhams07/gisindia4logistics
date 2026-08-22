"""Fetch sub-district (taluka/tehsil) and village boundaries.

India-wide village boundaries total several GB and are NOT committed to this
repo. Options, in recommended order:

1. OpenStreetMap admin boundaries via Overpass (ODbL) — fetched by this script.
   admin_level mapping for India: 4=state, 5=division (some states), 6=district,
   7=sub-district (taluka/tehsil, some states), 10=village. Coverage varies by
   state; Maharashtra, Karnataka, Kerala etc. are well mapped, others patchy.

2. ISRO Bhuvan (bhuvan.nrsc.gov.in) — official boundaries via the "Boundary
   Services" / thematic download (free registration required for bulk data).

3. Survey of India (surveyofindia.gov.in) — authoritative but licensing is
   restrictive; check terms before redistribution.

Usage:
    python scripts/fetch/fetch_village_boundaries.py --district Pune --state Maharashtra --level taluka
    python scripts/fetch/fetch_village_boundaries.py --district Pune --state Maharashtra --level village
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

LEVEL_TO_ADMIN = {"division": 5, "district": 6, "taluka": 7, "village": 10}


def overpass(query: str, timeout_s: int = 300) -> dict:
    last_err = None
    sess = http_session()
    for url in OVERPASS_URLS:
        try:
            r = sess.post(url, data={"data": query}, timeout=timeout_s + 60)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last_err = e
    raise RuntimeError(f"All Overpass mirrors failed: {last_err}")


def relations_geojson(data: dict, name: str, level: int) -> dict:
    """Assemble OSM relation multipolygon boundaries into GeoJSON features."""
    features = []
    for el in data.get("elements", []):
        if el.get("type") != "relation":
            continue
        tags = el.get("tags", {})
        members = [m for m in el.get("members", []) if m.get("type") == "way"]
        # collect member way geometry returned by `out geom`
        rings = []
        for m in members:
            if "geometry" in m:
                coords = [[p["lon"], p["lat"]] for p in m["geometry"]]
                if len(coords) >= 4:
                    rings.append(coords)
        if not rings:
            continue
        # naive assembly: outer ring = longest; full topology assembly needs osmium
        outer = max(rings, key=len)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [outer]},
            "properties": {
                "osm_id": el["id"], "name": tags.get("name"),
                "admin_level": tags.get("admin_level", level),
                "lga": tags.get("lga"),
            },
        })
    return {"type": "FeatureCollection", "name": name, "features": features}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--district", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--level", choices=list(LEVEL_TO_ADMIN), default="village")
    ap.add_argument("--output", default=str(DATA_DIR / "administrative"))
    args = ap.parse_args()

    bbox = district_bbox(args.district, args.state)
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    level = LEVEL_TO_ADMIN[args.level]
    print(f"Fetching admin_level={level} ({args.level}) for {args.district}, {args.state} bbox={bbox}")
    query = f"""
    [out:json][timeout:300];
    (
      relation["boundary"="administrative"]["admin_level"="{level}"]({south},{west},{north},{east});
    );
    out body geom;
    """
    fc = relations_geojson(overpass(query), f"{args.district}_{args.level}", level)
    out = pathlib.Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.district.lower().replace(' ', '_')}_{args.level}.geojson"
    path.write_text(json.dumps(fc))
    print(f"Wrote {len(fc['features'])} {args.level} boundaries -> {path}")
    print("NOTE: member-way ring assembly here is naive (longest outer ring).")
    print("For exact topology use the Geofabrik PBF + osmium extract instead:")
    print("  osmium extract --bbox=... india-latest.osm.pbf -o region.osm.pbf")


if __name__ == "__main__":
    main()
