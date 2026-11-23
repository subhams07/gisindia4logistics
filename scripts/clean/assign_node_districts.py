"""assign_node_districts.py — Current LGD district for the economic and agri node files.

industrial_nodes, pm_mitra_parks, cold_chain_storages and enam_mandis name a
district but carried names from before recent reorganisations or spelling
variants (Aurangabad for Chhatrapati Sambhaji Nagar, Alwar for the new
Kotputli-Behror, Raigarh — a Chhattisgarh district — for Maharashtra's Raigad).
This script sets, for every row, `district` and `district_code` to the
india_districts_lgd polygon that contains the point, and refuses to write if a
point falls outside its own state.

Idempotent. Usage: python scripts/clean/assign_node_districts.py
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from standardize import DATA_DIR  # noqa: E402

FILES = ("industrial_nodes", "pm_mitra_parks", "cold_chain_storages", "enam_mandis")


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    districts = gpd.read_parquet(DATA_DIR / "administrative" / "india_districts_lgd.parquet",
                                 columns=["state", "district", "district_code", "geometry"])
    districts = districts.rename(columns={"state": "poly_state", "district": "poly_district", "district_code": "poly_code"})
    for stem in FILES:
        path = DATA_DIR / "logistics_hubs" / f"{stem}.csv"
        raw = path.read_text(encoding="utf-8")
        table = pd.read_csv(path, dtype=str, keep_default_na=False)
        points = gpd.GeoDataFrame(table[["state"]], crs=4326, geometry=gpd.points_from_xy(
            pd.to_numeric(table.longitude), pd.to_numeric(table.latitude)))
        hit = gpd.sjoin(points, districts, predicate="within", how="left")
        hit = hit[~hit.index.duplicated(keep="first")].reindex(table.index)
        wrong = table.index[hit.poly_state.isna() | (hit.poly_state != table.state)]
        if len(wrong):
            raise SystemExit(f"{stem}.csv: rows outside their own state: {table.loc[wrong, 'name'].tolist()}")
        table["district"] = hit.poly_district.to_numpy()
        codes = hit.poly_code.map(lambda c: "" if pd.isna(c) else str(int(c)))
        if "district_code" in table.columns:
            table["district_code"] = codes.to_numpy()
        else:
            table.insert(table.columns.get_loc("district") + 1, "district_code", codes.to_numpy())
        table.to_csv(path, index=False, lineterminator="\r\n" if "\r\n" in raw else "\n")
        print(f"{stem}.csv: {len(table)} rows; district and LGD district_code from the polygons")


if __name__ == "__main__":
    main()
