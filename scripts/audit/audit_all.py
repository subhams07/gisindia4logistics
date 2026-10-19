"""Full data audit for GIS4Logistics.

Checks every committed dataset for completeness, counts, schema, geometry,
cross-layer consistency, and analysis reproducibility. Writes a markdown
report to docs/audit_report.md and exits non-zero on any FAIL.

Check severity:
- FAIL  : wrong/incomplete data — must be fixed before shipping
- WARN  : suspicious — documented justification required
- PASS  : clean

Usage: python scripts/audit/audit_all.py [--fail-on-warn]
"""
from __future__ import annotations

import argparse
from contextvars import ContextVar
import glob
import io
import os
import pathlib
import sys


import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
# Direct `python scripts/audit/audit_all.py` runs need the repository root for `scripts.*` imports.
if str(pathlib.Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from standardize import DATA_DIR, REPO_ROOT  # noqa: E402

# ---------------------------------------------------------------- reference
# District counts per state/UT, verified against the source's own LGD codes
# (incl. 2024-25 formations: Arunachal Bichom/Keyi Panyor, Chhattisgarh 2025
# batch incl. Khairagarh, Goa Kushavati; Maharashtra 36 — the source's
# Dhule sub-districts carried code 598, which is Kerala's Alappuzha, and
# were once dissolved into a duplicate "Malegaon" DHULE; that code is now
# corrected to 498 in process_panindia_boundaries). Documented source gaps: Ladakh source predates the 2024
# five-district split (2 only); Puducherry lacks Mahe/Yanam (2 of 4); J&K
# = 20 coded + 2 PoK rows with null codes; Delhi = 11 coded + Nazul (null).
EXPECTED_DISTRICTS = {
    "Andhra Pradesh": 26, "Arunachal Pradesh": 27, "Assam": 35, "Bihar": 38,
    "Chhattisgarh": 33, "Goa": 3, "Gujarat": 33, "Haryana": 22,
    "Himachal Pradesh": 12, "Jharkhand": 24, "Karnataka": 31,
    "Kerala": 14, "Madhya Pradesh": 55, "Maharashtra": 36, "Manipur": 16,
    "Meghalaya": 12, "Mizoram": 11, "Nagaland": 16, "Odisha": 30,
    "Punjab": 23, "Rajasthan": 41, "Sikkim": 6, "Tamil Nadu": 38,
    "Telangana": 33, "Tripura": 8, "Uttar Pradesh": 75, "Uttarakhand": 13,
    "West Bengal": 23, "Andaman and Nicobar Islands": 3, "Chandigarh": 1,
    "Dadra and Nagar Haveli and Daman and Diu": 3, "Delhi": 12,
    "Jammu and Kashmir": 22, "Ladakh": 2, "Lakshadweep": 1, "Puducherry": 2,
}

# SoI publishes village files for exactly these 27 (the 9 border/NE absent)
SOI_PUBLISHED_STATES = [
    "Andaman and Nicobar Islands", "Andhra Pradesh", "Bihar", "Chandigarh",
    "Chhattisgarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
    "Goa", "Gujarat", "Haryana", "Jharkhand", "Karnataka", "Kerala",
    "Lakshadweep", "Madhya Pradesh", "Maharashtra", "Odisha", "Puducherry",
    "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal",
]

# Census 2011 total villages (incl. uninhabited) — SOFT reference only:
# SoI revenue-village definitions differ from Census; use ±25% tolerance
CENSUS_VILLAGES_2011 = {
    "Uttar Pradesh": 107276, "Madhya Pradesh": 55266, "Bihar": 45103,
    "Rajasthan": 44673, "Odisha": 51749, "Maharashtra": 43664,
    "West Bengal": 40852, "Jharkhand": 32616, "Karnataka": 30383,
    "Chhattisgarh": 20126, "Gujarat": 18283, "Tamil Nadu": 16317,
    "Punjab": 12618, "Haryana": 7356, "Uttarakhand": 16793,
    "Tripura": 870, "Sikkim": 452, "Andaman and Nicobar Islands": 550,
    "Goa": 384, "Chandigarh": 24, "Delhi": 112, "Puducherry": 140,
    "Lakshadweep": 26, "Dadra and Nagar Haveli and Daman and Diu": 106,
    # AP/Telangana excluded: no reliable post-split (2022 districts) baseline
}

RESULTS: list[dict] = []
_RESULT_SINK = ContextVar("audit_results", default=None)
BASELINE_PATH = REPO_ROOT / "data" / "audit_baseline.json"


def check(dataset: str, name: str, severity: str, ok: bool | None, detail: str = "") -> None:
    status = "PASS" if ok else ("FAIL" if severity == "FAIL" else "WARN")
    if ok:
        status = "PASS"
    sink = _RESULT_SINK.get()
    sink = RESULTS if sink is None else sink
    sink.append({"dataset": dataset, "check": name, "status": status,
                    "severity": severity, "detail": detail})
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def audit_boundaries(source_root=DATA_DIR):
    print("\n== LGD boundaries (states/districts/subdistricts) ==")
    states = gpd.read_file(source_root / "administrative" / "india_states_lgd.geojson")
    check("states_lgd", "36 states/UTs", "FAIL", len(states) == 36, f"{len(states)}")
    check("states_lgd", "valid geometries", "FAIL", states.geometry.is_valid.all())
    check("states_lgd", "no null state names", "FAIL", states.state.notna().all())

    dist = gpd.read_file(source_root / "administrative" / "india_districts_lgd.geojson")
    check("districts_lgd", "valid geometries", "FAIL", dist.geometry.is_valid.all())
    check("districts_lgd", "no CR/LF in names", "FAIL",
          not dist.district.str.contains(r"[\r\n]", regex=True).any())
    counts = dist.groupby("state")["district"].count()
    mismatches = {s: (int(counts.get(s, 0)), exp)
                  for s, exp in EXPECTED_DISTRICTS.items()
                  if counts.get(s, 0) != exp}
    check("districts_lgd", "per-state counts == verified reference", "FAIL",
          not mismatches, str(mismatches) if mismatches else "all 36 match")
    jk = dist[dist.state == "Jammu and Kashmir"]
    check("districts_lgd", "J&K = 20 coded + 2 PoK (null code)", "FAIL",
          len(jk) == 22 and jk.district_code.isna().sum() == 2,
          f"{len(jk)} rows, {int(jk.district_code.isna().sum())} null-coded")

    sub = gpd.read_file(source_root / "administrative" / "india_subdistricts_lgd.gpkg")
    check("subdistricts_lgd", ">=6,500 features", "FAIL", len(sub) >= 6500, f"{len(sub)}")
    check("subdistricts_lgd", "valid geometries", "FAIL", sub.geometry.is_valid.all())
    check("subdistricts_lgd", "no CR/LF in names", "FAIL",
          not sub.sub_district.str.contains(r"[\r\n]", regex=True).any())
    # cross-layer: every village-layer district_code must exist in districts
    sub_codes = set(zip(dist.state, dist.district_code.astype("Float64")))
    bad = [(s, c) for s, c in zip(sub.state, sub.district_code.astype("Float64"))
           if (s, c) not in sub_codes]
    check("subdistricts_lgd", "district codes join to districts layer", "FAIL",
          not bad, f"{len(bad)} orphans, e.g. {bad[:3]}" if bad else "")
    # SoI splits sub-districts into RURAL/URBAN pairs sharing one LGD code —
    # duplicates are legitimate only in that pattern; null codes excluded
    base = sub.dropna(subset=["sub_district_code"])
    real_dups = base.duplicated(
        ["sub_district_code", "sub_district"], keep=False).sum()
    check("subdistricts_lgd", "no true duplicate subdistrict codes (RURAL/URBAN pairs OK)", "FAIL",
          real_dups == 0, f"{int(real_dups)} real dups")


def audit_villages(source_root=DATA_DIR):
    print("\n== SoI village files ==")
    files = sorted(glob.glob(str(source_root / "administrative" / "villages" / "*_soi_villages.geojson")))
    slugs = {pathlib.Path(f).stem.replace("_soi_villages", "") for f in files}
    # pune_* is a district extract, not a state file
    state_slugs = {s for s in slugs if not s.startswith("pune")}
    expected_slugs = {s.lower().replace(" ", "_") for s in SOI_PUBLISHED_STATES}
    expected_slugs = {s.replace("andaman_and_nicobar_islands", "andaman_and_nicobar_islands")
                      for s in expected_slugs}
    missing = expected_slugs - state_slugs
    extra = state_slugs - expected_slugs
    check("villages", "exactly the 27 published states present", "FAIL",
          not missing and not extra, f"missing={sorted(missing)} extra={sorted(extra)}")

    dist = gpd.read_file(source_root / "administrative" / "india_districts_lgd.geojson")
    dist_counts = dist.groupby("state")["district"].count()

    for f in files:
        g = gpd.read_file(f)
        slug = pathlib.Path(f).stem.replace("_soi_villages", "")
        state = slug.replace("_", " ").title()
        fixes = {"Dadra And Nagar Haveli And Daman And Diu": "Dadra and Nagar Haveli and Daman and Diu",
                 "Andaman And Nicobar Islands": "Andaman and Nicobar Islands",
                 "Jammu And Kashmir": "Jammu and Kashmir"}
        state = fixes.get(state, state)
        if slug.startswith("pune"):
            continue
        has = {"village", "village_code", "district", "district_code",
               "sub_district", "sub_district_code"} <= set(g.columns)
        check("villages", f"{slug}: full schema", "FAIL", has,
              "" if has else f"missing { {'village','village_code','district','district_code','sub_district','sub_district_code'} - set(g.columns) }")
        check("villages", f"{slug}: village names present", "WARN",
              g["village"].notna().all() if "village" in g else False,
              f"{int(g['village'].isna().sum())} blank in SoI source" if "village" in g else "no col")
        check("villages", f"{slug}: geometries valid+non-empty", "FAIL",
              bool(g.geometry.is_valid.all()) and not g.geometry.is_empty.any())
        ref = CENSUS_VILLAGES_2011.get(state)
        if ref:
            ratio = len(g) / ref
            check("villages", f"{slug}: count vs census-2011 soft ref", "WARN",
                  0.75 <= ratio <= 1.3, f"{len(g)} vs {ref} (x{ratio:.2f})")
        nd = g.district.nunique() if "district" in g else 0
        exp = dist_counts.get(state)
        if exp:
            check("villages", f"{slug}: districts covered == districts layer", "WARN",
                  nd >= exp - 1, f"{nd}/{exp}")


def audit_analysis(source_root=DATA_DIR, artifact_root=None):
    artifact_root = source_root / "analysis" if artifact_root is None else artifact_root
    print("\n== analysis outputs ==")
    village_files = [f for f in glob.glob(str(source_root / "administrative" / "villages" / "*_soi_villages.geojson"))
                     if "pune" not in f]
    summaries = [f for f in glob.glob(str(artifact_root / "*_district_access_summary.csv"))
                 if "india_district" not in f]
    check("analysis", "36 state summaries", "FAIL", len(summaries) == 36, f"{len(summaries)}")

    roll = pd.read_csv(artifact_root / "india_district_access_summary.csv")
    check("analysis", "rollup has 36 states + 36 __STATE__ rows", "FAIL",
          roll.state.nunique() == 36 and (roll.district == "__STATE__").sum() == 36,
          f"{roll.state.nunique()} states, {(roll.district=='__STATE__').sum()} state rows")

    # per-state: village_access row count == source village file count
    for vf in village_files:
        slug = pathlib.Path(vf).stem.replace("_soi_villages", "")
        av = artifact_root / f"{slug}_village_access.csv"
        if not av.exists():
            check("analysis", f"{slug}: village access table exists", "FAIL", False)
            continue
        n_src = len(gpd.read_file(vf))
        n_out = len(pd.read_csv(av))
        check("analysis", f"{slug}: row count matches source", "FAIL",
              n_src == n_out, f"{n_out} vs {n_src}")

    # reproducibility: recompute 40 sample villages in Haryana
    av = pd.read_csv(artifact_root / "haryana_village_access.csv")
    vill = gpd.read_file(source_root / "administrative" / "villages" / "haryana_soi_villages.geojson")
    st = pd.read_csv(source_root / "rail" / "railway_stations.csv")
    sample = vill.sample(40, random_state=42)
    pts = gpd.GeoSeries(sample.geometry.representative_point(), crs=4326).to_crs(7755)
    fac = gpd.GeoDataFrame(st, geometry=gpd.points_from_xy(st.longitude, st.latitude), crs=4326).to_crs(7755)
    d = fac.geometry.unary_union
    recomputed = pts.map(lambda p: p.distance(d) / 1000).round(2).values
    joined = sample.assign(rek=recomputed).merge(
        av, left_on=["district", "village"], right_on=["district", "village"])
    ok = (joined.rek - joined.dist_rail_station_km).abs().max() < 0.15
    check("analysis", "recomputed distances match stored (Haryana sample)", "FAIL",
          bool(ok), f"max delta {(joined.rek - joined.dist_rail_station_km).abs().max():.3f} km")

    # national rollup arithmetic: state row villages == sum(district villages)
    bad = []
    for st_name, grp in roll.groupby("state"):
        s = grp[grp.district == "__STATE__"]
        if len(s) and s.villages.iloc[0] != grp[grp.district != "__STATE__"].villages.sum():
            bad.append(st_name)
    check("analysis", "state rows == sum of district rows", "FAIL", not bad, str(bad))

    # Producer contracts are registered independently in BOTH modes.
    # Keep legacy analysis limited to village access reproducibility.
    return


def audit_rail(source_root=DATA_DIR):
    print("\n== rail ==")
    st = pd.read_csv(source_root / "rail" / "railway_stations.csv")
    check("rail_stations", "coords in India bbox", "FAIL",
          bool(st.longitude.between(68, 97.5).all() and st.latitude.between(6, 37.5).all()))
    check("rail_stations", "unique station codes", "WARN",
          st.station_code.duplicated().sum() == 0,
          f"{int(st.station_code.duplicated().sum())} dup codes")
    cat = pd.read_csv(source_root / "rail" / "station_categories.csv")
    nsg1 = (cat.category == "NSG1").sum()
    check("station_categories", "NSG1 count sane (15-30)", "WARN", 15 <= nsg1 <= 30, f"{nsg1}")
    join = cat.station_code.isin(st.station_code).mean()
    check("station_categories", ">=90% codes join to stations", "FAIL", join >= 0.9, f"{join:.1%}")
    ft = pd.read_csv(source_root / "rail" / "freight_terminals.csv")
    have = ft[pd.to_numeric(ft.latitude, errors="coerce").notna()]
    check("freight_terminals", "84 GCT rows", "FAIL", len(ft) == 84, f"{len(ft)}")
    check("freight_terminals", "geocoded coords in bbox", "FAIL",
          bool(have.longitude.astype(float).between(68, 97.5).all()
               and have.latitude.astype(float).between(6, 37.5).all()))

    # DFC Network & Stations
    dfc_p = source_root / "rail" / "dfc_network.geojson"
    check("dfc_network", "exists", "FAIL", dfc_p.exists())
    if dfc_p.exists():
        gdf_dfc = gpd.read_file(dfc_p)
        check("dfc_network", "valid geometries", "FAIL", bool(gdf_dfc.geometry.is_valid.all()))
        check("dfc_network", ">= 2 corridors", "FAIL", len(gdf_dfc) >= 2, f"{len(gdf_dfc)}")

    dfc_st_p = source_root / "rail" / "dfc_stations.csv"
    check("dfc_stations", "exists", "FAIL", dfc_st_p.exists())
    if dfc_st_p.exists():
        df_dfc_st = pd.read_csv(dfc_st_p)
        check("dfc_stations", ">= 40 stations", "FAIL", len(df_dfc_st) >= 40, f"{len(df_dfc_st)}")
        check("dfc_stations", "coords in bbox", "FAIL",
              bool(df_dfc_st.longitude.between(68, 97.5).all() and df_dfc_st.latitude.between(6, 37.5).all()))


def audit_hubs(source_root=DATA_DIR):
    print("\n== logistics hubs ==")
    expected = {"ports.csv": (12, "major_port"), "icps.csv": (15, "icp"),
                "icds.csv": (30, "icd"), "air_cargo.csv": (15, "air_cargo"),
                "mmlps.csv": (15, "mmlp"), "inland_waterway_terminals.csv": (30, "iw_terminal"),
                "fci_depots.csv": (40, "foodgodown")}
    cols = ["name", "hub_type", "state", "city", "latitude", "longitude",
            "operator", "capacity_notes", "source_url"]
    for f, (min_rows, type_key) in expected.items():
        p = source_root / "logistics_hubs" / f
        df = pd.read_csv(p)
        check("hubs", f"{f}: schema", "FAIL", list(df.columns) == cols)
        check("hubs", f"{f}: >= {min_rows} rows", "FAIL", len(df) >= min_rows, f"{len(df)}")
        check("hubs", f"{f}: coords in bbox + per-row source", "FAIL",
              bool(df.longitude.between(68, 97.5).all() and df.latitude.between(6, 37.5).all()
                   and df.source_url.notna().all()))

    # NICDC Industrial Nodes & PM MITRA
    ind_p = source_root / "logistics_hubs" / "industrial_nodes.csv"
    check("industrial_nodes", "exists", "FAIL", ind_p.exists())
    if ind_p.exists():
        df_ind = pd.read_csv(ind_p)
        check("industrial_nodes", ">= 20 nodes", "FAIL", len(df_ind) >= 20, f"{len(df_ind)}")
        check("industrial_nodes", "coords in bbox", "FAIL",
              bool(df_ind.longitude.between(68, 97.5).all() and df_ind.latitude.between(6, 37.5).all()))

    mitra_p = source_root / "logistics_hubs" / "pm_mitra_parks.csv"
    check("pm_mitra_parks", "exists", "FAIL", mitra_p.exists())
    if mitra_p.exists():
        df_mitra = pd.read_csv(mitra_p)
        check("pm_mitra_parks", "7 parks", "FAIL", len(df_mitra) == 7, f"{len(df_mitra)}")
        check("pm_mitra_parks", "coords in bbox", "FAIL",
              bool(df_mitra.longitude.between(68, 97.5).all() and df_mitra.latitude.between(6, 37.5).all()))

    # Cold Chain & e-NAM Mandis
    cold_p = source_root / "logistics_hubs" / "cold_chain_storages.csv"
    check("cold_chain_storages", "exists", "FAIL", cold_p.exists())
    if cold_p.exists():
        df_cold = pd.read_csv(cold_p)
        check("cold_chain_storages", ">= 10 storages", "FAIL", len(df_cold) >= 10, f"{len(df_cold)}")
        check("cold_chain_storages", "coords in bbox", "FAIL",
              bool(df_cold.longitude.between(68, 97.5).all() and df_cold.latitude.between(6, 37.5).all()))

    enam_p = source_root / "logistics_hubs" / "enam_mandis.csv"
    check("enam_mandis", "exists", "FAIL", enam_p.exists())
    if enam_p.exists():
        df_enam = pd.read_csv(enam_p)
        check("enam_mandis", ">= 15 mandis", "FAIL", len(df_enam) >= 15, f"{len(df_enam)}")
        check("enam_mandis", "coords in bbox", "FAIL",
              bool(df_enam.longitude.between(68, 97.5).all() and df_enam.latitude.between(6, 37.5).all()))


def audit_roads(source_root=DATA_DIR, *, fast=False):
    print("\n== roads ==")
    parquet = source_root / "roads" / "india_nh_network.parquet"
    if parquet.exists():
        nh = pd.read_parquet(parquet, columns=["nh"]) if fast else gpd.read_parquet(parquet)
    else:
        nh = gpd.read_file(source_root / "roads" / "india_nh_network.geojson", ignore_geometry=fast)
    if not fast:
        check("nh_network", "valid geometries", "FAIL", bool(nh.geometry.is_valid.all()))
    bad_nh = (~nh.nh.str.fullmatch(r"(\d{1,4}[A-Z]?)(;\d{1,4}[A-Z]?)*", na=False)).sum()
    check("nh_network", "NH numbers clean", "FAIL", bad_nh == 0, f"{int(bad_nh)} odd values")
    n_routes = nh.nh.str.split(";").explode().replace("", None).dropna().nunique()
    check("nh_network", "500-800 distinct routes", "WARN", 500 <= n_routes <= 800, f"{n_routes}")
    pune = source_root / "roads" / "pune_sample_roads.geojson"
    check("pune_sample", "exists", "FAIL", pune.exists())
    
    # Toll Plazas
    t_path = source_root / "roads" / "toll_plazas.csv"
    check("toll_plazas", "exists", "FAIL", t_path.exists())
    if t_path.exists():
        tp = pd.read_csv(t_path)
        t_cols = ["name", "toll_type", "nh_number", "state", "district", "district_code", "latitude", "longitude", "booth_count", "source_url"]
        check("toll_plazas", "schema", "FAIL", list(tp.columns) == t_cols)
        check("toll_plazas", ">= 1000 rows", "FAIL", len(tp) >= 1000, f"{len(tp)}")
        check("toll_plazas", "coords in India bbox", "FAIL",
              bool(tp.longitude.between(68, 97.5).all() and tp.latitude.between(6, 37.5).all()))


def audit_demographics(source_root=DATA_DIR):
    print("\n== demographics ==")
    c = pd.read_csv(source_root / "demographic" / "census2011_district_key_indicators.csv")
    check("census", "640 districts", "FAIL", len(c) == 640, f"{len(c)}")
    check("census", "population total exact", "FAIL",
          c.Population.sum() == 1_210_854_977, f"{c.Population.sum():,}")
    d11 = gpd.read_file(source_root / "administrative" / "india_districts.geojson")
    census_pairs = set(zip(c.state.str.lower(), c.district.str.lower().str.strip()))
    b_pairs = set(zip(d11.state.str.lower(), d11.district_key))
    unmatched = census_pairs - b_pairs
    check("census", "district keys join to 2011 boundaries", "WARN",
          len(unmatched) <= 20, f"{len(unmatched)} unmatched, e.g. {sorted(unmatched)[:5]}")

    est_p = source_root / "demographic" / "district_population_estimates.csv"
    if est_p.exists():
        est = pd.read_csv(est_p)
        check("population_estimates", "780 current districts", "FAIL", len(est) == 780, f"{len(est)}")
        diff = abs(est.pop_2011.sum() - 1_210_854_977)
        check("population_estimates", "total population conserved (<0.01% delta)", "FAIL",
              diff / 1_210_854_977 < 0.0001, f"{est.pop_2011.sum():,} (delta {diff})")
        valid_methods = set(est.method.unique()) <= {"census2011", "blended", "area_share"}
        check("population_estimates", "valid allocation methods", "FAIL", valid_methods,
              str(est.method.value_counts().to_dict()))


def audit_freight(source_root=DATA_DIR):
    print("\n== freight & transport demand ==")
    # Rail freight
    rf_p = source_root / "freight" / "rail_freight_annual.csv"
    if rf_p.exists():
        rf = pd.read_csv(rf_p)
        check("rail_freight", "schema", "FAIL", list(rf.columns) == [
            "metric", "fy", "entity_type", "entity_code", "commodity_group", "value", "unit", "source_url", "license"])
        check("rail_freight", ">= 50 series", "FAIL", len(rf) >= 50, f"{len(rf)}")
        fy24_all = rf[(rf.fy == "2023-24") & (rf.entity_code == "ALL_INDIA") & (rf.commodity_group == "all")]
        check("rail_freight", "FY23-24 anchor matches PIB (1,591 MT)", "FAIL",
              len(fy24_all) == 1 and abs(fy24_all.value.iloc[0] - 1591.0) < 1.0,
              f"{fy24_all.value.iloc[0] if len(fy24_all) else 'missing'} MT")

    # Port throughput
    pt_p = source_root / "freight" / "port_throughput_annual.csv"
    if pt_p.exists():
        pt = pd.read_csv(pt_p)
        check("port_throughput", "schema", "FAIL", list(pt.columns) == [
            "metric", "fy", "entity_type", "entity_code", "commodity_group", "value", "unit", "source_url", "license"])
        check("port_throughput", ">= 60 series", "FAIL", len(pt) >= 60, f"{len(pt)}")
        fy24_ports = pt[(pt.fy == "2023-24") & (pt.entity_code == "ALL_MAJOR_PORTS")]
        check("port_throughput", "FY23-24 anchor matches IPA (819 MT)", "FAIL",
              len(fy24_ports) == 1 and abs(fy24_ports.value.iloc[0] - 819.0) < 1.0,
              f"{fy24_ports.value.iloc[0] if len(fy24_ports) else 'missing'} MT")
        paradip_24 = pt[(pt.fy == "2023-24") & (pt.entity_code == "INPRT")].value.iloc[0]
        check("port_throughput", "Paradip is #1 major port (>= 140 MT)", "FAIL",
              paradip_24 >= 140.0, f"{paradip_24} MT")

    # Road indicators
    rd_p = source_root / "freight" / "road_indicators_annual.csv"
    if rd_p.exists():
        rd = pd.read_csv(rd_p)
        check("road_indicators", "schema", "FAIL", list(rd.columns) == [
            "metric", "fy", "entity_type", "entity_code", "commodity_group", "value", "unit", "source_url", "license"])
        check("road_indicators", ">= 10 series", "FAIL", len(rd) >= 10, f"{len(rd)}")


def audit_geoparquet(source_root=DATA_DIR):
    print("\n== geoparquet hybrid integrity ==")
    # 1. NH Parquet vs GeoJSON
    nh_pq = source_root / "roads" / "india_nh_network.parquet"
    nh_gj = source_root / "roads" / "india_nh_network.geojson"
    if nh_pq.exists() and nh_gj.exists():
        gdf_pq = gpd.read_parquet(nh_pq)
        gdf_gj = gpd.read_file(nh_gj)
        check("geoparquet", "NH parquet row count matches GeoJSON", "FAIL", len(gdf_pq) == len(gdf_gj), f"{len(gdf_pq)} vs {len(gdf_gj)}")
        check("geoparquet", "NH parquet valid geometries", "FAIL", gdf_pq.geometry.is_valid.all())

    # 2. Districts Parquet
    d_pq = source_root / "administrative" / "india_districts_lgd.parquet"
    if d_pq.exists():
        gdf_dpq = gpd.read_parquet(d_pq)
        check("geoparquet", "districts parquet has 780 rows", "FAIL", len(gdf_dpq) == 780, f"{len(gdf_dpq)}")
        check("geoparquet", "districts parquet valid geometries", "FAIL", gdf_dpq.geometry.is_valid.all())

    # 3. Subdistricts Parquet
    sd_pq = source_root / "administrative" / "india_subdistricts_lgd.parquet"
    if sd_pq.exists():
        gdf_sdpq = gpd.read_parquet(sd_pq)
        check("geoparquet", "subdistricts parquet >= 6,500 features", "FAIL", len(gdf_sdpq) >= 6500, f"{len(gdf_sdpq)}")


def audit_catalog(source_root=DATA_DIR):
    print("\n== catalog vs disk ==")
    import yaml
    cat = yaml.safe_load(open(REPO_ROOT / "catalog.yaml", encoding="utf-8"))
    check("catalog", "parses", "FAIL", True)
    n_missing = 0
    for ds in cat["datasets"]:
        p = ds.get("path")
        if p and "(generated" not in p and "data/analysis" not in p:
            import glob as _glob
            target = source_root / p[5:] if p.startswith("data/") else REPO_ROOT / p
            matches = _glob.glob(str(target))
            if not matches:
                n_missing += 1
                check("catalog", f"{ds['name']}: path exists", "FAIL", False, p)
    check("catalog", "all committed paths exist", "FAIL", n_missing == 0)


def collect_counts(source_root=DATA_DIR, artifact_root=None) -> dict:
    artifact_root = source_root / "analysis" if artifact_root is None else artifact_root
    """Cheap counts for baseline drift (CSV row counts; geojson counts read
    only in deep mode and cached here)."""
    import glob as g
    counts = {}
    for f in g.glob(str(source_root / "logistics_hubs" / "*.csv")):
        counts[f"hubs:{pathlib.Path(f).stem}"] = len(pd.read_csv(f))
    for f in g.glob(str(source_root / "freight" / "*.csv")):
        counts[f"freight:{pathlib.Path(f).stem}"] = len(pd.read_csv(f))
    for key, f in [("rail:stations", "rail/railway_stations.csv"),
                   ("rail:categories", "rail/station_categories.csv"),
                   ("rail:freight", "rail/freight_terminals.csv"),
                   ("census:districts", "demographic/census2011_district_key_indicators.csv"),
                   ("demographic:estimates", "demographic/district_population_estimates.csv"),
                   ("analysis:nh_summary", "analysis/nh_district_travel_time_summary.csv"),
                   ("analysis:nh_port_matrix", "analysis/nh_district_port_matrix.csv")]:
        p = artifact_root / f[9:] if f.startswith("analysis/") else source_root / f
        if p.exists():
            counts[key] = len(pd.read_csv(p))
    for f in g.glob(str(artifact_root / "*_village_access.csv")):
        counts[f"analysis:{pathlib.Path(f).stem}"] = len(pd.read_csv(f, usecols=["unit"]))
    return counts


def check_baseline(source_root=DATA_DIR, artifact_root=None):
    print("== baseline drift ==")
    import json
    baseline_path = source_root / "audit_baseline.json"
    if not baseline_path.exists():
        check("baseline", "baseline file exists", "FAIL", False,
              "run audit_all.py --update-baseline")
        return
    base = json.loads(baseline_path.read_text())
    current = collect_counts(source_root, artifact_root)
    drift = {k: (base.get(k), v) for k, v in current.items() if base.get(k) != v}
    check("baseline", "counts match baseline", "FAIL", not drift,
          f"{len(drift)} drifted, e.g. {dict(list(drift.items())[:3])}" if drift
          else f"{len(current)} series stable")


KINDS = ("rail_station", "freight_terminal", "port", "icd", "mmlp", "air_cargo", "icp", "toll_plaza", "expressway")
PORT_KEYS = ("deendayal_(kandla)", "mumbai", "jawaharlal_nehru_(jnpt/navi_mumbai)", "mormugao", "new_mangalore", "cochin", "v.o._chidambaranar_(tuticorin)", "chennai", "kamarajar_(ennore)", "visakhapatnam", "paradip", "kolkata_(syama_prasad_mookerjee)_incl._haldia_dock_complex")
IDENTITY = ["state", "district", "district_code"]
SUMMARY_COLUMNS = IDENTITY + ["pop_2011", "is_island", "highway_access_dist_km", "highway_access_time_min"] + [column for kind in KINDS for column in (f"nearest_{kind}_name", f"{kind}_straight_km", f"{kind}_road_distance_km", f"{kind}_drive_time_hours", f"{kind}_drive_time_min")]
MATRIX_COLUMNS = IDENTITY + [column for key in PORT_KEYS for column in (f"drive_hours_to_{key}", f"road_km_to_{key}")]
COST_COLUMNS = IDENTITY + ["pop_2011", "is_island", "target_port", "road_distance_km", "road_drive_time_hours", "road_cost_per_ton_inr", "rail_cost_per_ton_inr", "dfc_cost_per_ton_inr", "optimal_mode", "modal_shift_savings_inr", "modal_shift_savings_pct", "break_even_distance_km"]
HINTERLAND_COLUMNS = IDENTITY + ["pop_2011", "is_island", "primary_port", "primary_port_probability", "primary_port_drive_hours", "secondary_port", "secondary_port_probability", "secondary_port_drive_hours", "hinterland_category", "contestability_index"]


def rounded_route_valid(distance, hours):
    """Existence of an unrounded 35..90 km/h route inside published bins."""
    if pd.isna(distance) or pd.isna(hours):
        return bool(pd.isna(distance) and pd.isna(hours))
    try:
        if isinstance(distance, (bool, np.bool_)) or isinstance(hours, (bool, np.bool_)):
            return False
        d, t = float(distance), float(hours)
        if not np.isfinite([d, t]).all() or min(d, t) < 0:
            return False
        return bool(d + .05 + 1e-9 >= 35 * max(0., t - .005)
                    and max(0., d - .05) <= 90 * (t + .005) + 1e-9)
    except (TypeError, ValueError):
        return False


def frames_semantically_equal(left, right):
    """Ordered, null-preserving equality without dtype identity requirements."""
    if list(left.columns) != list(right.columns) or len(left) != len(right):
        return False
    for column in left:
        a, b = left[column].reset_index(drop=True), right[column].reset_index(drop=True)
        if not a.isna().equals(b.isna()):
            return False
        present = a.notna()
        try:
            an, bn = pd.to_numeric(a[present], errors="coerce"), pd.to_numeric(b[present], errors="coerce")
            if an.notna().all() and bn.notna().all():
                if not np.isfinite(an.astype(float)).all() or not np.isfinite(bn.astype(float)).all():
                    return False
                if not np.allclose(an.astype(float), bn.astype(float), atol=1e-9, rtol=0):
                    return False
            elif not a[present].astype(str).equals(b[present].astype(str)):
                return False
        except (OverflowError, TypeError, ValueError):
            return False  # Oversized or malformed values are unequal, never a crash.
    return True


def _contract_severity(artifact_root):
    from scripts.analyze.producer_manifest import MANIFEST_FILENAME
    return "FAIL" if (pathlib.Path(artifact_root) / MANIFEST_FILENAME).exists() else "WARN"


def _route_checks(frame, dataset, distance, hours, *, straight=None, minutes=None, severity="FAIL"):
    check(dataset, f"paired route columns {distance}/{hours}", severity, distance in frame and hours in frame)
    if distance not in frame or hours not in frame:
        return
    check(dataset, f"rounding-aware 35..90 envelope {hours}", "FAIL",
          all(rounded_route_valid(d, t) for d, t in zip(frame[distance], frame[hours])))
    present = frame[distance].notna() & frame[hours].notna()
    if straight is not None and straight in frame:
        s = pd.to_numeric(frame[straight], errors="coerce")
        d = pd.to_numeric(frame[distance], errors="coerce")
        check(dataset, f"straight <= road + 0.1 {straight}", "FAIL",
              bool((s[present].notna() & np.isfinite(s[present]) & (s[present] >= 0) & (s[present] <= d[present] + .1 + 1e-9)).all()) and s.isna().equals(frame[distance].isna()))
    if minutes is not None and minutes in frame:
        m = pd.to_numeric(frame[minutes], errors="coerce")
        h = pd.to_numeric(frame[hours], errors="coerce")
        check(dataset, f"minutes == 60 hours {minutes}", "FAIL",
              bool((m[present].notna() & np.isfinite(m[present]) & (m[present] >= 0) & ((m[present] - 60 * h[present]).abs() <= .8 + 1e-9)).all()) and m.isna().equals(frame[hours].isna()))


def _iso_checks(path, severity, thresholds_hours=None, simplify_m=500.0):
    from scripts.analyze.producer_manifest import read_manifest
    from shapely.geometry import shape
    from pyproj import Transformer
    from shapely.ops import transform
    statuses = {"ok", "invalid_coordinates", "no_highway_within_150km", "feeder_exceeds_threshold", "unsupported_threshold"}
    if thresholds_hours is None:
        thresholds_hours = (4., 8., 12., 24.) if path.name == "major_ports_isochrones.geojson" else (4., 8., 12.)
    document = read_manifest(path)
    if not isinstance(document, dict) or not isinstance(document.get("features"), list):
        check(path.name, "valid GeoJSON collection", "FAIL", False)
        return
    check(path.name, "FeatureCollection", "FAIL", document.get("type") == "FeatureCollection")
    groups = {}
    projector = Transformer.from_crs(4326, 7755, always_xy=True).transform
    for index, feature in enumerate(document.get("features", [])):
        p = feature.get("properties") or {}
        status = p.get("status")
        threshold = p.get("time_threshold_hours")
        key = (p.get("hub_type"), p.get("hub_name"))
        expected_type = "major_port" if path.name == "major_ports_isochrones.geojson" else "mmlp"
        check(path.name, f"feature {index}: hub identity/type", severity,
              key[0] == expected_type and isinstance(key[1], str) and bool(key[1].strip()))
        valid = status in statuses and isinstance(threshold, (float, int)) and not isinstance(threshold, bool) and np.isfinite(threshold) and threshold > 0
        check(path.name, f"feature {index}: explicit status/threshold", severity, valid)
        count = p.get("reachable_node_count")
        count_ok = isinstance(count, (int, float)) and not isinstance(count, bool) and np.isfinite(count) and count >= 0 and int(count) == count
        check(path.name, f"feature {index}: integral actual node count", severity, count_ok)
        geometry = feature.get("geometry")
        area = None
        projected = None
        geometry_ok = geometry is None
        if geometry is not None:
            geom = shape(geometry)
            geometry_ok = geom.geom_type in {"Polygon", "MultiPolygon"} and geom.is_valid and not geom.is_empty
            if geometry_ok:
                x0, y0, x1, y1 = geom.bounds
                geometry_ok = -180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90
                if geometry_ok:
                    projected = transform(projector, geom)
                    area = projected.area
                    geometry_ok = np.isfinite(area) and area > 0
                    projected = projected if geometry_ok else None
            # Geometry corruption was already FAIL before the new contract.
            check(path.name, f"feature {index}: valid polygon", "FAIL", geometry_ok)
        check(path.name, f"feature {index}: status geometry/null policy", severity,
              geometry_ok and ((status == "ok" and geometry is not None) or (status != "ok" and geometry is None)))
        if status == "ok":
            count, snap, feeder = p.get("reachable_node_count"), p.get("snap_feeder_km"), p.get("feeder_time_hours")
            valid_count = isinstance(count, (int, float)) and not isinstance(count, bool) and np.isfinite(count) and count >= 3 and int(count) == count
            check(path.name, f"feature {index}: node count/snap/feeder", severity,
                  valid_count and isinstance(snap, (int, float)) and not isinstance(snap, bool) and np.isfinite(snap) and 0 <= snap <= 150
                  and isinstance(feeder, (int, float)) and not isinstance(feeder, bool) and np.isfinite(feeder) and valid and 0 <= feeder <= threshold)
            check(path.name, f"feature {index}: labelled approximate coverage", severity,
                  p.get("coverage_method") == "approximate_highway_catchment")
        groups.setdefault(key, []).append((threshold, p.get("reachable_node_count"), area, status, projected))
    for key, values in groups.items():
        thresholds = [v[0] for v in values]
        expected = set(thresholds_hours)
        check(path.name, f"{key}: exact threshold set", severity,
              all(isinstance(t, (int, float)) for t in thresholds) and len(thresholds) == len(expected) and set(thresholds) == expected)
        good = sorted((v for v in values if v[3] == "ok" and isinstance(v[0], (int, float))), key=lambda v: v[0])
        monotonic = all(isinstance(a[1], (int, float)) and isinstance(b[1], (int, float)) and a[1] <= b[1]
                        and a[2] is not None and b[2] is not None and a[2] <= b[2] + 1e-3
                        for a, b in zip(good, good[1:]))
        check(path.name, f"{key}: monotonic nodes/projected area", severity, monotonic)
        # Operational tolerance (not proof of nested truth): simplification moves each boundary by at most
        # simplify_m, so a smaller catchment must sit inside the larger one buffered by 2*simplify_m + 1 m.
        tolerance = 2 * float(simplify_m) + 1.0
        nested, leftover = True, 0.0
        for small, large in zip(good, good[1:]):
            try:
                if small[4] is None or large[4] is None:
                    nested = False
                    continue
                area_left = small[4].difference(large[4].buffer(tolerance)).area
                leftover = max(leftover, area_left)
                nested = nested and bool(area_left <= 1.0)
            except Exception:
                nested = False
        check(path.name, "approximate nesting", severity, nested, f"{key}; tolerance_m={tolerance:g}; max_leftover_m2={leftover:.6g}")


QUALIFIED_IDENTITY = "qualified identity: state+code / uncoded state+district"
CROSS_STATE_WARN = "cross-state shared code (disclosed)"
SAME_STATE_WARN = "same-state label with multiple codes (disclosed)"
DISCLOSURE_DATASET = "source_disclosures"


def _identity_records(frame):
    return frame[IDENTITY].to_dict("records")


def _exact_identities(left_records, frame):
    """Literal ordered comparison; malformed stored identities are simply not equal."""
    from scripts.audit.identity_contract import IdentityError, exact_identities_equal
    try:
        return bool(set(IDENTITY) <= set(frame) and exact_identities_equal(left_records, _identity_records(frame)))
    except IdentityError:
        return False


def _disclosure_warnings(records, overlaps=None):
    """Named WARNs for retained upstream identity conditions; nothing is corrected or deduplicated."""
    from scripts.audit.identity_contract import cross_state_codes, same_state_label_codes
    def describe(rows):
        return "; ".join(f"row {i} {records[i]['state']}/{records[i]['district']}" for i in rows)
    for code, rows in cross_state_codes(records).items():
        check(DISCLOSURE_DATASET, CROSS_STATE_WARN, "WARN", False, f"code {code}: {describe(rows)}; rows and codes preserved literally")
    for (state, district), rows in same_state_label_codes(records).items():
        codes = sorted({records[i]["district_code"] for i in rows})
        extra = (overlaps or {}).get((records[rows[0]]["state"], records[rows[0]]["district"]), "")
        check(DISCLOSURE_DATASET, SAME_STATE_WARN, "WARN", False, f"{state}/{district}: codes {codes}: {describe(rows)}; rows and codes preserved literally{extra}")


def _overlap_details(source_root):
    """Best-effort overlap fractions from the accepted S2 disclosure builder; absent on any problem."""
    try:
        from scripts.analyze import producer_limitations as limitations
        entries = limitations._source_disclosures(source_root)
    except Exception:
        return {}
    return {(e["state"], e["district"]): "; overlap fractions " + ", ".join(
        f"rows {o['source_rows']}: {o['fraction_of_first']}/{o['fraction_of_second']}" for o in e["overlaps"])
        for e in entries if e["kind"] == "same_state_label_multiple_codes"}


def _port_key(name):
    return name.replace("Port of ", "").replace(" Port", "").replace(" ", "_").lower()


def _port_classes(summary, matrix):
    """Per summary row: ("non_string"|"parity"|"minor", key). Parity needs matrix columns for a matrix port."""
    comparable = (matrix is not None and len(summary) == len(matrix)
                  and {"port_road_distance_km", "port_drive_time_hours"} <= set(summary))
    classes = []
    for name in summary["nearest_port_name"]:
        if not isinstance(name, str):
            classes.append(("non_string", None))
            continue
        key = _port_key(name)
        has_columns = comparable and key in PORT_KEYS and {f"road_km_to_{key}", f"drive_hours_to_{key}"} <= set(matrix)
        classes.append(("parity" if has_columns else "minor", key))
    return classes


# --- Isochrone coverage coherence (N1d): graph-free, audit-local --------------------------------------------------
# Stored-output consistency of the schema-2 sidecar coverage against the written ISO artifacts, using the DECLARED
# component sizes. It does not rebuild any graph and certifies neither graph truth nor catchment accuracy.
COVERAGE_DATASET = "isochrone_coverage"
COVERAGE_COHERENCE = "sidecar coverage equals ISO artifact properties and geometry/status coherence"
COVERAGE_WARN = "component-bounded catchment (disclosed)"
_COVERAGE_STATUSES = {"ok", "invalid_coordinates", "no_highway_within_150km", "feeder_exceeds_threshold", "unsupported_threshold"}
_COVERAGE_PROPERTIES = ("hub_type", "hub_name", "time_threshold_hours", "status", "reachable_node_count", "snap_feeder_km")


def _coverage_probe(root):
    """('absent'|'legacy'|'unsupported'|'v2', document): structural recognition only; never raises."""
    from scripts.analyze.producer_limitations import SIDECAR_FILENAME, TOP_LEVEL_KEYS, TOP_LEVEL_KEYS_V2
    from scripts.analyze.producer_manifest import read_manifest
    path = pathlib.Path(root) / SIDECAR_FILENAME
    if not os.path.lexists(path):
        return "absent", None
    try:
        document = read_manifest(path)
    except Exception:
        return "unsupported", None
    if isinstance(document, dict):
        version = document.get("schema_version")
        if type(version) is int and version == 1 and set(document) == TOP_LEVEL_KEYS:
            return "legacy", document
        if type(version) is int and version == 2 and set(document) == TOP_LEVEL_KEYS_V2:
            return "v2", document
    return "unsupported", None


def _coverage_geometry_ok(geometry, projector):
    from shapely.geometry import shape
    from shapely.ops import transform
    try:
        geom = shape(geometry)
        if geom.geom_type not in {"Polygon", "MultiPolygon"} or geom.is_empty or not geom.is_valid:
            return False
        x0, y0, x1, y1 = geom.bounds
        if not (-180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90):
            return False
        area = transform(projector, geom).area
        return bool(np.isfinite(area) and area > 0)
    except Exception:
        return False


def _coverage_corroborates(root, document, national):
    """Every artifact feature against the declared entries, field by field with exact types; False on any mismatch."""
    from pyproj import Transformer
    from scripts.analyze.producer_manifest import REQUIRED_ARTIFACTS, read_manifest
    entries = document["isochrone_coverage"]["entries"]
    projector = Transformer.from_crs(4326, 7755, always_xy=True).transform
    for hub_type, name in (("major_port", REQUIRED_ARTIFACTS[5]), ("mmlp", REQUIRED_ARTIFACTS[6])):
        declared = [entry for entry in entries if entry["hub_type"] == hub_type]
        try:
            collection = read_manifest(pathlib.Path(root) / name)
        except Exception:
            return False
        features = collection.get("features") if isinstance(collection, dict) else None
        if not isinstance(features, list) or collection.get("type") != "FeatureCollection" or (national and not declared):
            return False
        runs = []
        for feature in features:
            if (not isinstance(feature, dict) or feature.get("type") != "Feature" or "geometry" not in feature
                    or not isinstance(feature.get("properties"), dict) or any(key not in feature["properties"] for key in _COVERAGE_PROPERTIES)):
                return False
            key = (feature["properties"]["hub_type"], feature["properties"]["hub_name"])
            if not runs or runs[-1][0] != key:
                runs.append((key, []))
            runs[-1][1].append(feature)
        if len(runs) != len(declared):
            return False
        for (key, group), entry in zip(runs, declared):
            thresholds, statuses, counts = entry["thresholds_hours"], entry["statuses"], entry["reachable_node_counts"]
            if key != (hub_type, entry["hub_name"]) or not len(group) == len(thresholds) == len(statuses) == len(counts):
                return False
            for feature, threshold, status, count in zip(group, thresholds, statuses, counts):
                p = feature["properties"]
                if type(p["time_threshold_hours"]) is not float or not np.isfinite(p["time_threshold_hours"]) or p["time_threshold_hours"] != threshold:
                    return False
                if type(p["status"]) is not str or p["status"] not in _COVERAGE_STATUSES or p["status"] != status:
                    return False
                if type(p["reachable_node_count"]) is not int or p["reachable_node_count"] != count:
                    return False
                feeder, wanted = p["snap_feeder_km"], entry["snap_feeder_km"]
                if wanted is None:
                    if feeder is not None:
                        return False
                elif type(feeder) is not float or not np.isfinite(feeder) or not 0 <= feeder <= 150 or abs(feeder - wanted) > 1e-9:
                    return False
                if status == "ok":
                    if feature["geometry"] is None or not _coverage_geometry_ok(feature["geometry"], projector):
                        return False
                elif feature["geometry"] is not None:
                    return False
    return True


def _coverage_fast(root, manifest, severity, national):
    """One coherence record per applicable case, then one WARN per bounded hub only after a complete pass."""
    from scripts.analyze.producer_limitations import SIDECAR_FILENAME, validate_limitations
    state, document = _coverage_probe(root)
    detail, hubs = "", []
    if state in ("absent", "legacy"):
        if not national:
            return
        detail = "coverage: schema-2 sidecar required"
    elif state == "unsupported":
        detail = "coverage: sidecar unreadable or unsupported"
    elif manifest is None:
        detail = "coverage: valid manifest required"
    elif validate_limitations(pathlib.Path(root) / SIDECAR_FILENAME, manifest, require_coverage=True):
        detail = "coverage: sidecar format invalid"
    elif not _coverage_corroborates(root, document, national):
        detail = "coverage: artifact corroboration failed"
    else:
        hubs = [entry for entry in document["isochrone_coverage"]["entries"] if entry["component_bounded_thresholds"]]
    check(COVERAGE_DATASET, COVERAGE_COHERENCE, severity, not detail, detail)
    for entry in hubs:
        check(COVERAGE_DATASET, COVERAGE_WARN, "WARN", False,
              f"{entry['hub_type']} {entry['hub_order']} {entry['hub_name']}: catchment spans the whole snapped road-graph component of "
              f"{entry['snap_component_node_count']} nodes at thresholds {entry['component_bounded_thresholds']}; declared, not verified against the graph")


def _coverage_prerequisite_issues(source_root, root, manifest):
    """Fixed data issues for direct national full mode; exceptions are caught locally and never echoed."""
    from scripts.analyze.producer_limitations import SIDECAR_FILENAME, validate_limitations
    issues = []
    try:
        valid = not validate_limitations(pathlib.Path(root) / SIDECAR_FILENAME, manifest, source_root=source_root, require_coverage=True)
    except Exception:
        valid = False
    if not valid:
        issues.append("national: schema-2 sidecar source validation failed")
    try:
        state, document = _coverage_probe(root)
        corroborated = state == "v2" and _coverage_corroborates(root, document, True)
    except Exception:
        corroborated = False
    if not corroborated:
        issues.append("national: isochrone coverage artifact corroboration failed")
    return issues


def _national_fast_checks(root, manifest):
    from scripts.analyze.producer_limitations import SIDECAR_FILENAME, validate_limitations
    from scripts.analyze.producer_manifest import CODE_CLOSURE_DEFERRED, validate_national_profile
    if manifest is None:
        check("provenance", "national profile", "FAIL", False, "a valid manifest is required")
        return
    issues = validate_national_profile(manifest)
    check("provenance", "national profile", "FAIL", not issues, "; ".join(issues))
    # Without a code root the closure is NOT verified here; full mode is the authority.
    check("provenance", "code closure validation deferred to full mode", "WARN", False, CODE_CLOSURE_DEFERRED)
    issues = validate_limitations(root / SIDECAR_FILENAME, manifest, require_coverage=True)
    check(DISCLOSURE_DATASET, "sidecar format validation", "FAIL", not issues, "; ".join(issues))


def audit_producers_fast(artifact_root, *, national=False):
    """No graph build or Dijkstra. All rows, both isochrones, strict metadata."""
    from scripts.analyze.producer_io import is_island_state
    from scripts.analyze.producer_manifest import MANIFEST_FILENAME, REQUIRED_ARTIFACTS, read_manifest, validate_manifest
    from scripts.audit.identity_contract import IdentityError, validate_identities
    root = pathlib.Path(artifact_root)
    # National mode applies the strict contract regardless of manifest presence; legacy keeps WARN without one.
    severity = "FAIL" if national else _contract_severity(root)
    manifest_path = root / MANIFEST_FILENAME
    check("provenance", "manifest present (legacy pre-regeneration debt)", "WARN", manifest_path.exists())
    thresholds = {"major_port": [4., 8., 12., 24.], "mmlp": [4., 8., 12.]}
    simplify_m, manifest = 500.0, None
    if manifest_path.exists():
        issues = validate_manifest(manifest_path, root)
        check("provenance", "manifest hash/schema/config", "FAIL", not issues, "; ".join(issues))
        if not issues:
            manifest = read_manifest(manifest_path)
            thresholds = manifest["config"]["thresholds_hours"]
            simplify_m = manifest["config"]["simplify_m"]
    if national:
        _national_fast_checks(root, manifest)
    identities = {}
    frames = {}
    schemas = [SUMMARY_COLUMNS, SUMMARY_COLUMNS, MATRIX_COLUMNS, COST_COLUMNS, HINTERLAND_COLUMNS]
    for name, columns in zip(REQUIRED_ARTIFACTS[:5], schemas):
        path = root / name
        check(name, "artifact exists", "FAIL", path.exists())
        if not path.exists():
            continue
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        frames[name] = frame
        check(name, "exact ordered schema", severity, list(frame.columns) == columns)
        if set(IDENTITY) <= set(frame):
            check(name, "nonempty district/state labels", severity,
                  all(isinstance(v, str) and bool(v.strip()) for col in ("state", "district") for v in frame[col]))
            # Frozen S1 contract on the raw records: no code-only matching or numeric coercion.
            records = _identity_records(frame)
            try:
                validate_identities(records)
            except IdentityError as exc:
                check(name, QUALIFIED_IDENTITY, severity, False, str(exc))
            else:
                check(name, QUALIFIED_IDENTITY, severity, True)
                identities[name] = records
        else:
            check(name, "district identity present", "FAIL", False)
        if "is_island" in frame and "state" in frame:
            expected = frame.state.map(is_island_state)
            check(name, "geographic island flags", severity, frame.is_island.isin([True, False]).all() and (frame.is_island == expected).all())
    summary = frames.get(REQUIRED_ARTIFACTS[0])
    parquet = frames.get(REQUIRED_ARTIFACTS[1])
    if summary is not None and parquet is not None:
        check("producer_summary", "CSV/parquet ordered semantic values/nulls", severity, frames_semantically_equal(summary, parquet))
    if identities.get(REQUIRED_ARTIFACTS[0]) is not None:
        _disclosure_warnings(identities[REQUIRED_ARTIFACTS[0]])
    if summary is not None:
        for name, frame in frames.items():
            if set(IDENTITY) <= set(frame) and set(IDENTITY) <= set(summary):
                check(name, "all ordered identities match summary", severity,
                      _exact_identities(_identity_records(summary), frame))
            if "pop_2011" in frame and "pop_2011" in summary:
                check(name, "population matches summary including missing", severity,
                      frames_semantically_equal(summary[["pop_2011"]], frame[["pop_2011"]]))
        for kind in KINDS:
            _route_checks(summary, "summary", f"{kind}_road_distance_km", f"{kind}_drive_time_hours", straight=f"{kind}_straight_km", minutes=f"{kind}_drive_time_min", severity=severity)
    matrix = frames.get(REQUIRED_ARTIFACTS[2])
    if matrix is not None:
        for key in PORT_KEYS:
            _route_checks(matrix, "matrix", f"road_km_to_{key}", f"drive_hours_to_{key}", severity=severity)
        if "state" in matrix:
            islands = matrix.state.map(is_island_state)
            cols = [c for c in matrix if c.startswith(("road_km_to_", "drive_hours_to_"))]
            check("matrix", "islands have no fabricated mainland port routes", "FAIL", matrix.loc[islands, cols].isna().all().all())
    if summary is not None and "nearest_port_name" in summary:
        # Always emitted: every selected-port row is parity-checked, minor/no-column, or non-string.
        classes = _port_classes(summary, matrix)
        counts = {kind: sum(1 for k, _ in classes if k == kind) for kind in ("parity", "minor", "non_string")}
        check("summary", "selected-port coverage", severity, sum(counts.values()) == len(summary),
              f"parity_checked={counts['parity']}; minor_or_no_column={counts['minor']}; non_string={counts['non_string']}; total={len(summary)}")
        for index, (kind, key) in enumerate(classes):
            if kind == "parity":
                check("summary", f"row {index}: selected major port matrix parity", severity,
                      frames_semantically_equal(pd.DataFrame({"d": [summary.iloc[index].port_road_distance_km], "t": [summary.iloc[index].port_drive_time_hours]}), pd.DataFrame({"d": [matrix.iloc[index][f"road_km_to_{key}"]], "t": [matrix.iloc[index][f"drive_hours_to_{key}"]]})))
    hinterland = frames.get(REQUIRED_ARTIFACTS[4])
    if hinterland is not None and "hinterland_category" in hinterland:
        check("hinterland", "valid hinterland categories", "FAIL", set(hinterland.hinterland_category.dropna()) <= {"Captive Hinterland", "Dominant / Mildly Contested", "Highly Contested / Split Hinterland", "Island / Isolated", "Isolated"})
        if "state" in hinterland:
            islands = hinterland.state.map(is_island_state)
            check("hinterland", "island category and null shares", severity,
                  (hinterland.loc[islands, "hinterland_category"] == "Island / Isolated").all()
                  and all(hinterland.loc[islands, c].isna().all() for c in ("primary_port_probability", "secondary_port_probability", "primary_port_drive_hours", "secondary_port_drive_hours", "contestability_index") if c in hinterland))
    if hinterland is not None and {"primary_port_probability", "secondary_port_probability", "contestability_index"} <= set(hinterland):
        a, b = hinterland.primary_port_probability, hinterland.secondary_port_probability
        valid = a.notna() | b.notna()
        check("hinterland", "shares bounds/dominance/rounded partial sum", "FAIL", bool((a[valid].between(0, 100) & b[valid].between(0, 100) & (a[valid] >= b[valid]) & (a[valid] + b[valid] <= 100.1 + 1e-9)).all()))
        check("hinterland", "contestability matches rounded shares", severity,
              bool((hinterland.loc[valid, "contestability_index"].between(0, 1) & ((hinterland.loc[valid, "contestability_index"] - (1 - (a[valid] - b[valid]) / 100)).abs() <= .0015 + 1e-9)).all()))
        if "hinterland_category" in hinterland:
            category_ok = []
            for primary, category in zip(a[valid], hinterland.loc[valid, "hinterland_category"]):
                category_ok.append((category == "Captive Hinterland" and primary >= 74.95 - 1e-9)
                                   or (category == "Dominant / Mildly Contested" and 49.95 - 1e-9 <= primary <= 75.05 + 1e-9)
                                   or (category == "Highly Contested / Split Hinterland" and primary <= 50.05 + 1e-9))
            check("hinterland", "category agrees with rounded primary share", severity, all(category_ok))
    for name in REQUIRED_ARTIFACTS[5:]:
        path = root / name
        check(name, "artifact exists", "FAIL", path.exists())
        if path.exists():
            kind = "major_port" if name == "major_ports_isochrones.geojson" else "mmlp"
            try:
                _iso_checks(path, severity, thresholds[kind], simplify_m)
            except Exception as exc:  # One unreadable artifact must not erase its sibling's records.
                check(name, "section crashed", "FAIL", False, type(exc).__name__)
    try:  # Last and independently guarded: the ISO sibling records above are always kept.
        _coverage_fast(root, manifest, severity, national)
    except Exception as exc:
        check(COVERAGE_DATASET, COVERAGE_COHERENCE, severity, False, f"coverage: corroboration crashed ({type(exc).__name__})")


def _guarded(section, function, *args, **kwargs):
    """Run one audit section; any exception becomes a sanitized FAIL and later sections continue."""
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        check(section, "section crashed", "FAIL", False, type(exc).__name__)
        return None


def audit_producers_full(source_root, artifact_root, cache_dir, *, national=False, code_root=None):
    """Independent guarded sections: provenance, freight, gravity, population, source identity,
    ports, isochrone sources, disclosures, then the bounded graph oracles.

    Only the canonical graph builder may populate the explicit cache directory.
    Source/analysis artifacts and report files are never rewritten here. National mode verifies
    the actual code closure and input files BEFORE any graph work or cache creation.
    """
    source_root, root = pathlib.Path(source_root), pathlib.Path(artifact_root)
    state = {"severity": "FAIL" if national else _contract_severity(root), "manifest": None, "invalid": False, "national": bool(national),
             "code_root": REPO_ROOT if code_root is None else pathlib.Path(code_root), "national_issues": []}
    severity = state["severity"]
    _guarded("provenance", _full_provenance, source_root, root, state)
    _guarded("freight_recompute", _full_freight, root, state)
    _guarded("hinterland_recompute", _full_gravity, source_root, root, state)
    _guarded("population_source", _full_population, source_root, root, state)
    _guarded("district_source", _source_identity_checks, source_root, root, severity)
    _guarded("port_source", _source_port_checks, source_root, root, severity)
    _guarded("isochrone_source", _source_isochrone_checks, source_root, root, severity)
    _guarded("source_disclosures", _full_disclosures, source_root, root, state)
    if national:
        issues = _guarded("graph_oracle", _national_prerequisites, source_root, root, state)
        if issues is None or issues:
            if issues:
                check("graph_oracle", "blocked: national prerequisites", "FAIL", False, "; ".join(issues))
            return
    audit_producer_graph_oracles(source_root, artifact_root, cache_dir)


def _full_provenance(source_root, root, state):
    from scripts.analyze.producer_manifest import MANIFEST_FILENAME, read_manifest, validate_manifest, validate_national_profile
    manifest_path = root / MANIFEST_FILENAME
    if not manifest_path.exists():
        return
    issues = validate_manifest(manifest_path, root, source_root=source_root, code_root=state["code_root"])
    check("provenance", "source/code hashes and model config", "FAIL", not issues, "; ".join(issues))
    if issues:
        state["invalid"] = True
        return
    manifest = read_manifest(manifest_path)
    state["manifest"] = manifest
    logical_hashes = "; ".join(f"{name}={entry['sha256']}" for name, entry in sorted(manifest["inputs"].items()))
    check("provenance", "validated run/source hash provenance", "FAIL", True,
          f"run_id={manifest['run_id']}; source={manifest['graph']['selected_source']}; {logical_hashes}")
    if state["national"]:
        state["national_issues"] = validate_national_profile(manifest, code_root=state["code_root"])
        check("provenance", "national profile", "FAIL", not state["national_issues"], "; ".join(state["national_issues"]))


def _national_prerequisites(source_root, root, state):
    """Fixed-message issues; nothing here echoes a path. Existence and containment of every input."""
    issues = []
    manifest = state["manifest"]
    if manifest is None:
        issues.append("national: a valid manifest is required" if state["invalid"] else "national: manifest missing")
        return issues
    issues.extend(state["national_issues"])
    inputs = manifest.get("inputs")
    base = pathlib.Path(source_root).resolve()
    missing = False
    for logical in (inputs if isinstance(inputs, dict) else {}):
        try:
            target = (base / logical).resolve()
            missing = missing or not (target.is_file() and target.is_relative_to(base))
        except (OSError, ValueError, TypeError):
            missing = True
    if missing:
        issues.append("national: an input is missing or outside the source root")
    issues.extend(_coverage_prerequisite_issues(source_root, root, manifest))
    return list(dict.fromkeys(issues))


def _full_freight(root, state):
    from scripts.analyze import freight_cost_core as core
    from scripts.analyze.producer_io import is_island_state
    severity, manifest = state["severity"], state["manifest"]
    if state["invalid"]:
        check("freight_recompute", "required recomputation prerequisites", "FAIL", False, "blocked: invalid manifest")
        return
    parameters = manifest["config"]["freight_parameters"] if manifest is not None else None
    summary = pd.read_csv(root / "nh_district_travel_time_summary.csv")
    costs = pd.read_csv(root / "district_freight_modal_split.csv")
    if not set(COST_COLUMNS) <= set(costs) or not {"port_road_distance_km", "port_drive_time_hours", "state"} <= set(summary):
        check("freight_recompute", "required recomputation schema", severity, False)
        return
    check("freight_recompute", "same ordered identities", severity, frames_semantically_equal(summary[IDENTITY], costs[IDENTITY]))
    if len(summary) != len(costs):
        return
    for index, row in summary.iterrows():
        try:
            canonical_state = core.normalize_freight_state(row.state)
        except Exception as exc:
            # C6: an unrecognized state is an unconditional FAIL (sanitized type only); later rows continue.
            check("freight_recompute", f"row {index}: canonical state", "FAIL", False, type(exc).__name__)
            continue
        d, t = row.port_road_distance_km, row.port_drive_time_hours
        actual = costs.iloc[index]
        check("freight_recompute", f"row {index}: target port identity", severity,
              frames_semantically_equal(pd.DataFrame({"port": [row.get("nearest_port_name")]}), pd.DataFrame({"port": [actual.target_port]})))
        terminal = row.get("freight_terminal_road_distance_km")
        valid_values = all(pd.isna(v) or (isinstance(v, (int, float, np.number)) and not isinstance(v, (bool, np.bool_)) and np.isfinite(v) and v >= 0) for v in [d, t, terminal])
        check("freight_recompute", f"row {index}: finite nonnegative observations", "FAIL", valid_values)
        if not valid_values:
            continue
        paired = pd.isna(d) == pd.isna(t)
        check("freight_recompute", f"row {index}: paired source observations", "FAIL", paired)
        if not paired:
            continue
        if is_island_state(row.state) or pd.isna(d):
            nullcols = [c for c in COST_COLUMNS[6:] if c != "optimal_mode"]
            check("freight_recompute", f"row {index}: unreachable null/N/A", severity,
                  actual[nullcols].isna().all() and actual.optimal_mode == "N/A (Island / Isolated)")
            continue
        terminal = None if pd.isna(terminal) else terminal
        unit = core.calculate_unit_costs(d, t, terminal, canonical_state, parameters)
        expected = {"road_distance_km": round(unit.road.distance_km, 1), "road_drive_time_hours": round(unit.road.transit_time_hours, 2),
                    "road_cost_per_ton_inr": round(unit.road.cost_per_ton_inr, 1), "rail_cost_per_ton_inr": round(unit.conventional_rail.cost_per_ton_inr, 1),
                    "dfc_cost_per_ton_inr": round(unit.dfc_rail.cost_per_ton_inr, 1) if unit.dfc_rail else np.nan,
                    "optimal_mode": unit.optimal_mode, "modal_shift_savings_inr": round(unit.modal_shift_savings_per_ton_inr, 1),
                    "modal_shift_savings_pct": round(unit.modal_shift_savings_pct, 1), "break_even_distance_km": unit.break_even_distance_km}
        check("freight_recompute", f"row {index}: rounded pure core values", severity,
              frames_semantically_equal(pd.DataFrame([expected]), pd.DataFrame([{c: actual[c] for c in expected}])))


def _full_gravity(source_root, root, state):
    """Accepted pure builder with the manifest's alpha/beta and column-keyed capacities; no new formula."""
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA, build_port_hinterland
    severity, manifest = state["severity"], state["manifest"]
    prerequisites = "gravity recomputation prerequisites"
    if manifest is None:
        check("hinterland_recompute", prerequisites, severity, False, "blocked: invalid manifest" if state["invalid"] else "unavailable without manifest (legacy)")
        return
    population_path = pathlib.Path(source_root) / "demographic" / "district_population_estimates.csv"
    if not population_path.exists():
        check("hinterland_recompute", prerequisites, severity, False, "population source missing")
        return
    config = manifest["config"]
    port_data = {column: config["capacities"][name] for column, (name, _) in PORT_MATRIX_DATA.items()}
    expected = build_port_hinterland(pd.read_csv(root / "nh_district_port_matrix.csv"), pd.read_csv(population_path),
                                     port_data=port_data, alpha=config["alpha"], beta=config["beta"])
    # The stored artifact is observed through the default CSV reader, which reads the builder's literal
    # "N/A" placeholders as missing. Apply the same observation to the pure-builder frame, in memory only
    # (same writer, same reader); values, model formula and tolerance are untouched.
    expected = pd.read_csv(io.StringIO(expected.to_csv(index=False)))
    stored = pd.read_csv(root / "district_port_hinterland_catchment.csv")
    check("hinterland_recompute", "pure builder stored-output consistency", severity,
          set(HINTERLAND_COLUMNS) <= set(stored) and frames_semantically_equal(expected[HINTERLAND_COLUMNS], stored[HINTERLAND_COLUMNS]))


def _exact_tuple_corroboration(summary, population, state):
    """Separate from W0: literal (state, district, strict code) tuples, only when uniquely exact for every row."""
    from scripts.audit.identity_contract import IdentityError, strict_code
    name = "exact-tuple corroboration"
    def key(record):
        if type(record["state"]) is not str or type(record["district"]) is not str:
            raise IdentityError("label")
        return record["state"], record["district"], strict_code(record["district_code"])
    index = {}
    for record in population.to_dict("records"):
        try:
            index.setdefault(key(record), []).append(record["pop_2011"])
        except (IdentityError, KeyError):
            continue
    matched, reason = [], None
    for record in summary.to_dict("records"):
        try:
            found = index.get(key(record), [])
        except (IdentityError, KeyError):
            reason = "a summary identity is not a strict literal tuple"
            break
        if len(found) != 1:
            reason = "not every summary identity has exactly one literal population tuple"
            break
        matched.append(found[0])
    if reason is not None:
        # Legacy/pre-national runs pass with the reason; a national run requires applicability.
        check("population_source", name, "FAIL", not state["national"], f"not applicable: {reason}")
        return
    check("population_source", name, state["severity"],
          frames_semantically_equal(pd.DataFrame({"pop_2011": matched}), summary[["pop_2011"]]))


def _full_population(source_root, root, state):
    from scripts.analyze.producer_io import district_population
    severity = state["severity"]
    population_path = pathlib.Path(source_root) / "demographic" / "district_population_estimates.csv"
    check("population_source", "W0 population source available", severity, population_path.exists(),
          "" if population_path.exists() else "population source missing")
    if not population_path.exists():
        return
    summary = pd.read_csv(root / "nh_district_travel_time_summary.csv")
    population = pd.read_csv(population_path)
    expected = district_population(summary, population)
    for name in ("nh_district_travel_time_summary.csv", "district_freight_modal_split.csv", "district_port_hinterland_catchment.csv"):
        frame = pd.read_csv(root / name)
        check(name, "W0 code-first population/null policy", severity, "pop_2011" in frame and frames_semantically_equal(pd.DataFrame({"pop_2011": expected}), frame[["pop_2011"]]))
    _exact_tuple_corroboration(summary, population, state)


def _full_disclosures(source_root, root, state):
    from scripts.audit.identity_contract import IdentityError, validate_identities
    declared = pathlib.Path(source_root) / "administrative" / "india_districts_lgd.geojson"
    if not declared.exists():
        check(DISCLOSURE_DATASET, "source identity disclosure prerequisites", state["severity"], False, "declared district GeoJSON missing")
        return
    districts = gpd.read_file(declared)
    if not set(IDENTITY) <= set(districts):
        check(DISCLOSURE_DATASET, "source identity validation", "FAIL", False, "missing primary district identity columns")
        return
    records = _identity_records(districts)
    try:
        validate_identities(records)
    except IdentityError as exc:
        check(DISCLOSURE_DATASET, "source identity validation", "FAIL", False, str(exc))
        return
    _disclosure_warnings(records, _overlap_details(source_root))
    if state["national"] and state["manifest"] is not None:
        from scripts.analyze.producer_limitations import SIDECAR_FILENAME, validate_limitations
        issues = validate_limitations(root / SIDECAR_FILENAME, state["manifest"], source_root=source_root, require_coverage=True)
        check(DISCLOSURE_DATASET, "sidecar source-grounded validation", "FAIL", not issues, "; ".join(issues))


def audit_producer_graph_oracles(source_root, artifact_root, cache_dir):
    """Full-only wrapper: corruption/missing sources always fail closed."""
    from scripts.audit.producer_graph_oracle import graph_oracle_checks
    try:
        results = graph_oracle_checks(source_root, artifact_root, cache_dir)
    except Exception as exc:
        check("graph_oracle", "independent graph oracles unavailable/corrupt", "FAIL", False,
              type(exc).__name__)
        return
    for result in results:
        check(result["dataset"], result["check"], "FAIL", result["ok"], result["detail"])



def _source_producer_checks(source_root, artifact_root, severity):
    """Wrapper over the three source helpers (identity, selected ports, isochrone hubs)."""
    _source_identity_checks(source_root, artifact_root, severity)
    _source_port_checks(source_root, artifact_root, severity)
    _source_isochrone_checks(source_root, artifact_root, severity)


def _source_identity_checks(source_root, artifact_root, severity):
    """Declared GeoJSON only (never the parquet); literal ordered identities against all five tables."""
    from scripts.analyze.producer_manifest import REQUIRED_ARTIFACTS
    source_root, artifact_root = pathlib.Path(source_root), pathlib.Path(artifact_root)
    declared = source_root / "administrative" / "india_districts_lgd.geojson"
    check("district_source", "declared district GeoJSON available", severity, declared.exists(),
          "" if declared.exists() else "declared district GeoJSON missing")
    if not declared.exists():
        return
    districts = gpd.read_file(declared)
    records = _identity_records(districts) if set(IDENTITY) <= set(districts) else None
    for name in REQUIRED_ARTIFACTS[:5]:
        path = artifact_root / name
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        check(name, "source-derived district count and ordered identities", severity,
              records is not None and _exact_identities(records, frame))


def _source_port_checks(source_root, artifact_root, severity):
    """Full-source classification of each selected nearest port; unknown names never pass silently."""
    source_root, artifact_root = pathlib.Path(source_root), pathlib.Path(artifact_root)
    ports_path = source_root / "logistics_hubs" / "ports.csv"
    check("port_source", "source ports available for selected-port classification", severity, ports_path.exists(),
          "" if ports_path.exists() else "source ports missing")
    if not ports_path.exists():
        return
    ports = pd.read_csv(ports_path)
    names = set(ports["name"].dropna())
    major = set(ports.loc[ports["hub_type"] == "major_port", "name"]) if "hub_type" in ports else set()
    summary = pd.read_csv(artifact_root / "nh_district_travel_time_summary.csv")
    matrix_path = artifact_root / "nh_district_port_matrix.csv"
    matrix = pd.read_csv(matrix_path) if matrix_path.exists() else None
    counts = dict.fromkeys(("parity_checked", "major_rows", "minor_or_no_column", "non_string", "unknown", "unmapped_major"), 0)
    for name, (kind, _) in zip(summary["nearest_port_name"], _port_classes(summary, matrix)):
        if kind == "non_string":
            counts["non_string"] += 1
        elif name not in names:
            counts["unknown"] += 1
        elif name in major:
            counts["major_rows"] += 1
            counts["parity_checked" if kind == "parity" else "unmapped_major"] += 1
        else:
            counts["minor_or_no_column"] += 1
    ok = counts["unknown"] == 0 and counts["unmapped_major"] == 0 and counts["parity_checked"] == counts["major_rows"]
    check("summary", "selected-port classification", severity, ok,
          "; ".join(f"{key}={value}" for key, value in counts.items()) + f"; total={len(summary)}")


def _source_isochrone_checks(source_root, artifact_root, severity):
    """Source hub names/invalid coordinates/thresholds against each isochrone artifact."""
    from scripts.analyze.producer_manifest import MANIFEST_FILENAME, read_manifest, validate_config
    source_root, artifact_root = pathlib.Path(source_root), pathlib.Path(artifact_root)
    thresholds = {"major_port": [4., 8., 12., 24.], "mmlp": [4., 8., 12.]}
    manifest_path = artifact_root / MANIFEST_FILENAME
    if manifest_path.exists():
        try:
            config = read_manifest(manifest_path).get("config")
            if not validate_config(config):
                thresholds = config["thresholds_hours"]
        except Exception:
            pass  # Unavailable/invalid manifest: fall back to the documented defaults; provenance FAILs separately.
    pairs = (("ports.csv", "major_ports_isochrones.geojson", "major_port"), ("mmlps.csv", "mmlp_isochrones.geojson", "mmlp"))

    def one(source, output, kind):
        path = source_root / "logistics_hubs" / source
        check(output, "isochrone source available", severity, path.exists(), "" if path.exists() else "source hubs missing")
        if not path.exists():
            return
        hubs = pd.read_csv(path)
        if kind == "major_port":
            hubs = hubs[hubs.hub_type == kind]
        features = read_manifest(artifact_root / output)["features"]
        expected = [(name, kind, t) for name in hubs["name"] for t in thresholds[kind]]
        actual = [(f.get("properties", {}).get("hub_name"), f.get("properties", {}).get("hub_type"), f.get("properties", {}).get("time_threshold_hours")) for f in features]
        check(output, "source-derived feature count and ordered hub/threshold identities", severity, actual == expected)
        for _, hub in hubs.iterrows():
            latitude, longitude = pd.to_numeric(pd.Series([hub.get("latitude"), hub.get("longitude")]), errors="coerce")
            if not np.isfinite([latitude, longitude]).all() or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                selected = [f for f in features if f.get("properties", {}).get("hub_name") == hub["name"]]
                check(output, "invalid-coordinate source identity retained with null geometry", severity,
                      len(selected) == len(thresholds[kind]) and all(f.get("geometry") is None and f.get("properties", {}).get("status") == "invalid_coordinates" for f in selected))

    for source, output, kind in pairs:
        try:
            one(source, output, kind)
        except Exception as exc:  # Each artifact is independent; sanitized type only.
            check(output, "section crashed", "FAIL", False, type(exc).__name__)


def _real(path):
    """Real path through the deepest existing ancestor (symlinks and Windows junctions), then the
    nonexistent remainder appended, case-normalized. Nothing is created."""
    path = pathlib.Path(os.path.abspath(path))
    missing = []
    while not os.path.lexists(path) and path != path.parent:
        missing.append(path.name)
        path = path.parent
    resolved = pathlib.Path(os.path.realpath(path))
    for part in reversed(missing):
        resolved = resolved / part
    return pathlib.Path(os.path.normcase(str(resolved)))


def _overlaps(a, b):
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _check_write_roots(read_roots, writes):
    """C5: each write root vs each read root, and write roots vs each other: not equal and neither
    contains the other. Read roots may nest. Raises ValueError before anything is created."""
    reads = [_real(path) for path in read_roots]
    resolved = {label: _real(path) for label, path in writes.items()}
    labels = list(resolved)
    for label in labels:
        if any(_overlaps(resolved[label], read) for read in reads):
            raise ValueError(f"{label} must not equal or overlap a read root")
    for position, first in enumerate(labels):
        for second in labels[position + 1:]:
            if _overlaps(resolved[first], resolved[second]):
                raise ValueError("cache and report directories must not overlap")


class AuditRunner:
    """Instance roots/result sink; never mutate module DATA_DIR per run."""

    def __init__(self, fast=False, *, source_root=None, artifact_root=None, cache_dir=None, require_manifest=False,
                 national=False, report_dir=None):
        self.fast = bool(fast)
        self.source_root = pathlib.Path(DATA_DIR if source_root is None else source_root)
        self.artifact_root = pathlib.Path(artifact_root) if artifact_root is not None else self.source_root / "analysis"
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir is not None else REPO_ROOT / "outputs" / "cache"
        self.national = bool(national)
        self.require_manifest = bool(require_manifest) or self.national
        self.report_dir = pathlib.Path(report_dir) if report_dir is not None else None
        writes = {"cache directory": self.cache_dir}
        if self.report_dir is not None:
            writes["report directory"] = self.report_dir
        _check_write_roots((self.source_root, self.artifact_root), writes)
        self.results = []

    def _report_directory(self, override=None):
        """Effective report directory, validated against every read root and the cache. No mkdir here."""
        if override is not None:
            directory = pathlib.Path(override)
        elif self.report_dir is not None:
            directory = self.report_dir
        else:
            legacy = _real(self.artifact_root) == _real(self.source_root / "analysis")
            if self.national or self.require_manifest or not legacy:
                raise ValueError("explicit report directory required")
            directory = REPO_ROOT / "docs"
        _check_write_roots((self.source_root, self.artifact_root), {"report directory": directory, "cache directory": self.cache_dir})
        return directory

    def run(self):
        from scripts.analyze.producer_manifest import MANIFEST_FILENAME
        self.results = []
        token = _RESULT_SINK.set(self.results)
        try:
            if self.require_manifest:
                check("provenance", "required manifest present", "FAIL", (self.artifact_root / MANIFEST_FILENAME).exists())
            calls = [(fn, (self.source_root,), {}) for fn in (audit_boundaries, audit_rail, audit_hubs, audit_freight, audit_demographics, audit_geoparquet, audit_catalog)]
            national = {"national": True} if self.national else {}
            calls += [(check_baseline, (self.source_root, self.artifact_root), {}),
                      (audit_roads, (self.source_root,), {"fast": self.fast}),
                      (audit_producers_fast, (self.artifact_root,), dict(national))]
            if not self.fast:
                calls += [(audit_villages, (self.source_root,), {}), (audit_analysis, (self.source_root, self.artifact_root), {}),
                          (audit_producers_full, (self.source_root, self.artifact_root, self.cache_dir), dict(national))]
            for fn, args, kwargs in calls:
                try:
                    fn(*args, **kwargs)
                except Exception as exc:
                    # Avoid paths/credentials accidentally included in exception text.
                    check(fn.__name__, "AUDIT CRASHED", "FAIL", False, type(exc).__name__)
        finally:
            _RESULT_SINK.reset(token)
        return self.results

    def write_report(self, report_dir=None):
        directory = self._report_directory(report_dir)
        report = directory / ("audit_report_fast.md" if self.fast else "audit_report.md")
        directory.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(self.results, columns=["dataset", "check", "status", "severity", "detail"])
        counts = frame.status.value_counts()
        summary = f"Checks: {len(frame)} — PASS {counts.get('PASS', 0)} / WARN {counts.get('WARN', 0)} / FAIL {counts.get('FAIL', 0)}\n\n"
        report.write_text("# Data Audit Report\n\n" + summary + ("Stage: fast metadata/semantics\n\n" if self.fast else "Stage: full geometry/recomputation\n\n") + frame.to_markdown(index=False), encoding="utf-8")
        return report


def staged_validator(source_root, cache_dir, report_dir):
    """Callable for ``promote_staged_outputs(..., validate=...)``.

    Runs the national fast and full audits against the staged directory (explicit cache/report roots,
    manifest required) and returns every FAIL as "dataset: check" plus any source-grounded sidecar
    issue. An empty list means acceptance. This is per-file validation; it does not make publication a
    dataset transaction.
    """
    def validate(stage_dir):
        from scripts.analyze.producer_limitations import SIDECAR_FILENAME, validate_limitations
        from scripts.analyze.producer_manifest import MANIFEST_FILENAME, read_manifest
        issues = []
        try:
            for fast in (True, False):
                runner = AuditRunner(fast=fast, source_root=source_root, artifact_root=stage_dir, cache_dir=cache_dir,
                                     require_manifest=True, national=True, report_dir=report_dir)
                issues.extend(f"{r['dataset']}: {r['check']}" for r in runner.run() if r["status"] == "FAIL")
                runner.write_report()
        except ValueError as exc:
            return [f"roots: {exc}"]
        try:
            manifest = read_manifest(pathlib.Path(stage_dir) / MANIFEST_FILENAME)
        except (OSError, ValueError, TypeError):
            issues.append("limitations: manifest unavailable")
        else:
            issues.extend(validate_limitations(pathlib.Path(stage_dir) / SIDECAR_FILENAME, manifest, source_root=source_root, require_coverage=True))
        return issues
    return validate


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fail-on-warn", action="store_true")
    ap.add_argument("--fast", action="store_true",
                    help="skip heavy geometry checks (village re-reads, reproducibility)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="(re)write data/audit_baseline.json from current data")
    ap.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    ap.add_argument("--analysis-dir", type=pathlib.Path)
    ap.add_argument("--cache-dir", type=pathlib.Path)
    ap.add_argument("--require-manifest", action="store_true")
    ap.add_argument("--national", action="store_true",
                    help="national profile and sidecar checks (implies --require-manifest); needs --report-dir")
    ap.add_argument("--report-dir", type=pathlib.Path,
                    help="directory for the audit report (required unless auditing the legacy data/analysis layout)")
    args = ap.parse_args()
    source_root = args.data_dir
    artifact_root = args.analysis_dir or source_root / "analysis"

    if args.update_baseline:
        import json
        deep_counts = {}
        states = gpd.read_file(source_root / "administrative" / "india_states_lgd.geojson")
        deep_counts["admin:states"] = len(states)
        dist = gpd.read_file(source_root / "administrative" / "india_districts_lgd.geojson")
        deep_counts["admin:districts"] = len(dist)
        sub = gpd.read_file(source_root / "administrative" / "india_subdistricts_lgd.gpkg")
        deep_counts["admin:subdistricts"] = len(sub)
        import glob as g
        for f in g.glob(str(source_root / "administrative" / "villages" / "*_soi_villages.geojson")):
            deep_counts[f"villages:{pathlib.Path(f).stem}"] = len(gpd.read_file(f))
        deep_counts.update(collect_counts(source_root, artifact_root))
        baseline_path = source_root / "audit_baseline.json"
        baseline_path.write_text(json.dumps(deep_counts, indent=1, sort_keys=True))
        print(f"baseline written: {len(deep_counts)} series -> {baseline_path}")
        return

    try:
        runner = AuditRunner(fast=args.fast, source_root=source_root,
                             artifact_root=artifact_root, cache_dir=args.cache_dir,
                             require_manifest=args.require_manifest or args.national,
                             national=args.national, report_dir=args.report_dir)
        # Real runners resolve and validate the effective report directory now, before any audit or graph
        # cache write; duck-typed test doubles without the private helper keep their argument-map contract.
        getattr(runner, "_report_directory", lambda: None)()
    except ValueError as exc:
        print(f"audit configuration error: {exc}", file=sys.stderr)
        sys.exit(2)
    df = pd.DataFrame(runner.run())
    n_fail = (df.status == "FAIL").sum()
    n_warn = (df.status == "WARN").sum()
    print(f"\n==== AUDIT: {len(df)} checks | {len(df)-n_fail-n_warn} PASS | {n_warn} WARN | {n_fail} FAIL ====")

    try:
        report = runner.write_report()
    except ValueError as exc:
        print(f"audit configuration error: {exc}", file=sys.stderr)
        sys.exit(2)
    print(f"report -> {report}")

    if n_fail or (args.fail_on_warn and n_warn):
        sys.exit(1)


if __name__ == "__main__":
    main()
