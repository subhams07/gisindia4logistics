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
import glob
import pathlib
import sys
import traceback

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR, REPO_ROOT  # noqa: E402

# ---------------------------------------------------------------- reference
# District counts per state/UT, verified against the source's own LGD codes
# (incl. 2024-25 formations: Arunachal Bichom/Keyi Panyor, Chhattisgarh 2025
# batch incl. Khairagarh, Goa Kushavati, Maharashtra 37 = 36 + derived
# Malegaon-598). Documented source gaps: Ladakh source predates the 2024
# five-district split (2 only); Puducherry lacks Mahe/Yanam (2 of 4); J&K
# = 20 coded + 2 PoK rows with null codes; Delhi = 11 coded + Nazul (null).
EXPECTED_DISTRICTS = {
    "Andhra Pradesh": 26, "Arunachal Pradesh": 27, "Assam": 35, "Bihar": 38,
    "Chhattisgarh": 33, "Goa": 3, "Gujarat": 33, "Haryana": 22,
    "Himachal Pradesh": 12, "Jharkhand": 24, "Karnataka": 31,
    "Kerala": 14, "Madhya Pradesh": 55, "Maharashtra": 37, "Manipur": 16,
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
BASELINE_PATH = REPO_ROOT / "data" / "audit_baseline.json"


def check(dataset: str, name: str, severity: str, ok: bool | None, detail: str = "") -> None:
    status = "PASS" if ok else ("FAIL" if severity == "FAIL" else "WARN")
    if ok:
        status = "PASS"
    RESULTS.append({"dataset": dataset, "check": name, "status": status,
                    "severity": severity, "detail": detail})
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def audit_boundaries():
    print("\n== LGD boundaries (states/districts/subdistricts) ==")
    states = gpd.read_file(DATA_DIR / "administrative" / "india_states_lgd.geojson")
    check("states_lgd", "36 states/UTs", "FAIL", len(states) == 36, f"{len(states)}")
    check("states_lgd", "valid geometries", "FAIL", states.geometry.is_valid.all())
    check("states_lgd", "no null state names", "FAIL", states.state.notna().all())

    dist = gpd.read_file(DATA_DIR / "administrative" / "india_districts_lgd.geojson")
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

    sub = gpd.read_file(DATA_DIR / "administrative" / "india_subdistricts_lgd.gpkg")
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


def audit_villages():
    print("\n== SoI village files ==")
    files = sorted(glob.glob(str(DATA_DIR / "administrative" / "villages" / "*_soi_villages.geojson")))
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

    dist = gpd.read_file(DATA_DIR / "administrative" / "india_districts_lgd.geojson")
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


def audit_analysis():
    print("\n== analysis outputs ==")
    village_files = [f for f in glob.glob(str(DATA_DIR / "administrative" / "villages" / "*_soi_villages.geojson"))
                     if "pune" not in f]
    summaries = [f for f in glob.glob(str(DATA_DIR / "analysis" / "*_district_access_summary.csv"))
                 if "india_district" not in f]
    check("analysis", "36 state summaries", "FAIL", len(summaries) == 36, f"{len(summaries)}")

    roll = pd.read_csv(DATA_DIR / "analysis" / "india_district_access_summary.csv")
    check("analysis", "rollup has 36 states + 36 __STATE__ rows", "FAIL",
          roll.state.nunique() == 36 and (roll.district == "__STATE__").sum() == 36,
          f"{roll.state.nunique()} states, {(roll.district=='__STATE__').sum()} state rows")

    # per-state: village_access row count == source village file count
    for vf in village_files:
        slug = pathlib.Path(vf).stem.replace("_soi_villages", "")
        av = DATA_DIR / "analysis" / f"{slug}_village_access.csv"
        if not av.exists():
            check("analysis", f"{slug}: village access table exists", "FAIL", False)
            continue
        n_src = len(gpd.read_file(vf))
        n_out = len(pd.read_csv(av))
        check("analysis", f"{slug}: row count matches source", "FAIL",
              n_src == n_out, f"{n_out} vs {n_src}")

    # reproducibility: recompute 40 sample villages in Haryana
    import shapely
    av = pd.read_csv(DATA_DIR / "analysis" / "haryana_village_access.csv")
    vill = gpd.read_file(DATA_DIR / "administrative" / "villages" / "haryana_soi_villages.geojson")
    st = pd.read_csv(DATA_DIR / "rail" / "railway_stations.csv")
    import geopandas as gpd2  # noqa: F401
    sample = vill.sample(40, random_state=42)
    pts = gpd.GeoSeries(sample.geometry.representative_point(), crs=4326).to_crs(7755)
    fac = gpd.GeoDataFrame(st, geometry=gpd.points_from_xy(st.longitude, st.latitude), crs=4326).to_crs(7755)
    d = fac.geometry.unary_union
    recomputed = pts.map(lambda p: p.distance(d) / 1000).round(2).values
    stored = av.set_index(["district", "village"])
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


def audit_rail():
    print("\n== rail ==")
    st = pd.read_csv(DATA_DIR / "rail" / "railway_stations.csv")
    check("rail_stations", "coords in India bbox", "FAIL",
          bool(st.longitude.between(68, 97.5).all() and st.latitude.between(6, 37.5).all()))
    check("rail_stations", "unique station codes", "WARN",
          st.station_code.duplicated().sum() == 0,
          f"{int(st.station_code.duplicated().sum())} dup codes")
    cat = pd.read_csv(DATA_DIR / "rail" / "station_categories.csv")
    nsg1 = (cat.category == "NSG1").sum()
    check("station_categories", "NSG1 count sane (15-30)", "WARN", 15 <= nsg1 <= 30, f"{nsg1}")
    join = cat.station_code.isin(st.station_code).mean()
    check("station_categories", ">=90% codes join to stations", "FAIL", join >= 0.9, f"{join:.1%}")
    ft = pd.read_csv(DATA_DIR / "rail" / "freight_terminals.csv")
    have = ft[pd.to_numeric(ft.latitude, errors="coerce").notna()]
    check("freight_terminals", "84 GCT rows", "FAIL", len(ft) == 84, f"{len(ft)}")
    check("freight_terminals", "geocoded coords in bbox", "FAIL",
          bool(have.longitude.astype(float).between(68, 97.5).all()
               and have.latitude.astype(float).between(6, 37.5).all()))


def audit_hubs():
    print("\n== logistics hubs ==")
    expected = {"ports.csv": (12, "major_port"), "icps.csv": (15, "icp"),
                "icds.csv": (30, "icd"), "air_cargo.csv": (15, "air_cargo"),
                "mmlps.csv": (15, "mmlp"), "inland_waterway_terminals.csv": (30, "iw_terminal"),
                "fci_depots.csv": (40, "foodgodown")}
    cols = ["name", "hub_type", "state", "city", "latitude", "longitude",
            "operator", "capacity_notes", "source_url"]
    for f, (min_rows, type_key) in expected.items():
        p = DATA_DIR / "logistics_hubs" / f
        df = pd.read_csv(p)
        check("hubs", f"{f}: schema", "FAIL", list(df.columns) == cols)
        check("hubs", f"{f}: >= {min_rows} rows", "FAIL", len(df) >= min_rows, f"{len(df)}")
        check("hubs", f"{f}: coords in bbox + per-row source", "FAIL",
              bool(df.longitude.between(68, 97.5).all() and df.latitude.between(6, 37.5).all()
                   and df.source_url.notna().all()))


def audit_roads():
    print("\n== roads ==")
    nh = gpd.read_file(DATA_DIR / "roads" / "india_nh_network.geojson")
    check("nh_network", "valid geometries", "FAIL", bool(nh.geometry.is_valid.all()))
    import re as _re
    bad_nh = (~nh.nh.str.fullmatch(r"(\d{1,4}[A-Z]?)(;\d{1,4}[A-Z]?)*", na=False)).sum()
    check("nh_network", "NH numbers clean", "FAIL", bad_nh == 0, f"{int(bad_nh)} odd values")
    n_routes = nh.nh.str.split(";").explode().replace("", None).dropna().nunique()
    check("nh_network", "500-800 distinct routes", "WARN", 500 <= n_routes <= 800, f"{n_routes}")
    pune = DATA_DIR / "roads" / "pune_sample_roads.geojson"
    check("pune_sample", "exists", "FAIL", pune.exists())


def audit_demographics():
    print("\n== demographics ==")
    c = pd.read_csv(DATA_DIR / "demographic" / "census2011_district_key_indicators.csv")
    check("census", "640 districts", "FAIL", len(c) == 640, f"{len(c)}")
    check("census", "population total exact", "FAIL",
          c.Population.sum() == 1_210_854_977, f"{c.Population.sum():,}")
    d11 = gpd.read_file(DATA_DIR / "administrative" / "india_districts.geojson")
    census_pairs = set(zip(c.state.str.lower(), c.district.str.lower().str.strip()))
    b_pairs = set(zip(d11.state.str.lower(), d11.district_key))
    unmatched = census_pairs - b_pairs
    check("census", "district keys join to 2011 boundaries", "WARN",
          len(unmatched) <= 20, f"{len(unmatched)} unmatched, e.g. {sorted(unmatched)[:5]}")

    est_p = DATA_DIR / "demographic" / "district_population_estimates.csv"
    if est_p.exists():
        est = pd.read_csv(est_p)
        check("population_estimates", "781 current districts", "FAIL", len(est) == 781, f"{len(est)}")
        diff = abs(est.pop_2011.sum() - 1_210_854_977)
        check("population_estimates", "total population conserved (<0.01% delta)", "FAIL",
              diff / 1_210_854_977 < 0.0001, f"{est.pop_2011.sum():,} (delta {diff})")
        valid_methods = set(est.method.unique()) <= {"census2011", "blended", "area_share"}
        check("population_estimates", "valid allocation methods", "FAIL", valid_methods,
              str(est.method.value_counts().to_dict()))


def audit_freight():
    print("\n== freight & transport demand ==")
    # Rail freight
    rf_p = DATA_DIR / "freight" / "rail_freight_annual.csv"
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
    pt_p = DATA_DIR / "freight" / "port_throughput_annual.csv"
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
    rd_p = DATA_DIR / "freight" / "road_indicators_annual.csv"
    if rd_p.exists():
        rd = pd.read_csv(rd_p)
        check("road_indicators", "schema", "FAIL", list(rd.columns) == [
            "metric", "fy", "entity_type", "entity_code", "commodity_group", "value", "unit", "source_url", "license"])
        check("road_indicators", ">= 10 series", "FAIL", len(rd) >= 10, f"{len(rd)}")


def audit_catalog():
    print("\n== catalog vs disk ==")
    import yaml
    cat = yaml.safe_load(open(REPO_ROOT / "catalog.yaml", encoding="utf-8"))
    check("catalog", "parses", "FAIL", True)
    n_missing = 0
    for ds in cat["datasets"]:
        p = ds.get("path")
        if p and "(generated" not in p and "data/analysis" not in p:
            import glob as _glob
            matches = _glob.glob(str(REPO_ROOT / p))
            if not matches:
                n_missing += 1
                check("catalog", f"{ds['name']}: path exists", "FAIL", False, p)
    check("catalog", "all committed paths exist", "FAIL", n_missing == 0)


def collect_counts() -> dict:
    """Cheap counts for baseline drift (CSV row counts; geojson counts read
    only in deep mode and cached here)."""
    import glob as g
    counts = {}
    for f in g.glob(str(DATA_DIR / "logistics_hubs" / "*.csv")):
        counts[f"hubs:{pathlib.Path(f).stem}"] = len(pd.read_csv(f))
    for f in g.glob(str(DATA_DIR / "freight" / "*.csv")):
        counts[f"freight:{pathlib.Path(f).stem}"] = len(pd.read_csv(f))
    for key, f in [("rail:stations", "rail/railway_stations.csv"),
                   ("rail:categories", "rail/station_categories.csv"),
                   ("rail:freight", "rail/freight_terminals.csv"),
                   ("census:districts", "demographic/census2011_district_key_indicators.csv"),
                   ("demographic:estimates", "demographic/district_population_estimates.csv")]:
        p = DATA_DIR / f
        if p.exists():
            counts[key] = len(pd.read_csv(p))
    for f in g.glob(str(DATA_DIR / "analysis" / "*_village_access.csv")):
        counts[f"analysis:{pathlib.Path(f).stem}"] = len(pd.read_csv(f, usecols=["unit"]))
    return counts


def check_baseline():
    print("== baseline drift ==")
    import json
    if not BASELINE_PATH.exists():
        check("baseline", "baseline file exists", "FAIL", False,
              "run audit_all.py --update-baseline")
        return
    base = json.loads(BASELINE_PATH.read_text())
    current = collect_counts()
    drift = {k: (base.get(k), v) for k, v in current.items() if base.get(k) != v}
    check("baseline", "counts match baseline", "FAIL", not drift,
          f"{len(drift)} drifted, e.g. {dict(list(drift.items())[:3])}" if drift
          else f"{len(current)} series stable")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fail-on-warn", action="store_true")
    ap.add_argument("--fast", action="store_true",
                    help="skip heavy geometry checks (village re-reads, reproducibility)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="(re)write data/audit_baseline.json from current data")
    args = ap.parse_args()

    if args.update_baseline:
        import json
        deep_counts = {}
        states = gpd.read_file(DATA_DIR / "administrative" / "india_states_lgd.geojson")
        deep_counts["admin:states"] = len(states)
        dist = gpd.read_file(DATA_DIR / "administrative" / "india_districts_lgd.geojson")
        deep_counts["admin:districts"] = len(dist)
        sub = gpd.read_file(DATA_DIR / "administrative" / "india_subdistricts_lgd.gpkg")
        deep_counts["admin:subdistricts"] = len(sub)
        import glob as g
        for f in g.glob(str(DATA_DIR / "administrative" / "villages" / "*_soi_villages.geojson")):
            deep_counts[f"villages:{pathlib.Path(f).stem}"] = len(gpd.read_file(f))
        deep_counts.update(collect_counts())
        BASELINE_PATH.write_text(json.dumps(deep_counts, indent=1, sort_keys=True))
        print(f"baseline written: {len(deep_counts)} series -> {BASELINE_PATH}")
        return

    heavy = () if args.fast else (audit_villages, audit_analysis)
    for fn in (audit_boundaries, audit_rail, audit_hubs, audit_freight, audit_demographics,
               audit_catalog, check_baseline, *heavy):
        try:
            fn()
        except Exception:
            RESULTS.append({"dataset": fn.__name__, "check": "AUDIT CRASHED",
                            "status": "FAIL", "severity": "FAIL",
                            "detail": traceback.format_exc(limit=3)})
            print(f"  [FAIL] {fn.__name__} crashed")

    df = pd.DataFrame(RESULTS)
    n_fail = (df.status == "FAIL").sum()
    n_warn = (df.status == "WARN").sum()
    print(f"\n==== AUDIT: {len(df)} checks | {len(df)-n_fail-n_warn} PASS | {n_warn} WARN | {n_fail} FAIL ====")

    report = REPO_ROOT / "docs" / "audit_report.md"
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("# Data Audit Report\n\n")
        fh.write(f"Checks: {len(df)} — PASS {len(df)-n_fail-n_warn} / WARN {n_warn} / FAIL {n_fail}\n\n")
        fh.write(df.to_markdown(index=False))
    print(f"report -> {report}")

    if n_fail or (args.fail_on_warn and n_warn):
        sys.exit(1)


if __name__ == "__main__":
    main()
