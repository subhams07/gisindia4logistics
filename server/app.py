"""
server/app.py
Main FastAPI application entrypoint for GIS4Logistics API Server.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from server.config import settings
from server.dependencies import DataStore
from server.routers.admin import router as admin_router
from server.routers.hubs import router as hubs_router
from server.routers.routing import router as routing_router, capture_request_store
from server.routers.simulation import router as simulation_router
from server.services.metadata_service import MetadataService
from server.services import routing_facade
from server.services.error_contract import route_error_contract
from server.services.exceptions import GISIndia4LogisticsError


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Data requests initialize the store; liveness and docs remain data-lazy.
    yield
    # Clean up on shutdown if needed


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_version_headers_middleware(request, call_next):
    try:
        response = await call_next(request)
    except Exception as exc:
        # Generic failures are never client errors, even ordinary ValueError.
        response = safe_error_response(exc)
    metadata = getattr(request.state, "gis_metadata", None)
    if metadata is None:
        store = getattr(request.state, "gis_store", None)
        if store is not None:
            metadata = routing_facade.metadata_for_store(store).model_dump()
        else:
            # Read configured provenance only; never acquire a store for headers.
            metadata = MetadataService.get_instance(
                settings.DATA_PATH / "version_manifest.json").get_metadata().model_dump()
    for field, header in (("api_version", "X-GIS4L-API-Version"),
                          ("data_version", "X-GIS4L-Data-Version"),
                          ("model_version", "X-GIS4L-Model-Version")):
        response.headers[header] = metadata[field]
    return response


def safe_error_response(error: Exception):
    contract = route_error_contract(error)
    return JSONResponse(status_code=contract["status"], content={"detail": contract})


@app.exception_handler(GISIndia4LogisticsError)
async def domain_error_handler(request: Request, error: GISIndia4LogisticsError):
    return safe_error_response(error)


@app.exception_handler(RequestValidationError)
@app.exception_handler(ValidationError)
async def request_validation_handler(request: Request, error: Exception):
    # Never echo validation input, errors or local paths.
    return safe_error_response(ValidationError.from_exception_data("Request", []))


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, error: Exception):
    return safe_error_response(error)

# Mount API Routers
app.include_router(admin_router, prefix=settings.API_V1_PREFIX, dependencies=[Depends(capture_request_store)])
app.include_router(hubs_router, prefix=settings.API_V1_PREFIX, dependencies=[Depends(capture_request_store)])
app.include_router(routing_router, prefix=settings.API_V1_PREFIX, dependencies=[Depends(capture_request_store)])
app.include_router(simulation_router, prefix=settings.API_V1_PREFIX, dependencies=[Depends(capture_request_store)])


def _missing_core_components(store: DataStore) -> list[str]:
    components = {
        "districts": store.districts_df,
        "district_access": store.district_access_df,
        "travel_time": store.travel_time_df,
        "port_matrix": store.port_matrix_df,
        "highway_graph": store.nh_graph,
    }
    return [name for name, value in components.items() if value is None]


@app.get("/health/live", tags=["Health & Status"])
def liveness():
    """Confirm that the API process is running."""
    return {"status": "alive", "service": settings.PROJECT_NAME, "version": settings.VERSION}


@app.get("/health/ready", tags=["Health & Status"])
def readiness(request: Request):
    """Confirm that core data and routing components are loaded."""
    store = DataStore.get_instance()
    request.state.gis_store = store
    missing = _missing_core_components(store)
    payload = {
        "status": "ready" if not missing else "degraded",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "missing_components": missing,
    }
    return payload if not missing else JSONResponse(status_code=503, content=payload)


@app.get("/", tags=["Health & Status"])
def root_info(request: Request):
    store = DataStore.get_instance()
    request.state.gis_store = store
    missing = _missing_core_components(store)
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "operational" if not missing else "degraded",
        "missing_components": missing,
        "documentation_url": "/docs",
        "redoc_url": "/redoc",
        "datasets_online": {
            "states_count": len(store.states_df) if store.states_df is not None else 0,
            "districts_count": len(store.districts_df) if store.districts_df is not None else 0,
            "rail_stations_count": len(store.rail_stations_df) if store.rail_stations_df is not None else 0,
            "dfc_stations_count": len(store.dfc_stations_df) if store.dfc_stations_df is not None else 0,
            "toll_plazas_count": len(store.toll_plazas_df) if store.toll_plazas_df is not None else 0,
            "logistics_hubs_categories": list(store.hubs_dict.keys()),
            "highway_network_nodes": len(store.nh_node_list) if store.nh_node_list is not None else 0
        }
    }
