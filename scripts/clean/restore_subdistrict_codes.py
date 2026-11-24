r"""Restore full alphanumeric SoI ABDB sub-district codes (Audit Item #18).

`scripts/clean/process_panindia_boundaries.py` previously truncated `SUBDIS_LGD`
with `str.extract(r"^(\d+)")` and cast it to `Int64`, stripping `N001`..`N009`
carveout suffixes off 342 sub-districts and creating 577 colliding rows across
241 codes.

This idempotent CLI reads the raw SoI ABDB `Sub_district Boundary.shp`, matches
100% of the 6,636 committed rows in `data/administrative/india_subdistricts_lgd`
1:1 via normalized `(state, abdb_district_code, sub_district)` (with geometry
centroid/area tie-breaking when multiple raw candidates share a key), and
updates ONLY `sub_district_code` (as nullable string) in both the GeoPackage and
GeoParquet files while asserting zero unmatched rows and zero geometry or
other-column changes.

Usage:
    python scripts/clean/restore_subdistrict_codes.py --raw-shp "<path/to/Sub_district Boundary.shp>"
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import pathlib
import sys

import geopandas as gpd
import pandas as pd

try:
    from scripts.clean.process_panindia_boundaries import (
        SUBDISTRICT_DISTRICT_CODE_FIXES,
        fix_mojibake,
        normalize_sub_district_code,
    )
    from scripts.clean.standardize import DATA_DIR, clean_state_name
except ModuleNotFoundError:  # direct script execution
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from scripts.clean.process_panindia_boundaries import (
        SUBDISTRICT_DISTRICT_CODE_FIXES,
        fix_mojibake,
        normalize_sub_district_code,
    )
    from scripts.clean.standardize import DATA_DIR, clean_state_name


def _key_part_text(val) -> str | None:
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    return s if s else None


def _key_part_int(val) -> int | None:
    if val is None or pd.isna(val):
        return None
    return int(val)


def prepare_raw_subdistricts(raw: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Normalize raw SoI ABDB Sub_district Boundary rows to match committed keys."""
    df = raw[raw["STATE_UT"].notna()].copy()
    df["state"] = (
        df["STATE_UT"]
        .map(clean_state_name)
        .str.replace(" And ", " and ", regex=False)
    )
    if df["state"].isna().any():
        raise ValueError("unmapped STATE_UT values in raw subdistrict shapefile")

    df["abdb_district_code"] = pd.to_numeric(
        df["DIST_LGD"].astype("string").str.extract(r"^(\d+)", expand=False),
        errors="coerce",
    ).astype("Int64")
    df["district_raw"] = (
        df["DISTRICT"]
        .map(fix_mojibake)
        .map(lambda s: s.strip() if isinstance(s, str) else s)
    )
    for (state, district, bad), good in SUBDISTRICT_DISTRICT_CODE_FIXES.items():
        hit = (
            df["state"].eq(state)
            & df["district_raw"].eq(district)
            & df["abdb_district_code"].eq(bad)
        )
        df.loc[hit, "abdb_district_code"] = good

    df["sub_district_norm"] = (
        df["SUB_DIST"]
        .map(fix_mojibake)
        .map(lambda s: s.strip() if isinstance(s, str) else s)
    )
    df["normalized_code"] = pd.Series(
        [normalize_sub_district_code(v) for v in df["SUBDIS_LGD"]],
        index=df.index,
        dtype="string",
    )
    return df


def match_subdistrict_codes(
    committed: gpd.GeoDataFrame,
    raw: gpd.GeoDataFrame,
) -> pd.Series:
    """Match every row in `committed` 1:1 to a row in `raw` and return restored codes."""
    raw_norm = prepare_raw_subdistricts(raw)
    raw_by_key: dict[tuple, list[int]] = defaultdict(list)
    for idx, state, abdb, name in zip(
        raw_norm.index,
        raw_norm["state"],
        raw_norm["abdb_district_code"],
        raw_norm["sub_district_norm"],
    ):
        key = (_key_part_text(state), _key_part_int(abdb), _key_part_text(name))
        raw_by_key[key].append(idx)

    committed_by_key: dict[tuple, list[int]] = defaultdict(list)
    for idx, state, abdb, name in zip(
        committed.index,
        committed["state"],
        committed["abdb_district_code"],
        committed["sub_district"],
    ):
        key = (_key_part_text(state), _key_part_int(abdb), _key_part_text(name))
        committed_by_key[key].append(idx)

    unmatched = [k for k in committed_by_key if k not in raw_by_key]
    if unmatched:
        raise ValueError(
            f"{len(unmatched)} committed sub-district keys missing from raw shapefile, "
            f"e.g. {unmatched[:5]}"
        )

    # Lazy EPSG:4326 conversion only for keys that need geometry tie-breaking.
    raw_4326 = None
    restored: dict[int, str | None] = {}

    for key, c_indices in committed_by_key.items():
        r_indices = raw_by_key[key]
        if len(c_indices) > len(r_indices):
            raise ValueError(
                f"key {key} has {len(c_indices)} committed rows but only {len(r_indices)} raw rows"
            )
        if len(c_indices) == 1 and len(r_indices) == 1:
            val = raw_norm.at[r_indices[0], "normalized_code"]
            restored[c_indices[0]] = None if pd.isna(val) else str(val)
            continue

        # Tie-break multiple candidates by geometry centroid distance and area difference.
        if raw_4326 is None:
            raw_4326 = raw_norm.to_crs(4326) if raw_norm.crs and raw_norm.crs.to_epsg() != 4326 else raw_norm
        available = list(r_indices)
        for ci in c_indices:
            c_geom = committed.geometry.loc[ci]
            c_cent = c_geom.centroid
            c_area = c_geom.area
            best_ri = min(
                available,
                key=lambda ri: (
                    c_cent.distance(raw_4326.geometry.loc[ri].centroid),
                    abs(c_area - raw_4326.geometry.loc[ri].area),
                ),
            )
            available.remove(best_ri)
            val = raw_norm.at[best_ri, "normalized_code"]
            restored[ci] = None if pd.isna(val) else str(val)

    if len(restored) != len(committed):
        raise ValueError(f"matched {len(restored)} of {len(committed)} committed rows")

    return pd.Series(
        [restored[i] for i in committed.index],
        index=committed.index,
        dtype="string",
    )


def restore_subdistrict_codes(raw_shp: pathlib.Path, data_dir: pathlib.Path = DATA_DIR) -> dict:
    """Restore `sub_district_code` in committed GPKG and Parquet files in-place."""
    admin = data_dir / "administrative"
    gpkg_path = admin / "india_subdistricts_lgd.gpkg"
    pq_path = admin / "india_subdistricts_lgd.parquet"

    if not raw_shp.exists():
        raise FileNotFoundError(f"raw shapefile not found: {raw_shp}")

    raw = gpd.read_file(raw_shp)
    gpkg_before = gpd.read_file(gpkg_path)
    pq_before = gpd.read_parquet(pq_path)

    if len(gpkg_before) != len(pq_before) or list(gpkg_before.columns) != list(pq_before.columns):
        raise ValueError("GPKG and Parquet subdistrict layers differ in shape or columns before update")
    if not (gpkg_before.geometry.to_wkb() == pq_before.geometry.to_wkb()).all():
        raise ValueError("GPKG and Parquet subdistrict geometries differ before update")

    new_codes = match_subdistrict_codes(gpkg_before, raw)

    # Preserve every column and geometry verbatim; update only sub_district_code.
    wkb_before = gpkg_before.geometry.to_wkb()
    non_code_cols = [c for c in gpkg_before.columns if c not in ("sub_district_code", "geometry")]
    attrs_before = gpkg_before[non_code_cols].copy()

    updated = gpkg_before.copy()
    updated["sub_district_code"] = new_codes

    gpkg_tmp = gpkg_path.with_name(f".{gpkg_path.stem}.tmp{gpkg_path.suffix}")
    pq_tmp = pq_path.with_name(f".{pq_path.stem}.tmp{pq_path.suffix}")
    try:
        updated.to_file(gpkg_tmp, layer="subdistricts", driver="GPKG")
        updated.to_parquet(pq_tmp, engine="pyarrow", compression="snappy", index=False)

        # Re-read staged outputs and verify strict parity and zero unintended mutations
        # before atomically replacing committed files.
        gpkg_after = gpd.read_file(gpkg_tmp)
        pq_after = gpd.read_parquet(pq_tmp)

        if len(gpkg_after) != len(gpkg_before):
            raise ValueError("GPKG row count changed")
        if len(pq_after) != len(pq_before):
            raise ValueError("Parquet row count changed")
        if list(gpkg_after.columns) != list(gpkg_before.columns):
            raise ValueError("GPKG columns changed")
        if list(pq_after.columns) != list(pq_before.columns):
            raise ValueError("Parquet columns changed")
        if not (gpkg_after.geometry.to_wkb() == wkb_before).all():
            raise ValueError("GPKG geometries changed")
        if not (pq_after.geometry.to_wkb() == wkb_before).all():
            raise ValueError("Parquet geometries changed")
        for col in non_code_cols:
            a = attrs_before[col]
            b_gpkg = gpkg_after[col]
            b_pq = pq_after[col]
            if not ((a == b_gpkg) | (a.isna() & b_gpkg.isna())).all():
                raise ValueError(f"GPKG column changed: {col}")
            if not ((a == b_pq) | (a.isna() & b_pq.isna())).all():
                raise ValueError(f"Parquet column changed: {col}")

        gpkg_codes = gpkg_after["sub_district_code"].astype("string")
        pq_codes = pq_after["sub_district_code"].astype("string")
        if not ((gpkg_codes == pq_codes) | (gpkg_codes.isna() & pq_codes.isna())).all():
            raise ValueError("GPKG and Parquet sub_district_code differ after write")

        gpkg_tmp.replace(gpkg_path)
        pq_tmp.replace(pq_path)
    finally:
        for tmp_path in (gpkg_tmp, pq_tmp):
            if tmp_path.exists():
                tmp_path.unlink()

    coded = gpkg_after.dropna(subset=["sub_district_code"])
    within_state_dups = int(coded.duplicated(["state", "sub_district_code"], keep=False).sum())
    national_dups = coded[coded.duplicated("sub_district_code", keep=False)]
    alphanumeric = int(
        coded["sub_district_code"].astype("string").str.contains(r"[^0-9]", regex=True).sum()
    )
    summary = {
        "rows": len(gpkg_after),
        "non_null_codes": len(coded),
        "null_codes": int(gpkg_after["sub_district_code"].isna().sum()),
        "unique_codes": int(coded["sub_district_code"].nunique()),
        "alphanumeric_codes": alphanumeric,
        "within_state_dup_rows": within_state_dups,
        "national_dup_codes": sorted(national_dups["sub_district_code"].unique().tolist()),
        "national_dup_rows": len(national_dups),
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--raw-shp",
        type=pathlib.Path,
        required=True,
        help="Path to raw SoI ABDB 'Sub_district Boundary.shp'",
    )
    parser.add_argument(
        "--data-dir",
        type=pathlib.Path,
        default=DATA_DIR,
        help="Data directory containing administrative/india_subdistricts_lgd.{gpkg,parquet}",
    )
    args = parser.parse_args()
    summary = restore_subdistrict_codes(args.raw_shp, data_dir=args.data_dir)
    print(
        f"Restored sub_district_code across {summary['rows']} rows: "
        f"{summary['non_null_codes']} non-null ({summary['null_codes']} null), "
        f"{summary['unique_codes']} unique ({summary['alphanumeric_codes']} alphanumeric), "
        f"{summary['within_state_dup_rows']} within-state dup rows, "
        f"{summary['national_dup_rows']} national cross-state dup rows "
        f"({summary['national_dup_codes']})."
    )


if __name__ == "__main__":
    main()
