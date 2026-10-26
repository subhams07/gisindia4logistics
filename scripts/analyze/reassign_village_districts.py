"""Reassign every village / habitation access row to its CURRENT district polygon.

``data/analysis/<state>_village_access.csv`` carried the district name/code copied from the
SoI village files (real LGD codes, but stale / truncated for newly formed districts, with
mojibake spellings such as ``DEVBHUMI DW>RKA``) or, for the 9 habitation states, a spatial
join. Spatial location wins: ``district`` / ``district_code`` become the canonical
``india_districts_lgd`` polygon name / code (state-scoped, see
``scripts/clean/district_assignment.py``); the source values are kept in
``source_district`` / ``source_district_code``.

Per file:
  * geometry is loaded from ``data/administrative/villages_parquet`` (GeoJSON fallback);
  * the row count and the ``village_code`` sequence must match BY POSITION (village_code
    is not unique, so it is never used as a join key);
  * only ``district`` / ``district_code`` are rewritten, new columns are inserted right
    after ``district_code``; every other column (all ``dist_*`` / ``nearest_*``) must come
    back byte-identical (checked on the in-memory frame and again on the re-read file);
  * the SoI 'Disputed (Jharkhand)' placeholder row is kept and flagged
    ``exclude_from_district_stats``.

Idempotent and deterministic: a second run reads ``source_*`` from the file and yields the
same bytes. Afterwards the 36 district summaries, the national composite and its parquet
are rebuilt (``--no-summaries`` to skip).

Usage: python scripts/analyze/reassign_village_districts.py [--no-summaries] [--state Haryana]
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import sys

import geopandas as gpd
import pandas as pd

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "clean"))
sys.path.insert(0, str(HERE.parent))
from standardize import DATA_DIR  # noqa: E402
import district_assignment as da  # noqa: E402

ANALYSIS_DIR = DATA_DIR / "analysis"
ADMIN_DIR = DATA_DIR / "administrative"
CHANGED = {"district", "district_code", *da.NEW_COLUMNS}


def slug_of(name: str) -> str:
    return name.lower().replace(" ", "_")


def norm_code(s: pd.Series) -> pd.Series:
    """Comparable village_code strings (parquet may hold ints/floats, the CSV holds text)."""
    out = s.astype(object).where(s.notna(), "").astype(str).str.replace("\r\n", "\n", regex=False).str.strip()
    return out.str.replace(r"\.0$", "", regex=True)


def load_geometry(slug: str) -> gpd.GeoDataFrame:
    for cand in (ADMIN_DIR / "villages_parquet" / f"{slug}_soi_villages.parquet",
                 ADMIN_DIR / "villages_parquet" / f"{slug}_habitations.parquet"):
        if cand.exists():
            return gpd.read_parquet(cand, columns=["village_code", "geometry"])
    for cand in (ADMIN_DIR / "villages" / f"{slug}_soi_villages.geojson",
                 ADMIN_DIR / "villages" / f"{slug}_habitations.geojson"):
        if cand.exists():
            return gpd.read_file(cand)[["village_code", "geometry"]]
    raise FileNotFoundError(f"No village geometry for {slug}")


def read_text_table(path: pathlib.Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False)


def reassign_state(state: str, path: pathlib.Path) -> tuple[pd.DataFrame, dict]:
    slug = slug_of(state)
    before = read_text_table(path)
    geo = load_geometry(slug)
    if len(geo) != len(before):
        raise AssertionError(f"{state}: {len(geo)} geometries vs {len(before)} access rows")
    if not (norm_code(geo["village_code"]).to_numpy() == norm_code(before["village_code"]).to_numpy()).all():
        raise AssertionError(f"{state}: village_code sequence differs by position")
    geoms = geo.geometry.to_crs(4326) if geo.crs is not None else geo.geometry
    after = da.reassign_frame(before, state, geoms)

    # --- invariants: nothing but district columns may differ ---------------------------
    keep = [c for c in before.columns if c not in CHANGED]
    a_keep = after[keep].astype(str)
    if not a_keep.equals(before[keep].astype(str)):
        raise AssertionError(f"{state}: non-district columns changed")
    # new columns immediately after district_code
    cols = list(after.columns)
    i = cols.index("district_code")
    if cols[i + 1:i + 1 + len(da.NEW_COLUMNS)] != da.NEW_COLUMNS:
        raise AssertionError(f"{state}: new column order wrong: {cols[:i + 7]}")

    src_name = after["source_district"].astype(str)
    src_code = pd.to_numeric(after["source_district_code"], errors="coerce").astype("Int64")
    code_changed = ~((after["district_code"] == src_code) | (after["district_code"].isna() & src_code.isna()))
    stats = {
        "state": state, "rows": len(after),
        "name_changed": int((after["district"].astype(str) != src_name).sum()),
        "code_changed": int(code_changed.sum()),
        "within": int((after["district_assignment"] == "within").sum()),
        "nearest_in_state": int((after["district_assignment"] == "nearest_in_state").sum()),
        "max_nearest_km": float(after["district_assignment_km"].max()) if len(after) else 0.0,
        "excluded": int(after["exclude_from_district_stats"].sum()),
    }
    return after, stats


def write_state(after: pd.DataFrame, path: pathlib.Path, before: pd.DataFrame) -> None:
    out = after.copy()
    out["district_code"] = out["district_code"].astype(object).where(out["district_code"].notna(), "")
    out["source_district_code"] = out["source_district_code"].astype(object).where(
        out["source_district_code"].notna(), "")
    out.to_csv(path, index=False, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    back = read_text_table(path)
    keep = [c for c in before.columns if c not in CHANGED]
    if not back[keep].equals(before[keep]):
        raise AssertionError(f"{path.name}: re-read non-district columns differ from original")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", help="only this state (default: all 36)")
    ap.add_argument("--no-summaries", action="store_true", help="skip rebuilding district summaries")
    args = ap.parse_args()

    states = sorted(da._load_districts()["state"].unique())
    if args.state:
        states = [s for s in states if s.lower() == args.state.lower()]
        if not states:
            raise SystemExit(f"Unknown state {args.state!r}")
    all_stats = []
    for st in states:
        path = ANALYSIS_DIR / f"{slug_of(st)}_village_access.csv"
        if not path.exists():
            print(f"[skip] {st}: {path.name} not found")
            continue
        before = read_text_table(path)
        after, stats = reassign_state(st, path)
        write_state(after, path, before)
        all_stats.append(stats)
        print(f"{st:45s} rows={stats['rows']:>7,} name_changed={stats['name_changed']:>6,} "
              f"code_changed={stats['code_changed']:>6,} within={stats['within']:>7,} "
              f"nearest={stats['nearest_in_state']:>4} max_km={stats['max_nearest_km']:.3f}", flush=True)
    t = pd.DataFrame(all_stats)
    if len(t):
        print(f"\nTOTAL rows={t.rows.sum():,} name_changed={t.name_changed.sum():,} "
              f"code_changed={t.code_changed.sum():,} within={t.within.sum():,} "
              f"nearest_in_state={t.nearest_in_state.sum():,} "
              f"max nearest distance={t.max_nearest_km.max():.3f} km "
              f"excluded placeholders={t.excluded.sum()}")
    if not args.no_summaries and not args.state:
        import nearest_facility as nf  # noqa: E402  (sibling script)
        nf.rebuild_summaries_from_village_tables()


if __name__ == "__main__":
    main()
