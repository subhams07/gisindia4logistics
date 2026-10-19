"""
scripts/analyze/make_readme_figures.py
Generate the README hero maps (light and dark variants) into docs/img/.

Maps (781 current LGD districts, EPSG:7755, state outlines overlaid):
1. readme_rail_access_{light,dark}.png - median straight-line distance from a
   district's villages/settlements to the nearest railway station
   (``rail_station_median_km`` in data/analysis/india_district_access_summary.csv).
2. readme_icd_drive_{light,dark}.png - drive time along the National Highway
   network to the nearest inland container depot
   (``icd_drive_time_hours`` in data/analysis/nh_district_travel_time_summary.csv).

Colour: one sequential blue ramp with 5 fixed, human-readable classes
(not quantiles). Island districts / districts without a value get a neutral
grey with its own legend entry. Every map carries the boundary disclaimer.

Joins
-----
* Travel-time table: joined on ``district_code`` (normalised float -> Int64)
  together with ``state``, because the LGD code 598 is carried by two districts
  (Alappuzha, Dhule) and three polygons (Nazul, Mirpur, Muzaffarabad) have no
  code at all. Those three fall back to an exact (state, district) name match.
* Access-summary table: has NO district code column (only state + district
  names, and some names carry legacy-font mojibake or older spellings), so it
  is joined by normalised, state-scoped name: exact first, then a one-to-one
  fuzzy pass. Every fuzzy pairing and every unmatched polygon is printed.

Usage: python scripts/analyze/make_readme_figures.py [--out-dir docs/img]
"""
from __future__ import annotations

import argparse
import difflib
import re
import unicodedata
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
FOOTER = ("Source: GISIndia4Logistics v1.1.0 committed data. Boundaries indicative "
          "(Survey of India ABDB), not authoritative depictions.")

# Sequential single-hue blue ramp, 5 classes, index 0 = smallest values.
# Light mode: light -> dark = small -> large (more = darker blue).
# Dark mode: same hue on a dark surface; larger values = lighter/brighter
# ("more ink" on dark), so light -> dark is reversed relative to value order:
# dark-mode steps run from the deepest blue (small) to the palest (large).
RAMPS = {
    "light": ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"],
    "dark": ["#184f95", "#256abf", "#3987e5", "#6da7ec", "#b7d3f6"],
}
THEMES = {
    "light": dict(surface="#fcfcfb", text="#0b0b0b", muted="#52514e", nodata="#d9d8d4"),
    "dark": dict(surface="#1a1a19", text="#f0efec", muted="#c3c2b7", nodata="#4a4a46"),
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
        title="Drive time to the nearest inland container depot",
        subtitle="Estimated drive time along the National Highway network from each district to its nearest ICD",
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


# Verified same-district spellings that differ too much for fuzzy matching.
SPELLING_ALIASES = {"TWENTY-FOUR": "24", "TWENTY FOUR": "24", "HOWRAH": "HAORA"}


def name_variants(name: str) -> list[str]:
    """Normalised spelling variants: full, without parenthetical, parenthetical only."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper().replace(">", "A").replace("@", "I").replace("\\", "I")  # legacy-font mojibake
    for old, new in SPELLING_ALIASES.items():
        s = s.replace(old, new)
    variants = {s}
    variants.add(re.sub(r"\([^)]*\)", " ", s))
    variants.update(re.findall(r"\(([^)]*)\)", s))
    out = []
    for v in variants:
        v = re.sub(r"[^A-Z0-9]", "", v)
        if v:
            out.append(v)
    return out


def name_score(a: str, b: str) -> float:
    best = 0.0
    for x in name_variants(a):
        for y in name_variants(b):
            if x == y:
                return 1.0
            r = difflib.SequenceMatcher(None, x, y).ratio()
            if min(len(x), len(y)) >= 6 and (x in y or y in x):
                r = max(r, 0.9)
            best = max(best, r)
    return best


def join_by_state_name(polys: gpd.GeoDataFrame, table: pd.DataFrame, cutoff: float = 0.8):
    """One-to-one state-scoped name match polygon -> table row index.

    Returns (mapping poly_index -> table_index, list of report tuples).
    """
    mapping: dict[int, int] = {}
    report: list[tuple[str, str, str, float]] = []
    used: set[int] = set()
    for state, pg in polys.groupby("state"):
        tg = table[table["state"].str.strip().str.upper() == state.strip().upper()]
        # exact (normalised) matches first
        for pi, prow in pg.iterrows():
            for ti, trow in tg.iterrows():
                if ti not in used and name_variants(prow["district"])[0] == name_variants(trow["district"])[0]:
                    mapping[pi] = ti
                    used.add(ti)
                    break
        # one-to-one fuzzy pass, best score first, ties left unmatched
        left_p = [i for i in pg.index if i not in mapping]
        left_t = [i for i in tg.index if i not in used]
        cands = sorted(
            ((name_score(polys.at[p, "district"], table.at[t, "district"]), p, t)
             for p in left_p for t in left_t),
            key=lambda x: (-x[0], x[1], x[2]),
        )
        for score, p, t in cands:
            if score < cutoff:
                break
            if p in mapping or t in used:
                continue
            rivals = [s for s, pp, tt in cands if s >= score - 1e-9 and (pp == p) != (tt == t)
                      and pp not in mapping and tt not in used and (pp == p or tt == t)]
            if rivals:  # ambiguous tie: do not guess
                continue
            mapping[p] = t
            used.add(t)
            report.append((state, polys.at[p, "district"], table.at[t, "district"], score))
    return mapping, report


def load_data():
    districts = gpd.read_parquet(DATA_DIR / "administrative" / "india_districts_lgd.parquet")
    states = gpd.read_parquet(DATA_DIR / "administrative" / "india_states_lgd.parquet")
    access = pd.read_csv(DATA_DIR / "analysis" / "india_district_access_summary.csv")
    travel = pd.read_csv(DATA_DIR / "analysis" / "nh_district_travel_time_summary.csv")
    districts = districts.reset_index(drop=True)
    districts["district_code"] = norm_code(districts["district_code"])
    travel["district_code"] = norm_code(travel["district_code"])
    access = access[access["district"] != "__STATE__"].reset_index(drop=True)
    return districts, states, access, travel


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
    """rail_station_median_km per polygon. The summary has no code column, so join by state-scoped name."""
    print("\n[join] access summary -> district polygons (no district-code column exists; state-scoped name join)")
    print(f"  access rows (excluding __STATE__): {len(access)}; polygons: {len(districts)}")
    if access.duplicated(["state", "district"]).any():
        print("  WARNING: duplicate (state, district) rows in access summary")
    mapping, report = join_by_state_name(districts, access)
    n_exact = len(mapping) - len(report)
    print(f"  exact normalised-name matches: {n_exact}")
    print(f"  fuzzy / alias matches (one-to-one, cutoff 0.80): {len(report)}")
    for state, pname, aname, score in report:
        print(f"    [{score:.2f}] {state}: polygon {pname!r} <- access row {aname!r}")
    vals = pd.Series(np.nan, index=districts.index, dtype=float)
    for p, t in mapping.items():
        vals[p] = access.at[t, "rail_station_median_km"]
    unmatched_p = districts.index.difference(list(mapping))
    unused_t = access.index.difference(list(mapping.values()))
    print(f"  polygons with a value: {len(mapping)}/{len(districts)}; without: {len(unmatched_p)}")
    for p in unmatched_p:
        print(f"    unmatched polygon: {districts.at[p, 'state']} / {districts.at[p, 'district']} "
              f"(code {districts.at[p, 'district_code']})")
    for t in unused_t:
        print(f"    unused access row: {access.at[t, 'state']} / {access.at[t, 'district']}")
    n_nan = int(access.loc[list(mapping.values()), "rail_station_median_km"].isna().sum())
    print(f"  matched rows with NaN measure: {n_nan}")
    return vals


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
    districts_p.plot(ax=ax, color=colors, edgecolor=th["surface"], linewidth=0.07)
    states_p.plot(ax=ax, facecolor="none", edgecolor=to_rgba(th["muted"], 0.6), linewidth=0.35)
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
    fig.text(0.05, 0.03, FOOTER, ha="left", va="center", fontsize=6.6, color=th["muted"])

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

    districts, states, access, travel = load_data()
    print(f"[load] districts={len(districts)} states={len(states)} access={len(access)} travel={len(travel)}")
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
