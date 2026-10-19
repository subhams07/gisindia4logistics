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
import math
import os
import tempfile
from numbers import Real
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, ".")
from scripts.clean.standardize import DATA_DIR

from scripts.analyze import freight_cost_core as freight_core
from scripts.analyze.freight_cost_core import (
    FreightCostParameters as FreightCostParameters,
    ir_telescopic_freight_rate as ir_telescopic_freight_rate,
)


def compute_modal_costs(params: FreightCostParameters = None, output_csv: Path = None, *, data_dir: Path = None) -> pd.DataFrame:
    """Validate the whole batch before publication; preserve displayed source identity.

    State normalization is calculation-only. Null route observations mean
    unreachable, not an island; absent table columns and corrupt values fail.
    """
    params = freight_core.resolve_parameters(params)
    root = DATA_DIR if data_dir is None else Path(data_dir)
    output_csv = Path(output_csv) if output_csv is not None else root / "analysis" / "district_freight_modal_split.csv"

    print("=== GIS4Logistics Multi-Modal Freight Cost Engine ===")
    print("User Parameters:")
    print(f"  - Road: Linehaul INR {params.road_linehaul_rate:.2f}/t-km | Toll/Plaza: INR {params.toll_cost_per_plaza:.0f} ({params.truck_payload_tons:.0f}T payload)")
    print(f"  - Rail: Base Rate INR {params.rail_base_class_rate:.2f}/t-km | Speed: {params.rail_commercial_speed_kmh:.0f} km/h | Yard Delay: {params.rail_yard_detention_hours:.0f} hrs")
    print(f"  - DFC : Linehaul INR {params.dfc_linehaul_rate:.2f}/t-km | Speed: {params.dfc_commercial_speed_kmh:.0f} km/h | Transfer: {params.dfc_yard_transfer_hours:.0f} hrs")
    print(f"  - Time: Inventory Holding Cost INR {params.inventory_holding_rate:.2f}/t-hour")

    p_travel = root / "analysis" / "nh_district_travel_time_summary.csv"
    try:
        df_travel = pd.read_csv(p_travel)
    except (OSError, ValueError) as exc:
        raise freight_core.SourceValidationError("Freight route source is unavailable or invalid") from exc
    required = {"state", "district", "nearest_port_name", "port_road_distance_km", "port_drive_time_hours"}
    if not required.issubset(df_travel.columns):
        raise freight_core.SourceValidationError("Missing required freight route columns")

    def observation(value, name):
        # Validate even skipped rows: isolation must not hide corrupt numbers.
        if value is None or pd.isna(value):
            return None
        if isinstance(value, bool) or not isinstance(value, Real):
            raise freight_core.SourceValidationError(f"Invalid stored {name}")
        try:
            number = float(value)
        except (OverflowError, ValueError, TypeError):
            raise freight_core.SourceValidationError(f"Invalid stored {name}") from None
        if not math.isfinite(number) or number < 0:
            raise freight_core.SourceValidationError(f"Invalid stored {name}")
        return number

    columns = ["state", "district", "district_code", "pop_2011", "is_island", "target_port",
               "road_distance_km", "road_drive_time_hours", "road_cost_per_ton_inr",
               "rail_cost_per_ton_inr", "dfc_cost_per_ton_inr", "optimal_mode",
               "modal_shift_savings_inr", "modal_shift_savings_pct", "break_even_distance_km"]
    results = []
    for _, row in df_travel.iterrows():
        state = row.get("state")
        canonical = freight_core.normalize_freight_state(state)
        district = row.get("district")
        if not isinstance(district, str) or not district.strip():
            raise freight_core.SourceValidationError("Invalid stored district name")
        flag = row.get("is_island", False)
        is_island = (pd.notna(flag) and bool(flag)) or canonical in {"Andaman And Nicobar Islands", "Lakshadweep"}
        target_port = row.get("nearest_port_name")
        road_km = observation(row["port_road_distance_km"], "road distance")
        drive_hours = observation(row["port_drive_time_hours"], "drive time")
        terminal_km = observation(row.get("freight_terminal_road_distance_km"), "freight terminal distance")
        if (road_km is None) != (drive_hours is None):
            raise freight_core.SourceValidationError("Inconsistent stored route observations")
        result = {"state": state, "district": district, "district_code": row.get("district_code"),
                  "pop_2011": row.get("pop_2011", 0), "is_island": bool(is_island), "target_port": target_port}
        if is_island or road_km is None or drive_hours is None:
            result.update(dict.fromkeys(columns[6:], np.nan))
            result["optimal_mode"] = "N/A (Island / Isolated)"
        else:
            if not isinstance(target_port, str) or not target_port.strip():
                raise freight_core.SourceValidationError("Invalid stored nearest port name")
            unit = freight_core.calculate_unit_costs(road_km, drive_hours, terminal_km, canonical, params)
            result.update({
                "road_distance_km": round(unit.road.distance_km, 1),
                "road_drive_time_hours": round(unit.road.transit_time_hours, 2),
                "road_cost_per_ton_inr": round(unit.road.cost_per_ton_inr, 1),
                "rail_cost_per_ton_inr": round(unit.conventional_rail.cost_per_ton_inr, 1),
                "dfc_cost_per_ton_inr": round(unit.dfc_rail.cost_per_ton_inr, 1) if unit.dfc_rail is not None else np.nan,
                "optimal_mode": unit.optimal_mode,
                "modal_shift_savings_inr": round(unit.modal_shift_savings_per_ton_inr, 1),
                "modal_shift_savings_pct": round(unit.modal_shift_savings_pct, 1),
                "break_even_distance_km": unit.break_even_distance_km,
            })
        results.append(result)

    out_df = pd.DataFrame(results, columns=columns)
    # Single-output replacement only, after complete source/model validation.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output_csv.parent, prefix=f".{output_csv.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        out_df.to_csv(temporary, index=False)
        os.replace(temporary, output_csv)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Wrote {len(out_df)} district multi-modal freight cost summaries -> {output_csv}")

    # Summary Insights
    valid = out_df[~out_df.is_island.astype(bool) & out_df.road_cost_per_ton_inr.notna()]
    print("\n=== National Freight Economics & Modal Split Summary ===")
    print(f"Mainland Districts Analyzed: {len(valid)}")
    print("Optimal Mode Distribution:")
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
