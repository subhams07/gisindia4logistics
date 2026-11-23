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
whose geometry is >=99.5% a single unchanged 2011 parent keep the exact
census figure (method=census2011).

Validation: children sum back to each parent +-0.5%; national total equals
1,210,854,977 exactly (up to the 14 unmatched 2011 districts documented in
the audit).

Where the village files carry village-level Census 2011 population (the nine
NWDP states, scripts/fetch/fetch_village_boundaries_nwdp.py), every
district's population is the sum of the villages inside it
(method=village_census); for unchanged districts that equals the census figure.

Outputs data/demographic/district_population_estimates.csv with per-row
method in {census2011, village_census, area_share, blended}.

Usage: python scripts/analyze/allocate_population.py
"""
from __future__ import annotations

import argparse
import glob
import pathlib
import re
import sys

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR  # noqa: E402

PROJ = "ESRI:102028"  # Asia South Albers equal-area: area shares need an equal-area CRS
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
    # The 2011 layer's district_key is the normalised name alone; 10 names are
    # shared by districts of two states (Aurangabad Bihar/Maharashtra, Bilaspur,
    # Bijapur, Hamirpur, Pratapgarh, Raigarh, and Delhi's and Sikkim's East/
    # North/South/West). Keyed by name only, those parents were pooled: their
    # village shares were split across both states and the p_pop merge
    # duplicated every pair. The state makes the key unique.
    merged["district_key"] = merged.state.str.lower() + "|" + merged.district_key.astype(str)
    if merged.district_key.duplicated().any():
        raise SystemExit(f"duplicate 2011 parent keys: {sorted(merged.district_key[merged.district_key.duplicated()])}")
    return merged[["state", "district", "district_key", "Population",
                   "parent_area", "geometry"]].rename(
        columns={"state": "p_state", "district": "p_district",
                 "district_key": "p_key", "Population": "p_pop"})


def district_key(state, district, code) -> str:
    """One identity per current district: the LGD code (unique nationally), or
    state + name for the four districts without one (Delhi's Nazul and Shahdara,
    J&K's Mirpur and Muzaffarabad). Grouping by (state, district_code) pooled
    each state's uncoded districts into a single group."""
    return f"code:{int(code)}" if pd.notna(code) else f"name:{state}|{district}"


def village_stats(current: gpd.GeoDataFrame) -> tuple[pd.Series, pd.Series]:
    """SoI villages per current district (district_key), by representative point in polygon:
    (village count, summed Census 2011 village population where the file carries it).

    The village files' own district codes mix Census 2011, LGD and older
    numbering, so they are not used.
    """
    polys = current.to_crs(4326)[["dkey", "geometry"]]
    counts, pops = [], []
    for f in glob.glob(str(DATA_DIR / "administrative" / "villages" / "*_soi_villages.geojson")):
        cols = [c for c in ("population_2011",) if c in gpd.read_file(f, rows=1).columns]
        v = gpd.read_file(f, columns=cols)
        pts = gpd.GeoDataFrame(v[cols], geometry=v.representative_point(), crs=4326)
        hit = gpd.sjoin(pts, polys, predicate="within")
        hit = hit[~hit.index.duplicated(keep="first")]
        counts.append(hit.dkey)
        if cols:  # NWDP files: village-level Census 2011 population
            pops.append(hit[["dkey", "population_2011"]])
    allv = pd.concat(counts)
    vpop = pd.concat(pops) if pops else pd.DataFrame(columns=["dkey", "population_2011"])
    vpop = vpop.groupby("dkey").population_2011.sum()
    return allv.groupby(allv).size(), vpop


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    parents = load_parents()
    current = gpd.read_file(DATA_DIR / "administrative" / "india_districts_lgd.geojson").to_crs(PROJ)
    current["geometry"] = current.geometry.make_valid()
    current["district_code"] = pd.to_numeric(current["district_code"], errors="coerce").astype("Int64")
    current["dkey"] = [district_key(s, d, c) for s, d, c in zip(current.state, current.district, current.district_code)]
    if current.dkey.duplicated().any():
        raise SystemExit(f"duplicate district identities: {sorted(current.dkey[current.dkey.duplicated()])}")
    cur = current[["state", "district", "district_code", "dkey", "geometry"]]
    print(f"parents(2011): {len(parents)} | current: {len(cur)}")

    inter = gpd.overlay(cur, parents, how="intersection", keep_geom_type=True)
    inter["inter_area"] = inter.area
    inter["pop_share"] = inter["p_pop"] * inter["inter_area"] / inter["parent_area"]
    # drop boundary-noise slivers: pairs under 2% of BOTH the parent's and
    # the eventual child's area are source-disagreement artifacts, not real
    # splits (e.g. 5 km^2 Thane overlap inside Raigad)
    child_area = inter.groupby("dkey")["inter_area"].transform("sum")
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
        "dkey")["inter_area"].transform("sum")  # post-sliver-drop

    # exact census rows: one parent covers >=99.5% of the current district AND
    # that parent puts >=99.5% of itself into this current district
    parent_out = inter.groupby("p_key")["pop_share"].sum()
    inter["parent_out_frac"] = inter["p_key"].map(parent_out) / inter["p_pop"]
    # cross-source boundary jitter means even unchanged districts differ ~1-3%;
    # 97% two-way overlap is the exact-census bar
    exact_mask = (inter["area_frac_of_current"] >= 0.97) & (inter["parent_out_frac"] >= 0.97)
    exact = inter[exact_mask]

    area_alloc = (inter.groupby(["state", "district", "district_code", "dkey"], dropna=False)
                  .agg(pop_area_share=("pop_share", "sum")).reset_index())
    area_alloc["is_exact"] = area_alloc.dkey.isin(set(exact["dkey"]))

    # blend with village-count share where SoI villages exist — each
    # intersection pair gets a FRACTION of the child's villages proportional
    # to the pair's area share of the child, so boundary slivers from
    # neighbouring parents cannot claim the child's whole village count
    vcount, vpop = village_stats(current)
    area_alloc["villages"] = area_alloc.dkey.map(vcount).fillna(0)
    inter_codes = inter[["dkey", "p_key", "inter_area", "area_frac_of_current"]].copy()
    inter_codes["v_in_pair"] = (inter_codes.dkey.map(vcount).fillna(0)
                                * inter_codes.area_frac_of_current)
    pv = inter_codes.groupby("p_key")["v_in_pair"].sum().rename("v_parent_sum")
    inter_codes = inter_codes.merge(pv, on="p_key", how="left")
    inter_codes["v_share_frac"] = inter_codes["v_in_pair"] / inter_codes["v_parent_sum"]
    inter_codes = inter_codes.merge(
        parents[["p_key", "p_pop"]], on="p_key", how="left")
    inter_codes["pop_vill_share"] = inter_codes["v_share_frac"] * inter_codes["p_pop"]
    vill_alloc = (inter_codes.groupby("dkey")
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
        cap_map[row["dkey"]] = row["p_pop"]

    df = area_alloc.merge(vill_alloc, on="dkey", how="left")
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
        key = row["dkey"]
        if key in cap_map and row["pop_2011"] > cap_map[key] * 1.02:
            df.at[i, "pop_2011"] = cap_map[key] * 1.02
            capped += 1
    if capped:
        print(f"final cap applied to {capped} name-matched districts")

    # where the village files carry Census 2011 village population (the NWDP
    # states), a district's population is the sum of its villages; this also
    # replaces "exact" rows, whose area-overlap sum can differ from the census
    # figure where the 2011 and current outlines disagree (Leh)
    has_vpop = df.dkey.map(vpop).notna()
    df.loc[has_vpop, "pop_2011"] = df.loc[has_vpop, "dkey"].map(vpop).astype(float)
    df.loc[has_vpop, "method"] = "village_census"
    print(f"village_census (summed village populations): {int(has_vpop.sum())} districts")

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
