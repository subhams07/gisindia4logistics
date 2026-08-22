"""Fetch and standardize OFFICIAL village boundaries from Survey of India.

Source: Survey of India "Village Boundary Data Base of Entire India"
https://surveyofindia.gov.in/pages/village-boundary-data-base-of-entire-india

Each state is a direct .zip download (no login). Data is shapefile in the
national LCC projection with official LGD codes at state/district/
sub-district/village levels — the highest-quality open village source for
India and the canonical one for this repo.

LICENSING: the SoI page carries no explicit open-data license (site footer is
"all rights reserved"). Downloads are free for anyone; redistribution inside
this repo is NOT clearly permitted, so this script fetches to your local
data/raw/ and nothing is committed. See docs/legal_compliance.md.

Coverage (27 states/UTs): the border/Himalayan and NE states (Andhra? no —
Assam, Arunachal Pradesh, Himachal Pradesh, Jammu & Kashmir, Ladakh, Manipur,
Meghalaya, Mizoram, Nagaland) are NOT published on the SoI page.

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
    "STATE_UT": "state", "State_LGD": "state_code",
    "District": "district", "Dist_LGD": "district_code",
    "Sub_dist": "sub_district", "Subdis_LGD": "sub_district_code",
    "Subdis_Typ": "sub_district_type",
    "Vill_name": "village", "Vill_Cat": "village_category", "Vill_LGD": "village_code",
}


def slug_for(state_input: str) -> str:
    s = state_input.strip().lower()
    for slug, name in STATE_ZIPS.items():
        if s in (slug.lower(), name.lower()):
            return slug
    raise SystemExit(f"State not published by SoI: {state_input}. "
                     f"Available: {sorted(set(STATE_ZIPS.values()))}")


def fetch(slug: str) -> pathlib.Path:
    raw = DATA_DIR / "raw" / "soi_villages"
    raw.mkdir(parents=True, exist_ok=True)
    dest = raw / f"{slug.replace(' ', '_')}.zip"
    if dest.exists():
        return dest
    from urllib.parse import quote
    url = f"{BASE}/{quote(slug)}.zip"
    print(f"Downloading {url} ...")
    r = http_session().get(url, timeout=900)
    r.raise_for_status()
    dest.write_bytes(r.content)
    print(f"  saved {len(r.content)/1e6:.1f} MB -> {dest}")
    return dest


def load_standardized(zip_path: pathlib.Path) -> gpd.GeoDataFrame:
    zf = zipfile.ZipFile(zip_path)
    shp_member = next(n for n in zf.namelist() if n.lower().endswith(".shp"))
    with tempfile.TemporaryDirectory() as td:  # shapefile sidecars needed on disk
        zf.extractall(td)
        gdf = gpd.read_file(pathlib.Path(td) / shp_member)
    cols = {k: v for k, v in RENAME.items() if k in gdf.columns}
    gdf = gdf.rename(columns=cols)
    keep = [c for c in RENAME.values() if c in gdf.columns] + ["geometry"]
    gdf = gdf[keep].to_crs(4326)
    return gdf


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", help="state name as used in this repo (e.g. 'Sikkim')")
    ap.add_argument("--district", help="optionally filter to one district")
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

    # validation summary
    print(f"villages: {len(gdf)} | districts: {gdf.district.nunique()} | "
          f"sub-districts: {gdf.sub_district.nunique()}")
    print(f"unique village LGD codes: {gdf.village_code.nunique()} "
          f"(duplicates: {len(gdf) - gdf.village_code.nunique()})")
    print(f"null village codes: {gdf.village_code.isna().sum()} | "
          f"valid geometries: {gdf.geometry.is_valid.all()}")

    out_dir = DATA_DIR / "administrative" / "villages"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{label}_villages.geojson"
    write_geojson(path, __import__("json").loads(gdf.to_json()))
    print(f"Wrote {path} ({path.stat().st_size/1e6:.1f} MB)")
    print("NOT committed to the repo — check SoI redistribution terms first "
          "(docs/legal_compliance.md).")


if __name__ == "__main__":
    main()
