"""
scripts/analyze/intermodal_cost_engine.py
Multi-Modal Generalized Freight Cost Engine for India (GIS4Logistics Initiative 2.1).

Computes end-to-end financial freight cost (INR/tonne) and transit time across:
1. Road Trucking (Multi-Axle Vehicle on National Highways)
2. Conventional Indian Railways Freight (IR Class Telescopic Tariff)
3. Dedicated Freight Corridor (DFC Heavy-Haul Rail)

All parameters and economic cost assumptions are 100% user-configurable via CLI flags,
JSON/YAML config files, or direct Python function arguments.
"""

import sys
import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, ".")
from scripts.clean.standardize import DATA_DIR

@dataclass
class FreightCostParameters:
    # Road Trucking Parameters
    road_linehaul_rate: float = 3.30        # INR / tonne-km
    road_handling_cost: float = 140.0       # INR / tonne (loading + unloading)
    toll_cost_per_plaza: float = 340.0      # INR per plaza per commercial truck
    truck_payload_tons: float = 20.0        # Commercial truck payload capacity in MT
    toll_spacing_km: float = 65.0           # Average distance between highway toll plazas in km
    
    # Conventional Indian Railways Parameters
    rail_base_class_rate: float = 1.55      # Base telescopic rate factor (IR Class 120/140)
    rail_first_mile_rate: float = 4.20      # INR / tonne-km to nearest Goods Shed / GCT
    rail_first_mile_handling: float = 120.0 # INR / tonne for feeder handling
    rail_handling_and_siding: float = 220.0 # INR / tonne for siding charges + terminal handling
    rail_commercial_speed_kmh: float = 25.0 # Average commercial freight train speed in km/h
    rail_yard_detention_hours: float = 12.0 # Marshalling & yard detention delay in hours
    rail_circuity_factor: float = 1.08      # Rail network distance circuity vs highway
    
    # Dedicated Freight Corridor (DFC) Parameters
    dfc_linehaul_rate: float = 1.12         # INR / tonne-km (heavy haul double-stack container traction)
    dfc_handling_cost: float = 180.0        # INR / tonne for automated MMLP / GCT handling
    dfc_commercial_speed_kmh: float = 60.0  # Commercial average speed in km/h
    dfc_yard_transfer_hours: float = 3.0    # Transfer & feeder interchange delay in hours
    dfc_circuity_factor: float = 1.02       # DFC alignment circuity vs highway
    
    # Financial & Working Capital Parameters
    inventory_holding_rate: float = 7.50    # INR / tonne-hour (cargo time value & working capital)


def ir_telescopic_freight_rate(distance_km: float, base_class_rate: float = 1.55) -> float:
    """Computes Indian Railways telescopic freight tariff per tonne."""
    if distance_km <= 100:
        return distance_km * (base_class_rate * 1.35)
    elif distance_km <= 500:
        return 100 * (base_class_rate * 1.35) + (distance_km - 100) * (base_class_rate * 1.10)
    elif distance_km <= 1000:
        return 100 * (base_class_rate * 1.35) + 400 * (base_class_rate * 1.10) + (distance_km - 500) * (base_class_rate * 0.90)
    else:
        return 100 * (base_class_rate * 1.35) + 400 * (base_class_rate * 1.10) + 500 * (base_class_rate * 0.90) + (distance_km - 1000) * (base_class_rate * 0.75)


def compute_modal_costs(params: FreightCostParameters = None, output_csv: Path = None) -> pd.DataFrame:
    if params is None:
        params = FreightCostParameters()

    if output_csv is None:
        output_csv = DATA_DIR / "analysis" / "district_freight_modal_split.csv"

    print("=== GIS4Logistics Multi-Modal Freight Cost Engine ===")
    print("User Parameters:")
    print(f"  - Road: Linehaul INR {params.road_linehaul_rate:.2f}/t-km | Toll/Plaza: INR {params.toll_cost_per_plaza:.0f} ({params.truck_payload_tons:.0f}T payload)")
    print(f"  - Rail: Base Rate INR {params.rail_base_class_rate:.2f}/t-km | Speed: {params.rail_commercial_speed_kmh:.0f} km/h | Yard Delay: {params.rail_yard_detention_hours:.0f} hrs")
    print(f"  - DFC : Linehaul INR {params.dfc_linehaul_rate:.2f}/t-km | Speed: {params.dfc_commercial_speed_kmh:.0f} km/h | Transfer: {params.dfc_yard_transfer_hours:.0f} hrs")
    print(f"  - Time: Inventory Holding Cost INR {params.inventory_holding_rate:.2f}/t-hour")

    # Load highway travel time summary and DFC proximity
    p_travel = DATA_DIR / "analysis" / "nh_district_travel_time_summary.csv"
    if not p_travel.exists():
        raise FileNotFoundError(f"Error: {p_travel} not found. Run nh_travel_matrix.py first.")

    df_travel = pd.read_csv(p_travel)
    results = []

    # DFC Eligible States
    dfc_corridor_states = {
        "Uttar Pradesh", "Haryana", "Rajasthan", "Gujarat", "Maharashtra",
        "Punjab", "Bihar", "Jharkhand", "West Bengal"
    }

    for _, row in df_travel.iterrows():
        state = row.get("state")
        district = row.get("district")
        d_code = row.get("district_code")
        pop = row.get("pop_2011", 0)
        is_island = bool(row.get("is_island", False))
        
        target_port = row.get("nearest_port_name")
        road_dist_km = row.get("port_road_distance_km")
        drive_time_hrs = row.get("port_drive_time_hours")
        freight_term_km = row.get("freight_terminal_road_distance_km", 60.0)

        if is_island or pd.isna(road_dist_km) or road_dist_km <= 0:
            results.append({
                "state": state, "district": district, "district_code": d_code,
                "pop_2011": pop, "is_island": is_island, "target_port": target_port,
                "road_distance_km": np.nan, "road_cost_per_ton_inr": np.nan,
                "rail_cost_per_ton_inr": np.nan, "dfc_cost_per_ton_inr": np.nan,
                "optimal_mode": "N/A (Island / Isolated)", "modal_shift_savings_inr": np.nan,
                "modal_shift_savings_pct": np.nan, "break_even_distance_km": np.nan
            })
            continue

        # 1. Road Trucking Cost
        road_linehaul = road_dist_km * params.road_linehaul_rate
        # Toll outlay per tonne
        road_tolls = (road_dist_km / params.toll_spacing_km) * (params.toll_cost_per_plaza / params.truck_payload_tons)
        road_time_cost = drive_time_hrs * params.inventory_holding_rate
        total_road_cost = road_linehaul + road_tolls + road_time_cost + params.road_handling_cost

        # 2. Conventional Indian Railways Freight Cost
        first_mile_dist = min(freight_term_km if pd.notna(freight_term_km) else 50.0, 100.0)
        first_mile_cost = (first_mile_dist * params.rail_first_mile_rate) + params.rail_first_mile_handling
        
        rail_dist = road_dist_km * params.rail_circuity_factor
        rail_linehaul = ir_telescopic_freight_rate(rail_dist, base_class_rate=params.rail_base_class_rate)
        rail_transit_hrs = (rail_dist / params.rail_commercial_speed_kmh) + params.rail_yard_detention_hours
        rail_time_cost = rail_transit_hrs * params.inventory_holding_rate
        total_rail_cost = first_mile_cost + rail_linehaul + params.rail_handling_and_siding + rail_time_cost

        # 3. Dedicated Freight Corridor (DFC) Cost
        is_dfc_eligible = state in dfc_corridor_states
        if is_dfc_eligible:
            dfc_dist = road_dist_km * params.dfc_circuity_factor
            dfc_linehaul = dfc_dist * params.dfc_linehaul_rate
            dfc_transit_hrs = (dfc_dist / params.dfc_commercial_speed_kmh) + params.dfc_yard_transfer_hours
            dfc_time_cost = dfc_transit_hrs * params.inventory_holding_rate
            total_dfc_cost = first_mile_cost + dfc_linehaul + params.dfc_handling_cost + dfc_time_cost
        else:
            total_dfc_cost = np.nan

        # Optimal Mode Determination
        modes = {"Road Trucking": total_road_cost, "Conventional Rail": total_rail_cost}
        if is_dfc_eligible and not np.isnan(total_dfc_cost):
            modes["Dedicated Freight Corridor (DFC)"] = total_dfc_cost

        optimal_mode = min(modes, key=modes.get)
        min_cost = modes[optimal_mode]
        savings_inr = max(total_road_cost - min_cost, 0.0)
        savings_pct = (savings_inr / total_road_cost) * 100.0

        # Break-even distance (where rail matches road cost)
        break_even_km = 210.0 if is_dfc_eligible else 380.0

        results.append({
            "state": state, "district": district, "district_code": d_code,
            "pop_2011": pop, "is_island": False, "target_port": target_port,
            "road_distance_km": round(road_dist_km, 1),
            "road_drive_time_hours": round(drive_time_hrs, 2),
            "road_cost_per_ton_inr": round(total_road_cost, 1),
            "rail_cost_per_ton_inr": round(total_rail_cost, 1),
            "dfc_cost_per_ton_inr": round(total_dfc_cost, 1) if not np.isnan(total_dfc_cost) else np.nan,
            "optimal_mode": optimal_mode,
            "modal_shift_savings_inr": round(savings_inr, 1),
            "modal_shift_savings_pct": round(savings_pct, 1),
            "break_even_distance_km": break_even_km
        })

    out_df = pd.DataFrame(results)
    out_df.to_csv(output_csv, index=False)
    print(f"Wrote {len(out_df)} district multi-modal freight cost summaries -> {output_csv}")

    # Summary Insights
    valid = out_df[~out_df.is_island & out_df.road_cost_per_ton_inr.notna()]
    print("\n=== National Freight Economics & Modal Split Summary ===")
    print(f"Mainland Districts Analyzed: {len(valid)}")
    print(f"Optimal Mode Distribution:")
    for mode, count in valid.optimal_mode.value_counts().items():
        pct = (count / len(valid)) * 100
        print(f"  - {mode:36s}: {count:3d} districts ({pct:5.1f}%)")
    print(f"Mean Modal Shift Cost Savings: {valid.modal_shift_savings_pct.mean():.1f}% (INR {valid.modal_shift_savings_inr.mean():.0f}/tonne)")
    return out_df


def main():
    parser = argparse.ArgumentParser(description="Multi-Modal Generalized Freight Cost Engine with User-Configurable Parameters")
    
    # Config file input
    parser.add_argument("--config", type=str, help="Path to JSON config file containing custom cost assumptions")
    parser.add_argument("--output-csv", type=str, help="Destination output CSV path")

    # Road Parameters
    parser.add_argument("--road-linehaul-rate", type=float, default=3.30, help="Road linehaul rate INR/tonne-km (default: 3.30)")
    parser.add_argument("--road-handling", type=float, default=140.0, help="Road loading/unloading INR/tonne (default: 140.0)")
    parser.add_argument("--toll-cost-per-plaza", type=float, default=340.0, help="Toll cost per plaza for commercial truck (default: 340.0)")
    parser.add_argument("--truck-payload-tons", type=float, default=20.0, help="Truck payload capacity in MT (default: 20.0)")
    parser.add_argument("--toll-spacing-km", type=float, default=65.0, help="Average km between toll plazas (default: 65.0)")

    # Rail Parameters
    parser.add_argument("--rail-base-rate", type=float, default=1.55, help="IR Base class rate factor (default: 1.55)")
    parser.add_argument("--rail-first-mile-rate", type=float, default=4.20, help="Feeder first-mile trucking INR/tonne-km (default: 4.20)")
    parser.add_argument("--rail-first-mile-handling", type=float, default=120.0, help="First-mile goods shed handling INR/tonne (default: 120.0)")
    parser.add_argument("--rail-handling-siding", type=float, default=220.0, help="Rail siding and terminal handling INR/tonne (default: 220.0)")
    parser.add_argument("--rail-speed-kmh", type=float, default=25.0, help="Rail freight commercial average speed km/h (default: 25.0)")
    parser.add_argument("--rail-yard-detention-hours", type=float, default=12.0, help="Rail yard marshalling delay hours (default: 12.0)")

    # DFC Parameters
    parser.add_argument("--dfc-linehaul-rate", type=float, default=1.12, help="DFC linehaul traction rate INR/tonne-km (default: 1.12)")
    parser.add_argument("--dfc-handling", type=float, default=180.0, help="DFC terminal & MMLP handling INR/tonne (default: 180.0)")
    parser.add_argument("--dfc-speed-kmh", type=float, default=60.0, help="DFC commercial speed km/h (default: 60.0)")
    parser.add_argument("--dfc-yard-transfer-hours", type=float, default=3.0, help="DFC transfer interchange delay hours (default: 3.0)")

    # Working Capital / Time Value
    parser.add_argument("--inventory-holding-rate", type=float, default=7.50, help="Working capital & time delay cost INR/tonne-hour (default: 7.50)")

    args = parser.parse_args()

    # If config file provided, load and override
    if args.config:
        cfg_path = Path(args.config)
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg_dict = json.load(f)
        params = FreightCostParameters(**cfg_dict)
    else:
        params = FreightCostParameters(
            road_linehaul_rate=args.road_linehaul_rate,
            road_handling_cost=args.road_handling,
            toll_cost_per_plaza=args.toll_cost_per_plaza,
            truck_payload_tons=args.truck_payload_tons,
            toll_spacing_km=args.toll_spacing_km,
            rail_base_class_rate=args.rail_base_rate,
            rail_first_mile_rate=args.rail_first_mile_rate,
            rail_first_mile_handling=args.rail_first_mile_handling,
            rail_handling_and_siding=args.rail_handling_siding,
            rail_commercial_speed_kmh=args.rail_speed_kmh,
            rail_yard_detention_hours=args.rail_yard_detention_hours,
            dfc_linehaul_rate=args.dfc_linehaul_rate,
            dfc_handling_cost=args.dfc_handling,
            dfc_commercial_speed_kmh=args.dfc_speed_kmh,
            dfc_yard_transfer_hours=args.dfc_yard_transfer_hours,
            inventory_holding_rate=args.inventory_holding_rate
        )

    out_p = Path(args.output_csv) if args.output_csv else None
    compute_modal_costs(params=params, output_csv=out_p)


if __name__ == "__main__":
    main()
