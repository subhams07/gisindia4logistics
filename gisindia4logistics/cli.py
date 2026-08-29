"""
gisindia4logistics.cli
Command-line interface for GISIndia4Logistics.
"""

import sys
import argparse


def cmd_serve(args):
    """Launch the FastAPI REST API Server."""
    import uvicorn
    print(f"Starting GISIndia4Logistics REST API Server on http://{args.host}:{args.port} ...")
    print(f"OpenAPI documentation available at http://{args.host}:{args.port}/docs")
    uvicorn.run("server.app:app", host=args.host, port=args.port, reload=args.reload)


def cmd_mcp(args):
    """Launch the Model Context Protocol (MCP) stdio JSON-RPC tool server."""
    from mcp_server.server import run_stdio_server
    run_stdio_server()


def cmd_route(args):
    """Calculate commercial highway shortest-path route and tolls."""
    from gisindia4logistics.sdk import route_highway
    try:
        orig_lat, orig_lon = map(float, args.origin.split(","))
        dest_lat, dest_lon = map(float, args.dest.split(","))
    except Exception:
        print("Error: Coordinates must be formatted as 'lat,lon' (e.g. 18.5204,73.8567)")
        sys.exit(1)

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
    print(f"  Toll Plazas      : {res.get('tolls_encountered_count', 0)} plazas encountered")
    print(f"  Estimated Toll   : INR {res['estimated_toll_cost_inr']:,.2f}")
    print("=" * 60 + "\n")


def cmd_cost(args):
    """Run intermodal freight cost simulation and optimization."""
    from gisindia4logistics.sdk import calculate_freight_cost
    res = calculate_freight_cost(
        origin_district=args.origin,
        target_port=args.port,
        payload_tons=args.payload,
        road_linehaul_rate=args.road_rate,
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
    p_route = subparsers.add_parser("route", help="Calculate commercial highway route and FASTag tolls")
    p_route.add_argument("--origin", type=str, required=True, help="Origin coordinates 'lat,lon' (e.g. 18.5204,73.8567)")
    p_route.add_argument("--dest", type=str, required=True, help="Destination coordinates 'lat,lon' (e.g. 18.9500,72.9500)")
    p_route.add_argument("--vehicle", type=str, default="MAV_20T", choices=["MAV_20T", "LMV", "2_AXLE_TRUCK"], help="Vehicle class")
    p_route.set_defaults(func=cmd_route)

    # cost
    p_cost = subparsers.add_parser("cost", help="Calculate intermodal freight cost (Road vs Rail vs DFC)")
    p_cost.add_argument("--origin", type=str, required=True, help="Origin district name (e.g. 'Indore', 'Pune')")
    p_cost.add_argument("--port", "--target-port", dest="port", type=str, help="Destination port name (e.g. 'Jawaharlal Nehru Port (JNPT)', 'Paradip Port')")
    p_cost.add_argument("--payload", type=float, default=20.0, help="Payload in tonnes (default: 20.0)")
    p_cost.add_argument("--road-rate", type=float, help="Road linehaul rate INR/t-km")
    p_cost.add_argument("--dfc-rate", type=float, help="DFC linehaul rate INR/t-km")
    p_cost.add_argument("--toll-cost", type=float, help="Toll cost per plaza in INR")
    p_cost.set_defaults(func=cmd_cost)

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
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
