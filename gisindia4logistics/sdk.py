"""
gisindia4logistics.sdk
High-level typed Python SDK for GISIndia4Logistics.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

from fastapi import HTTPException
from pydantic import BaseModel
from server.models.phase1 import HighwayRoutePlanRequest, LocationReference
from server.services import routing_facade
from server.services.error_contract import domain_error_from_status, route_error_contract
from server.services.exceptions import AmbiguousLocationError
from server.services.serialization import serialize_result, serialize_nearest_facilities

from server.dependencies import DataStore
from server.routers.admin import canonical_state, get_district_scorecard, validate_village_map_paths

from server.routers.hubs import get_nearest_hubs
from server.models.schemas import (
    FreightCostSimulationRequest,
    FreightCostSimulationResponse,
    CostParametersOverride,
    NearestFacilitiesRequest,
    RouteRequest,
    PortGravitySimulationRequest,
)
from server.routers.simulation import simulate_freight_cost, simulate_port_gravity
from scripts.analyze import plot_villages as _village_maps
from server.config import settings
from gisindia4logistics.data_root import DataNotFoundError, require_data_root  # noqa: F401 (re-exported)

def _ensure_data() -> None:
    """Fail with an actionable DataNotFoundError unless data (or an injected store) exists."""
    if DataStore._instance is None:
        require_data_root()


def get_data_store() -> DataStore:
    """Returns the singleton in-memory GISIndia4Logistics DataStore instance."""
    _ensure_data()
    return DataStore.get_instance()


def _to_dict(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dict(x) for x in obj]
    return obj


def _domain_call(function, *args, **kwargs):
    """Bridge legacy HTTP-only functions without exposing their details."""
    try:
        return function(*args, **kwargs)
    except HTTPException as exc:
        error = domain_error_from_status(exc.status_code)
        if isinstance(error, AmbiguousLocationError) and isinstance(exc.detail, dict):
            candidate_error = AmbiguousLocationError("", candidates=exc.detail.get("candidates"))
            error.candidates = route_error_contract(candidate_error)["candidates"]
        raise error from None


def get_district(district_name_or_code: Union[str, int], *, state: str | None = None) -> Dict[str, Any]:
    """
    Get full logistics and demographic indicators for any Indian district (1-780).
    
    Example:
        >>> dist = gis.get_district("Pune")
        >>> print(dist["nearest_highway_km"], dist["nearest_port"]["name"])
    """
    store = get_data_store()
    return _domain_call(get_district_scorecard, code_or_name=str(district_name_or_code), store=store, state=state)


def route_highway(
    origin: Tuple[float, float],
    destination: Tuple[float, float],
    vehicle_type: str = "MAV_20T"
) -> Dict[str, Any]:
    """
    Calculate strategic National Highway shortest-path route, transit hours,
    and distance-based heuristic toll costs, not official FASTag tariffs.
    
    Example:
        >>> route = gis.route_highway(origin=(18.5204, 73.8567), destination=(18.9500, 72.9500))
        >>> print(route["distance_km"], route["drive_time_formatted"], route["estimated_toll_cost_inr"])
    """
    req = RouteRequest.model_validate({
        "origin": list(origin) if isinstance(origin, tuple) else origin,
        "destination": list(destination) if isinstance(destination, tuple) else destination,
        "vehicle_type": vehicle_type,
    }, strict=True)
    _ensure_data()
    return routing_facade.legacy_highway_route(req)


def plan_highway_route(
    origin: dict[str, Any] | LocationReference,
    destination: dict[str, Any] | LocationReference,
    vehicle_type: str = "MAV_20T",
    *,
    include_route_geometry: bool = True,
    geometry_detail: str = "none",
) -> Dict[str, Any]:
    """Return a strict JSON route; the facade revalidates nested mutable inputs.

    Either geometry suppression control wins; no official tolls are implied.
    """
    # Extract Python values before instance validators can trust mutated fields
    # or call .strip() on a mutated hub name. Do not coerce through JSON mode.
    request = HighwayRoutePlanRequest(
        origin=origin.model_dump(mode="python", warnings=False) if isinstance(origin, BaseModel) else origin,
        destination=destination.model_dump(mode="python", warnings=False) if isinstance(destination, BaseModel) else destination,
        vehicle_type=vehicle_type,
        include_route_geometry=include_route_geometry, geometry_detail=geometry_detail,
    )
    _ensure_data()
    return serialize_result(routing_facade.plan_highway_route(request))


def calculate_freight_cost(
    origin_district: str | None = None,
    target_port: Optional[str] = None,
    payload_tons: float = 20.0,
    road_linehaul_rate: Optional[float] = None,
    toll_cost_per_plaza: Optional[float] = None,
    rail_base_class_rate: Optional[float] = None,
    dfc_linehaul_rate: Optional[float] = None,
    inventory_holding_rate: Optional[float] = None,
    *,
    origin_district_code: int | None = None,
    origin_state: str | None = None,
    truck_payload_tons: float | None = None,
    road_handling_cost: float | None = None,
    toll_spacing_km: float | None = None,
    rail_first_mile_rate: float | None = None,
    rail_first_mile_handling: float | None = None,
    rail_handling_and_siding: float | None = None,
    rail_commercial_speed_kmh: float | None = None,
    rail_yard_detention_hours: float | None = None,
    dfc_handling_cost: float | None = None,
    dfc_commercial_speed_kmh: float | None = None,
    dfc_yard_transfer_hours: float | None = None,
) -> Dict[str, Any]:
    """
    Simulate and optimize generalized freight cost (INR/tonne) and transit hours
    across Road Trucking, Conventional Indian Railways, and Dedicated Freight Corridor (DFC).
    Shipment weight scales totals continuously; only truck_payload_tons changes
    vehicle capacity used to allocate toll cost per tonne.
    
    Example:
        >>> cost = gis.calculate_freight_cost("Indore", target_port="Jawaharlal Nehru Port (JNPT/Navi Mumbai)", payload_tons=25.0)
        >>> print(cost["optimal_mode"], f"Savings: {cost['modal_shift_savings_pct']:.1f}%")
    """
    params = None
    override_kwargs = {}
    if road_linehaul_rate is not None:
        override_kwargs["road_linehaul_rate"] = road_linehaul_rate
    if toll_cost_per_plaza is not None:
        override_kwargs["toll_cost_per_plaza"] = toll_cost_per_plaza
    if rail_base_class_rate is not None:
        override_kwargs["rail_base_class_rate"] = rail_base_class_rate
    if dfc_linehaul_rate is not None:
        override_kwargs["dfc_linehaul_rate"] = dfc_linehaul_rate
    if inventory_holding_rate is not None:
        override_kwargs["inventory_holding_rate"] = inventory_holding_rate
    # Shipment quantity never implicitly changes vehicle capacity.
    additional_overrides = {
        "truck_payload_tons": truck_payload_tons,
        "road_handling_cost": road_handling_cost,
        "toll_spacing_km": toll_spacing_km,
        "rail_first_mile_rate": rail_first_mile_rate,
        "rail_first_mile_handling": rail_first_mile_handling,
        "rail_handling_and_siding": rail_handling_and_siding,
        "rail_commercial_speed_kmh": rail_commercial_speed_kmh,
        "rail_yard_detention_hours": rail_yard_detention_hours,
        "dfc_handling_cost": dfc_handling_cost,
        "dfc_commercial_speed_kmh": dfc_commercial_speed_kmh,
        "dfc_yard_transfer_hours": dfc_yard_transfer_hours,
    }
    override_kwargs.update({key: value for key, value in additional_overrides.items() if value is not None})

    if override_kwargs:
        params = CostParametersOverride(**override_kwargs)

    req = FreightCostSimulationRequest(
        origin_district=origin_district,
        origin_district_code=origin_district_code,
        origin_state=origin_state,
        target_port=target_port,
        payload_tons=payload_tons,
        custom_parameters=params
    )
    store = get_data_store()
    res = _domain_call(simulate_freight_cost, req=req, store=store)
    return FreightCostSimulationResponse.model_validate(res).model_dump(mode="json")


def find_nearest(
    latitude: float,
    longitude: float,
    top_k: int = 3
) -> Dict[str, Any]:
    """
    Geodesic WGS84 distance search finding the closest infrastructure across all multi-modal
    categories (Ports, ICDs, MMLPs, Air Cargo, Waterways, Cold Storages, Mandis, Tolls).
    
    Example:
        >>> nearest = gis.find_nearest(latitude=28.6139, longitude=77.2090, top_k=2)
        >>> print(nearest["ports"], nearest["toll_plazas"])
    """
    query = NearestFacilitiesRequest(latitude=latitude, longitude=longitude, top_k=top_k)
    store = get_data_store()
    return serialize_nearest_facilities(_domain_call(
        get_nearest_hubs, latitude=query.latitude, longitude=query.longitude,
        top_k=query.top_k, store=store,
    ))


def simulate_port_catchment(
    alpha: float = 0.85,
    beta: float = 1.65,
    custom_port_capacities: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """
    Simulate national port hinterland market share and captured population across
    all 12 Major Commercial Ports using the Huff/Reilly gravity model.

    Args:
        custom_port_capacities: optional capacity overrides in MT keyed by port name,
            e.g. {"Paradip Port": 160.0}, as on the REST endpoint
    """
    req = PortGravitySimulationRequest(alpha=alpha, beta=beta, custom_port_capacities=custom_port_capacities)
    store = get_data_store()
    res = _domain_call(simulate_port_gravity, req=req, store=store)
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

    Same implementation, file names and result keys as the MCP tool
    ``gis_plot_villages_map``.

    Args:
        state: State name (e.g. 'Haryana', 'Maharashtra'); spelling variants are accepted
        district: District name (e.g. 'Ambala', 'Pune')
        metric: one of 'dist_rail_station_km', 'dist_nh_km', 'dist_icd_km',
            'dist_freight_terminal_km', 'dist_port_km', 'dist_air_cargo_km',
            'dist_mmlp_km', 'dist_toll_plaza_km'
        output_format: 'html', 'png', or 'both'
        output_path: Optional destination ending in .html or .png (with 'both', the
            other file takes the same name with the other suffix); default
            ``<GIS4LOGISTICS_OUTPUT_PATH>/maps/<state>_<district>_village_<metric>_map.<ext>``

    Returns:
        {"status", "state", "district", "metric", "villages_count",
         "outputs": {"html_map_path", "png_map_path"}} (keys for the formats written)
    """
    _village_maps.village_map_paths(state, district, metric, output_format, settings.OUTPUT_PATH / "maps", output_path)
    _village_maps.validate_metric(metric)
    _ensure_data()
    state = _domain_call(canonical_state, state)
    _domain_call(validate_village_map_paths, _village_maps.DATA_DIR, state)
    return _village_maps.render_village_map(state, district, metric, output_format,
                                            output_dir=settings.OUTPUT_PATH / "maps", output_path=output_path)
