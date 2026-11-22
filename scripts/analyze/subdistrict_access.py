"""Sub-district (taluka/tehsil) accessibility analysis — all of India.

Computes geodesic straight-line distance from every sub-district point (6,636
current LGD-coded subdistricts) to the nearest facility of each type —
national coverage in one table, complementing the village-level analysis
(which covers 27 states). Reveals the worst-served talukas nationally.
District name and code come from the current LGD district layer by the same
spatial assignment as the village tables (nearest_facility.assign_current_districts).

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
from nearest_facility import DISTRICTS_PATH, assign_current_districts, load_facilities, nearest_geodesic  # noqa: E402


def main() -> None:
    sub = gpd.read_file(DATA_DIR / "administrative" / "india_subdistricts_lgd.gpkg")
    pts = sub.geometry.representative_point()
    print(f"sub-districts: {len(pts)} across {sub.state.nunique()} states")

    facilities = load_facilities(["rail_station", "icd", "port", "air_cargo", "icp"])
    print(f"facility layers: { {k: len(v) for k, v in facilities.items()} }")

    districts = gpd.read_parquet(DISTRICTS_PATH)
    assigned = pd.concat([assign_current_districts(grp, state, districts)
                          for state, grp in sub.groupby("state")]).reindex(sub.index)
    base = pd.DataFrame({
        "state": sub["state"].values,
        "district": assigned["district"].values,
        "district_code": assigned["district_code"].astype("Int64").values,
        "sub_district": sub["sub_district"].values,
        "sub_district_code": sub["sub_district_code"].astype("Int64").values,
    })
    for kind, gdf in facilities.items():
        best, km = nearest_geodesic(pts, gdf.geometry)
        base[f"nearest_{kind}"] = gdf["fac_name"].to_numpy()[best]
        base[f"dist_{kind}_km"] = km.round(2)

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
