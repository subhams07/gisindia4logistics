"""Village-to-facility accessibility analysis.

For every village in a state (Survey of India village boundaries), computes
straight-line distance to the nearest facility of each selected type
(rail station, port, ICD, ICP, air-cargo terminal, MMLP, IWAI terminal, ...).
Distances are computed in EPSG:7755 (metres) via spatial-indexed nearest join,
then summarized per district with Census-2011 population weighting.

This is the repo's first *analysis* output (vs data cataloguing). Catchment
thresholds default to 25 km; tune per use case. Road-network travel time is a
future upgrade (see AGENTS.md backlog).

Usage:
    python scripts/analyze/nearest_facility.py --state Haryana
    python scripts/analyze/nearest_facility.py --state Sikkim --thresholds 10,25,50
    python scripts/analyze/nearest_facility.py --state Maharashtra --district Pune
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR  # noqa: E402

ANALYSIS_DIR = DATA_DIR / "analysis"
PROJ = 7755  # India NSF LCC (metres)

FACILITY_SOURCES = {
    "nh": DATA_DIR / "roads" / "india_nh_network.geojson",
    "expressway": DATA_DIR / "roads" / "india_nh_network.geojson",
    "rail_station": DATA_DIR / "rail" / "railway_stations.csv",
    "freight_terminal": DATA_DIR / "rail" / "freight_terminals.csv",
    "port": DATA_DIR / "logistics_hubs" / "ports.csv",
    "icd": DATA_DIR / "logistics_hubs" / "icds.csv",
    "icp": DATA_DIR / "logistics_hubs" / "icps.csv",
    "air_cargo": DATA_DIR / "logistics_hubs" / "air_cargo.csv",
    "mmlp": DATA_DIR / "logistics_hubs" / "mmlps.csv",
    "iw_terminal": DATA_DIR / "logistics_hubs" / "inland_waterway_terminals.csv",
    "fci_depot": DATA_DIR / "logistics_hubs" / "fci_depots.csv",
    "toll_plaza": DATA_DIR / "roads" / "toll_plazas.csv",
}

STATE_FILE_MAP = {
    "andaman and nicobar islands": "andaman_and_nicobar_islands",
    "dadra and nagar haveli and daman and diu": "dadra_and_nagar_haveli_and_daman_and_diu",
    "madhya pradesh": "madhya_pradesh", "uttar pradesh": "uttar_pradesh",
    "tamil nadu": "tamil_nadu", "west bengal": "west_bengal",
    "andhra pradesh": "andhra_pradesh",
    "arunachal pradesh": "arunachal_pradesh",
    "himachal pradesh": "himachal_pradesh",
    "jammu and kashmir": "jammu_and_kashmir",
}

_ROADS_CACHE: gpd.GeoDataFrame | None = None


def villages_geojson_path(state: str):
    """Path to the SoI village file or border habitations file, or None."""
    s = state.lower().replace(" ", "_")
    s = STATE_FILE_MAP.get(state.lower(), s)
    p = DATA_DIR / "administrative" / "villages" / f"{s}_soi_villages.geojson"
    if p.exists():
        return p
    p_hab = DATA_DIR / "administrative" / "villages" / f"{s}_habitations.geojson"
    if p_hab.exists():
        return p_hab
    return None


def load_facilities(kinds: list[str]) -> dict[str, gpd.GeoDataFrame]:
    global _ROADS_CACHE
    out = {}
    for kind in kinds:
        path = FACILITY_SOURCES[kind]
        if not path.exists():
            print(f"  skipping {kind} (no file: {path.name})")
            continue
        if path.suffix == ".geojson":
            if _ROADS_CACHE is None:
                _ROADS_CACHE = gpd.read_file(path)
            if kind == "nh":
                gdf = _ROADS_CACHE.copy()
                gdf["fac_name"] = gdf["nh"].fillna(gdf["name"]).fillna("NH")
                out["nh"] = gdf
            elif kind == "expressway":
                exp = _ROADS_CACHE[_ROADS_CACHE["highway"] == "motorway"].copy()
                exp["fac_name"] = exp["name"].fillna(exp["nh"]).fillna("Expressway")
                out["expressway"] = exp
        else:
            df = pd.read_csv(path)
            df = df[df["latitude"].notna() & df["longitude"].notna()]
            # hub CSVs use `name`; the stations table uses `station_name`
            label = "station_name" if "station_name" in df else "name"
            df = df.rename(columns={label: "fac_name"})
            gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["longitude"], df["latitude"]), crs=4326)
            if len(gdf):
                out[kind] = gdf
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", help="one state, or omit with --all")
    ap.add_argument("--all", action="store_true",
                    help="run for every state/UT (villages where available, "
                         "district centroids for the 9 states without village data)")
    ap.add_argument("--district", help="restrict analysis to one district")
    ap.add_argument("--facilities", default=",".join(FACILITY_SOURCES),
                    help=f"comma list from: {','.join(FACILITY_SOURCES)}")
    ap.add_argument("--thresholds", default="10,25,50",
                    help="catchment distances in km for share-below stats")
    args = ap.parse_args()

    if args.all:
        states = gpd.read_file(DATA_DIR / "administrative" / "india_states_lgd.geojson")
        for st in sorted(states["state"].unique()):
            print(f"\n########## {st} ##########", flush=True)
            try:
                run_one(st, args)
            except SystemExit as e:
                print(f"  SKIP: {e}", flush=True)
            except Exception as e:
                print(f"  ERROR: {e}", flush=True)
        build_national_summary()
        return
    if not args.state:
        ap.error("--state or --all required")
    run_one(args.state, args)


def build_national_summary() -> pd.DataFrame:
    states_gdf = gpd.read_file(DATA_DIR / "administrative" / "india_states_lgd.geojson")
    frames = []
    for st in sorted(states_gdf["state"].unique()):
        label = st.lower().replace(" ", "_")
        spath = ANALYSIS_DIR / f"{label}_district_access_summary.csv"
        if spath.exists():
            df = pd.read_csv(spath)
            df.insert(0, "state", st)
            frames.append(df)
    if frames:
        composite = pd.concat(frames, ignore_index=True)
        out_path = ANALYSIS_DIR / "india_district_access_summary.csv"
        composite.to_csv(out_path, index=False)
        print(f"\nWrote national composite ({len(composite)} rows) -> {out_path}", flush=True)
        return composite
    return pd.DataFrame()


def run_one(state: str, args) -> None:
    thresholds = [float(t) for t in args.thresholds.split(",")]
    kinds = [k.strip() for k in args.facilities.split(",")]
    args_state, args_district = state, args.district
    # local aliases keep the body below unchanged
    class _A: pass
    args = _A()
    args.state, args.district = args_state, args_district

    print(f"Loading villages for {args.state} ...")
    vpath = villages_geojson_path(args.state)
    if vpath is not None:
        villages = gpd.read_file(vpath)
        unit = "village"
    else:
        # no SoI village file (9 border/NE states) — fall back to district
        # centroids so every state has coverage; unit marked in outputs
        districts = gpd.read_file(DATA_DIR / "administrative" / "india_districts_lgd.geojson")
        villages = districts[districts["state"].str.lower() == args.state.lower()].copy()
        villages = villages.rename(columns={"district_code": "village_code"})
        villages["village"] = villages["district"]
        if not len(villages):
            raise SystemExit(f"No village file and no districts for {args.state}")
        unit = "district_centroid"
        print(f"  no village data for {args.state} — using district centroids")
    if args.district and "district" in villages:
        villages = villages[villages["district"].str.lower() == args.district.lower()]
        label = f"{args.state.lower().replace(' ', '_')}_{args.district.lower().replace(' ', '_')}"
    else:
        label = args.state.lower().replace(" ", "_")

    # one representative point per village polygon
    pts = villages.copy()
    pts["geometry"] = villages.geometry.representative_point()
    pts = pts.to_crs(PROJ)
    print(f"  {len(pts)} villages across {villages.district.nunique() if 'district' in villages else '?'} districts")

    facilities = load_facilities(kinds)
    print(f"Facility layers: { {k: len(v) for k, v in facilities.items()} }")

    base = pd.DataFrame({
        "village": villages["village"].values,
        "district": villages["district"].values if "district" in villages else None,
        "district_code": (pd.to_numeric(villages["district_code"], errors="coerce").values
                          if "district_code" in villages else None),
        "village_code": villages["village_code"].values,
        "unit": unit,
    })
    for kind, gdf in facilities.items():
        fac = gdf.to_crs(PROJ)
        joined = gpd.sjoin_nearest(pts, fac[["fac_name", "geometry"]],
                                   how="left", distance_col=f"dist_{kind}")
        # equidistant ties return duplicate rows — keep first (min distance)
        joined = joined[~joined.index.duplicated(keep="first")]
        base[f"nearest_{kind}"] = joined["fac_name"].values
        base[f"dist_{kind}_km"] = (joined[f"dist_{kind}"].values / 1000).round(2)

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    vpath = ANALYSIS_DIR / f"{label}_village_access.csv"
    base.to_csv(vpath, index=False)
    print(f"Wrote per-village table -> {vpath}")

    # --- district summary ---
    dist_cols = [c for c in base.columns if c.startswith("dist_") and c.endswith("_km")]
    rows = []
    for district, grp in base.groupby("district", dropna=False):
        row = {"district": district, "villages": len(grp)}
        for c in dist_cols:
            kind = c[5:-3]
            row[f"{kind}_median_km"] = grp[c].median()
            row[f"{kind}_mean_km"] = grp[c].mean().round(2)
            for t in thresholds:
                row[f"{kind}_within_{t:g}km_pct"] = (grp[c] <= t).mean() * 100
        rows.append(row)
    summary = pd.DataFrame(rows)

    # population weighting via state-scoped district_code / district name
    # against the estimates file (covers all 781 current districts: exact census
    # or allocated; see scripts/analyze/allocate_population.py).
    import re as _re
    import difflib as _difflib

    def _norm(s):
        s = _re.sub(r"\s+", " ", str(s).lower().strip())
        s = _re.sub(r"\s*\([^)]*\)", "", s)
        s = s.replace(">", "a").replace("<", "a").replace("|", "i").replace("#", "u")
        return s.replace(" ", "")

    est_path = DATA_DIR / "demographic" / "district_population_estimates.csv"
    if est_path.exists():
        est = pd.read_csv(est_path)
        s_est = est[est["state"].str.lower() == args.state.lower()].copy()
        code_map = {float(c): p for c, p in zip(s_est["district_code"], s_est["pop_2011"]) if pd.notna(c)}
        name_map = {_norm(d): p for d, p in zip(s_est["district"], s_est["pop_2011"])}

        pops = []
        for d in summary["district"]:
            grp_v = base[base["district"] == d]
            dc = grp_v["district_code"].dropna().iloc[0] if ("district_code" in grp_v and len(grp_v.dropna(subset=["district_code"]))) else None
            p = None
            if dc is not None:
                try:
                    p = code_map.get(float(dc))
                except (ValueError, TypeError):
                    p = None
            if p is None:
                target = _norm(d)
                p = name_map.get(target)
                if p is None and name_map:
                    matches = _difflib.get_close_matches(target, list(name_map.keys()), n=1, cutoff=0.7)
                    if matches:
                        p = name_map[matches[0]]
            pops.append(p)
        summary["census2011_population"] = pops
    else:
        census = pd.read_csv(DATA_DIR / "demographic" / "census2011_district_key_indicators.csv")
        ck = census["district"].astype(str).str.lower().str.replace(r"\s+", " ", regex=True).str.strip()
        ck = ck.str.replace(r"\s*\([^)]*\)", "", regex=True)
        sel = census["state"].str.lower() == args.state.lower()
        census_map = dict(zip(ck[sel], census.loc[sel, "Population"]))
        summary["census2011_population"] = (
            summary["district"].astype(str).str.lower().str.strip().map(census_map))

    # state-level row: district means weighted by Census 2011 population
    # (post-2011 districts carry no 2011 population and are excluded from weighting)
    state_row = {"district": "__STATE__", "villages": int(summary["villages"].sum())}
    m = summary["census2011_population"].notna()
    pop = summary.loc[m, "census2011_population"]
    for c in dist_cols:
        kind = c[5:-3]
        col = f"{kind}_mean_km"
        state_row[col] = (summary.loc[m, col] * pop).sum() / pop.sum() if m.any() else None
        state_row[f"{kind}_median_km"] = None
        # share of villages within thresholds, state-wide
        for t in thresholds:
            state_row[f"{kind}_within_{t:g}km_pct"] = (base[c] <= t).mean() * 100
    state_row["census2011_population"] = float(pop.sum()) if m.any() else None
    summary = pd.concat([summary, pd.DataFrame([state_row])], ignore_index=True)

    spath = ANALYSIS_DIR / f"{label}_district_access_summary.csv"
    summary.to_csv(spath, index=False)
    print(f"Wrote district summary -> {spath}")
    print("\nPreview (first rows):")
    with pd.option_context("display.width", 200):
        print(summary.head(8).to_string(index=False))


if __name__ == "__main__":
    main()
