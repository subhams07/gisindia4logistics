"""
tests/test_server.py
Comprehensive test suite for GIS4Logistics FastAPI Server and MCP Tools.
"""

from fastapi.testclient import TestClient
from server.app import app
from mcp_server.server import handle_rpc_request

client = TestClient(app)


def test_root_endpoint():
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "operational"
    assert data["datasets_online"]["states_count"] == 36
    assert data["datasets_online"]["districts_count"] == 781


def test_admin_states():
    res = client.get("/api/v1/admin/states")
    assert res.status_code == 200
    states = res.json()
    assert len(states) == 36
    mh = [s for s in states if s["state"] == "Maharashtra"]
    assert len(mh) == 1
    assert mh[0]["district_count"] == 37


def test_admin_districts():
    res = client.get("/api/v1/admin/districts?state=Haryana")
    assert res.status_code == 200
    districts = res.json()
    assert len(districts) == 22


def test_district_scorecard():
    res = client.get("/api/v1/admin/districts/Pune")
    assert res.status_code == 200
    sc = res.json()
    assert sc["district"].upper() == "PUNE"
    assert sc["state"].lower() == "maharashtra"
    assert sc["pop_2011"] > 9000000
    assert sc["nearest_highway_km"] is not None
    assert sc["nearest_port"]["name"] is not None


def test_hubs_query():
    res = client.get("/api/v1/hubs?hub_type=ports")
    assert res.status_code == 200
    ports = res.json()
    assert len(ports) >= 12


def test_nearest_hubs():
    # Query coordinates for New Delhi (28.6139, 77.2090)
    res = client.get("/api/v1/hubs/nearest?latitude=28.6139&longitude=77.2090&top_k=2")
    assert res.status_code == 200
    nearest = res.json()
    assert "ports" in nearest
    assert "toll_plazas" in nearest
    assert len(nearest["toll_plazas"]) == 2


def test_rail_stations_and_dfc():
    res = client.get("/api/v1/hubs/rail/stations?search=Pune")
    assert res.status_code == 200
    stations = res.json()
    assert len(stations) >= 1
    assert any(s["station_code"] == "PUNE" for s in stations)

    res_dfc = client.get("/api/v1/hubs/rail/dfc")
    assert res_dfc.status_code == 200
    dfc = res_dfc.json()
    assert dfc["stations_count"] >= 50
    assert len(dfc["corridors"]) == 3


def test_highway_routing():
    # Route between Pune (18.5204, 73.8567) and Navi Mumbai / JNPT (18.9500, 72.9500)
    payload = {
        "origin": [18.5204, 73.8567],
        "destination": [18.9500, 72.9500],
        "vehicle_type": "MAV_20T"
    }
    res = client.post("/api/v1/route/highway", json=payload)
    assert res.status_code == 200
    route = res.json()
    assert 80.0 <= route["distance_km"] <= 180.0
    assert route["drive_time_hours"] > 0
    assert route["estimated_toll_cost_inr"] >= 0


def test_freight_cost_simulation():
    payload = {
        "origin_district": "Pune",
        "payload_tons": 25.0,
        "custom_parameters": {
            "road_linehaul_rate": 3.80,
            "toll_cost_per_plaza": 400.0,
            "rail_base_class_rate": 1.40,
            "dfc_linehaul_rate": 1.05
        }
    }
    res = client.post("/api/v1/simulate/freight-cost", json=payload)
    assert res.status_code == 200
    sim = res.json()
    assert sim["origin_district"].upper() == "PUNE"
    assert sim["road"]["cost_per_ton_inr"] > 0
    assert sim["conventional_rail"]["cost_per_ton_inr"] > 0
    assert sim["optimal_mode"] in ["Road Trucking", "Conventional Rail", "Dedicated Freight Corridor (DFC)"]


def test_port_gravity_simulation():
    payload = {
        "alpha": 0.90,
        "beta": 1.70
    }
    res = client.post("/api/v1/simulate/port-gravity", json=payload)
    assert res.status_code == 200
    shares = res.json()
    assert len(shares) >= 10
    total_pct = sum(item["market_share_districts_pct"] for item in shares)
    assert 99.0 <= total_pct <= 101.0


def test_mcp_tools_rpc():
    # 1. tools/list
    req_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    res_list = handle_rpc_request(req_list)
    assert "result" in res_list
    tools = res_list["result"]["tools"]
    assert len(tools) == 5

    # 2. tools/call gis_get_district_scorecard
    req_call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "gis_get_district_scorecard",
            "arguments": {"district_name_or_code": "Pune"}
        }
    }
    res_call = handle_rpc_request(req_call)
    assert "result" in res_call
    content = res_call["result"]["content"][0]["text"]
    assert "maharashtra" in content.lower()
