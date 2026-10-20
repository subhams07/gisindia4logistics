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

Joins (all on LGD ``district_code``, scoped by state)
------------------------------------------------------
* Rail map: the value is recomputed from the per-state village files
  ``data/analysis/<state_slug>_village_access.csv`` (median of
  ``dist_rail_station_km`` over all villages of the district), because the
  district summary has no code column. The village files use an older SoI code
  scheme (new districts carry different codes and some codes collide with another
  district), so a pure district_code join would paint wrong districts; rows are
  therefore grouped by state + district name and assigned to polygons, with the
  code only breaking ties. The script prints a diagnostic of what a pure code
  join would have done.
* ICD map: ``nh_district_travel_time_summary.csv`` joined on (state,
  district_code); island districts have no road route.
* Polygons with a null code (Nazul, Mirpur, Muzaffarabad) fall back to an exact
  normalised (state, district) name match. LGD code 598 is carried by two
  districts in different states, hence the state scoping. Nothing is guessed:
  unmatched polygons are drawn grey and reported.
* No-data polygons are drawn first, valued polygons on top, so a no-data
  polygon can never hide a valued one (the layer has two overlapping
  Maharashtra "DHULE" polygons).

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


def norm_name(name: str) -> str:
    """Upper-case alphanumerics only, accents stripped (for exact name fallback)."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def state_slug(name: str) -> str:
    """'Dadra and Nagar Haveli and Daman and Diu' -> 'dadra_and_nagar_haveli_and_daman_and_diu'."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def name_variants(name: str) -> list[str]:
    """Ordered normalised spellings: full, without parenthetical, parenthetical content.

    Legacy-font mojibake in the source names (``>``=A, ``@``=I, ``\\``=I) is repaired first.
    """
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper().replace(">", "A").replace("@", "I").replace("\\", "I")
    s = s.replace("TWENTY-FOUR", "24").replace("TWENTY FOUR", "24")
    s = s.replace("HOWRAH", "HAORA")  # same district, two official spellings
    cands = [s, re.sub(r"\([^)]*\)", " ", s)] + re.findall(r"\(([^)]*)\)", s)
    out: list[str] = []
    for v in cands:
        v = re.sub(r"[^A-Z0-9]", "", v)
        if v and v not in out:
            out.append(v)
    return out


def name_score(a: str, b: str) -> float:
    """1.0 for an identical normalised spelling, else fuzzy ratio (substring of >=6 chars counts 0.9)."""
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


def load_data():
    districts = gpd.read_parquet(DATA_DIR / "administrative" / "india_districts_lgd.parquet")
    states = gpd.read_parquet(DATA_DIR / "administrative" / "india_states_lgd.parquet")
    travel = pd.read_csv(DATA_DIR / "analysis" / "nh_district_travel_time_summary.csv")
    districts = districts.reset_index(drop=True)
    districts["district_code"] = norm_code(districts["district_code"])
    travel["district_code"] = norm_code(travel["district_code"])
    return districts, states, travel


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


def join_access(districts: gpd.GeoDataFrame, cutoff: float = 0.8) -> pd.Series:
    """Median ``dist_rail_station_km`` per polygon from the per-state village files.

    Intended key is (state, district_code), but the village files carry the older SoI
    code scheme: for newer districts the codes differ from the polygon layer, and some
    codes collide with a *different* district. So the key is (state, district name): every
    village row is grouped by its district name (including rows with a null code and
    mojibake/misspelt variants such as 'DEVBHUMI DWARKAl'), each group is assigned to the
    best-scoring polygon of the same state (>= cutoff), with the district code only used
    to break ties between equally named polygons. Ambiguous groups are left unassigned.
    """
    print("\n[join] per-state village files -> district polygons (median dist_rail_station_km)")
    slugs = {st: state_slug(st) for st in sorted(districts["state"].unique())}
    missing = [st for st, sl in slugs.items() if not (DATA_DIR / "analysis" / f"{sl}_village_access.csv").exists()]
    print(f"  states in polygon layer: {len(slugs)}; mapped to a village file: {len(slugs) - len(missing)}"
          + (f"; MISSING: {missing}" if missing else ""))
    frames = []
    for st, sl in slugs.items():
        f = DATA_DIR / "analysis" / f"{sl}_village_access.csv"
        if f.exists():
            v = pd.read_csv(f, usecols=["district", "district_code", "dist_rail_station_km"])
            v["state"] = st
            frames.append(v)
    vill = pd.concat(frames, ignore_index=True)
    vill["district_code"] = norm_code(vill["district_code"])
    n_null_code = int(vill["district_code"].isna().sum())
    vill = vill.dropna(subset=["dist_rail_station_km", "district"]).reset_index(drop=True)
    print(f"  village rows used: {len(vill)}; of which null district_code: {n_null_code}")

    # --- diagnostic: what a pure (state, district_code) join would do ---
    grp_code = vill.dropna(subset=["district_code"]).groupby(["state", "district_code"])["district"].agg(
        lambda x: x.value_counts().index[0])
    n_code = n_code_bad = 0
    for _, r in districts.dropna(subset=["district_code"]).iterrows():
        key = (r["state"], r["district_code"])
        if key in grp_code.index:
            n_code += 1
            if name_score(grp_code[key], r["district"]) < cutoff:
                n_code_bad += 1
    n_coded = int(districts["district_code"].notna().sum())
    print(f"  [diagnostic] a pure (state, district_code) join would hit {n_code}/{n_coded} polygons, "
          f"{n_code_bad} of them with a DIFFERENT district name (wrong-district join); "
          f"{n_coded - n_code} polygons have no village group with their code")

    vals = pd.Series(np.nan, index=districts.index, dtype=float)
    nvill = pd.Series(0, index=districts.index, dtype=int)
    stats = dict(exact=0, fuzzy=0, tie=0)
    fuzzy_log, unassigned_groups = [], []
    assigned_p: set[int] = set()
    rows_used = 0
    for st in slugs:
        pg = districts[districts["state"] == st]
        vg = vill[vill["state"] == st]
        names = sorted(vg["district"].unique())
        bucket: dict[int, list[str]] = {i: [] for i in pg.index}
        for nm in names:
            scores = [(name_score(nm, pg.at[i, "district"]), i) for i in pg.index]
            top = max(sc for sc, _ in scores)
            if top < cutoff:
                unassigned_groups.append((st, nm, int((vg["district"] == nm).sum())))
                continue
            best = [i for sc, i in scores if sc >= top - 1e-9]
            if len(best) > 1:  # equally good polygons: tie-break on district code, else do not guess
                codes = vg.loc[vg["district"] == nm, "district_code"].dropna()
                modal = codes.mode().iloc[0] if len(codes) else None
                cb = [i for i in best if modal is not None and pg.at[i, "district_code"] == modal]
                if len(cb) != 1:
                    stats["tie"] += 1
                    unassigned_groups.append((st, nm + " [ambiguous]", int((vg["district"] == nm).sum())))
                    continue
                best = cb
            i = best[0]
            bucket[i].append(nm)
            if top < 1.0:
                stats["fuzzy"] += 1
                fuzzy_log.append((st, nm, pg.at[i, "district"], top))
            else:
                stats["exact"] += 1
        for i, nms in bucket.items():
            if nms:
                x = vg.loc[vg["district"].isin(nms), "dist_rail_station_km"]
                vals[i], nvill[i] = x.median(), len(x)
                rows_used += len(x)
                assigned_p.add(i)
    print(f"  village name-groups assigned: exact {stats['exact']}, fuzzy/variant {stats['fuzzy']} "
          f"(cutoff {cutoff}), ambiguous-left-out {stats['tie']}")
    for st, nm, pn, sc in fuzzy_log:
        print(f"    [{sc:.2f}] {st}: village-file {nm!r} -> polygon {pn!r}")
    print(f"  village rows aggregated into a polygon: {rows_used}/{len(vill)}")
    print(f"  village name-groups with no polygon: {len(unassigned_groups)}")
    for st, nm, n in unassigned_groups:
        print(f"    {st}: {nm!r} ({n} rows)")
    miss = districts.index.difference(sorted(assigned_p))
    print(f"  polygons with a value: {len(assigned_p)}/{len(districts)}; without (drawn grey): {len(miss)}")
    for i in miss:
        print(f"    no-data polygon: {districts.at[i, 'state']} / {districts.at[i, 'district']} "
              f"(code {districts.at[i, 'district_code']})")
    for st, dn in (("Gujarat", "DEVBHUMI DWARKA"), ("Maharashtra", "DHULE")):
        for i in districts.index[(districts["state"] == st) & (districts["district"].str.upper() == dn)]:
            print(f"  check {st} / {dn} (code {districts.at[i, 'district_code']}): "
                  + (f"median {vals[i]:.2f} km from {nvill[i]} villages" if nvill[i] else "no value (no-data)"))
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

    districts, states, travel = load_data()
    print(f"[load] districts={len(districts)} states={len(states)} travel={len(travel)}")
    values = {
        "rail_access": join_access(districts),
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
