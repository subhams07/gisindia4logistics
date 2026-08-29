"""
mcp_server/server.py
Model Context Protocol (MCP) stdio server for Antigravity, Codex, and AI assistants.
Exposes GIS4Logistics datasets, routing, and simulation tools via JSON-RPC.
"""

import sys
import json
import logging
from typing import Dict, Any, Optional

from mcp_server.tools import (
    tool_get_district_scorecard,
    tool_calculate_intermodal_freight_cost,
    tool_find_nearest_facilities,
    tool_highway_route_and_tolls,
    tool_simulate_port_catchment
)

LOGGER = logging.getLogger(__name__)
MCP_PROTOCOL_VERSION = "2024-11-05"

TOOLS_METADATA = [
    {
        "name": "gis_get_district_scorecard",
        "description": "Get a comprehensive logistics and accessibility scorecard for an Indian district (demographics, nearest NH, toll plaza, railway station, ICD, port, MMLP, and village accessibility shares).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "district_name_or_code": {
                    "type": "string",
                    "description": "District name (e.g. 'Pune', 'Indore', 'Ludhiana') or LGD code (e.g. '521')"
                }
            },
            "required": ["district_name_or_code"]
        }
    },
    {
        "name": "gis_calculate_intermodal_freight_cost",
        "description": "Simulates and compares generalized freight costs (INR/tonne and total shipment cost) across Road Trucking, Conventional Rail, and Dedicated Freight Corridor (DFC) with custom economic parameter overrides.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "origin_district": {
                    "type": "string",
                    "description": "Origin district name (e.g. 'Pune', 'Kanpur', 'Ahmedabad')"
                },
                "target_port": {
                    "type": "string",
                    "description": "Optional Major Port destination represented in the district-port matrix (e.g. 'Jawaharlal Nehru Port (JNPT)' or 'Paradip Port')"
                },
                "payload_tons": {
                    "type": "number",
                    "description": "Cargo payload in metric tonnes (default: 20.0)"
                },
                "road_linehaul_rate": {
                    "type": "number",
                    "description": "Road trucking linehaul rate INR/tonne-km (default: 3.30)"
                },
                "toll_cost_per_plaza": {
                    "type": "number",
                    "description": "Average toll fee per plaza in INR (default: 340.0)"
                },
                "rail_base_class_rate": {
                    "type": "number",
                    "description": "Indian Railways goods tariff base rate factor (default: 1.55)"
                },
                "dfc_linehaul_rate": {
                    "type": "number",
                    "description": "Dedicated Freight Corridor heavy-haul rate INR/tonne-km (default: 1.12)"
                },
                "inventory_holding_rate": {
                    "type": "number",
                    "description": "Cargo time value and working capital cost INR/tonne-hour (default: 7.50)"
                }
            },
            "required": ["origin_district"]
        }
    },
    {
        "name": "gis_find_nearest_facilities",
        "description": "Finds nearest multi-modal logistics infrastructure (Sea Ports, ICDs, MMLPs, Air Cargo, Inland Waterway Terminals, Cold Storages, APMC Mandis, and Toll Plazas) to any coordinate in India.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "minimum": -90, "maximum": 90, "description": "Latitude coordinate (e.g. 18.5204)"},
                "longitude": {"type": "number", "minimum": -180, "maximum": 180, "description": "Longitude coordinate (e.g. 73.8567)"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Number of nearest facilities per category (default: 3)"}
            },
            "required": ["latitude", "longitude"]
        }
    },
    {
        "name": "gis_highway_route_and_tolls",
        "description": "Estimates strategic National Highway graph distance, driving hours, and distance-based FASTag toll expense between two points; not turn-by-turn navigation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "origin_lat": {"type": "number", "description": "Origin latitude"},
                "origin_lon": {"type": "number", "description": "Origin longitude"},
                "dest_lat": {"type": "number", "description": "Destination latitude"},
                "dest_lon": {"type": "number", "description": "Destination longitude"},
                "vehicle_type": {
                    "type": "string",
                    "description": "Vehicle class: 'MAV_20T', 'LMV', '2_AXLE_TRUCK' (default: 'MAV_20T')"
                }
            },
            "required": ["origin_lat", "origin_lon", "dest_lat", "dest_lon"]
        }
    },
    {
        "name": "gis_simulate_port_catchment",
        "description": "Simulates national port hinterland market share and captured population across all 12 Major Commercial Ports using the Huff/Reilly gravity model.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "alpha": {"type": "number", "description": "Port throughput capacity sensitivity exponent (default: 0.85)"},
                "beta": {"type": "number", "description": "Highway drive-time distance decay friction exponent (default: 1.65)"}
            }
        }
    },
    {
        "name": "gis_plot_villages_map",
        "description": "Generates an interactive Leaflet HTML map or high-resolution PNG cartographic map of all villages in an Indian district, color-coded by accessibility metrics (railway, highway, ICD, port, or toll plaza proximity).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "State name, e.g. 'Haryana', 'Maharashtra'"},
                "district": {"type": "string", "description": "District name, e.g. 'Ambala', 'Pune'"},
                "metric": {
                    "type": "string",
                    "enum": ["dist_rail_station_km", "dist_nh_km", "dist_icd_km", "dist_freight_terminal_km", "dist_port_km", "dist_air_cargo_km", "dist_mmlp_km", "dist_toll_plaza_km"],
                    "description": "Accessibility metric: 'dist_rail_station_km', 'dist_nh_km', 'dist_icd_km', 'dist_freight_terminal_km', 'dist_port_km', 'dist_toll_plaza_km' (default: 'dist_rail_station_km')"
                },
                "output_format": {
                    "type": "string",
                    "enum": ["html", "png", "both"],
                    "description": "Output format: 'html', 'png', 'both' (default: 'html')"
                }
            },
            "required": ["state", "district"]
        }
    }
]


SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05"}


def handle_rpc_request(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": req.get("id") if isinstance(req, dict) else None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }

    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    # JSON-RPC Notification: notifications have no id and must not receive a response
    if req_id is None:
        if method == "notifications/initialized":
            LOGGER.info("MCP client initialized session")
        return None

    if method == "initialize":
        requested_version = params.get("protocolVersion")
        # Enforce supported protocol version
        negotiated_version = MCP_PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": negotiated_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "gisindia4logistics", "version": "1.0.0"},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS_METADATA}
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments") or {}

        try:
            if tool_name == "gis_get_district_scorecard":
                res = tool_get_district_scorecard(district_name_or_code=str(args["district_name_or_code"]))
            elif tool_name == "gis_calculate_intermodal_freight_cost":
                res = tool_calculate_intermodal_freight_cost(**args)
            elif tool_name == "gis_find_nearest_facilities":
                res = tool_find_nearest_facilities(
                    latitude=float(args["latitude"]),
                    longitude=float(args["longitude"]),
                    top_k=int(args.get("top_k", 3))
                )
            elif tool_name == "gis_highway_route_and_tolls":
                res = tool_highway_route_and_tolls(
                    origin_lat=float(args["origin_lat"]),
                    origin_lon=float(args["origin_lon"]),
                    dest_lat=float(args["dest_lat"]),
                    dest_lon=float(args["dest_lon"]),
                    vehicle_type=str(args.get("vehicle_type", "MAV_20T"))
                )
            elif tool_name == "gis_simulate_port_catchment":
                res = tool_simulate_port_catchment(
                    alpha=float(args.get("alpha", 0.85)),
                    beta=float(args.get("beta", 1.65))
                )
            elif tool_name == "gis_plot_villages_map":
                from mcp_server.tools import tool_plot_villages_map
                res = tool_plot_villages_map(
                    state=str(args["state"]),
                    district=str(args["district"]),
                    metric=str(args.get("metric", "dist_rail_station_km")),
                    output_format=str(args.get("output_format", "html"))
                )
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"}
                }

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(res, indent=2)}]}
            }
        except Exception as e:
            LOGGER.exception("MCP tool call failed: %s", tool_name)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                    "isError": True,
                }
            }

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not supported"}
        }


def run_stdio_server():
    """Reads JSON-RPC lines from stdin and writes responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            res = handle_rpc_request(req)
            if res is not None:
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()
        except Exception:
            LOGGER.exception("Failed to parse MCP request")
            err_res = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"}
            }
            sys.stdout.write(json.dumps(err_res) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_server()
