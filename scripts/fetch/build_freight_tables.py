"""Compile annual freight-flow and demand-side indicator series.

Compiles verified official annual logistics series (FY 2019-20 through 2024-25):
1. data/freight/rail_freight_annual.csv — Indian Railways revenue-earning originating
   freight loading by commodity (excluding Konkan Railway Corporation Ltd.), directly
   from the Economic Survey Statistical Appendix Table 1.27A (Ministry of Railways):
   - FY 2020-21 to 2024-25: Economic Survey 2025-26 Table 1.27A (tab1.27a.pdf)
   - FY 2019-20: Economic Survey 2023-24 Table 1.27A (tab127a.pdf)
2. data/freight/port_throughput_annual.csv — Annual cargo traffic across all 12 Major
   Ports (FY 2019-20 to 2023-24), directly from Ministry of Ports, Shipping & Waterways
   (Basic Port Statistics of India / Transport Research Wing & IPA).
3. data/freight/road_indicators_annual.csv — National/state road lengths & goods
   vehicles from Economic Survey 2025-26 Table 1.28 (TRW, MoRTH) and MoRTH Annual Reports.

Validation gate: every fiscal year's commodity / port rows must reconcile to within 1%
of their national total row; otherwise the script refuses to exit cleanly.

Usage:
    python scripts/fetch/build_freight_tables.py
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR  # noqa: E402

FREIGHT_DIR = DATA_DIR / "freight"

LICENSE_GODL = "GODL-India"
ES_2025_26_RAIL_URL = "https://www.indiabudget.gov.in/economicsurvey/doc/stat/tab1.27a.pdf"
ES_2023_24_RAIL_URL = "https://www.indiabudget.gov.in/economicsurvey/doc/stat/tab127a.pdf"
MOPSW_PORTS_URL = "https://shipmin.gov.in/division/transport-research"
ES_2025_26_ROAD_URL = "https://www.indiabudget.gov.in/economicsurvey/doc/stat/tab1.28.pdf"
MORTH_BRS_URL = "https://morth.nic.in/basic-road-statistics-india"


def build_rail_freight() -> pd.DataFrame:
    """Indian Railways annual revenue-earning originating freight loading (MT, excl. KRCL).

    Source: Ministry of Railways via Economic Survey Statistical Appendix Table 1.27A
    (2025-26 edition for FY 2020-21 to 2024-25; 2023-24 edition for FY 2019-20).
    """
    rows = []

    commodities_by_fy = {
        "2024-25": (ES_2025_26_RAIL_URL, {
            "all": 1614.9, "coal": 821.9, "raw_materials_steel_excl_iron_ore": 29.1,
            "pig_iron_and_finished_steel": 72.3, "iron_ore": 178.1, "cement": 149.5,
            "foodgrains": 49.8, "fertilizers": 59.7, "pol": 50.3, "containers": 88.4,
            "other": 115.8,
        }),
        "2023-24": (ES_2025_26_RAIL_URL, {
            "all": 1588.06, "coal": 786.8, "raw_materials_steel_excl_iron_ore": 38.8,
            "pig_iron_and_finished_steel": 69.9, "iron_ore": 180.9, "cement": 154.3,
            "foodgrains": 51.5, "fertilizers": 59.0, "pol": 49.9, "containers": 85.0,
            "other": 112.7,
        }),
        "2022-23": (ES_2025_26_RAIL_URL, {
            "all": 1509.1, "coal": 727.2, "raw_materials_steel_excl_iron_ore": 28.0,
            "pig_iron_and_finished_steel": 69.9, "iron_ore": 160.1, "cement": 143.9,
            "foodgrains": 70.9, "fertilizers": 56.3, "pol": 48.2, "containers": 79.2,
            "other": 125.1,
        }),
        "2021-22": (ES_2025_26_RAIL_URL, {
            "all": 1415.9, "coal": 652.8, "raw_materials_steel_excl_iron_ore": 29.0,
            "pig_iron_and_finished_steel": 68.5, "iron_ore": 168.4, "cement": 137.2,
            "foodgrains": 73.4, "fertilizers": 49.2, "pol": 44.5, "containers": 74.3,
            "other": 118.7,
        }),
        "2020-21": (ES_2025_26_RAIL_URL, {
            "all": 1230.9, "coal": 541.8, "raw_materials_steel_excl_iron_ore": 24.9,
            "pig_iron_and_finished_steel": 60.1, "iron_ore": 159.1, "cement": 120.4,
            "foodgrains": 62.8, "fertilizers": 53.8, "pol": 42.5, "containers": 63.2,
            "other": 102.4,
        }),
        "2019-20": (ES_2023_24_RAIL_URL, {
            "all": 1208.4, "coal": 586.9, "raw_materials_steel_excl_iron_ore": 25.6,
            "pig_iron_and_finished_steel": 53.1, "iron_ore": 153.4, "cement": 110.1,
            "foodgrains": 37.5, "fertilizers": 51.4, "pol": 44.7, "containers": 61.0,
            "other": 84.7,
        }),
    }

    for fy, (source_url, comms) in commodities_by_fy.items():
        for comm, val in comms.items():
            rows.append({
                "metric": "revenue_freight_loading",
                "fy": fy,
                "entity_type": "national",
                "entity_code": "ALL_INDIA",
                "commodity_group": comm,
                "value": val,
                "unit": "MT",
                "source_url": source_url,
                "license": LICENSE_GODL,
            })

    df = pd.DataFrame(rows)
    out_path = FREIGHT_DIR / "rail_freight_annual.csv"
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rail freight series -> {out_path}")
    return df


def build_port_throughput() -> pd.DataFrame:
    """Major Ports annual cargo throughput in Million Tonnes (MT).

    Source: Ministry of Ports, Shipping & Waterways (Basic Port Statistics of India /
    Transport Research Wing) and Indian Ports Association (IPA).
    """
    rows = []

    # Port, UN/LOCODE, 2023-24, 2022-23, 2021-22, 2020-21, 2019-20
    port_traffic = [
        ("Paradip", "INPRT", 145.38, 135.36, 116.13, 114.55, 112.69),
        ("Deendayal", "INIXY", 132.37, 137.56, 127.10, 117.56, 122.61),
        ("JNPA", "INNSA", 85.82, 83.86, 76.00, 64.81, 68.45),
        ("Visakhapatnam", "INVTZ", 81.09, 73.75, 69.03, 69.84, 72.72),
        ("Mumbai", "INBOM", 67.26, 63.61, 59.89, 53.32, 60.70),
        ("SMP Kolkata / Haldia", "INCCU", 66.39, 65.66, 58.18, 61.37, 63.98),
        ("Chennai", "INMAA", 51.60, 48.95, 48.56, 43.55, 46.76),
        ("New Mangalore", "INNML", 45.71, 41.42, 39.30, 36.50, 39.15),
        ("Kamarajar (Ennore)", "INENR", 45.28, 43.51, 38.74, 25.82, 31.75),
        ("V.O. Chidambaranar", "INTUT", 41.40, 38.04, 34.12, 31.79, 36.08),
        ("Cochin", "INCOK", 36.32, 35.25, 34.55, 31.50, 34.04),
        ("Mormugao", "INMRM", 20.63, 17.33, 18.46, 21.99, 16.02),
    ]

    for _port_name, locode, y24, y23, y22, y21, y20 in port_traffic:
        for fy, val in [("2023-24", y24), ("2022-23", y23), ("2021-22", y22), ("2020-21", y21), ("2019-20", y20)]:
            rows.append({
                "metric": "cargo_throughput",
                "fy": fy,
                "entity_type": "port",
                "entity_code": locode,
                "commodity_group": "all",
                "value": val,
                "unit": "MT",
                "source_url": MOPSW_PORTS_URL,
                "license": LICENSE_GODL,
            })

    # Official 12-Major-Port totals (MoPSW Basic Port Statistics / IPA)
    national_totals = [
        ("2023-24", 819.23),
        ("2022-23", 784.31),
        ("2021-22", 720.06),
        ("2020-21", 672.60),
        ("2019-20", 704.93),
    ]
    for fy, tot in national_totals:
        rows.append({
            "metric": "cargo_throughput",
            "fy": fy,
            "entity_type": "national",
            "entity_code": "ALL_MAJOR_PORTS",
            "commodity_group": "all",
            "value": tot,
            "unit": "MT",
            "source_url": MOPSW_PORTS_URL,
            "license": LICENSE_GODL,
        })

    df = pd.DataFrame(rows)
    out_path = FREIGHT_DIR / "port_throughput_annual.csv"
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} port throughput series -> {out_path}")
    return df


def build_road_indicators() -> pd.DataFrame:
    """MoRTH classified road network lengths (km) from Economic Survey Table 1.28 & MoRTH."""
    rows = []

    national_series = [
        ("national_highways", "2023-24", 146145, MORTH_BRS_URL),
        ("national_highways", "2022-23", 144955, MORTH_BRS_URL),
        ("national_highways", "2021-22", 141300, ES_2025_26_ROAD_URL),
        ("national_highways", "2020-21", 138400, ES_2025_26_ROAD_URL),
        ("national_highways", "2019-20", 132900, ES_2025_26_ROAD_URL),
        ("state_highways", "2021-22", 193700, ES_2025_26_ROAD_URL),
        ("state_highways", "2020-21", 191600, ES_2025_26_ROAD_URL),
        ("state_highways", "2019-20", 178700, ES_2025_26_ROAD_URL),
        ("total_road_network", "2021-22", 6592800, ES_2025_26_ROAD_URL),
        ("total_road_network", "2020-21", 6498300, ES_2025_26_ROAD_URL),
        ("total_road_network", "2019-20", 6360000, ES_2025_26_ROAD_URL),
    ]

    for cat, fy, val, src_url in national_series:
        rows.append({
            "metric": "road_length",
            "fy": fy,
            "entity_type": "national",
            "entity_code": "ALL_INDIA",
            "commodity_group": cat,
            "value": float(val),
            "unit": "km",
            "source_url": src_url,
            "license": LICENSE_GODL,
        })

    df = pd.DataFrame(rows)
    out_path = FREIGHT_DIR / "road_indicators_annual.csv"
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} road indicator series -> {out_path}")
    return df


def consistency_issues(rail: pd.DataFrame, port: pd.DataFrame, tol: float = 0.01) -> list[str]:
    """Parts that do not add up to their national row (relative tolerance ``tol``)."""
    issues = []

    def compare(label, parts, total):
        if total and abs(parts - total) > tol * total:
            issues.append(f"{label}: parts {parts:,.1f} vs total {total:,.1f}")

    nat = rail[rail.entity_type == "national"]
    for fy, g in nat.groupby("fy"):
        total = g.loc[g.commodity_group == "all", "value"].sum()
        compare(f"rail {fy} commodities", g.loc[g.commodity_group != "all", "value"].sum(), total)
        zones = rail[(rail.entity_type == "zone") & (rail.fy == fy)]
        if len(zones):
            compare(f"rail {fy} zones", zones.value.sum(), total)
    for fy, g in port.groupby("fy"):
        compare(f"ports {fy}", g.loc[g.entity_type == "port", "value"].sum(),
                g.loc[g.entity_type == "national", "value"].sum())
    return issues


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    FREIGHT_DIR.mkdir(parents=True, exist_ok=True)
    print("=== Compiling Verified Freight Flow Tables ===")
    df_rail = build_rail_freight()
    df_port = build_port_throughput()
    build_road_indicators()

    issues = consistency_issues(df_rail, df_port)
    if issues:
        for issue in issues:
            print(f"  [INCONSISTENT] {issue}")
        raise SystemExit(f"{len(issues)} freight reconciliation failure(s)")
    print("\nConsistency: all commodity and port parts reconcile to national rows (<1% tolerance).")


if __name__ == "__main__":
    main()
