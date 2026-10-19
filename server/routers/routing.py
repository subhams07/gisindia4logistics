"""
server/routers/routing.py
Canonical highway routing adapters and precomputed isochrone readers.
"""

from fastapi import APIRouter, HTTPException, Depends, Request
import geopandas as gpd

from server.dependencies import DataStore, get_data_store
from server.models.schemas import RouteRequest, RouteResponse
from server.models.phase1 import HighwayRoutePlanRequest, HighwayRouteResult
from server.services import routing_facade
from server.services.serialization import serialize_result

router = APIRouter(prefix="/route", tags=["Highway Routing & Isochrones"])

def capture_request_store(request: Request, store: DataStore = Depends(get_data_store)):
    """Capture the dependency-resolved store, including caller overrides."""
    request.state.gis_store = store
    return store


@router.post("/highway", response_model=RouteResponse)
def calculate_highway_route(req: RouteRequest, request: Request,
                            store: DataStore = Depends(capture_request_store)):
    """Estimate strategic NH-network distance, drive time, and toll expense."""
    result = routing_facade.legacy_highway_route(req, store)
    request.state.gis_metadata = result.get("metadata")
    return result


@router.post("/highway/plan", response_model=HighwayRouteResult)
def plan_highway_route(req: HighwayRoutePlanRequest, request: Request,
                       store: DataStore = Depends(capture_request_store)):
    """Strict rich contract; reject calculated NaN before model serialization."""
    result = serialize_result(routing_facade.plan_highway_route(req, store))
    request.state.gis_metadata = result.get("metadata")
    return result


@router.get("/isochrones/ports")
def get_port_isochrones(store: DataStore = Depends(get_data_store)):
    """Read approximate highway catchments (4/8/12/24h), not navigation polygons."""
    p_iso = store.data_dir / "analysis" / "major_ports_isochrones.geojson"
    if not p_iso.exists():
        raise HTTPException(status_code=404, detail="Port isochrones not generated")
    
    gdf = gpd.read_file(p_iso)
    return serialize_result(gdf.__geo_interface__, missing_nan=True)


@router.get("/isochrones/mmlps")
def get_mmlp_isochrones(store: DataStore = Depends(get_data_store)):
    """Read approximate MMLP highway catchments (4/8/12h), including null geometries."""
    p_iso = store.data_dir / "analysis" / "mmlp_isochrones.geojson"
    if not p_iso.exists():
        raise HTTPException(status_code=404, detail="MMLP isochrones not generated")
    
    gdf = gpd.read_file(p_iso)
    return serialize_result(gdf.__geo_interface__, missing_nan=True)
