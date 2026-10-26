"""
scripts/analyze/make_readme_figures.py
Generate the README hero maps (light and dark variants) into docs/img/.

Maps (780 current LGD districts, EPSG:7755, state outlines overlaid):
1. readme_rail_access_{light,dark}.png - median straight-line distance from a
   district's villages/settlements to the nearest railway station
   (``rail_station_median_km`` in data/analysis/india_district_access_summary.csv).
2. readme_icd_drive_{light,dark}.png - drive time along the National Highway
   network to the nearest inland container depot
   (``icd_drive_time_hours`` in data/analysis/nh_district_travel_time_summary.csv).

Colour: one sequential blue ramp with 5 fixed, human-readable classes
(not quantiles). Island districts / districts without a value get a neutral
grey with its own legend entry. Every map carries the boundary disclaimer.

Joins (strict, state-scoped, on the Survey of India ABDB ``district_code``)
-------------------------------------------------------------------------
* Rail map: ``rail_station_median_km`` from ``data/analysis/india_district_access_summary.csv``
  joined on (state, district_code). The summary is keyed on the same polygon layer
  (villages are assigned to their current polygon spatially by
  scripts/analyze/reassign_village_districts.py), so no name matching is needed.
* ICD map: ``nh_district_travel_time_summary.csv`` joined on (state, district_code);
  island districts have no road route.
* Polygons with a null code (Nazul, Mirpur, Muzaffarabad) fall back to an exact
  (state, district) name match. Joins stay state-scoped so a cross-state code collision
  can never paint the wrong district. Nothing is guessed: unmatched polygons are drawn
  grey and reported.
* No-data polygons are drawn first, valued polygons on top, so a no-data
  polygon can never hide a valued one if polygons ever overlap.

Usage: python scripts/analyze/make_readme_figures.py [--out-dir docs/img]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import geopandas as gpd  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.colors import to_rgba  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
DEFAULT_OUT = REPO_ROOT / "docs" / "img"

PROJ_EPSG = 7755
FOOTER_LINES = (
    "Source: GISIndia4Logistics v1.1.0 committed data. Boundaries indicative "
    "(Survey of India ABDB), not authoritative depictions.",
    "Map data \u00a9 OpenStreetMap contributors (ODbL).",
)

# Sequential single-hue blue ramp, 5 classes, index 0 = smallest values.
# Light mode: light -> dark = small -> large (more = darker blue).
# Dark mode: same hue on a dark surface; larger values = lighter/brighter
# ("more ink" on dark), so light -> dark is reversed relative to value order:
# dark-mode steps run from the deepest blue (small) to the palest (large).
RAMPS = {
    "light": ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"],
    "dark": ["#184f95", "#2a78d6", "#5598e7", "#86b6ef", "#cde2fb"],
}
THEMES = {
    "light": dict(surface="#fcfcfb", text="#0b0b0b", muted="#52514e", nodata="#d9d8d4"),
    "dark": dict(surface="#1a1a19", text="#f0efec", muted="#c3c2b7", nodata="#6b6a65"),
}

# Fixed class breaks (lower-inclusive: 5 falls in "5-10", 40 in ">=40").
MAPS = {
    "rail_access": dict(
        stem="readme_rail_access",
        title="How far is the nearest railway station?",
        subtitle="Median straight-line distance from each district's villages and settlements to the nearest station",
        legend_title="Median distance, km",
        bins=[5, 10, 20, 40],
        labels=["<5", "5–10", "10–20", "20–40", "≥40"],
        nodata_label="No data",
    ),
    "icd_drive": dict(
        stem="readme_icd_drive",
        title="Drive time to the nearest ICD / CFS",
        subtitle="Estimated National Highway drive time from each district to its nearest inland container depot or CFS",
        legend_title="Drive time, hours",
        bins=[2, 4, 6, 8],
        labels=["<2", "2–4", "4–6", "6–8", "≥8"],
        nodata_label="No road route",
    ),
}

FIG_W, FIG_H, DPI = 8, 9, 150


# --------------------------------------------------------------------------
# Loading and joining
# --------------------------------------------------------------------------
def norm_code(s: pd.Series) -> pd.Series:
    """Normalise float/int/str LGD codes to nullable Int64 (fails on non-integers)."""
    num = pd.to_numeric(s, errors="coerce")
    if ((num.dropna() % 1) != 0).any():
        raise ValueError("district_code contains non-integer values")
    return num.astype("Int64")


def load_data():
    districts = gpd.read_parquet(DATA_DIR / "administrative" / "india_districts_lgd.parquet")
    states = gpd.read_parquet(DATA_DIR / "administrative" / "india_states_lgd.parquet")
    travel = pd.read_csv(DATA_DIR / "analysis" / "nh_district_travel_time_summary.csv")
    districts = districts.reset_index(drop=True)
    districts["district_code"] = norm_code(districts["district_code"])
    travel["district_code"] = norm_code(travel["district_code"])
    access = pd.read_csv(DATA_DIR / "analysis" / "india_district_access_summary.csv")
    access["district_code"] = norm_code(access["district_code"])
    return districts, states, travel, access


def join_travel(districts: gpd.GeoDataFrame, travel: pd.DataFrame) -> pd.Series:
    """icd_drive_time_hours per polygon, joined on (state, district_code); name fallback if code is null."""
    print("\n[join] travel-time table -> district polygons")
    for label, df in (("polygons", districts), ("travel table", travel)):
        codes = df["district_code"].dropna()
        dup = codes[codes.duplicated(keep=False)]
        print(f"  {label}: {len(df)} rows, {df['district_code'].isna().sum()} null code(s), "
              f"{dup.nunique()} duplicated code(s) {sorted(dup.unique().tolist())}")
    key_t = travel.dropna(subset=["district_code"])  # null-code rows are resolved by name below
    if key_t.duplicated(["state", "district_code"]).any():
        raise SystemExit("(state, district_code) is not unique in the travel table; refusing to guess")
    merged = districts[["state", "district", "district_code"]].merge(
        key_t[["state", "district", "district_code", "icd_drive_time_hours", "is_island"]],
        on=["state", "district_code"], how="left", suffixes=("", "_t"), validate="m:1",
    )
    # null-code polygons stay unmatched in the merge above; resolve them by exact (state, district) name
    nocode = districts["district_code"].isna()
    matched_code = int((~nocode & merged["district_t"].notna()).sum())
    name_ok = 0
    for i in districts.index[nocode]:
        hit = travel[(travel["state"] == districts.at[i, "state"])
                     & (travel["district"] == districts.at[i, "district"])
                     & travel["district_code"].isna()]
        if len(hit) == 1:
            merged.loc[i, ["district_t", "icd_drive_time_hours", "is_island"]] = hit.iloc[0][
                ["district", "icd_drive_time_hours", "is_island"]].values
            name_ok += 1
        else:
            merged.loc[i, ["district_t", "icd_drive_time_hours", "is_island"]] = [np.nan, np.nan, np.nan]
    unmatched = int(merged["district_t"].isna().sum())
    print(f"  matched by (state, district_code): {matched_code}/{(~nocode).sum()} coded polygons")
    print(f"  matched by exact (state, district) name (null-code polygons): {name_ok}/{nocode.sum()}")
    print(f"  unmatched polygons: {unmatched}")
    unused = len(travel) - matched_code - name_ok
    print(f"  travel rows not used: {unused}")
    mism = merged[(merged["district_t"].notna()) & (merged["district"] != merged["district_t"])]
    print(f"  name mismatches between code-matched pairs (sanity): {len(mism)}")
    out = merged["icd_drive_time_hours"].astype(float)
    island = merged["is_island"].astype("boolean").fillna(False).astype(bool)
    out[island] = np.nan  # islands: no road route
    print(f"  island districts (no road route): {int(island.sum())}; NaN total after join: {int(out.isna().sum())}")
    return out


def join_access(districts: gpd.GeoDataFrame, access: pd.DataFrame) -> pd.Series:
    """``rail_station_median_km`` per polygon from the national access summary.

    Strict join on (state, district_code); null-code polygons by exact (state, district) name.
    """
    print("\n[join] national access summary -> district polygons (rail_station_median_km)")
    acc = access[access["district"] != "__STATE__"]
    key_a = acc.dropna(subset=["district_code"])
    if key_a.duplicated(["state", "district_code"]).any():
        raise SystemExit("(state, district_code) is not unique in the access summary; refusing to guess")
    print(f"  polygons: {len(districts)} ({int(districts['district_code'].isna().sum())} null code); "
          f"summary rows: {len(acc)} ({int(acc['district_code'].isna().sum())} null code)")
    merged = districts[["state", "district", "district_code"]].merge(
        key_a[["state", "district", "district_code", "rail_station_median_km"]],
        on=["state", "district_code"], how="left", suffixes=("", "_a"), validate="m:1",
    )
    nocode = districts["district_code"].isna()
    matched_code = int((~nocode & merged["district_a"].notna()).sum())
    name_ok = 0
    for i in districts.index[nocode]:
        hit = acc[(acc["state"] == districts.at[i, "state"])
                  & (acc["district"] == districts.at[i, "district"])
                  & acc["district_code"].isna()]
        if len(hit) == 1:
            merged.loc[i, ["district_a", "rail_station_median_km"]] = hit.iloc[0][
                ["district", "rail_station_median_km"]].values
            name_ok += 1
        else:
            merged.loc[i, ["district_a", "rail_station_median_km"]] = [np.nan, np.nan]
    unmatched = int(merged["district_a"].isna().sum())
    print(f"  matched by (state, district_code): {matched_code}/{(~nocode).sum()} coded polygons")
    print(f"  matched by exact (state, district) name (null-code polygons): {name_ok}/{nocode.sum()}")
    print(f"  unmatched polygons: {unmatched}; summary rows not used: {len(acc) - matched_code - name_ok}")
    mism = merged[merged["district_a"].notna() & (merged["district"] != merged["district_a"])]
    print(f"  name mismatches between code-matched pairs (sanity): {len(mism)}")
    out = merged["rail_station_median_km"].astype(float)
    print(f"  polygons with a value: {int(out.notna().sum())}/{len(districts)}; without (drawn grey): {int(out.isna().sum())}")
    return out


# --------------------------------------------------------------------------
# Plotting
# --------------------------------------------------------------------------
def classify(values: pd.Series, bins: list[float]) -> np.ndarray:
    """Class index 0..4 (lower-inclusive breaks); -1 where the value is NaN."""
    v = values.to_numpy(dtype=float)
    cls = np.digitize(np.nan_to_num(v), bins, right=False)
    cls[np.isnan(v)] = -1
    return cls


def render(districts_p: gpd.GeoDataFrame, states_p: gpd.GeoDataFrame, classes: np.ndarray,
           spec: dict, theme: str, out_path: Path) -> None:
    th, ramp = THEMES[theme], RAMPS[theme]
    colors = [th["nodata"] if c < 0 else ramp[c] for c in classes]
    fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=DPI, facecolor=th["surface"])
    ax = fig.add_axes([0.03, 0.075, 0.94, 0.79])
    ax.set_facecolor(th["surface"])
    # No-data polygons first, valued polygons on top (never paint grey over a value).
    nodata = classes < 0
    for mask, z in ((nodata, 1), (~nodata, 2)):
        if mask.any():
            districts_p[mask].plot(ax=ax, color=[c for c, m in zip(colors, mask) if m],
                                   edgecolor=th["surface"], linewidth=0.07, zorder=z)
    states_p.plot(ax=ax, facecolor="none", edgecolor=to_rgba(th["muted"], 0.6), linewidth=0.35, zorder=3)
    xmin, ymin, xmax, ymax = districts_p.total_bounds
    padx, pady = (xmax - xmin) * 0.01, (ymax - ymin) * 0.01
    ax.set_xlim(xmin - padx, xmax + padx)
    ax.set_ylim(ymin - pady, ymax + pady)
    ax.set_aspect("equal")
    ax.set_axis_off()

    handles = [Patch(facecolor=ramp[i], edgecolor=th["surface"], label=lab)
               for i, lab in enumerate(spec["labels"])]
    if (classes < 0).any():
        handles.append(Patch(facecolor=th["nodata"], edgecolor=th["surface"], label=spec["nodata_label"]))
    leg = ax.legend(handles=handles, title=spec["legend_title"], loc="lower left",
                    bbox_to_anchor=(0.035, 0.07), bbox_transform=fig.transFigure, frameon=False, fontsize=9, title_fontsize=9,
                    handlelength=1.6, handleheight=1.1, labelspacing=0.45, alignment="left")
    for t in leg.get_texts():
        t.set_color(th["text"])
    leg.get_title().set_color(th["text"])
    leg.get_title().set_fontweight("bold")

    fig.text(0.05, 0.955, spec["title"], ha="left", va="center", fontsize=14,
             fontweight="bold", color=th["text"])
    fig.text(0.05, 0.923, spec["subtitle"], ha="left", va="center", fontsize=8.5, color=th["muted"])
    for k, line in enumerate(FOOTER_LINES):
        fig.text(0.05, 0.036 - 0.018 * k, line, ha="left", va="center", fontsize=6.6, color=th["muted"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=DPI, facecolor=th["surface"],
                metadata={"Software": None},
                pil_kwargs={"optimize": True})
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                    help="output directory (default: docs/img)")
    args = ap.parse_args()
    out_dir = args.out_dir if args.out_dir.is_absolute() else (Path.cwd() / args.out_dir)

    districts, states, travel, access = load_data()
    print(f"[load] districts={len(districts)} states={len(states)} travel={len(travel)} access={len(access)}")
    values = {
        "rail_access": join_access(districts, access),
        "icd_drive": join_travel(districts, travel),
    }
    districts_p = districts.to_crs(PROJ_EPSG)
    states_p = states.to_crs(PROJ_EPSG)

    written = []
    print("\n[summary]")
    for key, spec in MAPS.items():
        v = values[key]
        cls = classify(v, spec["bins"])
        counts = [int((cls == i).sum()) for i in range(5)]
        nodata = int((cls < 0).sum())
        print(f"  {spec['title']}")
        for lab, n in zip(spec["labels"], counts):
            print(f"    {lab:>7}: {n}")
        print(f"    {spec['nodata_label']}: {nodata}   (total {sum(counts) + nodata})")
        print(f"    national median of district values (unweighted, n={int(v.notna().sum())}): {v.median():.2f}")
        for theme in ("light", "dark"):
            path = out_dir / f"{spec['stem']}_{theme}.png"
            render(districts_p, states_p, cls, spec, theme, path)
            written.append(path)
    print("\n[files]")
    for p in written:
        print(f"  {p}  {p.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
