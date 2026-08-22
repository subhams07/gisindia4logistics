"""Fetch and standardize OFFICIAL village boundaries from Survey of India.

Source: Survey of India "Village Boundary Data Base of Entire India"
https://surveyofindia.gov.in/pages/village-boundary-data-base-of-entire-india

Each state is a direct .zip download (no login). Data is shapefile in the
national LCC projection with official LGD codes at state/district/
sub-district/village levels — the highest-quality open village source for
India and the canonical one for this repo.

LICENSING: SoI publishes these as free, login-free downloads on a public
government portal; this repo redistributes them in good faith on that basis,
with attribution ("Village boundaries: Survey of India, Government of India").
No explicit open-data license is stated on the page — see docs/legal_compliance.md
for the full posture. Remove promptly if SoI objects.

Coverage (27 states/UTs): the border/Himalayan and NE states (Assam, Arunachal
Pradesh, Himachal Pradesh, Jammu & Kashmir, Ladakh, Manipur, Meghalaya,
Mizoram, Nagaland) are NOT published on the SoI page.

Usage:
    python scripts/fetch/fetch_village_boundaries_soi.py --state Sikkim
    python scripts/fetch/fetch_village_boundaries_soi.py --state Maharashtra --district Pune
    python scripts/fetch/fetch_village_boundaries_soi.py --list
"""
from __future__ import annotations

import argparse
import io
import pathlib
import sys
import tempfile
import zipfile

import geopandas as gpd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR, http_session, write_geojson  # noqa: E402

BASE = "https://surveyofindia.gov.in/documents"

# slug -> (state name used by repo, notes)
STATE_ZIPS = {
    "ANDAMAN_&_NICOBAR_ISLANDS": "Andaman and Nicobar Islands",
    "ANDHRA_PRADESH": "Andhra Pradesh",
    "BIHAR": "Bihar",
    "CHANDIGARH": "Chandigarh",
    "CHHATTISGARH": "Chhattisgarh",
    "Dadra Nagar Haveli & Daman Diu": "Dadra and Nagar Haveli and Daman and Diu",
    "DELHI": "Delhi",
    "GOA": "Goa",
    "GUJARAT": "Gujarat",
    "HARYANA": "Haryana",
    "JHARKHAND": "Jharkhand",
    "KARNATAKA": "Karnataka",
    "KERALA": "Kerala",
    "LAKSHYADWEEP": "Lakshadweep",
    "MADHYA_PRADESH": "Madhya Pradesh",
    "MAHARASHTRA": "Maharashtra",
    "ODISHA": "Odisha",
    "PUNDUCHERRY": "Puducherry",
    "PUNJAB": "Punjab",
    "RAJASTHAN": "Rajasthan",
    "SIKKIM": "Sikkim",
    "TAMILNADU": "Tamil Nadu",
    "TELANGANA": "Telangana",
    "TRIPURA": "Tripura",
    "UTTAR_PRADESH": "Uttar Pradesh",
    "UTTARAKHAND": "Uttarakhand",
    "WEST_BENGAL": "West Bengal",
}

RENAME = {
    "STATE_UT": "state", "STATE_LGD": "state_code",
    "DISTRICT": "district", "DIST_LGD": "district_code",
    "SUB_DIST": "sub_district", "SUBDIS_LGD": "sub_district_code",
    "SUBDIS_TYP": "sub_district_type",
    "VILL_NAME": "village", "VILL_CAT": "village_category", "VILL_LGD": "village_code",
}


def slug_for(state_input: str) -> str:
    s = state_input.strip().lower()
    for slug, name in STATE_ZIPS.items():
        if s in (slug.lower(), name.lower()):
            return slug
    raise SystemExit(f"State not published by SoI: {state_input}. "
                     f"Available: {sorted(set(STATE_ZIPS.values()))}")


def fetch(slug: str) -> pathlib.Path:
    import time
    import zipfile
    raw = DATA_DIR / "raw" / "soi_villages"
    raw.mkdir(parents=True, exist_ok=True)
    dest = raw / f"{slug.replace(' ', '_')}.zip"
    if dest.exists() and zipfile.is_zipfile(dest):
        return dest
    if dest.exists():  # corrupt partial from an interrupted download
        dest.unlink()
    from urllib.parse import quote
    url = f"{BASE}/{quote(slug)}.zip"
    last_err = None
    for attempt in range(4):
        try:
            print(f"Downloading {url} ...")
            r = http_session().get(url, timeout=1800)
            r.raise_for_status()
            if not r.content[:2] == b"PK":
                raise ValueError("response is not a zip archive")
            dest.write_bytes(r.content)
            if not zipfile.is_zipfile(dest):
                dest.unlink()
                raise ValueError("truncated zip")
            print(f"  saved {len(r.content)/1e6:.1f} MB -> {dest}")
            return dest
        except Exception as e:
            last_err = e
            if dest.exists():
                dest.unlink()
            time.sleep(30 * (attempt + 1))  # SoI throttles consecutive large downloads
    raise RuntimeError(f"Download failed after retries: {last_err}")


def load_standardized(zip_path: pathlib.Path) -> gpd.GeoDataFrame:
    zf = zipfile.ZipFile(zip_path)
    shp_member = next(n for n in zf.namelist() if n.lower().endswith(".shp"))
    with tempfile.TemporaryDirectory() as td:  # shapefile sidecars needed on disk
        zf.extractall(td)
        gdf = gpd.read_file(pathlib.Path(td) / shp_member)
    # some states ship mixed-case columns (e.g. Telangana's Dist_LGD/Vill_cat)
    gdf.columns = [c.upper() if c != "geometry" else c for c in gdf.columns]
    cols = {k: v for k, v in RENAME.items() if k in gdf.columns}
    gdf = gdf.rename(columns=cols)
    keep = [c for c in RENAME.values() if c in gdf.columns] + ["geometry"]
    gdf = gdf[keep].to_crs(4326)
    return gdf


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", help="state name as used in this repo (e.g. 'Sikkim')")
    ap.add_argument("--district", help="optionally filter to one district")
    ap.add_argument("--simplify", type=float, default=0.0005, metavar="TOL",
                    help="geometry simplification tolerance in degrees (default 0.0005 ~ 50 m; 0 disables)")
    ap.add_argument("--list", action="store_true", help="list states published by SoI")
    args = ap.parse_args()

    if args.list or not args.state:
        print("SoI publishes village boundaries for:")
        for name in sorted(set(STATE_ZIPS.values())):
            print(" ", name)
        print("NOT published: Assam, Arunachal Pradesh, Himachal Pradesh, "
              "Jammu & Kashmir, Ladakh, Manipur, Meghalaya, Mizoram, Nagaland")
        return

    slug = slug_for(args.state)
    state_name = STATE_ZIPS[slug]
    gdf = load_standardized(fetch(slug))
    if args.district:
        gdf = gdf[gdf["district"].str.lower() == args.district.strip().lower()]
        label = args.district.strip().lower().replace(" ", "_")
    else:
        label = state_name.lower().replace(" ", "_")

    import shapely
    from shapely import make_valid

    def robust_prep(geom, tol=args.simplify):
        """Simplify + grid-snap + repair one feature; GEOS chokes vectorized
        on some source polygons (Karnataka has free-hole shells), so this is
        per-feature with fallbacks."""
        if geom is None or geom.is_empty:
            return geom
        try:
            g = geom.simplify(tol) if tol > 0 else geom
            g = shapely.set_precision(g, 1e-5)
            return make_valid(g)
        except Exception:
            try:
                return make_valid(shapely.set_precision(geom.buffer(0), 1e-5))
            except Exception:
                parts = geom.geoms if hasattr(geom, "geoms") else [geom]
                polys = [p for p in parts if p.geom_type == "Polygon" and p.area > 0]
                return max(polys, key=lambda p: p.area) if polys else None

    gdf.geometry = gdf.geometry.map(robust_prep)
    # a handful of source rows carry zero-area/empty geometry (e.g. Dhaond in
    # MP's Burhanpur) — drop and report rather than write nulls
    n_empty = int((gdf.geometry.is_empty | gdf.geometry.isna()).sum())
    if n_empty:
        print(f"dropping {n_empty} empty-geometry villages present in source")
        gdf = gdf[~(gdf.geometry.is_empty | gdf.geometry.isna())]

    # validation summary (some UT files lack sub-district columns entirely)
    nsd = gdf["sub_district"].nunique() if "sub_district" in gdf else 0
    print(f"villages: {len(gdf)} | districts: {gdf.district.nunique()} | "
          f"sub-districts: {nsd}")
    print(f"unique village LGD codes: {gdf.village_code.nunique()} "
          f"(duplicates: {len(gdf) - gdf.village_code.nunique()})")
    print(f"null village codes: {gdf.village_code.isna().sum()} | "
          f"valid geometries: {gdf.geometry.is_valid.all()}")

    out_dir = DATA_DIR / "administrative" / "villages"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{label}_soi_villages.geojson"
    fc = __import__("json").loads(gdf.to_json())
    fc["name"] = f"{label}_soi_villages"
    fc["attribution"] = "Village boundaries: Survey of India, Government of India"
    write_geojson(path, fc)
    print(f"Wrote {path} ({path.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
