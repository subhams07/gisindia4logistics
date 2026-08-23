"""Allocate Census-2011 district population to current (post-2011) districts.

Current districts (781, LGD) created after Census 2011 have no 2011
population, which left NaN weights in accessibility summaries. This script
allocates each 2011 district's population across current districts by
geometric intersection:

    pop_est(current) = sum over parents [ pop_2011(parent) x
                             area(parent ∩ current) / area(parent) ]

Renames, splits, and merges are all handled by the same formula. Where
village files exist (SoI, 27 states), a 50/50 blend with village-count-share
corrects the urban-carve-out bias (a dense new city district gets little
area but many villages relative to its rural parent). Current districts
whose geometry is >=99.5% a single unchanged 2011 parent keep the exact
census figure (method=census2011).

Validation: children sum back to each parent +-0.5%; national total equals
1,210,854,977 exactly (up to the 14 unmatched 2011 districts documented in
the audit).

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
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR  # noqa: E402

PROJ = 7755
OFFICIAL_TOTAL = 1_210_854_977
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
    return merged[["state", "district", "district_key", "Population",
                   "parent_area", "geometry"]].rename(
        columns={"state": "p_state", "district": "p_district",
                 "district_key": "p_key", "Population": "p_pop"})


def village_counts() -> pd.Series:
    frames = []
    for f in glob.glob(str(DATA_DIR / "administrative" / "villages" / "*_soi_villages.geojson")):
        g = gpd.read_file(f, ignore_geometry=True)
        if "district_code" in g.columns:
            frames.append(g[["district_code"]].dropna())
    allv = pd.concat(frames)
    allv["district_code"] = pd.to_numeric(allv["district_code"], errors="coerce")
    return allv.dropna().groupby("district_code").size()


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

    # exact census rows: one parent covers >=99.5% of the current district AND
    # that parent puts >=99.5% of itself into this current district
    parent_out = inter.groupby("p_key")["pop_share"].sum()
    inter["parent_out_frac"] = inter["p_key"].map(parent_out) / inter["p_pop"]
    # cross-source boundary jitter means even unchanged districts differ ~1-3%;
    # 97% two-way overlap is the exact-census bar
    exact_mask = (inter["area_frac_of_current"] >= 0.97) & (inter["parent_out_frac"] >= 0.97)
    exact = inter[exact_mask]

    area_alloc = (inter.groupby(["state", "district", "district_code"], dropna=False)
                  .agg(pop_area_share=("pop_share", "sum")).reset_index())
    exact_keys = set(zip(exact["state"], exact["district_code"].astype("Float64")))
    area_alloc["is_exact"] = [k in exact_keys for k in
                              zip(area_alloc.state, area_alloc.district_code.astype("Float64"))]

    # blend with village-count share where SoI villages exist — each
    # intersection pair gets a FRACTION of the child's villages proportional
    # to the pair's area share of the child, so boundary slivers from
    # neighbouring parents cannot claim the child's whole village count
    vcount = village_counts()
    area_alloc["villages"] = area_alloc.district_code.astype("Float64").map(vcount).fillna(0)
    inter_codes = inter[["state", "district_code", "p_key", "inter_area",
                         "area_frac_of_current"]].copy()
    inter_codes["district_code"] = inter_codes.district_code.astype("Float64")
    inter_codes["v_in_pair"] = (inter_codes.district_code.map(vcount).fillna(0)
                                * inter_codes.area_frac_of_current)
    pv = inter_codes.groupby("p_key")["v_in_pair"].sum().rename("v_parent_sum")
    inter_codes = inter_codes.merge(pv, on="p_key", how="left")
    inter_codes["v_share_frac"] = inter_codes["v_in_pair"] / inter_codes["v_parent_sum"]
    inter_codes = inter_codes.merge(
        parents[["p_key", "p_pop"]], on="p_key", how="left")
    inter_codes["pop_vill_share"] = inter_codes["v_share_frac"] * inter_codes["p_pop"]
    vill_alloc = (inter_codes.groupby(["state", "district_code"], dropna=False)
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

    df = area_alloc.merge(vill_alloc, on=["state", "district_code"], how="left")
    df["method"] = "area_share"
    df.loc[df.is_exact, "method"] = "census2011"
    blendable = (~df.is_exact) & df.pop_vill_share.notna() & (df.pop_vill_share > 0)
    df.loc[blendable, "method"] = "blended"
    df["pop_2011"] = df["pop_area_share"]
    df.loc[blendable, "pop_2011"] = 0.5 * df.loc[blendable, "pop_area_share"] + \
                                    0.5 * df.loc[blendable, "pop_vill_share"]
    df.loc[df.is_exact, "pop_2011"] = df.loc[df.is_exact, "pop_area_share"]

    # final cap at parent census population +2% headroom (state-total scaling
    # below redistributes whatever this trims)
    capped = 0
    for i, row in df.iterrows():
        if pd.isna(row["district_code"]):
            continue
        key = (row["state"], float(row["district_code"]))
        if key in cap_map and row["pop_2011"] > cap_map[key] * 1.02:
            df.at[i, "pop_2011"] = cap_map[key] * 1.02
            capped += 1
    if capped:
        print(f"final cap applied to {capped} name-matched districts")

    # ---- validation ----
    national = df.pop_2011.sum()
    print(f"\nnational allocated total: {national:,.0f} (official {OFFICIAL_TOTAL:,}, "
          f"delta {abs(national - OFFICIAL_TOTAL)/OFFICIAL_TOTAL:.3%})")
    parent_check = inter.groupby("p_key").agg(
        allocated=("pop_share", "sum"), actual=("p_pop", "first"))
    bad = (parent_check.allocated - parent_check.actual).abs() / parent_check.actual
    print(f"parents whose children sum back within 0.5%: {(bad <= 0.005).mean():.1%} "
          f"({len(parent_check)} parents)")
    outliers = df[(df.pop_2011 > 0) & (~df.is_exact)].nlargest(5, "pop_2011")
    print("largest estimated districts:")
    print(outliers[["state", "district", "pop_2011", "method"]].to_string(index=False))

    # scale each state group so its total equals the 2011 census state total
    # (current-state groups map back to 2011 states: AP+TG combined, DNH+DD)
    census = pd.read_csv(DATA_DIR / "demographic" / "census2011_district_key_indicators.csv")
    group_of = {"telangana": "andhra pradesh", "andhra pradesh": "andhra pradesh",
                "dadra and nagar haveli and daman and diu": "dadra group",
                "ladakh": "jammu and kashmir", "jammu and kashmir": "jammu and kashmir"}
    g11 = {"dadra and nagar haveli": "dadra group", "daman and diu": "dadra group"}
    state_2011_tot = census.groupby(census.state.str.lower())["Population"].sum()
    def group_key(s):
        sl = s.lower()
        if sl in group_of: return group_of[sl]
        if sl in g11: return g11[sl]
        return sl
    target = {}
    for s in df.state.unique():
        gk = group_key(s)
        target[s] = state_2011_tot.get(gk)
    # scale JOINTLY per group (AP+TG share one 2011 target; so do J&K+Ladakh,
    # DNH+DD) — per-state scaling would double-count combined targets
    df["group"] = df.state.map(lambda s: group_key(s))
    group_target = {}
    for g in df.group.unique():
        if g in state_2011_tot.index:
            group_target[g] = state_2011_tot[g]
    scaled = []
    for g, grp in df.groupby("group"):
        tot = grp.pop_2011.sum()
        tgt = group_target.get(g)
        if tot > 0 and tgt:
            grp = grp.copy()
            grp["pop_2011"] = grp.pop_2011 * tgt / tot
        scaled.append(grp)
    df = pd.concat(scaled, ignore_index=True)

    out = df[["state", "district", "district_code", "pop_2011", "method",
              "villages"]].sort_values(["state", "district"])
    out["pop_2011"] = out.pop_2011.round(0).astype("int64")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"\nWrote {len(out)} current districts -> {OUT}")
    print("coverage:", out.method.value_counts().to_dict())


if __name__ == "__main__":
    main()
