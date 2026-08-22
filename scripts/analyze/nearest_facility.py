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
    "rail_station": DATA_DIR / "rail" / "railway_stations.csv",
    "port": DATA_DIR / "logistics_hubs" / "ports.csv",
    "icd": DATA_DIR / "logistics_hubs" / "icds.csv",
    "icp": DATA_DIR / "logistics_hubs" / "icps.csv",
    "air_cargo": DATA_DIR / "logistics_hubs" / "air_cargo.csv",
    "mmlp": DATA_DIR / "logistics_hubs" / "mmlps.csv",
    "iw_terminal": DATA_DIR / "logistics_hubs" / "inland_waterway_terminals.csv",
    "fci_depot": DATA_DIR / "logistics_hubs" / "fci_depots.csv",
}

STATE_FILE_MAP = {
    "andaman and nicobar islands": "andaman_and_nicobar_islands",
    "dadra and nagar haveli and daman and diu": "dadra_and_nagar_haveli_and_daman_and_diu",
    "madhya pradesh": "madhya_pradesh", "uttar pradesh": "uttar_pradesh",
    "tamil nadu": "tamil_nadu", "west bengal": "west_bengal",
    "andhra pradesh": "andhra_pradesh",
}


def villages_geojson_path(state: str) -> pathlib.Path:
    s = state.lower().replace(" ", "_")
    s = STATE_FILE_MAP.get(state.lower(), s)
    p = DATA_DIR / "administrative" / "villages" / f"{s}_soi_villages.geojson"
    if not p.exists():
        raise SystemExit(
            f"Village file not found: {p}\n"
            f"Generate it first: python scripts/fetch/fetch_village_boundaries_soi.py --state {state}")
    return p


def load_facilities(kinds: list[str]) -> dict[str, gpd.GeoDataFrame]:
    out = {}
    for kind in kinds:
        path = FACILITY_SOURCES[kind]
        if not path.exists():
            print(f"  skipping {kind} (no file: {path.name})")
            continue
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
    ap.add_argument("--state", required=True)
    ap.add_argument("--district", help="restrict analysis to one district")
    ap.add_argument("--facilities", default="rail_station,icd,port,air_cargo,icp",
                    help=f"comma list from: {','.join(FACILITY_SOURCES)}")
    ap.add_argument("--thresholds", default="10,25,50",
                    help="catchment distances in km for share-below stats")
    args = ap.parse_args()

    thresholds = [float(t) for t in args.thresholds.split(",")]
    kinds = [k.strip() for k in args.facilities.split(",")]

    print(f"Loading villages for {args.state} ...")
    villages = gpd.read_file(villages_geojson_path(args.state))
    if args.district:
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
        "village_code": villages["village_code"].values,
    })
    for kind, gdf in facilities.items():
        fac = gdf.to_crs(PROJ)
        joined = gpd.sjoin_nearest(pts, fac[["fac_name", "geometry"]],
                                   how="left", distance_col=f"dist_{kind}")
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

    # population weighting (Census 2011 district names; post-2011 districts
    # and renamed districts — e.g. Sikkim's Gyalshing vs "West District" —
    # get no population weight, by design)
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
