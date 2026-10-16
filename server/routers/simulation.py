"""
server/routers/simulation.py
Multi-Modal Generalized Freight Cost and Port Gravity Simulation endpoints.
"""

import math
import re
from typing import List, Optional, Tuple
from fastapi import APIRouter, HTTPException, Depends
import pandas as pd
from numbers import Real
from pydantic import ValidationError

from server.dependencies import DataStore, get_data_store
from server.models.schemas import (
    FreightCostSimulationRequest, FreightCostSimulationResponse,
    ModalCostBreakdown, PortGravitySimulationRequest, PortMarketShareItem
)
from scripts.analyze import freight_cost_core as freight_core
from scripts.clean.standardize import clean_state_name
from server.routers.admin import canonical_state

router = APIRouter(prefix="/simulate", tags=["Freight Simulation & Optimization"])

PORT_MATRIX_DATA = {
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


def _normalize_port_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _finite_nonnegative(value, name: str, *, status_code: int = 422) -> float:
    """Reject invalid model inputs/results before JSON can turn them into null."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(status_code=status_code, detail=f"{name} must be finite and nonnegative") from None
    if not math.isfinite(number) or number < 0:
        raise HTTPException(status_code=status_code, detail=f"{name} must be finite and nonnegative")
    return number


def _requested_port_route(
    target_port: str,
    district_row: pd.Series,
    port_matrix_df: Optional[pd.DataFrame],
) -> Tuple[str, float, float]:
    """Resolve a requested major port to its matrix-backed distance and drive time."""
    if port_matrix_df is None:
        raise HTTPException(status_code=503, detail="District-to-port matrix data not loaded")

    # Check island status
    island_flag = district_row.get("is_island", False)
    is_island = (pd.notna(island_flag) and bool(island_flag)) or str(district_row.get("state", "")).strip().lower() in {
        "andaman and nicobar islands", "lakshadweep"
    } or str(district_row.get("district", "")).lower() in {
        "nicobars", "north and middle andaman", "south andamans", "lakshadweep"
    }
    if is_island:
        raise HTTPException(
            status_code=422,
            detail=f"District '{district_row.get('district')}' is an island with no contiguous highway network connection to mainland major ports"
        )

    requested = _normalize_port_name(target_port)
    if not requested:
        raise HTTPException(status_code=422, detail="target_port must contain letters or digits")
    matches = []
    for time_column, (display_name, _) in PORT_MATRIX_DATA.items():
        normalized = _normalize_port_name(display_name)
        if requested == normalized or requested in normalized or normalized in requested:
            matches.append((time_column, display_name))

    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="Ambiguous target_port")
    if not matches:
        supported = ", ".join(display_name for display_name, _ in PORT_MATRIX_DATA.values())
        raise HTTPException(
            status_code=404,
            detail=f"Unknown or ambiguous target_port '{target_port}'. Supported values: {supported}",
        )

    time_column, display_name = matches[0]
    distance_column = time_column.replace("drive_hours_to_", "road_km_to_", 1)
    matrix = port_matrix_df

    # Composite match on state + district (and code if available)
    state_str = clean_state_name(district_row["state"])
    dist_str = str(district_row["district"]).lower()
    code_val = district_row.get("district_code")

    m_filter = (matrix["state"].map(clean_state_name) == state_str) & (matrix["district"].astype(str).str.lower() == dist_str)
    if pd.notna(code_val) and "district_code" in matrix.columns:
        numeric_codes = pd.to_numeric(matrix["district_code"], errors="coerce")
        matrix_row = matrix[m_filter & (numeric_codes == float(code_val))]
    else:
        matrix_row = matrix[m_filter]

    if matrix_row.empty:
        raise HTTPException(status_code=404, detail=f"District '{district_row['district']} ({district_row['state']})' not found in the district-to-port matrix")
    if matrix_row.shape[0] > 1:
        raise HTTPException(status_code=409, detail=f"Ambiguous district identity '{district_row['district']} ({district_row['state']})'")

    if time_column not in matrix_row.columns or distance_column not in matrix_row.columns:
        raise HTTPException(status_code=503, detail="Port matrix must be regenerated with distance columns")

    drive_hours = matrix_row.iloc[0][time_column]
    road_distance_km = matrix_row.iloc[0][distance_column]
    return (display_name,
            _freight_source_number(road_distance_km),
            _freight_source_number(drive_hours))


def _freight_source_number(value):
    """Stored numbers are strict; client coercion remains the schema's policy."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise HTTPException(status_code=503, detail="Freight source data is unavailable or invalid.")
    return _finite_nonnegative(value, "Freight source data", status_code=503)


def _required_source_text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=503, detail=f"Invalid stored {name}")
    return value


def _positive_power(base: float, exponent: float, *, status_code: int) -> float:
    """Keep the legacy utility arithmetic, rejecting overflow and underflow."""
    try:
        value = base ** exponent
    except OverflowError:
        raise HTTPException(status_code=status_code, detail="Gravity input exceeds numerical range") from None
    if not math.isfinite(value) or value <= 0:
        raise HTTPException(status_code=status_code, detail="Gravity input exceeds numerical range")
    return value


@router.post("/freight-cost", response_model=FreightCostSimulationResponse)
def simulate_freight_cost(req: FreightCostSimulationRequest, store: DataStore = Depends(get_data_store)):
    """Simulate end-to-end freight cost (Road vs Rail vs DFC) for any district with custom economic parameters."""
    # Revalidate a plain Python dump: Pydantic otherwise trusts typed instances,
    # including nested overrides mutated through __dict__ / model_construct.
    try:
        req = FreightCostSimulationRequest.model_validate(req.model_dump(mode="python"))
    except ValidationError:
        raise HTTPException(status_code=422, detail="Request input is invalid.") from None
    if store.travel_time_df is None:
        raise HTTPException(status_code=503, detail="Travel time summary data not loaded")

    df = store.travel_time_df
    if not {"district", "state"}.issubset(df.columns):
        raise HTTPException(status_code=503, detail="Invalid stored district identity")

    # Every supplied identifier is a constraint, never permission to fall back.
    m = df
    constraints = []
    if req.origin_district_code is not None:
        if "district_code" not in df.columns:
            raise HTTPException(status_code=503, detail="Invalid stored district identity")
        constraints.append(pd.to_numeric(df.district_code, errors="coerce") == req.origin_district_code)
    if req.origin_district:
        constraints.append(df.district.astype("string").str.lower() == req.origin_district.lower())
    if req.origin_state is not None:
        constraints.append(df.state.map(clean_state_name) == canonical_state(req.origin_state))
    for constraint in constraints:
        m = m[constraint.reindex(m.index).fillna(False)]
    if m.empty:
        identity_constraints = constraints[:int(req.origin_district_code is not None) + int(bool(req.origin_district))]
        if len(constraints) > 1 and any(c.any() for c in identity_constraints):
            raise HTTPException(status_code=422, detail="Conflicting supplied district identifiers")
        raise HTTPException(status_code=404, detail=f"District '{req.origin_district or req.origin_district_code}' not found with supplied identifiers")
    if len(m) != 1:
        raise HTTPException(status_code=409, detail=f"Ambiguous district identity '{req.origin_district or req.origin_district_code}'")
    match = m.iloc[0]

    # Validate source labels before island checks, matrix resolution or costs.
    d_name = _required_source_text(match.get("district"), "district name")
    source_state = _required_source_text(match.get("state"), "state name")
    try:
        st_name = freight_core.normalize_freight_state(source_state)
    except freight_core.SourceValidationError:
        raise HTTPException(status_code=503, detail="Freight source data is unavailable or invalid.") from None

    # Reject islands for land freight
    island_flag = match.get("is_island", False)
    if (pd.notna(island_flag) and bool(island_flag)) or clean_state_name(match["state"]) in {"Andaman And Nicobar Islands", "Lakshadweep"}:
        raise HTTPException(
            status_code=422,
            detail=f"District '{match['district']} ({match['state']})' is an island with no contiguous highway network connection to mainland major ports"
        )

    if req.target_port:
        target_port, road_dist_km, drive_time_hrs = _requested_port_route(
            req.target_port,
            match,
            store.port_matrix_df,
        )
    else:
        target_port = _required_source_text(match.get("nearest_port_name"), "nearest port name")
        road_dist_km = match.get("port_road_distance_km")
        drive_time_hrs = match.get("port_drive_time_hours")
    terminal = match.get("freight_terminal_road_distance_km")
    terminal = None if terminal is None or pd.isna(terminal) else terminal
    parameters = req.custom_parameters.model_dump(mode="python") if req.custom_parameters is not None else None
    payload = req.payload_tons if req.payload_tons is not None else FreightCostSimulationRequest.model_fields["payload_tons"].default
    try:
        unit = freight_core.calculate_unit_costs(road_dist_km, drive_time_hrs, terminal, st_name, parameters)
        shipment = freight_core.scale_shipment(unit, payload)
    except freight_core.SourceValidationError:
        raise HTTPException(status_code=503, detail="Freight source data is unavailable or invalid.") from None
    except (freight_core.ParameterValidationError, freight_core.ShipmentValidationError):
        raise HTTPException(status_code=422, detail="Freight input is invalid.") from None
    except freight_core.CalculationError:
        # Keep freight's existing overflow compatibility, not routing's policy.
        raise HTTPException(status_code=422, detail="Freight calculation exceeds numerical range.") from None

    def breakdown(mode, total):
        return ModalCostBreakdown(
            cost_per_ton_inr=round(mode.cost_per_ton_inr, 1),
            total_shipment_cost_inr=round(total, 1),
            transit_time_hours=round(mode.transit_time_hours, 1),
        )

    try:
        response = {
            "origin_district": d_name,
            "state": st_name,
            "target_port": target_port,
            "road_distance_km": round(unit.road.distance_km, 1),
            "road": breakdown(unit.road, shipment.road),
            "conventional_rail": breakdown(unit.conventional_rail, shipment.conventional_rail),
            "dfc_rail": None if unit.dfc_rail is None else breakdown(unit.dfc_rail, shipment.dfc_rail),
            "optimal_mode": unit.optimal_mode,
            "modal_shift_savings_per_ton_inr": round(unit.modal_shift_savings_per_ton_inr, 1),
            "modal_shift_savings_total_inr": round(shipment.modal_shift_savings_total_inr, 1),
            "modal_shift_savings_pct": round(unit.modal_shift_savings_pct, 1),
            "break_even_distance_km": unit.break_even_distance_km,
        }
        FreightCostSimulationResponse.model_validate(response)
    except ValidationError:
        raise HTTPException(status_code=503, detail="Freight calculation output is invalid.") from None
    return response


@router.post("/port-gravity", response_model=List[PortMarketShareItem])
def simulate_port_gravity(req: PortGravitySimulationRequest, store: DataStore = Depends(get_data_store)):
    """Simulate national port market contestability and captured hinterland population under custom parameters."""
    if store.port_matrix_df is None:
        raise HTTPException(status_code=503, detail="Port matrix data not loaded")

    df_matrix = store.port_matrix_df.copy()
    alpha = req.alpha if req.alpha is not None else 0.85
    beta = req.beta if req.beta is not None else 1.65
    alpha = _finite_nonnegative(alpha, "alpha")
    beta = _finite_nonnegative(beta, "beta")

    # Population lookup
    if (store.districts_df is None
            or not {"district_code", "pop_2011"}.issubset(store.districts_df.columns)):
        raise HTTPException(status_code=503, detail="District population data not loaded or invalid")
    pop_map = store.districts_df.dropna(subset=["district_code"]).set_index("district_code")["pop_2011"].to_dict()

    port_data = PORT_MATRIX_DATA.copy()

    if req.custom_port_capacities:
        supported = {name for name, _ in port_data.values()}
        if set(req.custom_port_capacities) - supported:
            raise HTTPException(status_code=422, detail="Unrecognized custom port capacity key")
        for k, (p_name, _) in port_data.items():
            if p_name in req.custom_port_capacities:
                port_data[k] = (p_name, float(req.custom_port_capacities[p_name]))

    capacity_utilities = {
        name: _positive_power(cap, alpha, status_code=422)
        for name, cap in port_data.values()
    }

    port_cols = [c for c in port_data.keys() if c in df_matrix.columns]

    captured_districts = {p_name: 0 for p_name, _ in port_data.values()}
    captured_pop = {p_name: 0 for p_name, _ in port_data.values()}
    total_valid = 0

    for _, row in df_matrix.iterrows():
        d_code = row.get("district_code")
        pop = _finite_nonnegative(pop_map.get(d_code, 0), "stored district population", status_code=503)

        utilities = {}
        for p_col in port_cols:
            clean_name, _ = port_data[p_col]
            h_val = row.get(p_col)
            if pd.notna(h_val):
                drive_hrs = _finite_nonnegative(h_val, "stored port drive time", status_code=503)
                if drive_hrs == 0:
                    continue
                denominator = _positive_power(drive_hrs / 5.0, beta, status_code=503)
                u = capacity_utilities[clean_name] / denominator
                status = 422 if req.custom_port_capacities and clean_name in req.custom_port_capacities else 503
                if not math.isfinite(u) or u <= 0:
                    raise HTTPException(status_code=status, detail="Gravity input exceeds numerical range")
                utilities[clean_name] = u

        if not utilities:
            continue

        p_winner = max(utilities, key=utilities.get)
        captured_districts[p_winner] += 1
        captured_pop[p_winner] = _finite_nonnegative(
            captured_pop[p_winner] + pop, "stored captured population", status_code=503
        )
        total_valid += 1

    out = []
    for p_name, count in captured_districts.items():
        out.append({
            "port_name": p_name,
            "captured_districts_count": count,
            "market_share_districts_pct": round((count / total_valid) * 100.0, 1) if total_valid > 0 else 0.0,
            "captured_population_cr": round(captured_pop[p_name] / 1e7, 2)
        })

    out.sort(key=lambda x: x["captured_districts_count"], reverse=True)
    return out
