"""
server/routers/simulation.py
Multi-Modal Generalized Freight Cost and Port Gravity Simulation endpoints.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
import pandas as pd
import numpy as np

from server.dependencies import DataStore, get_data_store
from server.models.schemas import (
    FreightCostSimulationRequest, FreightCostSimulationResponse,
    ModalCostBreakdown, PortGravitySimulationRequest, PortMarketShareItem
)
from scripts.analyze.intermodal_cost_engine import ir_telescopic_freight_rate

router = APIRouter(prefix="/simulate", tags=["Freight Simulation & Optimization"])


@router.post("/freight-cost", response_model=FreightCostSimulationResponse)
def simulate_freight_cost(req: FreightCostSimulationRequest, store: DataStore = Depends(get_data_store)):
    """Simulate end-to-end freight cost (Road vs Rail vs DFC) for any district with custom economic parameters."""
    if store.travel_time_df is None:
        raise HTTPException(status_code=500, detail="Travel time summary data not loaded")

    df = store.travel_time_df

    # Match district
    match = None
    if req.origin_district_code:
        m = df[df.district_code == req.origin_district_code]
        if not m.empty:
            match = m.iloc[0]

    if match is None and req.origin_district:
        m = df[df.district.str.lower() == req.origin_district.lower()]
        if not m.empty:
            match = m.iloc[0]

    if match is None:
        raise HTTPException(status_code=404, detail=f"District '{req.origin_district or req.origin_district_code}' not found")

    st_name = match["state"]
    d_name = match["district"]
    target_port = req.target_port or match.get("nearest_port_name", "Major Port")
    road_dist_km = match.get("port_road_distance_km", 450.0)
    drive_time_hrs = match.get("port_drive_time_hours", 7.0)
    freight_term_km = match.get("freight_terminal_road_distance_km", 50.0)

    # Cost parameters (use overrides if provided)
    p = req.custom_parameters
    road_linehaul = p.road_linehaul_rate if p and p.road_linehaul_rate is not None else 3.30
    road_handling = p.road_handling_cost if p and p.road_handling_cost is not None else 140.0
    toll_cost_plaza = p.toll_cost_per_plaza if p and p.toll_cost_per_plaza is not None else 340.0
    truck_payload = p.truck_payload_tons if p and p.truck_payload_tons is not None else 20.0
    toll_spacing = p.toll_spacing_km if p and p.toll_spacing_km is not None else 65.0
    
    rail_base_rate = p.rail_base_class_rate if p and p.rail_base_class_rate is not None else 1.55
    rail_first_mile = p.rail_first_mile_rate if p and p.rail_first_mile_rate is not None else 4.20
    rail_first_mile_h = p.rail_first_mile_handling if p and p.rail_first_mile_handling is not None else 120.0
    rail_handling = p.rail_handling_and_siding if p and p.rail_handling_and_siding is not None else 220.0
    rail_speed = p.rail_commercial_speed_kmh if p and p.rail_commercial_speed_kmh is not None else 25.0
    rail_delay = p.rail_yard_detention_hours if p and p.rail_yard_detention_hours is not None else 12.0
    
    dfc_linehaul = p.dfc_linehaul_rate if p and p.dfc_linehaul_rate is not None else 1.12
    dfc_handling = p.dfc_handling_cost if p and p.dfc_handling_cost is not None else 180.0
    dfc_speed = p.dfc_commercial_speed_kmh if p and p.dfc_commercial_speed_kmh is not None else 60.0
    dfc_delay = p.dfc_yard_transfer_hours if p and p.dfc_yard_transfer_hours is not None else 3.0
    
    inv_rate = p.inventory_holding_rate if p and p.inventory_holding_rate is not None else 7.50
    payload = req.payload_tons or 20.0

    # 1. Road Trucking Cost
    road_tolls = (road_dist_km / toll_spacing) * (toll_cost_plaza / truck_payload)
    road_time_cost = drive_time_hrs * inv_rate
    c_road_per_ton = (road_dist_km * road_linehaul) + road_tolls + road_time_cost + road_handling
    c_road_total = c_road_per_ton * payload

    # 2. Conventional Rail Freight Cost
    f_dist = min(freight_term_km if pd.notna(freight_term_km) else 50.0, 100.0)
    c_first_mile = (f_dist * rail_first_mile) + rail_first_mile_h
    rail_dist = road_dist_km * 1.08
    c_rail_tariff = ir_telescopic_freight_rate(rail_dist, base_class_rate=rail_base_rate)
    rail_transit_hrs = (rail_dist / rail_speed) + rail_delay
    c_rail_time = rail_transit_hrs * inv_rate
    c_rail_per_ton = c_first_mile + c_rail_tariff + rail_handling + c_rail_time
    c_rail_total = c_rail_per_ton * payload

    # 3. DFC Freight Cost
    dfc_corridor_states = {
        "Uttar Pradesh", "Haryana", "Rajasthan", "Gujarat", "Maharashtra",
        "Punjab", "Bihar", "Jharkhand", "West Bengal"
    }
    is_dfc_eligible = st_name in dfc_corridor_states

    if is_dfc_eligible:
        dfc_dist = road_dist_km * 1.02
        c_dfc_tariff = dfc_dist * dfc_linehaul
        dfc_transit_hrs = (dfc_dist / dfc_speed) + dfc_delay
        c_dfc_time = dfc_transit_hrs * inv_rate
        c_dfc_per_ton = c_first_mile + c_dfc_tariff + dfc_handling + c_dfc_time
        c_dfc_total = c_dfc_per_ton * payload
        dfc_breakdown = ModalCostBreakdown(
            cost_per_ton_inr=round(c_dfc_per_ton, 1),
            total_shipment_cost_inr=round(c_dfc_total, 1),
            transit_time_hours=round(dfc_transit_hrs, 1)
        )
    else:
        c_dfc_per_ton = np.nan
        dfc_breakdown = None

    # Optimal Mode & Savings
    modes = {"Road Trucking": c_road_per_ton, "Conventional Rail": c_rail_per_ton}
    if is_dfc_eligible and not np.isnan(c_dfc_per_ton):
        modes["Dedicated Freight Corridor (DFC)"] = c_dfc_per_ton

    optimal_mode = min(modes, key=modes.get)
    min_cost = modes[optimal_mode]
    savings_per_ton = max(c_road_per_ton - min_cost, 0.0)
    savings_total = savings_per_ton * payload
    savings_pct = (savings_per_ton / c_road_per_ton) * 100.0
    break_even_km = 210.0 if is_dfc_eligible else 380.0

    return {
        "origin_district": d_name,
        "state": st_name,
        "target_port": target_port,
        "road_distance_km": round(road_dist_km, 1),
        "road": ModalCostBreakdown(
            cost_per_ton_inr=round(c_road_per_ton, 1),
            total_shipment_cost_inr=round(c_road_total, 1),
            transit_time_hours=round(drive_time_hrs, 1)
        ),
        "conventional_rail": ModalCostBreakdown(
            cost_per_ton_inr=round(c_rail_per_ton, 1),
            total_shipment_cost_inr=round(c_rail_total, 1),
            transit_time_hours=round(rail_transit_hrs, 1)
        ),
        "dfc_rail": dfc_breakdown,
        "optimal_mode": optimal_mode,
        "modal_shift_savings_per_ton_inr": round(savings_per_ton, 1),
        "modal_shift_savings_total_inr": round(savings_total, 1),
        "modal_shift_savings_pct": round(savings_pct, 1),
        "break_even_distance_km": break_even_km
    }


@router.post("/port-gravity", response_model=List[PortMarketShareItem])
def simulate_port_gravity(req: PortGravitySimulationRequest, store: DataStore = Depends(get_data_store)):
    """Simulate national port market contestability and captured hinterland population under custom parameters."""
    if store.port_matrix_df is None:
        raise HTTPException(status_code=500, detail="Port matrix data not loaded")

    df_matrix = store.port_matrix_df.copy()
    alpha = req.alpha or 0.85
    beta = req.beta or 1.65

    # Population lookup
    pop_map = {}
    if store.districts_df is not None and "district_code" in store.districts_df.columns:
        pop_map = store.districts_df.dropna(subset=["district_code"]).set_index("district_code")["pop_2011"].to_dict()

    port_data = {
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

    if req.custom_port_capacities:
        for k, (p_name, _) in port_data.items():
            if p_name in req.custom_port_capacities:
                port_data[k] = (p_name, float(req.custom_port_capacities[p_name]))

    port_cols = [c for c in port_data.keys() if c in df_matrix.columns]

    captured_districts = {p_name: 0 for p_name, _ in port_data.values()}
    captured_pop = {p_name: 0 for p_name, _ in port_data.values()}
    total_valid = 0

    for _, row in df_matrix.iterrows():
        d_code = row.get("district_code")
        pop = pop_map.get(d_code, 0)

        utilities = {}
        for p_col in port_cols:
            clean_name, cap = port_data[p_col]
            h_val = row.get(p_col)
            if pd.notna(h_val) and float(h_val) > 0:
                drive_hrs = float(h_val)
                u = (cap ** alpha) / ((drive_hrs / 5.0) ** beta)
                utilities[clean_name] = u

        if not utilities:
            continue

        p_winner = max(utilities, key=utilities.get)
        captured_districts[p_winner] += 1
        captured_pop[p_winner] += pop
        total_valid += 1

    out = []
    for p_name, count in captured_districts.items():
        if count > 0:
            out.append({
                "port_name": p_name,
                "captured_districts_count": count,
                "market_share_districts_pct": round((count / total_valid) * 100.0, 1),
                "captured_population_cr": round(captured_pop[p_name] / 1e7, 2)
            })

    out.sort(key=lambda x: x["captured_districts_count"], reverse=True)
    return out
