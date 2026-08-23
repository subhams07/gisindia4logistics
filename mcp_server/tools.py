"""
mcp_server/tools.py
High-level GIS4Logistics tool implementations callable by MCP servers and AI agents.
"""

from typing import Dict, Any, Optional, List
from server.dependencies import DataStore
from server.routers.admin import get_district_scorecard
from server.routers.hubs import get_nearest_hubs
from server.routers.routing import calculate_highway_route
from server.routers.simulation import simulate_freight_cost, simulate_port_gravity
from server.models.schemas import (
    RouteRequest, FreightCostSimulationRequest, CostParametersOverride,
    PortGravitySimulationRequest
)

store = DataStore.get_instance()

def tool_get_district_scorecard(district_name_or_code: str) -> Dict[str, Any]:
    """
    Get a comprehensive logistics scorecard for an Indian district.
    Returns demographic population, nearest highway distance, nearest toll plaza,
    nearest railway station, nearest ICD/port/MMLP, and village accessibility shares.
    """
    return get_district_scorecard(code_or_name=district_name_or_code, store=store)


def tool_calculate_intermodal_freight_cost(
    origin_district: str,
    target_port: Optional[str] = None,
    payload_tons: float = 20.0,
    road_linehaul_rate: Optional[float] = 3.30,
    toll_cost_per_plaza: Optional[float] = 340.0,
    rail_base_class_rate: Optional[float] = 1.55,
    dfc_linehaul_rate: Optional[float] = 1.12,
    inventory_holding_rate: Optional[float] = 7.50
) -> Dict[str, Any]:
    """
    Simulates and compares end-to-end generalized freight costs across:
    1. Road Trucking (Multi-Axle Vehicle on National Highways)
    2. Conventional Indian Railways Freight (Telescopic Tariff)
    3. Dedicated Freight Corridor (DFC Heavy-Haul Rail)
    
    Returns cost per tonne, total shipment outlay, transit hours, optimal mode,
    financial savings, and break-even distance.
    """
    custom_params = CostParametersOverride(
        road_linehaul_rate=road_linehaul_rate,
        toll_cost_per_plaza=toll_cost_per_plaza,
        rail_base_class_rate=rail_base_class_rate,
        dfc_linehaul_rate=dfc_linehaul_rate,
        inventory_holding_rate=inventory_holding_rate
    )
    req = FreightCostSimulationRequest(
        origin_district=origin_district,
        target_port=target_port,
        payload_tons=payload_tons,
        custom_parameters=custom_params
    )
    res = simulate_freight_cost(req=req, store=store)
    return res.model_dump()


def tool_find_nearest_facilities(latitude: float, longitude: float, top_k: int = 3) -> Dict[str, Any]:
    """
    Finds the nearest logistics infrastructure (Sea Ports, ICDs, MMLPs, Air Cargo,
    Inland Waterway Terminals, Cold Storage, APMC Mandis, and Toll Plazas) to any coordinate.
    """
    return get_nearest_hubs(latitude=latitude, longitude=longitude, top_k=top_k, store=store)


def tool_highway_route_and_tolls(
    origin_lat: float, origin_lon: float,
    dest_lat: float, dest_lon: float,
    vehicle_type: str = "MAV_20T"
) -> Dict[str, Any]:
    """
    Calculates commercial highway driving route metrics: total road distance in km,
    driving hours, number of FASTag toll plazas encountered, and estimated toll outlay.
    """
    req = RouteRequest(
        origin=[origin_lat, origin_lon],
        destination=[dest_lat, dest_lon],
        vehicle_type=vehicle_type
    )
    res = calculate_highway_route(req=req, store=store)
    return res.model_dump()


def tool_simulate_port_catchment(alpha: float = 0.85, beta: float = 1.65) -> List[Dict[str, Any]]:
    """
    Simulates national port hinterland market share and captured population across
    all 12 Major Commercial Ports using the Huff/Reilly gravity model.
    """
    req = PortGravitySimulationRequest(alpha=alpha, beta=beta)
    res = simulate_port_gravity(req=req, store=store)
    return [item.model_dump() for item in res]
