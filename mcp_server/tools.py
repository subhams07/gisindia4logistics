"""
mcp_server/tools.py
High-level GIS4Logistics tool implementations callable by MCP servers and AI agents.
"""

from functools import lru_cache
from pathlib import Path
import re
from typing import Any

import pandas as pd
from server.models.phase1 import HighwayRoutePlanRequest

from gisindia4logistics.data_root import DataNotFoundError
from scripts.clean.standardize import clean_state_name
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


def tool_simulate_port_catchment(alpha: float = 0.85, beta: float = 1.65,
                                 custom_port_capacities: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """
    Simulates national port hinterland market share and captured population across
    all 12 Major Commercial Ports using the Huff/Reilly gravity model; optional
    per-port capacity overrides (MT), as on the REST endpoint.
    """
    req = PortGravitySimulationRequest(alpha=alpha, beta=beta, custom_port_capacities=custom_port_capacities)
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
    Same implementation, file names and result keys as the SDK's plot_villages.
    """
    from scripts.analyze import plot_villages

    plot_villages.village_map_paths(state, district, metric, output_format, settings.OUTPUT_PATH / "maps")
    plot_villages.validate_metric(metric)
    state = canonical_state(state)
    validate_village_map_paths(plot_villages.DATA_DIR, state)
    return plot_villages.render_village_map(state, district, metric, output_format,
                                            output_dir=settings.OUTPUT_PATH / "maps")


def _tools_data_dir() -> Path:
    if not isinstance(store, _LazyDataStore) and hasattr(store, "data_dir"):
        return Path(store.data_dir)
    if DataStore._instance is not None:
        return Path(DataStore._instance.data_dir)
    return Path(settings.DATA_PATH)


@lru_cache(maxsize=16)
def _cached_csv_records(path_str: str, mtime_ns: int, str_cols: tuple[str, ...]) -> pd.DataFrame:
    dtype_map = {col: "string" for col in str_cols}
    if str_cols:
        return pd.read_csv(path_str, dtype=dtype_map, keep_default_na=True)
    return pd.read_csv(path_str)


def _read_data_csv(rel_path: str, str_cols: tuple[str, ...] = ()) -> pd.DataFrame:
    path = _tools_data_dir() / rel_path
    if not path.is_file():
        raise DataNotFoundError(path)
    return _cached_csv_records(str(path), path.stat().st_mtime_ns, str_cols).copy()


def _filter_text_column(df: pd.DataFrame, col: str, query: str, *, allow_substring: bool = True) -> pd.DataFrame:
    q = query.strip()
    if not q or col not in df.columns:
        return df
    vals = df[col].fillna("").astype(str).str.strip().str.casefold()
    q_cf = q.casefold()
    exact = df[vals == q_cf]
    if not exact.empty or not allow_substring:
        return exact
    return df[vals.str.contains(re.escape(q_cf), regex=True, na=False)]


def _filter_state_column(df: pd.DataFrame, query: str | None) -> pd.DataFrame:
    if not query or not query.strip():
        return df
    q = query.strip()
    canon = clean_state_name(q) or q
    vals = df["state"].fillna("").astype(str).str.strip().str.casefold()
    return df[vals == canon.casefold()]


def tool_get_subdistrict_profile(
    state: str | None = None,
    district: str | None = None,
    sub_district: str | None = None,
    sub_district_code: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Query sub-district infrastructure access metrics and optional LGD directory linkage.

    Preserves alphanumeric SoI ABDB carveout codes (e.g. '00285N001', '07314NOO1')
    and requires at least one location filter with bounded result size.
    """
    if not any(isinstance(v, str) and v.strip() for v in (state, district, sub_district, sub_district_code)):
        raise ValueError("Provide at least one filter: state, district, sub_district, or sub_district_code")
    if not 1 <= int(limit) <= 100:
        raise ValueError("limit must be between 1 and 100")

    df = _read_data_csv(
        "analysis/india_subdistrict_access.csv",
        str_cols=("district_code", "sub_district_code"),
    )
    filtered = _filter_state_column(df, state)
    if district and district.strip():
        filtered = _filter_text_column(filtered, "district", district, allow_substring=True)
    if sub_district and sub_district.strip():
        filtered = _filter_text_column(filtered, "sub_district", sub_district, allow_substring=True)
    if sub_district_code and sub_district_code.strip():
        q_code = sub_district_code.strip()
        if re.fullmatch(r"[0-9]+", q_code):
            q_code = str(int(q_code))
        codes = filtered["sub_district_code"].fillna("").astype(str).str.strip()
        filtered = filtered[codes.str.casefold() == q_code.casefold()]

    total_matches = int(len(filtered))
    sliced = filtered.sort_values(["state", "district", "sub_district"], kind="mergesort").head(int(limit)).copy()

    # Attach official LGD sub-district name & Census 2011 code on matching string codes
    lgd_path = _tools_data_dir() / "administrative" / "lgd_subdistricts.csv"
    if lgd_path.is_file() and not sliced.empty:
        lgd = _cached_csv_records(
            str(lgd_path), lgd_path.stat().st_mtime_ns, ("sub_district_code", "census2011_code")
        )
        lgd_map = lgd.set_index("sub_district_code")[["sub_district_name", "census2011_code"]].to_dict("index")
        extras = [lgd_map.get(str(c).strip() if pd.notna(c) else "") for c in sliced["sub_district_code"]]
        sliced["lgd_subdistrict_name"] = [e["sub_district_name"] if e else None for e in extras]
        sliced["census2011_code"] = [e["census2011_code"] if e else None for e in extras]

    records = serialize_result(sliced.to_dict(orient="records"), missing_nan=True)
    return {
        "total_matches": total_matches,
        "returned": len(records),
        "truncated": total_matches > len(records),
        "subdistricts": records,
    }


def tool_get_subdistrict_census(
    state: str | None = None,
    district: str | None = None,
    sub_district: str | None = None,
    census_subdistrict_code: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Query Census 2011 sub-district PCA & Table A-01 demographic/area indicators (ORGI GODL-India)."""
    if not any(isinstance(v, str) and v.strip() for v in (state, district, sub_district, census_subdistrict_code)):
        raise ValueError("Provide at least one filter: state, district, sub_district, or census_subdistrict_code")
    if not 1 <= int(limit) <= 100:
        raise ValueError("limit must be between 1 and 100")

    df = _read_data_csv(
        "demographic/census2011_subdistrict_key_indicators.csv",
        str_cols=("census_state_code", "census_district_code", "census_subdistrict_code"),
    )
    filtered = _filter_state_column(df, state)
    if district and district.strip():
        filtered = _filter_text_column(filtered, "district", district, allow_substring=True)
    if sub_district and sub_district.strip():
        filtered = _filter_text_column(filtered, "sub_district", sub_district, allow_substring=True)
    if census_subdistrict_code and census_subdistrict_code.strip():
        q_code = census_subdistrict_code.strip()
        if re.fullmatch(r"[0-9]+", q_code):
            q_code = q_code.zfill(5)
        codes = filtered["census_subdistrict_code"].fillna("").astype(str).str.strip()
        filtered = filtered[codes == q_code]

    total_matches = int(len(filtered))
    sliced = filtered.sort_values(
        ["population", "census_state_code", "census_district_code", "census_subdistrict_code"],
        ascending=[False, True, True, True],
        kind="mergesort",
    ).head(int(limit))
    records = serialize_result(sliced.to_dict(orient="records"), missing_nan=True)
    return {
        "total_matches": total_matches,
        "returned": len(records),
        "truncated": total_matches > len(records),
        "source": "ORGI Census 2011 PCA & Table A-01 (GODL-India)",
        "records": records,
    }


FREIGHT_SERIES_FILES = {
    "rail": "freight/rail_freight_annual.csv",
    "port": "freight/port_throughput_annual.csv",
    "road": "freight/road_indicators_annual.csv",
}


def tool_get_freight_statistics(
    series: str = "all",
    fy: str | None = None,
    entity_code: str | None = None,
    commodity_group: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Query reconciled annual rail freight, major port throughput, and MoRTH road statistics."""
    if series not in ("all", "rail", "port", "road"):
        raise ValueError("series must be one of 'all', 'rail', 'port', 'road'")
    if not 1 <= int(limit) <= 200:
        raise ValueError("limit must be between 1 and 200")

    targets = list(FREIGHT_SERIES_FILES.items()) if series == "all" else [(series, FREIGHT_SERIES_FILES[series])]
    frames = []
    for name, rel_path in targets:
        part = _read_data_csv(rel_path)
        part.insert(0, "series", name)
        frames.append(part)
    df = pd.concat(frames, ignore_index=True)

    if fy and fy.strip():
        df = _filter_text_column(df, "fy", fy, allow_substring=False)
    if entity_code and entity_code.strip():
        df = _filter_text_column(df, "entity_code", entity_code, allow_substring=False)
    if commodity_group and commodity_group.strip():
        df = _filter_text_column(df, "commodity_group", commodity_group, allow_substring=True)

    total_matches = int(len(df))
    sliced = df.sort_values(
        ["fy", "series", "entity_code", "commodity_group"],
        ascending=[False, True, True, True],
        kind="mergesort",
    ).head(int(limit))
    records = serialize_result(sliced.to_dict(orient="records"), missing_nan=True)
    return {
        "total_matches": total_matches,
        "returned": len(records),
        "truncated": total_matches > len(records),
        "records": records,
    }
