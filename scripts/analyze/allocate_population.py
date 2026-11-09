"""Allocate Census-2011 district population to current (post-2011) districts.

Current districts (780, LGD) created after Census 2011 have no 2011
population, which left NaN weights in accessibility summaries. This script
allocates each 2011 district's population across current districts by
geometric intersection:

    pop_est(current) = sum over parents [ pop_2011(parent) x
                             area(parent ∩ current) / area(parent) ]

Renames, splits, and merges are all handled by the same formula. Where
village files exist (SoI, 27 states), a 50/50 blend with village-count-share
corrects the urban-carve-out bias (a dense new city district gets little
area but many villages relative to its rural parent). Current districts
whose geometry is a single unchanged 2011 parent (>=90% overlap both ways) keep the
exact Census 2011 figure of that parent (method=census2011) and are never
rescaled. Only the estimated rows (blended / area_share) are scaled, within
each state group (AP+Telangana, J&K+Ladakh, every other state alone), so that
the group total equals its Census 2011 target; the script raises if a group's
residual is not positive or the scale factor leaves [0.5, 2.0].

Outputs data/demographic/district_population_estimates.csv with per-row
method in {census2011, area_share, blended}.

Usage: python scripts/analyze/allocate_population.py
"""
from __future__ import annotations

import glob
import pathlib
import re
import sys

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR  # noqa: E402

PROJ = 7755
OFFICIAL_TOTAL = 1_210_854_977
EXACT_OVERLAP = 0.90  # two-way overlap needed to treat a current district as an unchanged 2011 district
OUT = DATA_DIR / "demographic" / "district_population_estimates.csv"


def norm(s: str) -> str:
    s = re.sub(r"\s+", " ", str(s).lower().strip())
    s = re.sub(r"\s*\([^)]*\)", "", s)
    for a, b in (("&", "and"), ("orissa", "odisha"), ("uttaranchal", "uttarakhand"),
                 ("pondicherry", "puducherry"), ("nct of ", ""),
                 (" district", ""), ("twenty four", "24"), (" - ", "-"), (" -", "-"), ("- ", "-")):
        s = s.replace(a, b)
    return s.replace(" ", "")  # space-insensitive matching


def load_parents() -> gpd.GeoDataFrame:
    d11 = gpd.read_file(DATA_DIR / "administrative" / "india_districts.geojson")
    census = pd.read_csv(DATA_DIR / "demographic" / "census2011_district_key_indicators.csv")
    census["key"] = list(zip(census.state.str.lower(), census.district.map(norm)))
    # alias rows: "Khandwa (East Nimar)" also keyed as "eastnimar"
    extra = []
    for _, r in census.iterrows():
        m = re.search(r"\(([^)]+)\)", str(r.district))
        if m and m.group(1).strip():
            extra.append({"key": (r.state.lower(), norm(m.group(1))), "Population": r.Population})
    census = pd.concat([census, pd.DataFrame(extra).drop_duplicates("key")], ignore_index=True)
    d11["key"] = list(zip(d11.state.str.lower(), d11.district.map(norm)))
    merged = d11.merge(census[["key", "Population"]], on="key", how="left", suffixes=("", "_c"))
    missed = merged[merged.Population.isna()]
    if len(missed):
        print(f"WARN: {len(missed)} 2011 districts lack census population "
              f"(excluded): {sorted(set(zip(missed.state, missed.district)))}")
        merged = merged[merged.Population.notna()]
    merged = merged.to_crs(PROJ)
    merged["geometry"] = merged.geometry.make_valid()
    merged["parent_area"] = merged.area
    # district_key is NOT unique across states (Delhi/Sikkim East-North-South-West, Bilaspur, Hamirpur,
    # Raigarh, Bijapur, Aurangabad, Pratapgarh): scope the parent identity by state
    merged["district_key"] = merged["state"].str.lower() + "|" + merged["district_key"].astype(str)
    return merged[["state", "district", "district_key", "Population",
                   "parent_area", "geometry"]].rename(
        columns={"state": "p_state", "district": "p_district",
                 "district_key": "p_key", "Population": "p_pop"})


# Current states whose 2011 population sits inside a combined 2011 state (Telangana in AP, Ladakh in J&K)
GROUP_OF_CURRENT = {"telangana": "andhra pradesh", "ladakh": "jammu and kashmir"}


def group_key(state: str) -> str:
    """Census-2011 state group a current state/UT belongs to."""
    sl = str(state).lower()
    return GROUP_OF_CURRENT.get(sl, sl)


def poly_key(state, district_code, district):
    """Identity of a current district polygon: (state, code); (state, name) when the code is null."""
    if pd.notna(district_code):
        return (str(state).lower(), int(district_code))
    return (str(state).lower(), str(district))


def village_counts() -> dict:
    """SoI village count per CURRENT district polygon, keyed by ``poly_key``.

    Counts come from the per-state village access tables after spatial reassignment
    (scripts/analyze/reassign_village_districts.py): every village is assigned to the polygon
    that contains it, scoped to its own state. The source ``district_code`` is never used
    (the SoI scheme collides with the polygon scheme across states). Only the 27 states
    published by SoI as village polygons are counted (border-state habitation points are
    not villages); the 'Disputed (Jharkhand)' placeholder is excluded.
    """
    counts: dict = {}
    admin = DATA_DIR / "administrative"
    pq = admin / "india_districts_lgd.parquet"
    names = (pd.read_parquet(pq, columns=["state"]) if pq.exists()
             else gpd.read_file(admin / "india_districts_lgd.geojson", ignore_geometry=True))["state"]
    state_by_slug = {st.lower().replace(" ", "_"): st for st in names.unique()}
    for f in sorted((DATA_DIR / "analysis").glob("*_village_access.csv")):
        slug = f.name[: -len("_village_access.csv")]
        if not ((admin / "villages_parquet" / f"{slug}_soi_villages.parquet").exists()
                or (admin / "villages" / f"{slug}_soi_villages.geojson").exists()):
            continue
        t = pd.read_csv(f, usecols=["district", "district_code", "exclude_from_district_stats"],
                        dtype={"district_code": "Int64"})
        t = t[~t["exclude_from_district_stats"].astype(bool)]
        state = state_by_slug[slug]
        for (code, name), n in t.groupby(["district_code", "district"], dropna=False).size().items():
            k = poly_key(state, code, name)
            counts[k] = counts.get(k, 0) + int(n)
    return counts


def main() -> None:
    parents = load_parents()
    current = gpd.read_file(DATA_DIR / "administrative" / "india_districts_lgd.geojson").to_crs(PROJ)
    current["geometry"] = current.geometry.make_valid()
    current["district_code"] = pd.to_numeric(current["district_code"], errors="coerce").astype("Int64")
    cur = current[["state", "district", "district_code", "geometry"]]
    print(f"parents(2011): {len(parents)} | current: {len(cur)}")

    inter = gpd.overlay(cur, parents, how="intersection", keep_geom_type=True)
    inter["inter_area"] = inter.area
    inter["pop_share"] = inter["p_pop"] * inter["inter_area"] / inter["parent_area"]
    # drop boundary-noise slivers: pairs under 2% of BOTH the parent's and
    # the eventual child's area are source-disagreement artifacts, not real
    # splits (e.g. 5 km^2 Thane overlap inside Raigad)
    child_area = inter.groupby(["state", "district_code"], dropna=False)["inter_area"].transform("sum")
    inter["child_area_tot"] = child_area
    sliver = (inter["inter_area"] / inter["parent_area"] < 0.02) & (inter["inter_area"] / child_area < 0.02)
    if sliver.any():
        print(f"dropping {int(sliver.sum())} boundary-noise sliver pairs "
              f"(<2% of both parent and child)")
        inter = inter[~sliver].copy()
    # renormalize so each parent's children sum back to its census total —
    # but only where the two boundary sources actually overlap; parents whose
    # geometry matches poorly (<70% coverage) are dropped, else renorm would
    # dump their entire population onto an intersection sliver
    # NOTE: no per-parent renormalization — it explodes when the two boundary
    # sources disagree (WB Sundarbans). State-total scaling below restores
    # exact totals robustly instead.
    inter["area_frac_of_current"] = inter["inter_area"] / inter.groupby(
        ["state", "district_code"], dropna=False)["inter_area"].transform("sum")  # post-sliver-drop

    # exact census rows: one parent covers >= EXACT_OVERLAP of the current district AND that same
    # parent puts >= EXACT_OVERLAP of its own area into THIS current district (a child carved out
    # of a larger parent is an estimate, not the parent's census figure). The 2011 (DataMeet) and
    # current (ABDB) boundaries disagree by several percent along coasts and borders, e.g. the
    # unchanged Mumbai Suburban overlaps 99.0% / 94.6%, so 97% would misclassify it.
    inter["parent_in_child_frac"] = inter["inter_area"] / inter["parent_area"]
    exact_mask = ((inter["area_frac_of_current"] >= EXACT_OVERLAP)
                  & (inter["parent_in_child_frac"] >= EXACT_OVERLAP))
    exact = inter[exact_mask]

    area_alloc = (inter.groupby(["state", "district", "district_code"], dropna=False)
                  .agg(pop_area_share=("pop_share", "sum")).reset_index())
    # exact rows are keyed by polygon identity (state + code, name for null-code polygons)
    exact_parent = {poly_key(r.state, r.district_code, r.district): (r.p_state, r.p_district, int(r.p_pop))
                    for r in exact.itertuples()}
    if len(exact_parent) != len(exact):
        raise SystemExit("a current district matched two exact parents; refusing to guess")
    area_alloc["is_exact"] = [poly_key(st, c, d) in exact_parent for st, c, d in
                              zip(area_alloc.state, area_alloc.district_code, area_alloc.district)]

    # blend with village-count share where SoI villages exist — each
    # intersection pair gets a FRACTION of the child's villages proportional
    # to the pair's area share of the child, so boundary slivers from
    # neighbouring parents cannot claim the child's whole village count
    vcount = village_counts()
    area_alloc["villages"] = [vcount.get(poly_key(st, c, d), 0) for st, c, d in
                              zip(area_alloc.state, area_alloc.district_code, area_alloc.district)]
    inter_codes = inter[["state", "district", "district_code", "p_key", "inter_area",
                         "area_frac_of_current"]].copy()
    inter_codes["v_in_pair"] = (pd.Series([vcount.get(poly_key(st, c, d), 0) for st, c, d in
                                           zip(inter_codes.state, inter_codes.district_code, inter_codes.district)],
                                          index=inter_codes.index, dtype=float)
                                * inter_codes.area_frac_of_current)
    pv = inter_codes.groupby("p_key")["v_in_pair"].sum().rename("v_parent_sum")
    inter_codes = inter_codes.merge(pv, on="p_key", how="left")
    inter_codes["v_share_frac"] = inter_codes["v_in_pair"] / inter_codes["v_parent_sum"]
    inter_codes = inter_codes.merge(
        parents[["p_key", "p_pop"]], on="p_key", how="left")
    inter_codes["pop_vill_share"] = inter_codes["v_share_frac"] * inter_codes["p_pop"]
    vill_alloc = (inter_codes.groupby(["state", "district", "district_code"], dropna=False)
                  .agg(pop_vill_share=("pop_vill_share", "sum")).reset_index())

    # name-matched pairs for final capping: a current district whose
    # normalized name equals a 2011 parent's (same state) can never plausibly
    # exceed that parent's census population by much — bounds boundary-noise
    # overcounts (e.g. South 24 Parganas vs the Sundarbans mismatch)
    inter["cur_key"] = inter["district"].map(norm)
    inter["par_key"] = inter["p_district"].map(norm)
    matched_pairs = inter[(inter.cur_key == inter.par_key) &
                          (inter.state.str.lower() == inter.p_state.str.lower())]
    cap_map = {}
    for _, row in matched_pairs.iterrows():
        cap_map[(row["state"], float(row["district_code"]))] = row["p_pop"]

    df = area_alloc.merge(vill_alloc, on=["state", "district", "district_code"], how="left")
    df["method"] = "area_share"
    df.loc[df.is_exact, "method"] = "census2011"
    blendable = (~df.is_exact) & df.pop_vill_share.notna() & (df.pop_vill_share > 0)
    df.loc[blendable, "method"] = "blended"
    df["pop_2011"] = df["pop_area_share"]
    df.loc[blendable, "pop_2011"] = 0.5 * df.loc[blendable, "pop_area_share"] + \
                                    0.5 * df.loc[blendable, "pop_vill_share"]
    # census2011 rows are held at the EXACT Census 2011 population of their parent district
    df["exact_census_pop"] = [exact_parent[poly_key(st, c, d)][2] if ex else np.nan for st, c, d, ex in
                              zip(df.state, df.district_code, df.district, df.is_exact)]
    df.loc[df.is_exact, "pop_2011"] = df.loc[df.is_exact, "exact_census_pop"]

    # final cap at parent census population +2% headroom (state-total scaling
    # below redistributes whatever this trims)
    capped = 0
    for i, row in df.iterrows():
        if pd.isna(row["district_code"]) or row["is_exact"]:
            continue
        key = (row["state"], float(row["district_code"]))
        if key in cap_map and row["pop_2011"] > cap_map[key] * 1.02:
            df.at[i, "pop_2011"] = cap_map[key] * 1.02
            capped += 1
    if capped:
        print(f"final cap applied to {capped} name-matched districts")

    # ---- pre-scaling diagnostics ----
    national = df.pop_2011.sum()
    print(f"\nnational allocated total (before group scaling): {national:,.0f} (official {OFFICIAL_TOTAL:,}, "
          f"delta {abs(national - OFFICIAL_TOTAL)/OFFICIAL_TOTAL:.3%})")
    parent_check = inter.groupby("p_key").agg(
        allocated=("pop_share", "sum"), actual=("p_pop", "first"))
    bad = (parent_check.allocated - parent_check.actual).abs() / parent_check.actual
    print(f"parents whose children sum back within 0.5%: {(bad <= 0.005).mean():.1%} "
          f"({len(parent_check)} parents)")
    outliers = df[(df.pop_2011 > 0) & (~df.is_exact)].nlargest(5, "pop_2011")
    print("largest estimated districts:")
    print(outliers[["state", "district", "pop_2011", "method"]].to_string(index=False))

    # ---- group targets: Census 2011 totals of the 2011 states each current group covers ----
    census = pd.read_csv(DATA_DIR / "demographic" / "census2011_district_key_indicators.csv")
    state_2011_tot = census.groupby(census.state.str.lower())["Population"].sum()
    df["group"] = df.state.map(group_key)
    targets = state_2011_tot.to_dict()  # census state names already include merged UTs (DNH+DD)
    missing = sorted(set(df.group) - set(targets))
    if missing:
        raise SystemExit(f"no Census 2011 target for state group(s): {missing}")
    print(f"\nCensus 2011 group targets (sum {sum(targets.values()):,}; official {OFFICIAL_TOTAL:,}):")

    # Hold `census2011` rows fixed; scale ONLY the estimated rows (blended / area_share) within each
    # group so the group total equals its target. Largest-remainder rounding keeps integer totals exact.
    df["pop_final"] = df["pop_2011"]
    factors = {}
    for g, idx in df.groupby("group").groups.items():
        grp = df.loc[idx]
        ex_sum = float(grp.loc[grp.is_exact, "pop_2011"].sum())
        est = grp[~grp.is_exact]
        est_sum = float(est["pop_2011"].sum())
        residual = int(targets[g]) - int(ex_sum)
        if est.empty or est_sum <= 0:
            factors[g] = (None, ex_sum, residual, int(targets[g]))
            print(f"  {g:45s} target {int(targets[g]):>12,}  exact rows {int(ex_sum):>12,}  "
                  f"no estimated rows to scale; unallocated residual {residual:,}")
            continue
        if residual <= 0:
            raise SystemExit(f"{g}: residual {residual:,} (target {int(targets[g]):,} minus exact rows "
                             f"{int(ex_sum):,}) is not positive; refusing to scale")
        f = residual / est_sum
        if not 0.5 <= f <= 2.0:
            raise SystemExit(f"{g}: scale factor {f:.4f} outside [0.5, 2.0] (residual {residual:,}, "
                             f"estimated rows {est_sum:,.0f}); refusing to scale")
        raw = est["pop_2011"].to_numpy(dtype=float) * f
        base = np.floor(raw).astype(np.int64)
        short = residual - int(base.sum())
        order = np.argsort(-(raw - base), kind="stable")
        base[order[:short]] += 1
        df.loc[est.index, "pop_final"] = base
        factors[g] = (f, ex_sum, residual, int(targets[g]))
        print(f"  {g:45s} target {int(targets[g]):>12,}  exact rows {int(ex_sum):>12,}  "
              f"estimated {est_sum:>14,.0f} -> {residual:>12,}  scale factor {f:.4f}")
    # unscaled groups (no estimated rows) keep their exact values
    df["pop_2011"] = df["pop_final"]

    # ---- verification ----
    national = int(round(df.pop_2011.sum()))
    print(f"\nnational total: {national:,}  (official {OFFICIAL_TOTAL:,}, delta {national - OFFICIAL_TOTAL:+,}; "
          f"sum of group targets {int(sum(targets.values())):,})")
    bad = []
    census_key = {(r.state.lower(), norm(r.district)): int(r.Population) for r in census.itertuples()}
    for r in df[df.is_exact].itertuples():
        ps, pd_, pp = exact_parent[poly_key(r.state, r.district_code, r.district)]
        src = census_key.get((ps.lower(), norm(pd_)))
        if src is None:  # alias spellings such as "Khandwa (East Nimar)"
            src = next((int(c.Population) for c in census.itertuples()
                        if c.state.lower() == ps.lower() and
                        norm(pd_) in {norm(c.district), norm(re.sub(r".*\(([^)]+)\).*", r"\1", c.district))}), None)
        if src is None or int(round(r.pop_2011)) != src:
            bad.append((r.state, r.district, int(round(r.pop_2011)), src))
    print(f"census2011 rows: {int(df.is_exact.sum())}; mismatches vs source Census 2011 district population: "
          f"{len(bad)} {bad[:5]}")
    # plausibility flags (blend itself unchanged): estimated rows far above the area-only estimate
    # or above their largest 2011 parent's population
    big_parent = (inter.sort_values("inter_area", ascending=False).drop_duplicates(["state", "district", "district_code"])
                  .set_index(["state", "district", "district_code"])["p_pop"])
    flags = []
    for r in df[~df.is_exact].itertuples():
        pp = big_parent.get((r.state, r.district, r.district_code))
        reasons = []
        if r.pop_area_share > 0 and r.pop_2011 > 2 * r.pop_area_share:
            reasons.append(f">2x area-only ({r.pop_area_share:,.0f})")
        if pp is not None and pd.notna(pp) and r.pop_2011 > pp:
            reasons.append(f">parent ({pp:,.0f})")
        if reasons:
            flags.append((r.state, r.district, int(r.pop_2011), r.method, "; ".join(reasons)))
    print(f"plausibility flags ({len(flags)}):")
    for f_ in flags:
        print("  ", f_)

    out = df[["state", "district", "district_code", "pop_2011", "method",
              "villages"]].sort_values(["state", "district"])
    out["pop_2011"] = out.pop_2011.round(0).astype("int64")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"\nWrote {len(out)} current districts -> {OUT}")
    print("coverage:", out.method.value_counts().to_dict())


if __name__ == "__main__":
    main()
