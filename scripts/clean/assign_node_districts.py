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


def load_districts(data_dir: pathlib.Path = DATA_DIR) -> gpd.GeoDataFrame:
    """Load current LGD district polygons for spatial assignment."""
    districts = gpd.read_parquet(
        data_dir / "administrative" / "india_districts_lgd.parquet",
        columns=["state", "district", "district_code", "geometry"],
    )
    return districts.rename(
        columns={"state": "poly_state", "district": "poly_district", "district_code": "poly_code"}
    )


def assign_table(table: pd.DataFrame, districts: gpd.GeoDataFrame, label: str = "nodes") -> pd.DataFrame:
    """Return a copy of `table` with `district` and `district_code` set from containing LGD polygons.

    Raises ValueError if any row has invalid/missing coordinates or lies outside its declared state.
    """
    out = table.copy()
    lon = pd.to_numeric(out["longitude"], errors="coerce")
    lat = pd.to_numeric(out["latitude"], errors="coerce")
    bad_xy = out.index[lon.isna() | lat.isna()]
    if len(bad_xy):
        raise ValueError(f"{label}: rows with missing/invalid coordinates: {out.loc[bad_xy, 'name'].tolist()}")

    points = gpd.GeoDataFrame(out[["state"]], crs=4326, geometry=gpd.points_from_xy(lon, lat))
    hit = gpd.sjoin(points, districts, predicate="within", how="left")
    hit = hit[~hit.index.duplicated(keep="first")].reindex(out.index)
    wrong = out.index[hit.poly_state.isna() | (hit.poly_state != out.state)]
    if len(wrong):
        raise ValueError(f"{label}: rows outside their own state: {out.loc[wrong, 'name'].tolist()}")

    out["district"] = hit.poly_district.to_numpy()
    codes = hit.poly_code.map(lambda c: "" if pd.isna(c) else str(int(c)))
    if "district_code" in out.columns:
        out["district_code"] = codes.to_numpy()
    else:
        out.insert(out.columns.get_loc("district") + 1, "district_code", codes.to_numpy())
    return out


def assign_file(
    path: pathlib.Path,
    districts: gpd.GeoDataFrame | None = None,
    data_dir: pathlib.Path = DATA_DIR,
) -> int:
    """Assign LGD `district` and `district_code` in-place to a node CSV, preserving line endings."""
    if districts is None:
        districts = load_districts(data_dir)
    raw = path.read_bytes()
    table = pd.read_csv(path, dtype=str, keep_default_na=False)
    updated = assign_table(table, districts, label=path.name)
    updated.to_csv(path, index=False, lineterminator="\r\n" if b"\r\n" in raw else "\n")
    return len(updated)


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    districts = load_districts(DATA_DIR)
    for stem in FILES:
        path = DATA_DIR / "logistics_hubs" / f"{stem}.csv"
        try:
            n = assign_file(path, districts=districts)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"{stem}.csv: {n} rows; district and LGD district_code from the polygons")


if __name__ == "__main__":
    main()
