"""
server/routers/admin.py
Administrative endpoints: States, Districts, Villages, and District Scorecards.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Depends
import pandas as pd

from server.dependencies import DataStore, get_data_store
from server.models.schemas import StateSummary, DistrictSummary, DistrictScorecard, VillageItem

router = APIRouter(prefix="/admin", tags=["Administrative & Demographics"])


@router.get("/states", response_model=List[StateSummary])
def list_states(store: DataStore = Depends(get_data_store)):
    """List all 36 Indian States/UTs with LGD code, district count, and village counts."""
    if store.states_df is None:
        raise HTTPException(status_code=500, detail="States data not loaded")
    return store.states_df.to_dict(orient="records")


@router.get("/districts", response_model=List[DistrictSummary])
def list_districts(
    state: Optional[str] = Query(None, description="Filter by state name"),
    search: Optional[str] = Query(None, description="Search by district name prefix/substring"),
    store: DataStore = Depends(get_data_store)
):
    """List all 781 current LGD districts with population estimates."""
    if store.districts_df is None:
        raise HTTPException(status_code=500, detail="Districts data not loaded")
    
    df = store.districts_df.copy()
    if state:
        df = df[df.state.str.lower() == state.lower()]
    if search:
        df = df[df.district.str.lower().str.contains(search.lower(), na=False)]
        
    clean_df = df.where(pd.notna(df), None)
    return clean_df.to_dict(orient="records")


@router.get("/districts/{code_or_name}", response_model=DistrictScorecard)
def get_district_scorecard(code_or_name: str, store: DataStore = Depends(get_data_store)):
    """Get a comprehensive logistics and accessibility scorecard for a specific district."""
    if store.districts_df is None:
        raise HTTPException(status_code=500, detail="Districts data not loaded")

    # Match by code or name
    df = store.districts_df
    match = None
    if code_or_name.isdigit():
        code = int(code_or_name)
        m = df[df.district_code == code]
        if not m.empty:
            match = m.iloc[0]
    
    if match is None:
        m = df[df.district.str.lower() == code_or_name.lower()]
        if not m.empty:
            match = m.iloc[0]

    if match is None:
        raise HTTPException(status_code=404, detail=f"District '{code_or_name}' not found")

    d_name = match["district"]
    st_name = match["state"]
    d_code = int(match["district_code"]) if pd.notna(match.get("district_code")) else None
    pop = int(match["pop_2011"]) if pd.notna(match.get("pop_2011")) else None

    scorecard = {
        "state": st_name,
        "district": d_name,
        "district_code": d_code,
        "pop_2011": pop,
        "is_island": st_name in ["Andaman and Nicobar Islands", "Lakshadweep"]
    }

    # Enrich from Travel Time summary
    if store.travel_time_df is not None:
        t_df = store.travel_time_df
        t_row = t_df[(t_df.district.str.lower() == d_name.lower()) & (t_df.state.str.lower() == st_name.lower())]
        if not t_row.empty:
            r = t_row.iloc[0]
            scorecard["nearest_highway_km"] = r.get("highway_access_dist_km")
            scorecard["nearest_toll_plaza"] = {
                "name": r.get("nearest_toll_plaza_name"),
                "road_distance_km": r.get("toll_plaza_road_distance_km"),
                "drive_time_hours": r.get("toll_plaza_drive_time_hours")
            }
            scorecard["nearest_rail_station"] = {
                "name": r.get("nearest_rail_station_name"),
                "road_distance_km": r.get("rail_station_road_distance_km"),
                "drive_time_hours": r.get("rail_station_drive_time_hours")
            }
            scorecard["nearest_freight_terminal"] = {
                "name": r.get("nearest_freight_terminal_name"),
                "road_distance_km": r.get("freight_terminal_road_distance_km"),
                "drive_time_hours": r.get("freight_terminal_drive_time_hours")
            }
            scorecard["nearest_port"] = {
                "name": r.get("nearest_port_name"),
                "road_distance_km": r.get("port_road_distance_km"),
                "drive_time_hours": r.get("port_drive_time_hours")
            }
            scorecard["nearest_icd"] = {
                "name": r.get("nearest_icd_name"),
                "road_distance_km": r.get("icd_road_distance_km"),
                "drive_time_hours": r.get("icd_drive_time_hours")
            }
            scorecard["nearest_mmlp"] = {
                "name": r.get("nearest_mmlp_name"),
                "road_distance_km": r.get("mmlp_road_distance_km"),
                "drive_time_hours": r.get("mmlp_drive_time_hours")
            }

    # Enrich from Access Rollup
    if store.district_access_df is not None:
        a_df = store.district_access_df
        a_row = a_df[(a_df.district.str.lower() == d_name.lower()) & (a_df.state.str.lower() == st_name.lower())]
        if not a_row.empty:
            ar = a_row.iloc[0]
            ten_km_val = ar.get("rail_station_within_10km_pct")
            scorecard["share_villages_within_10km_rail"] = ten_km_val
            scorecard["share_villages_within_5km_rail"] = ten_km_val  # Deprecated backward-compatible alias
            scorecard["share_villages_within_25km_rail"] = ar.get("rail_station_within_25km_pct")
            scorecard["share_villages_within_50km_icd"] = ar.get("icd_within_50km_pct")

    return scorecard


@router.get("/villages", response_model=List[VillageItem])
def query_villages(
    state: str = Query(..., description="State name, e.g. 'Haryana' or 'Sikkim'"),
    district: Optional[str] = Query(None, description="District name"),
    limit: int = Query(50, ge=1, le=500, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    store: DataStore = Depends(get_data_store)
):
    """Query settlement and village accessibility tables with pagination."""
    slug = state.lower().replace(" ", "_")
    p_access = store.data_dir / "analysis" / f"{slug}_village_access.csv"
    
    if not p_access.exists():
        raise HTTPException(status_code=404, detail=f"No village access data found for state '{state}'")

    df = pd.read_csv(p_access)
    if district:
        df = df[df.district.str.lower() == district.lower()]

    page = df.iloc[offset:offset+limit]
    clean_page = page.where(pd.notna(page), None)
    return clean_page.to_dict(orient="records")


@router.get("/villages/geojson")
def get_villages_geojson(
    state: str = Query(..., description="State name, e.g. 'Haryana', 'Maharashtra'"),
    district: str = Query(..., description="District name, e.g. 'Ambala', 'Pune'"),
    store: DataStore = Depends(get_data_store)
):
    """Get GeoJSON FeatureCollection of all village polygons/points in a district with pre-joined accessibility metrics."""
    from scripts.analyze.plot_villages import load_district_villages_gdf
    try:
        gdf = load_district_villages_gdf(state=state, district=district)
        return gdf.__geo_interface__
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/villages/map.html")
def get_villages_interactive_map(
    state: str = Query(..., description="State name, e.g. 'Haryana', 'Maharashtra'"),
    district: str = Query(..., description="District name, e.g. 'Ambala', 'Pune'"),
    metric: str = Query("dist_rail_station_km", description="Metric to visualize: dist_rail_station_km, dist_nh_km, dist_icd_km, dist_freight_terminal_km, dist_port_km, dist_toll_plaza_km"),
    store: DataStore = Depends(get_data_store)
):
    """Renders a standalone, responsive Leaflet.js interactive choropleth map directly in the browser."""
    from fastapi.responses import HTMLResponse
    from scripts.analyze.plot_villages import load_district_villages_gdf, generate_leaflet_html
    try:
        gdf = load_district_villages_gdf(state=state, district=district)
        html_code = generate_leaflet_html(gdf=gdf, state=state, district=district, metric=metric)
        return HTMLResponse(content=html_code, status_code=200)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
