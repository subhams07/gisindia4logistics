"""Sub-district (taluka/tehsil) accessibility analysis — all of India.

Computes straight-line distance from every sub-district centroid (6,636
current LGD-coded subdistricts) to the nearest facility of each type —
national coverage in one table, complementing the village-level analysis
(which covers 27 states). Reveals the worst-served talukas nationally.

Usage: python scripts/analyze/subdistrict_access.py
"""
from __future__ import annotations

import pathlib
import sys

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from nearest_facility import FACILITY_SOURCES, load_facilities  # noqa: E402

PROJ = 7755


def main() -> None:
    sub = gpd.read_file(DATA_DIR / "administrative" / "india_subdistricts_lgd.gpkg")
    pts = sub.copy()
    pts["geometry"] = sub.geometry.representative_point()
    pts = pts.to_crs(PROJ)
    print(f"sub-districts: {len(pts)} across {sub.state.nunique()} states")

    facilities = load_facilities(["rail_station", "icd", "port", "air_cargo", "icp"])
    print(f"facility layers: { {k: len(v) for k, v in facilities.items()} }")

    base = pd.DataFrame({
        "state": sub["state"].values,
        "district": sub["district"].values,
        "sub_district": sub["sub_district"].values,
        "sub_district_code": sub["sub_district_code"].astype("Int64").values,
    })
    for kind, gdf in facilities.items():
        fac = gdf.to_crs(PROJ)
        joined = gpd.sjoin_nearest(pts, fac[["fac_name", "geometry"]],
                                   how="left", distance_col=f"dist_{kind}")
        joined = joined[~joined.index.duplicated(keep="first")]  # tie dedupe
        base[f"nearest_{kind}"] = joined["fac_name"].values
        base[f"dist_{kind}_km"] = (joined[f"dist_{kind}"].values / 1000).round(2)

    out = DATA_DIR / "analysis" / "india_subdistrict_access.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    base.to_csv(out, index=False)
    print(f"Wrote {len(base)} sub-districts -> {out}")

    worst = base.nlargest(8, "dist_rail_station_km")[
        ["state", "district", "sub_district", "dist_rail_station_km"]]
    print("\nWorst-served sub-districts by rail distance:")
    print(worst.to_string(index=False))


if __name__ == "__main__":
    main()
