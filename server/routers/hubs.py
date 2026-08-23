"""
server/routers/hubs.py
Infrastructure and Logistics Hubs endpoints.
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Depends
import pandas as pd
import numpy as np

from server.dependencies import DataStore, get_data_store
from server.models.schemas import HubItem, RailStationItem, TollPlazaItem

router = APIRouter(prefix="/hubs", tags=["Infrastructure & Logistics Hubs"])


@router.get("", response_model=List[HubItem])
def list_hubs(
    hub_type: Optional[str] = Query(None, description="Filter by hub category: ports, icds, mmlps, air_cargo, icps, iw_terminals, fci_depots, industrial_nodes, pm_mitra_parks, cold_chain, enam_mandis"),
    state: Optional[str] = Query(None, description="Filter by state"),
    limit: int = Query(100, ge=1, le=1000),
    store: DataStore = Depends(get_data_store)
):
    """List multi-modal logistics hubs across India with filtering."""
    results = []
    
    types_to_scan = [hub_type] if hub_type and hub_type in store.hubs_dict else store.hubs_dict.keys()
    
    for k in types_to_scan:
        df = store.hubs_dict[k].copy()
        if "hub_type" not in df.columns:
            df["hub_type"] = k
        if state and "state" in df.columns:
            df = df[df.state.str.lower() == state.lower()]
        results.extend(df.to_dict(orient="records"))

    return results[:limit]


@router.get("/nearest")
def get_nearest_hubs(
    latitude: float = Query(..., description="Latitude of query location"),
    longitude: float = Query(..., description="Longitude of query location"),
    top_k: int = Query(3, ge=1, le=10, description="Number of nearest facilities per category"),
    store: DataStore = Depends(get_data_store)
):
    """Find the nearest logistics facilities (Ports, ICDs, MMLPs, Toll Plazas, Rail Stations) to any coordinate."""
    out = {}

    query_pt = np.array([longitude, latitude])

    # 1. Hubs search
    for hub_name, df in store.hubs_dict.items():
        if "latitude" in df.columns and "longitude" in df.columns:
            coords = df[["longitude", "latitude"]].values
            # Euclidean distance approximation
            dists_deg = np.linalg.norm(coords - query_pt, axis=1)
            dists_km = dists_deg * 111.0
            idx_sorted = np.argsort(dists_km)[:top_k]
            
            items = []
            for idx in idx_sorted:
                row = df.iloc[idx]
                items.append({
                    "name": row.get("name"),
                    "state": row.get("state"),
                    "city": row.get("city"),
                    "distance_straight_line_km": round(float(dists_km[idx]), 1),
                    "latitude": float(row.get("latitude")),
                    "longitude": float(row.get("longitude")),
                    "operator": row.get("operator", None)
                })
            out[hub_name] = items

    # 2. Nearest Toll Plazas
    if store.toll_plazas_df is not None:
        t_df = store.toll_plazas_df
        t_coords = t_df[["longitude", "latitude"]].values
        dists_km = np.linalg.norm(t_coords - query_pt, axis=1) * 111.0
        t_sorted = np.argsort(dists_km)[:top_k]
        toll_items = []
        for idx in t_sorted:
            r = t_df.iloc[idx]
            toll_items.append({
                "name": r.get("name"),
                "nh_number": r.get("nh_number"),
                "toll_type": r.get("toll_type"),
                "distance_km": round(float(dists_km[idx]), 1),
                "latitude": float(r.get("latitude")),
                "longitude": float(r.get("longitude"))
            })
        out["toll_plazas"] = toll_items

    return out


def sanitize_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clean = []
    for r in records:
        clean.append({k: (None if pd.isna(v) else v) for k, v in r.items()})
    return clean


@router.get("/rail/stations", response_model=List[RailStationItem])
def query_rail_stations(
    search: Optional[str] = Query(None, description="Station name or code search"),
    zone: Optional[str] = Query(None, description="Railway zone, e.g. 'CR', 'WR', 'NR'"),
    category: Optional[str] = Query(None, description="NSG category, e.g. 'NSG1', 'NSG2'"),
    limit: int = Query(50, ge=1, le=500),
    store: DataStore = Depends(get_data_store)
):
    """Query 8,697 Indian Railways stations with NSG classification and coordinates."""
    if store.rail_stations_df is None:
        raise HTTPException(status_code=500, detail="Rail stations data not loaded")

    df = store.rail_stations_df.copy()
    if search:
        df = df[
            df.station_name.str.lower().str.contains(search.lower(), na=False) |
            df.station_code.str.lower().str.contains(search.lower(), na=False)
        ]
    if zone:
        df = df[df.zone.str.upper() == zone.upper()]

    raw_records = df.iloc[:limit].to_dict(orient="records")
    return sanitize_records(raw_records)


@router.get("/rail/dfc")
def get_dfc_roster(store: DataStore = Depends(get_data_store)):
    """Get roster of Dedicated Freight Corridor alignments and 54 junction stations."""
    if store.dfc_stations_df is not None:
        stations = sanitize_records(store.dfc_stations_df.to_dict(orient="records"))
    else:
        stations = []
    return {
        "corridors": [
            {"corridor_name": "Western Dedicated Freight Corridor (WDFC)", "route": "Dadri (UP) to JNPT (Mumbai)", "length_km": 1506.0, "status": "Operational / Commissioning"},
            {"corridor_name": "Eastern Dedicated Freight Corridor (EDFC)", "route": "Sahnewal/Ludhiana (Punjab) to Sonnagar (Bihar)", "length_km": 1337.0, "status": "Operational"},
            {"corridor_name": "Dadri-Khurja Connecting Link", "route": "New Dadri (WDFC) to New Khurja (EDFC)", "length_km": 46.0, "status": "Operational"}
        ],
        "stations_count": len(stations),
        "stations": stations
    }


@router.get("/roads/toll-plazas", response_model=List[TollPlazaItem])
def query_toll_plazas(
    state: Optional[str] = Query(None, description="Filter by state"),
    nh_number: Optional[str] = Query(None, description="Filter by NH number, e.g. 'NH48'"),
    limit: int = Query(50, ge=1, le=500),
    store: DataStore = Depends(get_data_store)
):
    """Query 1,536 clustered National Toll Plazas and FASTag points."""
    if store.toll_plazas_df is None:
        raise HTTPException(status_code=500, detail="Toll plazas data not loaded")

    df = store.toll_plazas_df.copy()
    if state:
        df = df[df.state.str.lower() == state.lower()]
    if nh_number:
        df = df[df.nh_number.str.upper() == nh_number.upper()]

    raw_records = df.iloc[:limit].to_dict(orient="records")
    return sanitize_records(raw_records)
