"""
server/routers/routing.py
Highway routing, Dijkstra shortest-path calculations, and Isochrones.
"""

from fastapi import APIRouter, HTTPException, Depends
import geopandas as gpd
import numpy as np
from pyproj import Transformer
from scipy.sparse.csgraph import dijkstra

from server.dependencies import DataStore, get_data_store
from server.models.schemas import RouteRequest, RouteResponse

router = APIRouter(prefix="/route", tags=["Highway Routing & Isochrones"])

# Transformer from WGS84 (4326) to India LCC (7755)
transformer_to_lcc = Transformer.from_crs("EPSG:4326", "EPSG:7755", always_xy=True)
transformer_to_wgs = Transformer.from_crs("EPSG:7755", "EPSG:4326", always_xy=True)


@router.post("/highway", response_model=RouteResponse)
def calculate_highway_route(req: RouteRequest, store: DataStore = Depends(get_data_store)):
    """Estimate strategic NH-network distance, drive time, and toll expense."""
    if store.nh_graph is None or store.nh_distance_graph is None or store.nh_tree is None:
        raise HTTPException(status_code=503, detail="Highway routing network is initializing or not available")

    # Convert coordinates to LCC
    orig_lat, orig_lon = req.origin[0], req.origin[1]
    dest_lat, dest_lon = req.destination[0], req.destination[1]

    orig_x, orig_y = transformer_to_lcc.transform(orig_lon, orig_lat)
    dest_x, dest_y = transformer_to_lcc.transform(dest_lon, dest_lat)

    # Snap to nearest network nodes belonging to the mainland connected component
    main_lbl = getattr(store, "main_comp_label", 0)
    labels = getattr(store, "nh_comp_labels", None)

    orig_dists, orig_idxs = store.nh_tree.query([orig_x, orig_y], k=15)
    dest_dists, dest_idxs = store.nh_tree.query([dest_x, dest_y], k=15)

    orig_dist, orig_idx = orig_dists[0], orig_idxs[0]
    if labels is not None:
        for d, idx in zip(orig_dists, orig_idxs):
            if labels[idx] == main_lbl:
                orig_dist, orig_idx = d, idx
                break

    dest_dist, dest_idx = dest_dists[0], dest_idxs[0]
    if labels is not None:
        for d, idx in zip(dest_dists, dest_idxs):
            if labels[idx] == main_lbl:
                dest_dist, dest_idx = d, idx
                break

    # Run Dijkstra
    dist_matrix, predecessors = dijkstra(
        store.nh_graph,
        directed=False,
        indices=[orig_idx],
        unweighted=False,
        return_predecessors=True,
    )
    drive_hours = dist_matrix[0, dest_idx]

    if np.isinf(drive_hours):
        raise HTTPException(status_code=400, detail="No highway network path found between the specified points (e.g. island or disconnected component)")

    # Reconstruct the time-optimal path and sum its actual edge lengths.
    network_dist_km = 0.0
    current = int(dest_idx)
    while current != int(orig_idx):
        predecessor = int(predecessors[0, current])
        if predecessor < 0:
            raise HTTPException(status_code=400, detail="Could not reconstruct the highway network path")
        network_dist_km += float(store.nh_distance_graph[predecessor, current])
        current = predecessor

    # Feeder access time (at 35 km/h)
    feeder_hours = ((orig_dist + dest_dist) / 1000.0) / 35.0
    total_hours = drive_hours + feeder_hours
    total_dist_km = network_dist_km + ((orig_dist + dest_dist) / 1000.0)

    # Toll estimation
    toll_count = int(max(round(total_dist_km / 65.0), 1)) if total_dist_km >= 40.0 else 0
    toll_rate_per_plaza = 340.0 if req.vehicle_type == "MAV_20T" else (120.0 if req.vehicle_type == "LMV" else 220.0)
    toll_cost_inr = toll_count * toll_rate_per_plaza

    # Snapped WGS coordinates
    orig_snap_x, orig_snap_y = store.nh_node_xy[orig_idx]
    dest_snap_x, dest_snap_y = store.nh_node_xy[dest_idx]
    orig_snap_lon, orig_snap_lat = transformer_to_wgs.transform(orig_snap_x, orig_snap_y)
    dest_snap_lon, dest_snap_lat = transformer_to_wgs.transform(dest_snap_x, dest_snap_y)

    hours_int = int(total_hours)
    mins_int = int((total_hours - hours_int) * 60)
    formatted = f"{hours_int} hrs {mins_int} min"

    return {
        "distance_km": round(total_dist_km, 1),
        "drive_time_hours": round(total_hours, 2),
        "drive_time_formatted": formatted,
        "tolls_encountered_count": toll_count,
        "estimated_toll_cost_inr": round(toll_cost_inr, 2),
        "toll_estimation_method": "distance-based estimate using average 65 km toll spacing; not route-plaza intersection",
        "routing_scope": "strategic National Highway graph with modeled feeder access; not turn-by-turn navigation",
        "origin_snapped": [round(orig_snap_lat, 5), round(orig_snap_lon, 5)],
        "destination_snapped": [round(dest_snap_lat, 5), round(dest_snap_lon, 5)]
    }


@router.get("/isochrones/ports")
def get_port_isochrones(store: DataStore = Depends(get_data_store)):
    """Get pre-computed 1h, 2h, 4h, 8h vector drive-time isochrones for all 12 Major Commercial Ports."""
    p_iso = store.data_dir / "analysis" / "major_ports_isochrones.geojson"
    if not p_iso.exists():
        raise HTTPException(status_code=404, detail="Port isochrones not generated")
    
    gdf = gpd.read_file(p_iso)
    return gdf.__geo_interface__


@router.get("/isochrones/mmlps")
def get_mmlp_isochrones(store: DataStore = Depends(get_data_store)):
    """Get pre-computed 1h, 2h, 4h vector drive-time isochrones for all 20 Multimodal Logistics Parks."""
    p_iso = store.data_dir / "analysis" / "mmlp_isochrones.geojson"
    if not p_iso.exists():
        raise HTTPException(status_code=404, detail="MMLP isochrones not generated")
    
    gdf = gpd.read_file(p_iso)
    return gdf.__geo_interface__
