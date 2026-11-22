"""Compile annual freight-flow and demand-side indicator series.

Compiles official annual logistics series (FY 2019-20 through 2023-24/2024-25):
1. data/freight/rail_freight_annual.csv (Indian Railways revenue freight by commodity & zone)
2. data/freight/port_throughput_annual.csv (Major port traffic by port & commodity)
3. data/freight/road_indicators_annual.csv (MoRTH classified road network lengths by state)

The values are hand-transcribed and NOT re-verified against the cited pages
(catalog quality_status: unverified). The script reports, without failing,
whether the parts add up to the national rows:
- rail commodity groups vs the national total, and zone rows vs the national total
- major-port rows vs the all-major-ports total (1% tolerance)

Usage:
    python scripts/fetch/build_freight_tables.py
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clean"))
from standardize import DATA_DIR

FREIGHT_DIR = DATA_DIR / "freight"

LICENSE_GODL = "GODL-India"
PIB_RAIL_URL = "https://pib.gov.in/PressReleasePage.aspx?PRID=2016942"
PIB_PORTS_URL = "https://pib.gov.in/PressReleasePage.aspx?PRID=2017001"
MORTH_BRS_URL = "https://morth.nic.in/basic-road-statistics-india"


def build_rail_freight() -> pd.DataFrame:
    """Indian Railways annual revenue freight loading in Million Tonnes (MT)."""
    rows = []

    # Annual commodity breakdowns (MT) from Railway Board Year Books & PIB
    commodities_by_fy = {
        "2023-24": {
            "all": 1591.0, "coal": 781.2, "iron_ore": 180.5, "cement_and_clinker": 153.2,
            "foodgrains": 82.4, "fertilizers": 60.1, "pol": 50.8, "containers": 85.3, "other": 197.5
        },
        "2022-23": {
            "all": 1512.1, "coal": 732.1, "iron_ore": 168.4, "cement_and_clinker": 142.8,
            "foodgrains": 85.1, "fertilizers": 56.4, "pol": 48.9, "containers": 79.5, "other": 198.9
        },
        "2021-22": {
            "all": 1418.1, "coal": 653.2, "iron_ore": 168.1, "cement_and_clinker": 137.4,
            "foodgrains": 95.3, "fertilizers": 50.7, "pol": 43.8, "containers": 74.3, "other": 195.3
        },
        "2020-21": {
            "all": 1233.2, "coal": 542.4, "iron_ore": 152.8, "cement_and_clinker": 121.2,
            "foodgrains": 96.6, "fertilizers": 51.5, "pol": 41.2, "containers": 61.1, "other": 166.4
        },
        "2019-20": {
            "all": 1208.4, "coal": 587.3, "iron_ore": 153.4, "cement_and_clinker": 110.2,
            "foodgrains": 65.4, "fertilizers": 51.2, "pol": 44.5, "containers": 61.0, "other": 135.4
        },
    }

    for fy, comms in commodities_by_fy.items():
        for comm, val in comms.items():
            rows.append({
                "metric": "revenue_freight_loading",
                "fy": fy,
                "entity_type": "national",
                "entity_code": "ALL_INDIA",
                "commodity_group": comm,
                "value": val,
                "unit": "MT",
                "source_url": PIB_RAIL_URL,
                "license": LICENSE_GODL,
            })

    # Zone-wise totals for FY 2023-24 & 2022-23 (key freight originating zones)
    zone_shares_23_24 = {
        "ECoR": 256.2, "SECR": 235.4, "SER": 202.1, "ECR": 189.5, "WCR": 156.4,
        "WR": 108.2, "SCR": 135.8, "NR": 78.4, "NCR": 38.6, "CR": 82.5,
        "SR": 42.1, "SWR": 48.3, "NWR": 29.5, "NFR": 14.8, "NER": 3.6, "ER": 75.6
    }
    for z, val in zone_shares_23_24.items():
        rows.append({
            "metric": "revenue_freight_loading",
            "fy": "2023-24",
            "entity_type": "zone",
            "entity_code": z,
            "commodity_group": "all",
            "value": val,
            "unit": "MT",
            "source_url": PIB_RAIL_URL,
            "license": LICENSE_GODL,
        })

    df = pd.DataFrame(rows)
    out_path = FREIGHT_DIR / "rail_freight_annual.csv"
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rail freight series -> {out_path}")
    return df


def build_port_throughput() -> pd.DataFrame:
    """Major Ports cargo throughput in Million Tonnes (MT)."""
    rows = []

    # Port-wise totals across fiscal years (MT)
    # Source: Indian Ports Association (IPA) & Ministry of Ports, Shipping and Waterways
    port_traffic = [
        # Port, UN/LOCODE, 2023-24, 2022-23, 2021-22, 2020-21, 2019-20
        ("Paradip", "INPRT", 145.38, 135.36, 116.13, 114.55, 112.69),
        ("Deendayal", "INIXY", 132.50, 137.56, 127.10, 117.56, 122.61),
        ("JNPA", "INNSA", 86.00, 83.00, 76.00, 64.81, 68.45),
        ("Visakhapatnam", "INVTZ", 81.00, 73.75, 69.03, 69.84, 72.72),
        ("SMP Kolkata / Haldia", "INCCU", 66.00, 65.66, 58.18, 61.37, 63.98),
        ("Mumbai", "INBOM", 65.00, 63.61, 59.89, 53.32, 60.70),
        ("Chennai", "INMAA", 54.50, 48.95, 48.56, 43.55, 46.76),
        ("Kamarajar (Ennore)", "INENR", 48.00, 43.51, 38.74, 25.82, 31.75),
        ("New Mangalore", "INNML", 46.00, 41.42, 39.30, 36.50, 39.15),
        ("V.O. Chidambaranar", "INTUT", 41.50, 38.04, 34.12, 31.79, 36.08),
        ("Cochin", "INCOK", 36.50, 35.25, 34.55, 31.50, 34.04),
        ("Mormugao", "INMRM", 20.50, 17.33, 18.46, 21.99, 16.02),
    ]

    for port_name, locode, y24, y23, y22, y21, y20 in port_traffic:
        for fy, val in [("2023-24", y24), ("2022-23", y23), ("2021-22", y22), ("2020-21", y21), ("2019-20", y20)]:
            rows.append({
                "metric": "cargo_throughput",
                "fy": fy,
                "entity_type": "port",
                "entity_code": locode,
                "commodity_group": "all",
                "value": val,
                "unit": "MT",
                "source_url": PIB_PORTS_URL,
                "license": LICENSE_GODL,
            })

    # National totals
    for fy, tot in [("2023-24", 819.0), ("2022-23", 795.0), ("2021-22", 720.0), ("2020-21", 673.0), ("2019-20", 705.0)]:
        rows.append({
            "metric": "cargo_throughput",
            "fy": fy,
            "entity_type": "national",
            "entity_code": "ALL_MAJOR_PORTS",
            "commodity_group": "all",
            "value": tot,
            "unit": "MT",
            "source_url": PIB_PORTS_URL,
            "license": LICENSE_GODL,
        })

    df = pd.DataFrame(rows)
    out_path = FREIGHT_DIR / "port_throughput_annual.csv"
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} port throughput series -> {out_path}")
    return df


def build_road_indicators() -> pd.DataFrame:
    """MoRTH classified road network lengths in km across categories."""
    rows = []

    # National Totals by road category (MoRTH Basic Road Statistics / Annual Reports)
    national_series = [
        ("national_highways", "2023-24", 146145),
        ("national_highways", "2022-23", 144955),
        ("national_highways", "2021-22", 140995),
        ("national_highways", "2020-21", 136440),
        ("national_highways", "2019-20", 132500),
        ("state_highways", "2021-22", 179535),
        ("state_highways", "2019-20", 176818),
        ("major_district_roads", "2021-22", 612778),
        ("rural_roads", "2021-22", 4535511),
        ("total_road_network", "2021-22", 6331791),
        ("total_road_network", "2019-20", 6215797),
    ]

    for cat, fy, val in national_series:
        rows.append({
            "metric": "road_length",
            "fy": fy,
            "entity_type": "national",
            "entity_code": "ALL_INDIA",
            "commodity_group": cat,
            "value": float(val),
            "unit": "km",
            "source_url": MORTH_BRS_URL,
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
    print("=== Compiling Freight Flow Tables (Initiative 4) ===")
    df_rail = build_rail_freight()
    df_port = build_port_throughput()
    build_road_indicators()

    issues = consistency_issues(df_rail, df_port)
    for issue in issues:
        print(f"  [INCONSISTENT] {issue}")
    print("\nConsistency: " + ("parts add up to the national rows" if not issues else
                               f"{len(issues)} inconsistencies (data is unverified; see catalog.yaml)"))


if __name__ == "__main__":
    main()
