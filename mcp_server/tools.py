"""
mcp_server/tools.py
High-level GIS4Logistics tool implementations callable by MCP servers and AI agents.
"""

from contextlib import redirect_stdout
import sys
from typing import Any
from server.models.phase1 import HighwayRoutePlanRequest

from server.config import settings
from server.dependencies import DataStore
from server.models.schemas import (
    CostParametersOverride,
    FreightCostSimulationRequest,
    FreightCostSimulationResponse,
    PortGravitySimulationRequest,
    RouteRequest,
)
from server.routers.admin import canonical_state, get_district_scorecard, validate_village_map_paths
from server.routers.hubs import get_nearest_hubs
from server.services.routing_facade import legacy_highway_route, plan_highway_route
from server.routers.simulation import simulate_freight_cost, simulate_port_gravity
from server.services.serialization import serialize_result, serialize_nearest_facilities


class _LazyDataStore:
    """Compatibility view of tools.store without retaining a singleton snapshot."""

    def __getattr__(self, name):
        return getattr(DataStore.get_instance(), name)

    def __setattr__(self, name, value):
        setattr(DataStore.get_instance(), name, value)


# Existing attribute access and explicit tools.store injection remain supported.
# No data (including the highway graph) is loaded while importing MCP tools.
store = _LazyDataStore()


def _get_store() -> DataStore:
    return DataStore.get_instance() if isinstance(store, _LazyDataStore) else store


def serialize_tool_result(value: Any) -> Any:
    """Recursively encode router dicts/lists and nested Pydantic JSON values."""
    return serialize_result(value)

def tool_get_district_scorecard(district_name_or_code: str, state: str | None = None) -> dict[str, Any]:
    """
    Get a comprehensive logistics scorecard for an Indian district.
    Returns demographic population, nearest highway distance, nearest toll plaza,
    nearest railway station, nearest ICD/port/MMLP, and village accessibility shares.
    """
    kwargs = {"code_or_name": district_name_or_code, "store": _get_store()}
    if state is not None:
        kwargs["state"] = state
    return serialize_result(
        get_district_scorecard(**kwargs),
        missing_nan=True,
    )


def tool_calculate_intermodal_freight_cost(
    origin_district: str | None = None,
    target_port: str | None = None,
    payload_tons: float = 20.0,
    road_linehaul_rate: float | None = None,
    toll_cost_per_plaza: float | None = None,
    rail_base_class_rate: float | None = None,
    dfc_linehaul_rate: float | None = None,
    inventory_holding_rate: float | None = None,
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
    commodity_name: str | None = "General Merchandise",
) -> dict[str, Any]:
    """
    Simulates and compares end-to-end generalized freight costs across:
    1. Road Trucking (Multi-Axle Vehicle on National Highways)
    2. Conventional Indian Railways Freight (Telescopic Tariff)
    3. Dedicated Freight Corridor (DFC Heavy-Haul Rail)
    
    Shipment quantity scales totals, not vehicle capacity. Only
    truck_payload_tons changes capacity used for per-tonne toll allocation.
    Returns cost per tonne, total shipment outlay, transit hours, optimal mode,
    financial savings, and break-even distance.
    """
    arguments = locals()
    overrides = {name: arguments[name] for name in CostParametersOverride.model_fields
                 if arguments[name] is not None}
    custom_params = CostParametersOverride.model_validate(overrides, strict=True) if overrides else None
    req = FreightCostSimulationRequest.model_validate({
        "origin_district": origin_district,
        "origin_district_code": origin_district_code,
        "origin_state": origin_state,
        "target_port": target_port,
        "payload_tons": payload_tons,
        "commodity_name": commodity_name,
        "custom_parameters": custom_params,
    }, strict=True)
    res = simulate_freight_cost(req=req, store=_get_store())
    encoded = serialize_tool_result(res)
    return FreightCostSimulationResponse.model_validate(encoded).model_dump(mode="json")


def tool_find_nearest_facilities(latitude: float, longitude: float, top_k: int = 3) -> dict[str, Any]:
    """
    Finds the nearest logistics infrastructure (Sea Ports, ICDs, MMLPs, Air Cargo,
    Inland Waterway Terminals, Cold Storage, APMC Mandis, and Toll Plazas) to any coordinate.
    """
    if not -90.0 <= latitude <= 90.0:
        raise ValueError("latitude must be between -90 and 90")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError("longitude must be between -180 and 180")
    if not 1 <= top_k <= 10:
        raise ValueError("top_k must be between 1 and 10")
    return serialize_nearest_facilities(
        get_nearest_hubs(latitude=latitude, longitude=longitude, top_k=top_k, store=_get_store()),
    )


def tool_highway_route_and_tolls(
    origin_lat: float, origin_lon: float,
    dest_lat: float, dest_lon: float,
    vehicle_type: str = "MAV_20T"
) -> dict[str, Any]:
    """
    Calculates commercial highway driving route metrics: total road distance in km,
    driving hours and modelled distance-based toll counts/outlay. Counts are not
    encountered plazas; vehicle rates are not official FASTag tariffs.
    """
    req = RouteRequest.model_validate({
        "origin": [origin_lat, origin_lon],
        "destination": [dest_lat, dest_lon],
        "vehicle_type": vehicle_type,
    }, strict=True)
    res = legacy_highway_route(req=req, store=_get_store())
    return serialize_tool_result(res)


def tool_plan_highway_route(
    origin: dict[str, Any], destination: dict[str, Any], vehicle_type: str = "MAV_20T",
    include_route_geometry: bool = True, geometry_detail: str = "none",
) -> dict[str, Any]:
    """Canonical strategic route with quality/provenance, without toll claims."""
    request = HighwayRoutePlanRequest.model_validate({
        "origin": origin, "destination": destination, "vehicle_type": vehicle_type,
        "include_route_geometry": include_route_geometry, "geometry_detail": geometry_detail,
    })
    return serialize_result(plan_highway_route(request=request, store=_get_store()))


def tool_simulate_port_catchment(alpha: float = 0.85, beta: float = 1.65) -> list[dict[str, Any]]:
    """
    Simulates national port hinterland market share and captured population across
    all 12 Major Commercial Ports using the Huff/Reilly gravity model.
    """
    req = PortGravitySimulationRequest(alpha=alpha, beta=beta)
    res = simulate_port_gravity(req=req, store=_get_store())
    return serialize_tool_result(res)


def tool_plot_villages_map(
    state: str,
    district: str,
    metric: str = "dist_rail_station_km",
    output_format: str = "html"
) -> dict[str, Any]:
    """
    Generates an interactive Leaflet HTML map or high-resolution PNG map
    displaying all villages in a district colored by accessibility metric.
    """
    from scripts.analyze.plot_villages import (
        DATA_DIR,
        generate_leaflet_html,
        load_district_villages_gdf,
        plot_villages_static,
        safe_filename_component,
        validate_metric,
    )

    if output_format not in {"html", "png", "both"}:
        raise ValueError("output_format must be one of: html, png, both")
    validate_metric(metric)
    state = canonical_state(state)
    validate_village_map_paths(DATA_DIR, state)
    
    gdf = load_district_villages_gdf(state=state, district=district)
    out_files = {}
    output_dir = settings.OUTPUT_PATH / "maps"
    output_dir.mkdir(parents=True, exist_ok=True)
    district_slug = safe_filename_component(district)
    state_slug = safe_filename_component(state)

    if output_format in ["html", "both"]:
        html_code = generate_leaflet_html(gdf=gdf, state=state, district=district, metric=metric)
        html_p = output_dir / f"{state_slug}_{district_slug}_village_{metric}_map.html"
        with open(html_p, "w", encoding="utf-8") as f:
            f.write(html_code)
        out_files["html_map_path"] = str(html_p)

    if output_format in ["png", "both"]:
        png_p = output_dir / f"{state_slug}_{district_slug}_village_{metric}_map.png"
        with redirect_stdout(sys.stderr):
            plot_villages_static(gdf=gdf, state=state, district=district, metric=metric, output_png=png_p)
        out_files["png_map_path"] = str(png_p)

    return {
        "status": "success",
        "state": state,
        "district": district,
        "villages_count": len(gdf),
        "metric_plotted": metric,
        "outputs": out_files
    }
