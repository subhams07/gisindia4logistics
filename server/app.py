"""
server/app.py
Main FastAPI application entrypoint for GIS4Logistics API Server.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.config import settings
from server.dependencies import DataStore
from server.routers.admin import router as admin_router
from server.routers.hubs import router as hubs_router
from server.routers.routing import router as routing_router
from server.routers.simulation import router as simulation_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize in-memory spatial index and routing graphs on startup
    store = DataStore.get_instance()
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API Routers
app.include_router(admin_router, prefix=settings.API_V1_PREFIX)
app.include_router(hubs_router, prefix=settings.API_V1_PREFIX)
app.include_router(routing_router, prefix=settings.API_V1_PREFIX)
app.include_router(simulation_router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["Health & Status"])
def root_info():
    store = DataStore.get_instance()
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "operational",
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
