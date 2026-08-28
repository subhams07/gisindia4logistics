"""
gisindia4logistics.sdk
High-level typed Python SDK for GISIndia4Logistics.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
import pandas as pd
import geopandas as gpd

from server.dependencies import DataStore
from server.routers.admin import get_district_scorecard
from server.routers.routing import calculate_highway_route, RouteRequest
from server.routers.hubs import get_nearest_hubs
from server.models.schemas import FreightCostSimulationRequest, CostParametersOverride
from server.routers.simulation import simulate_freight_cost, simulate_port_gravity, PortGravitySimulationRequest
from scripts.analyze.plot_villages import (
    load_district_villages_gdf, generate_leaflet_html, plot_villages_static
)
from scripts.clean.standardize import DATA_DIR


def get_data_store() -> DataStore:
    """Returns the singleton in-memory GISIndia4Logistics DataStore instance."""
    return DataStore.get_instance()


def _to_dict(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dict(x) for x in obj]
    return obj


def get_district(district_name_or_code: Union[str, int]) -> Dict[str, Any]:
    """
    Retrieve comprehensive logistics scorecard, nearest infrastructure, and demographic indicators
    for any Indian district by name or LGD code.
    
    Example:
        >>> import gisindia4logistics as gis
        >>> pune = gis.get_district("Pune")
        >>> print(pune["nearest_highway_km"], pune["nearest_port"]["name"])
    """
    store = get_data_store()
    res = get_district_scorecard(code_or_name=str(district_name_or_code), store=store)
    return _to_dict(res)


def route_highway(
    origin: Tuple[float, float],
    destination: Tuple[float, float],
    vehicle_type: str = "MAV_20T"
) -> Dict[str, Any]:
    """
    Calculate shortest-path commercial highway route, driving hours, road distance in km,
    number of toll plazas encountered, and FASTag toll expense between two coordinates.
    
    Args:
        origin: (latitude, longitude) tuple of origin point
        destination: (latitude, longitude) tuple of destination point
        vehicle_type: 'MAV_20T', 'LMV', '2_AXLE_TRUCK' (default: 'MAV_20T')
        
    Example:
        >>> route = gis.route_highway(origin=(18.5204, 73.8567), destination=(18.9500, 72.9500))
        >>> print(f"{route['distance_km']:.1f} km, {route['drive_time_formatted']}, ₹{route['estimated_toll_cost_inr']}")
    """
    store = get_data_store()
    req = RouteRequest(
        origin=[float(origin[0]), float(origin[1])],
        destination=[float(destination[0]), float(destination[1])],
        vehicle_type=vehicle_type
    )
    res = calculate_highway_route(req=req, store=store)
    return _to_dict(res)


def calculate_freight_cost(
    origin_district: str,
    target_port: Optional[str] = None,
    payload_tons: float = 20.0,
    road_linehaul_rate: Optional[float] = None,
    toll_cost_per_plaza: Optional[float] = None,
    rail_base_class_rate: Optional[float] = None,
    dfc_linehaul_rate: Optional[float] = None,
    inventory_holding_rate: Optional[float] = None
) -> Dict[str, Any]:
    """
    Simulate and optimize generalized freight cost (INR/tonne) and transit hours
    across Road Trucking, Conventional Indian Railways, and Dedicated Freight Corridor (DFC).
    
    Example:
        >>> cost = gis.calculate_freight_cost("Indore", target_port="Jawaharlal Nehru Port (JNPT)", payload_tons=25.0)
        >>> print(cost["optimal_mode"], f"Savings: {cost['modal_shift_savings_pct']:.1f}%")
    """
    store = get_data_store()
    params = None
    if any(v is not None for v in [road_linehaul_rate, toll_cost_per_plaza, rail_base_class_rate, dfc_linehaul_rate, inventory_holding_rate]):
        params = CostParametersOverride()
        if road_linehaul_rate is not None:
            params.road_linehaul_rate = road_linehaul_rate
        if toll_cost_per_plaza is not None:
            params.toll_cost_per_plaza = toll_cost_per_plaza
        if rail_base_class_rate is not None:
            params.rail_base_class_rate = rail_base_class_rate
        if dfc_linehaul_rate is not None:
            params.dfc_linehaul_rate = dfc_linehaul_rate
        if inventory_holding_rate is not None:
            params.inventory_holding_rate = inventory_holding_rate
        params.truck_payload_tons = payload_tons

    req = FreightCostSimulationRequest(
        origin_district=origin_district,
        target_port=target_port,
        payload_tons=payload_tons,
        custom_parameters=params
    )
    res = simulate_freight_cost(req=req, store=store)
    return _to_dict(res)


def find_nearest(
    latitude: float,
    longitude: float,
    top_k: int = 3
) -> Dict[str, Any]:
    """
    Spatial KDTree search finding the closest infrastructure across all multi-modal
    categories (Ports, ICDs, MMLPs, Air Cargo, Waterways, Cold Storages, Mandis, Tolls).
    
    Example:
        >>> nearest = gis.find_nearest(latitude=28.6139, longitude=77.2090, top_k=2)
        >>> print(nearest["ports"], nearest["toll_plazas"])
    """
    store = get_data_store()
    return get_nearest_hubs(latitude=latitude, longitude=longitude, top_k=top_k, store=store)


def simulate_port_catchment(alpha: float = 0.85, beta: float = 1.65) -> List[Dict[str, Any]]:
    """
    Simulate national port hinterland market share and captured population across
    all 12 Major Commercial Ports using the Huff/Reilly gravity model.
    """
    store = get_data_store()
    req = PortGravitySimulationRequest(alpha=alpha, beta=beta)
    res = simulate_port_gravity(req=req, store=store)
    return [_to_dict(item) for item in res]


def plot_villages(
    state: str,
    district: str,
    metric: str = "dist_rail_station_km",
    output_format: str = "html",
    output_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Generate an interactive Leaflet HTML map or high-resolution PNG choropleth map
    of all villages in a district color-coded by accessibility metrics.
    
    Args:
        state: State name (e.g. 'Haryana', 'Maharashtra')
        district: District name (e.g. 'Ambala', 'Pune')
        metric: 'dist_rail_station_km', 'dist_nh_km', 'dist_icd_km', 'dist_port_km'
        output_format: 'html', 'png', or 'both'
        output_path: Optional custom destination file path
    """
    gdf = load_district_villages_gdf(state=state, district=district)
    out_files = {}

    if output_format in ["html", "both"]:
        html_code = generate_leaflet_html(gdf=gdf, state=state, district=district, metric=metric)
        dst_html = Path(output_path) if output_path and str(output_path).endswith(".html") else (DATA_DIR / "analysis" / f"{district.lower()}_village_{metric}_map.html")
        with open(dst_html, "w", encoding="utf-8") as f:
            f.write(html_code)
        out_files["html"] = str(dst_html)

    if output_format in ["png", "both"]:
        dst_png = Path(output_path) if output_path and str(output_path).endswith(".png") else (DATA_DIR / "analysis" / f"{district.lower()}_village_{metric}_map.png")
        plot_villages_static(gdf=gdf, state=state, district=district, metric=metric, output_png=dst_png)
        out_files["png"] = str(dst_png)

    return {
        "status": "success",
        "state": state,
        "district": district,
        "villages_count": len(gdf),
        "metric": metric,
        "files": out_files
    }
