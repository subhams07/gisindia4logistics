"""
gisindia4logistics.cli
Command-line interface for GISIndia4Logistics.
"""

import sys
import argparse


def _invalid_cli_input():
    from pydantic import ValidationError
    return ValidationError.from_exception_data("CLI input", [])


def _require_data_or_exit():
    """Refuse to start a long-running server that cannot serve anything."""
    from gisindia4logistics.data_root import DataNotFoundError, require_data_root
    try:
        require_data_root()
    except DataNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


def _warn_if_data_missing():
    """MCP initialize/tools/list work without data, so only warn on stderr."""
    from gisindia4logistics.data_root import PUBLIC_MESSAGE, data_root_available
    if not data_root_available():
        print(f"warning: {PUBLIC_MESSAGE}", file=sys.stderr)


def cmd_route_plan(args):
    """Emit a strict JSON strategic highway plan."""
    import json
    from gisindia4logistics.sdk import plan_highway_route

    def reject_constant(value):
        raise ValueError("Non-finite JSON input")

    try:
        origin = json.loads(args.origin, parse_constant=reject_constant)
        destination = json.loads(args.dest, parse_constant=reject_constant)
    except (ValueError, TypeError):
        raise _invalid_cli_input() from None
    result = plan_highway_route(
        origin, destination, args.vehicle,
        include_route_geometry=not args.no_geometry, geometry_detail=args.geometry,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


def cmd_serve(args):
    """Launch the FastAPI REST API Server."""
    _require_data_or_exit()
    import uvicorn
    print(f"Starting GISIndia4Logistics REST API Server on http://{args.host}:{args.port} ...")
    print(f"OpenAPI documentation available at http://{args.host}:{args.port}/docs")
    uvicorn.run("server.app:app", host=args.host, port=args.port, reload=args.reload)


def cmd_mcp(args):
    """Launch the Model Context Protocol (MCP) stdio JSON-RPC tool server."""
    _warn_if_data_missing()
    from mcp_server.server import run_stdio_server
    run_stdio_server()


def cmd_route(args):
    """Calculate commercial highway shortest-path route and tolls."""
    from gisindia4logistics.sdk import route_highway
    try:
        orig_lat, orig_lon = map(float, args.origin.split(","))
        dest_lat, dest_lon = map(float, args.dest.split(","))
    except (ValueError, TypeError):
        raise _invalid_cli_input() from None

    res = route_highway(
        origin=(orig_lat, orig_lon),
        destination=(dest_lat, dest_lon),
        vehicle_type=args.vehicle
    )

    print("\n" + "=" * 60)
    print("  HIGHWAY SHORTEST-PATH ROUTE & TOLL SUMMARY")
    print("=" * 60)
    print(f"  Driving Distance : {res['distance_km']:.1f} km")
    print(f"  Transit Duration : {res.get('drive_time_formatted', str(res.get('drive_time_hours', '')) + ' hrs')} ({res['drive_time_hours']:.2f} hrs)")
    print(f"  Vehicle Class    : {args.vehicle}")
    print(f"  Estimated Plazas : {res.get('tolls_encountered_count', 0)} (distance-based heuristic)")
    print(f"  Estimated Toll   : INR {res['estimated_toll_cost_inr']:,.2f}")
    print(f"  Toll Method      : {res['toll_estimation_method']}")
    print(f"  Routing Scope    : {res['routing_scope']}")
    quality = res.get('route_quality') or {}
    print(f"  Route Quality    : {quality.get('quality', 'unknown')}")
    for warning in quality.get('warnings', []):
        print(f"  Warning          : {warning}")
    for limitation in (res.get('metadata') or {}).get('limitations', []):
        print(f"  Limitation       : {limitation}")
    print("=" * 60 + "\n")


def cmd_cost(args):
    """Run intermodal freight cost simulation and optimization."""
    from gisindia4logistics.sdk import calculate_freight_cost
    res = calculate_freight_cost(
        origin_district=args.origin,
        origin_district_code=args.origin_code,
        origin_state=args.state,
        target_port=args.port,
        payload_tons=args.payload,
        truck_payload_tons=args.truck_payload,
        road_linehaul_rate=args.road_rate,
        rail_base_class_rate=args.rail_rate,
        inventory_holding_rate=args.inventory_rate,
        dfc_linehaul_rate=args.dfc_rate,
        toll_cost_per_plaza=args.toll_cost
    )

    port_name = res.get('target_port', 'Target Port')
    print("\n" + "=" * 60)
    print(f"  FREIGHT COST OPTIMIZATION: {res['origin_district'].upper()} ({res.get('state', '')}) -> {port_name}")
    print("=" * 60)
    print(f"  Payload Weight   : {args.payload:.1f} Tonnes")
    print(f"  Road Distance    : {res.get('road_distance_km', 0.0):.1f} km")
    print("-" * 60)
    print(f"  1. Road Trucking : INR {res['road']['cost_per_ton_inr']:,.2f} / tonne (Total: INR {res['road']['total_shipment_cost_inr']:,.2f})")
    print(f"  2. Conventional  : INR {res['conventional_rail']['cost_per_ton_inr']:,.2f} / tonne (Total: INR {res['conventional_rail']['total_shipment_cost_inr']:,.2f})")
    if res.get('dfc_rail'):
        print(f"  3. DFC Rail      : INR {res['dfc_rail']['cost_per_ton_inr']:,.2f} / tonne (Total: INR {res['dfc_rail']['total_shipment_cost_inr']:,.2f})")
    print("-" * 60)
    print(f"  OPTIMAL MODE     : {res['optimal_mode'].upper()}")
    print(f"  MODAL SAVINGS    : {res['modal_shift_savings_pct']:.1f}% (INR {res['modal_shift_savings_per_ton_inr']:,.2f} / tonne)")
    print(f"  BREAK-EVEN DIST  : {res.get('break_even_distance_km', 0):.0f} km")
    print("=" * 60 + "\n")


def cmd_district(args):
    """Print a district scorecard, with state disambiguation when needed."""
    import json

    from gisindia4logistics.sdk import get_district

    print(json.dumps(get_district(args.identifier, state=args.state), ensure_ascii=False, indent=2, allow_nan=False))


def cmd_plot_villages(args):
    """Plot village accessibility maps."""
    from gisindia4logistics.sdk import plot_villages
    res = plot_villages(
        state=args.state,
        district=args.district,
        metric=args.metric,
        output_format=args.format,
        output_path=args.output
    )
    print(f"Successfully generated maps for {res['villages_count']} villages in {args.district}, {args.state}:")
    for fmt, p in res["files"].items():
        print(f"  [{fmt.upper()}] -> {p}")


def cmd_audit(args):
    """Run data integrity audit."""
    from scripts.audit.audit_all import main as audit_main
    sys.argv = ["audit_all.py"] + (["--fast"] if args.fast else [])
    audit_main()


def _dispatch(args):
    if args.command in {"route", "route-plan", "cost", "district"}:
        from fastapi import HTTPException
        from server.services.error_contract import domain_error_from_status, route_error_contract
        from gisindia4logistics.data_root import DataNotFoundError
        try:
            args.func(args)
        except Exception as exc:
            if isinstance(exc, DataNotFoundError):
                raise
            if isinstance(exc, HTTPException):
                exc = domain_error_from_status(exc.status_code)
            contract = route_error_contract(exc)
            print(f"error: {contract['message']}", file=sys.stderr)
            raise SystemExit(2 if contract['status'] in {400, 404, 409, 422} else 1) from None
    else:
        args.func(args)


def main():
    parser = argparse.ArgumentParser(
        prog="gisindia4logistics",
        description="GISIndia4Logistics — Open Geospatial & Multimodal Freight Intelligence CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # serve
    p_serve = subparsers.add_parser("serve", help="Start the FastAPI REST API Server")
    p_serve.add_argument("--host", type=str, default="0.0.0.0", help="Host address (default: 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=8000, help="Port number (default: 8000)")
    p_serve.add_argument("--reload", action="store_true", help="Enable auto-reload for dev")
    p_serve.set_defaults(func=cmd_serve)

    # mcp
    p_mcp = subparsers.add_parser("mcp", help="Start the Model Context Protocol (MCP) JSON-RPC tool server")
    p_mcp.set_defaults(func=cmd_mcp)

    # route
    vehicles = ["LMV", "LCV", "2_AXLE_TRUCK", "MAV_20T", "7_AXLE_OVERSIZED"]
    p_route = subparsers.add_parser("route", help="Calculate strategic highway route and heuristic toll estimates")
    p_route.add_argument("--origin", type=str, required=True, help="Origin coordinates 'lat,lon' (e.g. 18.5204,73.8567)")
    p_route.add_argument("--dest", type=str, required=True, help="Destination coordinates 'lat,lon' (e.g. 18.9500,72.9500)")
    p_route.add_argument("--vehicle", type=str, default="MAV_20T", choices=vehicles, help="Vehicle class")
    p_route.set_defaults(func=cmd_route)

    p_plan = subparsers.add_parser("route-plan", help="Emit a rich strategic highway route as strict JSON")
    p_plan.add_argument("--origin", required=True, help="Origin location reference JSON")
    p_plan.add_argument("--dest", required=True, help="Destination location reference JSON")
    p_plan.add_argument("--vehicle", default="MAV_20T", choices=vehicles)
    p_plan.add_argument("--geometry", default="none", choices=["none", "full", "simplified"])
    p_plan.add_argument("--no-geometry", action="store_true", help="Suppress geometry regardless of detail")
    p_plan.set_defaults(func=cmd_route_plan)

    # cost
    p_cost = subparsers.add_parser("cost", help="Calculate intermodal freight cost (Road vs Rail vs DFC)")
    p_cost.add_argument("--origin", type=str, help="Origin district name (e.g. 'Indore', 'Pune'); name or code required")
    p_cost.add_argument("--origin-code", type=int, help="Origin LGD district code")
    p_cost.add_argument("--state", type=str, help="Origin state for disambiguation")
    p_cost.add_argument("--truck-payload", type=float, help="Vehicle capacity in tonnes (default: 20; independent of shipment)")
    p_cost.add_argument("--rail-rate", type=float, help="IR base class rate factor")
    p_cost.add_argument("--inventory-rate", type=float, help="Inventory holding rate INR/tonne-hour")
    p_cost.add_argument("--port", "--target-port", dest="port", type=str, help="Destination port name (e.g. 'Jawaharlal Nehru Port (JNPT)', 'Paradip Port')")
    p_cost.add_argument("--payload", type=float, default=20.0, help="Payload in tonnes (default: 20.0)")
    p_cost.add_argument("--road-rate", type=float, help="Road linehaul rate INR/t-km")
    p_cost.add_argument("--dfc-rate", type=float, help="DFC linehaul rate INR/t-km")
    p_cost.add_argument("--toll-cost", type=float, help="Toll cost per plaza in INR")
    p_cost.set_defaults(func=cmd_cost)

    p_district = subparsers.add_parser("district", help="Get a district scorecard by name or LGD code")
    p_district.add_argument("identifier", help="District name or LGD code")
    p_district.add_argument("--state", help="State used to disambiguate the district")
    p_district.set_defaults(func=cmd_district)

    # plot-villages
    p_plot = subparsers.add_parser("plot-villages", help="Generate interactive HTML or PNG village accessibility maps")
    p_plot.add_argument("--state", type=str, required=True, help="State name (e.g. 'Haryana')")
    p_plot.add_argument("--district", type=str, required=True, help="District name (e.g. 'Ambala')")
    p_plot.add_argument("--metric", type=str, default="dist_rail_station_km", help="Accessibility metric")
    p_plot.add_argument("--format", type=str, choices=["html", "png", "both"], default="html", help="Map format")
    p_plot.add_argument("--output", type=str, help="Custom output path")
    p_plot.set_defaults(func=cmd_plot_villages)

    # audit
    p_audit = subparsers.add_parser("audit", help="Run national spatial data integrity audit")
    p_audit.add_argument("--fast", action="store_true", help="Run fast verification")
    p_audit.set_defaults(func=cmd_audit)

    args = parser.parse_args()
    if hasattr(args, "func"):
        from gisindia4logistics.data_root import DataNotFoundError
        try:
            _dispatch(args)
        except DataNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
