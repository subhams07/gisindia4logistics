"""
scripts/analyze/port_hinterland_model.py
Port Hinterland Gravity & Catchment Model for India (GIS4Logistics Initiative 2.2).

Implements the Huff / Reilly Gravity Model to compute:
1. Port catchment probabilities P(ij) for all 781 districts across all 12 Major Commercial Sea Ports.
2. Identifies Captive vs Contested Port Hinterlands based on port throughput and road drive time.

All model parameters (alpha attraction power, beta friction exponent, port throughputs)
are fully customizable via CLI flags, JSON configs, or Python function arguments.
"""

import sys
import argparse
import json
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, ".")
from scripts.clean.standardize import DATA_DIR

def run_port_gravity_model(
    alpha: float = 0.85,
    beta: float = 1.65,
    custom_capacities: dict = None,
    output_csv: Path = None
) -> pd.DataFrame:
    if output_csv is None:
        output_csv = DATA_DIR / "analysis" / "district_port_hinterland_catchment.csv"

    print("=== GIS4Logistics Port Hinterland Gravity Model ===")
    print(f"User Parameters: Alpha (Throughput Power) = {alpha:.2f} | Beta (Distance Decay Friction) = {beta:.2f}")

    # 1. Load Port Matrix
    p_matrix = DATA_DIR / "analysis" / "nh_district_port_matrix.csv"
    if not p_matrix.exists():
        raise FileNotFoundError(f"Error: {p_matrix} not found. Run nh_travel_matrix.py first.")
    df_matrix = pd.read_csv(p_matrix)

    # Merge population estimates
    p_pop = DATA_DIR / "demographic" / "district_population_estimates.csv"
    if p_pop.exists():
        df_pop = pd.read_csv(p_pop)
        if "district_code" in df_pop.columns and "pop_2011" in df_pop.columns:
            pop_map = df_pop.dropna(subset=["district_code"]).set_index("district_code")["pop_2011"].to_dict()
            df_matrix["pop_2011"] = df_matrix["district_code"].map(pop_map).fillna(0).astype(int)

    # Port capacity map (FY24 MT) mapped to clean labels
    default_port_data = {
        "drive_hours_to_paradip": ("Paradip Port", 145.38),
        "drive_hours_to_deendayal_(kandla)": ("Deendayal Port (Kandla)", 132.50),
        "drive_hours_to_jawaharlal_nehru_(jnpt/navi_mumbai)": ("Jawaharlal Nehru Port (JNPT)", 86.00),
        "drive_hours_to_visakhapatnam": ("Visakhapatnam Port", 81.00),
        "drive_hours_to_kolkata_(syama_prasad_mookerjee)_incl._haldia_dock_complex": ("Syama Prasad Mookerjee Port (Kolkata/Haldia)", 66.00),
        "drive_hours_to_mumbai": ("Mumbai Port", 65.00),
        "drive_hours_to_chennai": ("Chennai Port", 54.50),
        "drive_hours_to_kamarajar_(ennore)": ("Kamarajar Port (Ennore)", 48.00),
        "drive_hours_to_new_mangalore": ("New Mangalore Port", 46.00),
        "drive_hours_to_v.o._chidambaranar_(tuticorin)": ("V.O. Chidambaranar Port (Tuticorin)", 41.50),
        "drive_hours_to_cochin": ("Cochin Port", 36.50),
        "drive_hours_to_mormugao": ("Mormugao Port", 20.50),
    }

    port_data = default_port_data.copy()
    if custom_capacities:
        for k, (p_name, _) in default_port_data.items():
            if p_name in custom_capacities:
                port_data[k] = (p_name, float(custom_capacities[p_name]))

    port_cols = [c for c in port_data.keys() if c in df_matrix.columns]
    print(f"Analyzing {len(port_cols)} Major Ports across {len(df_matrix)} Districts...")

    results = []

    for _, row in df_matrix.iterrows():
        state = row.get("state")
        district = row.get("district")
        d_code = row.get("district_code")
        pop = row.get("pop_2011", 0)
        is_island = bool(row.get("is_island", False))

        if is_island:
            results.append({
                "state": state, "district": district, "district_code": d_code,
                "pop_2011": pop, "is_island": True, "primary_port": "N/A (Island)",
                "primary_port_probability": np.nan, "primary_port_drive_hours": np.nan,
                "secondary_port": "N/A", "secondary_port_probability": np.nan,
                "secondary_port_drive_hours": np.nan, "hinterland_category": "Island / Isolated",
                "contestability_index": np.nan
            })
            continue

        # Compute gravity utility: U_j = (Capacity_j^alpha) / (Drive_Hours_ij^beta)
        utilities = {}
        hrs_map = {}
        for p_col in port_cols:
            clean_name, cap = port_data[p_col]
            h_val = row.get(p_col)
            if pd.notna(h_val) and float(h_val) > 0:
                drive_hrs = float(h_val)
                u = (cap ** alpha) / ((drive_hrs / 5.0) ** beta)
                utilities[clean_name] = u
                hrs_map[clean_name] = drive_hrs

        if not utilities:
            results.append({
                "state": state, "district": district, "district_code": d_code,
                "pop_2011": pop, "is_island": False, "primary_port": "N/A",
                "primary_port_probability": np.nan, "primary_port_drive_hours": np.nan,
                "secondary_port": "N/A", "secondary_port_probability": np.nan,
                "secondary_port_drive_hours": np.nan, "hinterland_category": "Isolated",
                "contestability_index": np.nan
            })
            continue

        total_u = sum(utilities.values())
        probs = {p: u / total_u for p, u in utilities.items()}
        sorted_ports = sorted(probs.items(), key=lambda x: x[1], reverse=True)

        p1_name, p1_prob = sorted_ports[0]
        p2_name, p2_prob = sorted_ports[1] if len(sorted_ports) > 1 else ("None", 0.0)

        # Categorize contestability
        if p1_prob >= 0.75:
            category = "Captive Hinterland"
        elif p1_prob >= 0.50:
            category = "Dominant / Mildly Contested"
        else:
            category = "Highly Contested / Split Hinterland"

        contestability_idx = round(1.0 - (p1_prob - p2_prob), 3)

        results.append({
            "state": state, "district": district, "district_code": d_code,
            "pop_2011": pop, "is_island": False,
            "primary_port": p1_name,
            "primary_port_probability": round(p1_prob * 100.0, 1),
            "primary_port_drive_hours": round(hrs_map.get(p1_name, 0.0), 2),
            "secondary_port": p2_name,
            "secondary_port_probability": round(p2_prob * 100.0, 1),
            "secondary_port_drive_hours": round(hrs_map.get(p2_name, 0.0), 2),
            "hinterland_category": category,
            "contestability_index": contestability_idx
        })

    out_df = pd.DataFrame(results)
    out_df.to_csv(output_csv, index=False)
    print(f"Wrote {len(out_df)} district port hinterland allocations -> {output_csv}")

    # Summary Statistics
    valid = out_df[~out_df.is_island & out_df.primary_port_probability.notna()]
    print("\n=== Port Hinterland Market Share & Catchment Summary ===")
    for port, count in valid.primary_port.value_counts().items():
        pct = (count / len(valid)) * 100
        pop_captured = valid[valid.primary_port == port].pop_2011.sum()
        print(f"  - {port:42s}: {count:3d} districts ({pct:5.1f}%) | Pop: {pop_captured/1e7:5.1f} Cr")

    print("\nHinterland Contestability Breakdown:")
    for cat, count in valid.hinterland_category.value_counts().items():
        print(f"  - {cat:36s}: {count:3d} districts ({(count/len(valid))*100:5.1f}%)")
    return out_df


def main():
    parser = argparse.ArgumentParser(description="Port Hinterland Gravity Model with User-Configurable Parameters")
    parser.add_argument("--alpha", type=float, default=0.85, help="Port throughput attractiveness sensitivity exponent (default: 0.85)")
    parser.add_argument("--beta", type=float, default=1.65, help="Drive-time distance decay friction exponent (default: 1.65)")
    parser.add_argument("--config", type=str, help="Path to JSON config file containing custom port capacities or exponents")
    parser.add_argument("--output-csv", type=str, help="Destination output CSV path")

    args = parser.parse_args()

    custom_caps = None
    alpha = args.alpha
    beta = args.beta

    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        alpha = cfg.get("alpha", alpha)
        beta = cfg.get("beta", beta)
        custom_caps = cfg.get("port_capacities", None)

    out_p = Path(args.output_csv) if args.output_csv else None
    run_port_gravity_model(alpha=alpha, beta=beta, custom_capacities=custom_caps, output_csv=out_p)


if __name__ == "__main__":
    main()
