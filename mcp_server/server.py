"""
mcp_server/server.py
Model Context Protocol (MCP) stdio server for Antigravity, Codex, and AI assistants.
Exposes GIS4Logistics datasets, routing, and simulation tools via JSON-RPC.
"""

import json
import logging
import math
import sys
from contextlib import redirect_stdout
from typing import Any
from fastapi import HTTPException
from pydantic import ValidationError
from server.models.phase1 import HighwayRoutePlanRequest, VehicleType
from server.models.schemas import CostParametersOverride, FreightCostSimulationRequest, PortGravitySimulationRequest, RouteRequest
from server.services.error_contract import domain_error_from_status, route_error_contract
from server.services.exceptions import AmbiguousLocationError, DataIntegrityError

from mcp_server.tools import (
    serialize_tool_result,
    tool_calculate_intermodal_freight_cost,
    tool_find_nearest_facilities,
    tool_get_district_scorecard,
    tool_highway_route_and_tolls,
    tool_plan_highway_route,
    tool_simulate_port_catchment,
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
        "inputSchema": {}
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
        "description": "Strategic National Highway graph distance/time and modelled distance-based toll count/outlay; not encountered plazas, official FASTag tariffs or turn-by-turn navigation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "origin_lat": {"type": "number", "description": "Origin latitude"},
                "origin_lon": {"type": "number", "description": "Origin longitude"},
                "dest_lat": {"type": "number", "description": "Destination latitude"},
                "dest_lon": {"type": "number", "description": "Destination longitude"},
                "vehicle_type": {
                    "type": "string",
                    "enum": [v.value for v in VehicleType],
                    "description": "Modelled vehicle class (default: MAV_20T)"
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


TOOLS_METADATA.append({
    "name": "gis_plan_highway_route",
    "description": "Canonical strategic highway route with nested location references, geometry, quality and provenance; no route-matched tolls or navigation guarantee.",
    "inputSchema": HighwayRoutePlanRequest.model_json_schema(),
})


def _freight_input_schema() -> dict[str, Any]:
    # Reuse REST field descriptions/constraints, never import the SDK (which
    # eagerly imports plotting). MCP flattens the same economic overrides.
    from server.models.schemas import (
        CostParametersOverride,
        FreightCostSimulationRequest,
    )

    properties = FreightCostSimulationRequest.model_json_schema()["properties"].copy()
    properties.pop("custom_parameters")
    for name, field in CostParametersOverride.model_json_schema()["properties"].items():
        properties[name] = {**field, "default": None}
    return {
        "type": "object", "properties": properties, "additionalProperties": False,
        "anyOf": [{"required": ["origin_district"]}, {"required": ["origin_district_code"]}],
    }


for _tool in TOOLS_METADATA:
    _tool["inputSchema"]["additionalProperties"] = False
    if _tool["name"] == "gis_calculate_intermodal_freight_cost":
        _tool["inputSchema"] = _freight_input_schema()
    elif _tool["name"] == "gis_get_district_scorecard":
        _tool["inputSchema"]["properties"]["state"] = {
            "type": ["string", "null"], "description": "State to disambiguate district names",
        }


def _validate_tool_arguments(schema: dict[str, Any], args: dict[str, Any]) -> None:
    """Validate advertised scalar inputs without JSON/Pydantic coercion."""
    properties = schema["properties"]
    unknown = args.keys() - properties.keys()
    if unknown:
        raise ValueError(f"Unknown arguments: {', '.join(sorted(unknown))}")
    missing = set(schema.get("required", [])) - args.keys()
    if missing:
        raise ValueError(f"Missing arguments: {', '.join(sorted(missing))}")
    if "anyOf" in schema and not any(
        all(args.get(name) is not None for name in option["required"])
        for option in schema["anyOf"]
    ):
        raise ValueError("origin_district or origin_district_code is required")
    for name, value in args.items():
        field = properties[name]
        variants = field.get("anyOf", [field])
        types = []
        for variant in variants:
            kind = variant.get("type")
            types.extend(kind if isinstance(kind, list) else [kind])
        valid = (
            (value is None and "null" in types)
            or (type(value) is str and "string" in types)
            or (type(value) is int and ("integer" in types or "number" in types))
            or (type(value) is float and "number" in types and math.isfinite(value))
        )
        if not valid:
            raise ValueError(f"{name} must have type {' or '.join(types)}")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"{name} must be one of {field['enum']}")
        if value is None:
            continue
        constraints = next((variant for variant in variants if variant.get("type") != "null"), field)
        for key, compare in (
            ("minimum", lambda x, y: x >= y), ("maximum", lambda x, y: x <= y),
            ("exclusiveMinimum", lambda x, y: x > y), ("exclusiveMaximum", lambda x, y: x < y),
        ):
            if key in constraints and not compare(value, constraints[key]):
                raise ValueError(f"{name} violates {key} {constraints[key]}")


SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05"}


def _tool_failure(req_id: Any, contract: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": {
        "isError": True,
        "content": [{"type": "text", "text": json.dumps(contract, allow_nan=False)}],
    }}


def _validate_input(tool_name: str, schema: dict[str, Any], args: dict[str, Any]) -> None:
    """Complete input-only validation before any store access or calculation."""
    if tool_name == "gis_plan_highway_route":
        HighwayRoutePlanRequest.model_validate_json(json.dumps(args, allow_nan=False))
        return
    _validate_tool_arguments(schema, args)
    if tool_name == "gis_highway_route_and_tolls":
        RouteRequest.model_validate({
            "origin": [args["origin_lat"], args["origin_lon"]],
            "destination": [args["dest_lat"], args["dest_lon"]],
            "vehicle_type": args.get("vehicle_type", "MAV_20T"),
        }, strict=True)
    elif tool_name == "gis_calculate_intermodal_freight_cost":
        overrides = {k: v for k, v in args.items() if k in CostParametersOverride.model_fields and v is not None}
        request = {k: v for k, v in args.items() if k in FreightCostSimulationRequest.model_fields}
        request["custom_parameters"] = CostParametersOverride.model_validate(overrides, strict=True) if overrides else None
        FreightCostSimulationRequest.model_validate(request, strict=True)
    elif tool_name == "gis_simulate_port_catchment":
        PortGravitySimulationRequest.model_validate(args, strict=True)


def handle_rpc_request(req: dict[str, Any]) -> dict[str, Any] | None:
    if (
        not isinstance(req, dict)
        or req.get("jsonrpc") != "2.0"
        or not isinstance(req.get("method"), str)
        or ("id" in req and (
            isinstance(req["id"], bool)
            or not isinstance(req["id"], (str, int, float, type(None)))
        ))
    ):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }

    method = req.get("method")
    req_id = req.get("id")

    # Only absence of id denotes a notification; an explicit null id is
    # discouraged by JSON-RPC but still requires a response.
    if "id" not in req:
        if method == "notifications/initialized":
            LOGGER.info("MCP client initialized session")
        return None

    raw_params = req.get("params")
    if raw_params is not None and not isinstance(raw_params, dict):
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32602, "message": "Invalid params: params must be a JSON object"},
        }
    params = raw_params or {}


    if method == "initialize":
        requested_version = params.get("protocolVersion")
        negotiated_version = (
            requested_version
            if isinstance(requested_version, str) and requested_version in SUPPORTED_PROTOCOL_VERSIONS
            else MCP_PROTOCOL_VERSION
        )
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
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            return {
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32602, "message": "Invalid params: arguments must be a JSON object"},
            }

        metadata = next((tool for tool in TOOLS_METADATA if tool["name"] == tool_name), None)
        if metadata is None:
            return {
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
            }
        try:
            _validate_input(tool_name, metadata["inputSchema"], args)
        except (ValueError, TypeError, OverflowError):
            LOGGER.exception("MCP tool call failed input validation: %s", tool_name)
            return _tool_failure(req_id, {
                "status": 422, "code": "invalid_request", "message": "Request input is invalid.",
            })
        try:
            if tool_name == "gis_plot_villages_map":
                from mcp_server.tools import tool_plot_villages_map
                res = tool_plot_villages_map(**args)
            else:
                handlers = {
                    "gis_get_district_scorecard": tool_get_district_scorecard,
                    "gis_calculate_intermodal_freight_cost": tool_calculate_intermodal_freight_cost,
                    "gis_find_nearest_facilities": tool_find_nearest_facilities,
                    "gis_highway_route_and_tolls": tool_highway_route_and_tolls,
                    "gis_plan_highway_route": tool_plan_highway_route,
                    "gis_simulate_port_catchment": tool_simulate_port_catchment,
                }
                res = handlers[tool_name](**args)

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{
                    "type": "text",
                    "text": json.dumps(serialize_tool_result(res), indent=2, allow_nan=False),
                }]}
            }
        except Exception as e:
            LOGGER.exception("MCP tool call failed: %s", tool_name)
            if isinstance(e, HTTPException):
                bridged = domain_error_from_status(e.status_code)
                if isinstance(bridged, AmbiguousLocationError) and isinstance(e.detail, dict):
                    bridged.candidates = e.detail.get("candidates", [])
                e = bridged
            elif isinstance(e, ValidationError):
                e = DataIntegrityError("Calculated response is invalid.")
            return _tool_failure(req_id, route_error_contract(e))

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not supported"}
        }


def run_stdio_server():
    """Reads JSON-RPC lines from stdin and writes responses to stdout."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    protocol_stdout = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line, parse_constant=_reject_json_constant)
            # Legacy graph/map code and third-party progress output must not
            # share the protocol stream. Include imports inside tool dispatch.
            with redirect_stdout(sys.stderr):
                res = handle_rpc_request(req)
            if res is not None:
                protocol_stdout.write(json.dumps(res, allow_nan=False) + "\n")
                protocol_stdout.flush()
        except Exception:
            LOGGER.exception("Failed to parse MCP request")
            err_res = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"}
            }
            protocol_stdout.write(json.dumps(err_res, allow_nan=False) + "\n")
            protocol_stdout.flush()


def _reject_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


if __name__ == "__main__":
    run_stdio_server()
