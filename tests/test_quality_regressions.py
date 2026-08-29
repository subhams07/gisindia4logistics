"""Regression tests for correctness and public-interface hardening."""

from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from mcp_server.server import handle_rpc_request
from scripts.analyze.plot_villages import generate_leaflet_html
from server.models.schemas import FreightCostSimulationRequest, RouteRequest
from server.routers.admin import get_district_scorecard
from server.routers.hubs import geodesic_distances_km, list_hubs
from server.routers.simulation import simulate_freight_cost


def test_scorecard_exposes_ten_km_value_and_deprecated_five_km_alias():
    store = SimpleNamespace(
        districts_df=pd.DataFrame(
            [{"state": "Maharashtra", "district": "Pune", "district_code": 521, "pop_2011": 9_429_408}]
        ),
        travel_time_df=None,
        district_access_df=pd.DataFrame(
            [
                {
                    "state": "Maharashtra",
                    "district": "Pune",
                    "rail_station_within_10km_pct": 42.5,
                    "rail_station_within_25km_pct": 73.0,
                    "icd_within_50km_pct": 61.0,
                }
            ]
        ),
    )

    scorecard = get_district_scorecard("Pune", store=store)

    assert scorecard["share_villages_within_10km_rail"] == 42.5
    assert scorecard["share_villages_within_5km_rail"] == 42.5


def test_unknown_hub_type_is_rejected():
    store = SimpleNamespace(hubs_dict={"ports": pd.DataFrame()})

    with pytest.raises(HTTPException) as exc:
        list_hubs(hub_type="unknown", state=None, limit=100, store=store)

    assert exc.value.status_code == 400


def test_target_port_changes_distance_and_time_used_by_freight_model():
    store = SimpleNamespace(
        travel_time_df=pd.DataFrame(
            [
                {
                    "state": "Maharashtra",
                    "district": "Pune",
                    "district_code": 521,
                    "nearest_port_name": "Jawaharlal Nehru Port (JNPT)",
                    "port_road_distance_km": 150.0,
                    "port_drive_time_hours": 3.0,
                    "freight_terminal_road_distance_km": 30.0,
                    "is_island": False,
                }
            ]
        ),
        port_matrix_df=pd.DataFrame(
            [
                {
                    "state": "Maharashtra",
                    "district": "Pune",
                    "district_code": 521,
                    "drive_hours_to_jawaharlal_nehru_(jnpt/navi_mumbai)": 3.0,
                    "road_km_to_jawaharlal_nehru_(jnpt/navi_mumbai)": 150.0,
                    "drive_hours_to_paradip": 20.0,
                    "road_km_to_paradip": 1_250.0,
                }
            ]
        ),
    )
    request = FreightCostSimulationRequest(origin_district="Pune", target_port="Paradip Port")

    result = simulate_freight_cost(request, store=store)

    assert result["target_port"] == "Paradip Port"
    assert result["road_distance_km"] == 1_250.0
    assert result["road"].transit_time_hours == 20.0


def test_island_freight_simulation_is_rejected():
    store = SimpleNamespace(
        travel_time_df=pd.DataFrame(
            [
                {
                    "state": "Andaman and Nicobar Islands",
                    "district": "Nicobars",
                    "district_code": 638,
                    "is_island": True,
                    "port_road_distance_km": None,
                }
            ]
        ),
        port_matrix_df=pd.DataFrame(),
    )
    request = FreightCostSimulationRequest(origin_district="Nicobars")

    with pytest.raises(HTTPException) as exc:
        simulate_freight_cost(request, store=store)

    assert exc.value.status_code == 422
    assert "island" in exc.value.detail.lower()


def test_geodesic_distance_is_used_for_nearest_facilities():
    distances = geodesic_distances_km(
        longitude=73.8567,
        latitude=18.5204,
        coordinates=[[72.8777, 19.0760]],
    )

    assert 115.0 < distances[0] < 125.0


@pytest.mark.parametrize(
    "payload",
    [
        {"origin": [95.0, 73.0], "destination": [19.0, 72.0]},
        {"origin": [18.0, 181.0], "destination": [19.0, 72.0]},
        {"origin": [18.0, 73.0], "destination": [19.0, 72.0], "vehicle_type": "SPACESHIP"},
    ],
)
def test_route_request_rejects_invalid_coordinates_and_vehicle_types(payload):
    with pytest.raises(ValidationError):
        RouteRequest(**payload)


def test_freight_request_rejects_non_positive_payload():
    with pytest.raises(ValidationError):
        FreightCostSimulationRequest(origin_district="Pune", payload_tons=0)


def test_cost_override_rejects_negative_assignment():
    from server.models.schemas import CostParametersOverride
    override = CostParametersOverride()
    with pytest.raises(ValidationError):
        override.road_linehaul_rate = -100.0


def test_nearest_request_rejects_out_of_bounds():
    from server.models.schemas import NearestFacilitiesRequest
    with pytest.raises(ValidationError):
        NearestFacilitiesRequest(latitude=95.0, longitude=73.0, top_k=3)
    with pytest.raises(ValidationError):
        NearestFacilitiesRequest(latitude=18.0, longitude=73.0, top_k=0)


def test_mcp_initialize_and_ping_are_supported():
    initialized = handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2099-01-01"},
        }
    )
    ping = handle_rpc_request({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": None})
    notification = handle_rpc_request({"jsonrpc": "2.0", "method": "notifications/initialized"})

    assert initialized["result"]["protocolVersion"] == "2024-11-05"
    assert initialized["result"]["serverInfo"]["name"] == "gisindia4logistics"
    assert "tools" in initialized["result"]["capabilities"]
    assert ping["result"] == {}
    assert notification is None


def test_mcp_tool_failure_returns_is_error():
    response = handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "gis_get_district_scorecard", "arguments": {}},
        }
    )

    assert "result" in response
    assert response["result"]["isError"] is True
    assert "Error:" in response["result"]["content"][0]["text"]


def test_map_path_traversal_is_rejected():
    from scripts.analyze.plot_villages import load_district_villages_gdf
    with pytest.raises(Exception):
        load_district_villages_gdf(state="../../../../tmp/evil", district="test")


def test_map_metric_must_be_allowlisted_before_rendering():
    empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")

    with pytest.raises(ValueError, match="Unsupported accessibility metric"):
        generate_leaflet_html(empty, state="Haryana", district="Ambala", metric="__proto__")
