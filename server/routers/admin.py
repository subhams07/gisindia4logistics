"""
server/routers/admin.py
Administrative endpoints: States, Districts, Villages, and District Scorecards.
"""

import math
from numbers import Real
from pathlib import Path
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from scripts.clean.standardize import clean_state_name
from server.dependencies import DataStore, get_data_store
from server.models.schemas import (
    DistrictScorecard,
    DistrictSummary,
    StateSummary,
    VillageItem,
)

router = APIRouter(prefix="/admin", tags=["Administrative & Demographics"])


def clean_stored_values(value):
    """Missing observations remain null; corrupt infinities are source failures."""
    if isinstance(value, dict):
        cleaned = {key: clean_stored_values(item) for key, item in value.items()}
        for key, item in cleaned.items():
            numeric_observation = key.endswith(("_km", "_hours", "_pct")) or key.startswith("share_villages_")
            if item is not None and numeric_observation:
                try:
                    number = float(item)
                except (ValueError, TypeError, OverflowError):
                    raise HTTPException(status_code=503, detail="Invalid stored numerical data") from None
                if not math.isfinite(number) or number < 0:
                    raise HTTPException(status_code=503, detail="Invalid stored numerical data")
                cleaned[key] = number
        return cleaned
    if isinstance(value, list):
        return [clean_stored_values(item) for item in value]
    if pd.isna(value):
        return None
    if isinstance(value, Real) and not math.isfinite(value):
        raise HTTPException(status_code=503, detail="Invalid stored numerical data")
    return value


def validated_stored_record(model, record):
    try:
        return model.model_validate(clean_stored_values(record)).model_dump(mode="json")
    except (ValidationError, ValueError, OverflowError, TypeError):
        raise HTTPException(status_code=503, detail="Invalid stored administrative data") from None


def canonical_state(state: str) -> str:
    # Match actual LGD geography, not a sanitized user-controlled filename.
    canonical = clean_state_name(state)
    if canonical is None:
        raise HTTPException(status_code=422, detail="Unknown or invalid state")
    return canonical


def contained_data_path(data_dir: Path, *parts: str) -> Path:
    """Reject symlink/junction escapes too; never include filesystem paths in errors."""
    try:
        root = Path(data_dir).resolve()
        path = root.joinpath(*parts).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Outside data root")
        return path
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(status_code=422, detail="Invalid data path") from None


def validate_village_map_paths(data_dir: Path, state: str) -> None:
    slug = state.lower().replace(" ", "_")
    for folder, suffix in [
        ("villages_parquet", "soi_villages.parquet"),
        ("villages_parquet", "habitations.parquet"),
        ("villages", "soi_villages.geojson"),
        ("villages", "habitations.geojson"),
    ]:
        contained_data_path(data_dir, "administrative", folder, f"{slug}_{suffix}")
    contained_data_path(data_dir, "analysis", f"{slug}_village_access.csv")
    contained_data_path(data_dir, "rail", "railway_stations.csv")


def unique_district_row(rows: pd.DataFrame):
    if len(rows) > 1:
        candidates = rows[["state", "district", "district_code"]].astype(object)
        candidates = candidates.where(pd.notna(candidates), None)
        records = candidates.to_dict(orient="records")
        # CSV blanks promote codes to floats. Normalize records, not a Series,
        # so pandas cannot promote the restored Python ints back to floats.
        for record in records:
            code = record["district_code"]
            if isinstance(code, float) and math.isfinite(code) and code > 0 and code.is_integer():
                record["district_code"] = int(code)
        raise HTTPException(status_code=409, detail={
            "message": "Ambiguous district data; specify state or correct duplicate records",
            "candidates": records,
        })
    return rows.iloc[0] if not rows.empty else None


def scorecard_enrichment_rows(
    df: pd.DataFrame, state: str, district: str, code,
    district_rows: pd.DataFrame,
):
    rows = df[(df.district.astype("string").str.lower() == str(district).lower()) & (df.state.map(clean_state_name) == clean_state_name(state))]
    if code is not None and pd.notna(code) and "district_code" in rows.columns:
        codes = pd.to_numeric(rows.district_code, errors="coerce")
        rows = rows[codes == code]
    else:
        # A single unkeyed rollup can still refer to several primary identities.
        # Missing codes may use name/state only when that pair is unique there.
        identities = district_rows[
            (district_rows.district.astype("string").str.lower() == str(district).lower())
            & (district_rows.state.map(clean_state_name) == clean_state_name(state))
        ]
        if len(identities) != 1:
            return rows.iloc[:0]
    if len(rows) > 1:
        raise HTTPException(status_code=409, detail="Ambiguous district enrichment data")
    return rows


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
    if state is not None:
        df = df[df.state.map(clean_state_name) == canonical_state(state)]
    if search:
        df = df[df.district.astype("string").str.lower().str.contains(search.lower(), na=False, regex=False)]
        
    clean_df = df.astype(object).where(pd.notna(df), None)
    return [validated_stored_record(DistrictSummary, row) for row in clean_df.to_dict(orient="records")]


@router.get("/districts/{code_or_name}", response_model=DistrictScorecard)
def get_district_scorecard(
    code_or_name: str,
    store: DataStore = Depends(get_data_store),
    state: str | None = None,
):
    """Get a comprehensive logistics and accessibility scorecard for a specific district."""
    if store.districts_df is None:
        raise HTTPException(status_code=500, detail="Districts data not loaded")

    # Scorecards need rows, not routing centroids. The routing resolver requires
    # boundary data and still selects first rows for some duplicates; do not use
    # it here until it offers an ambiguity-safe, row-only lookup.
    df = store.districts_df
    if state is not None:
        state = canonical_state(state)
        df = df[df.state.map(clean_state_name) == state]
    if not code_or_name.strip():
        raise HTTPException(status_code=422, detail="District identifier must not be blank")
    if code_or_name.isdigit():
        # Explicit codes never fall back to names when missing.
        if not code_or_name.isascii():
            raise HTTPException(status_code=422, detail="District code must contain ASCII digits")
        try:
            code = int(code_or_name)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid district code") from None
        if code <= 0:
            raise HTTPException(status_code=422, detail="District code must be positive")
        m = df[pd.to_numeric(df.district_code, errors="coerce") == code]
    else:
        m = df[df.district.astype("string").str.lower() == code_or_name.lower()]
    match = unique_district_row(m)

    if match is None:
        if state is not None:
            all_rows = store.districts_df
            if code_or_name.isdigit():
                exists = (pd.to_numeric(all_rows.district_code, errors="coerce") == code).any()
            else:
                exists = (all_rows.district.astype("string").str.lower() == code_or_name.lower()).any()
            if exists:
                raise HTTPException(status_code=422, detail="Conflicting district and state identifiers")
        raise HTTPException(status_code=404, detail=f"District '{code_or_name}' not found")

    d_name = match["district"] if pd.notna(match["district"]) else None
    st_name = clean_state_name(match["state"]) or match["state"]
    d_code = match.get("district_code")
    pop = match.get("pop_2011")

    scorecard = {
        "state": st_name,
        "district": d_name,
        "district_code": d_code,
        "pop_2011": pop,
        "is_island": st_name in ["Andaman And Nicobar Islands", "Lakshadweep"]
    }

    # Enrich from Travel Time summary
    if store.travel_time_df is not None:
        t_df = store.travel_time_df
        t_row = scorecard_enrichment_rows(t_df, st_name, d_name, d_code, store.districts_df)
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
        a_row = scorecard_enrichment_rows(a_df, st_name, d_name, d_code, store.districts_df)
        if not a_row.empty:
            ar = a_row.iloc[0]
            ten_km_val = ar.get("rail_station_within_10km_pct")
            scorecard["share_villages_within_10km_rail"] = ten_km_val
            scorecard["share_villages_within_5km_rail"] = ten_km_val  # Deprecated backward-compatible alias
            scorecard["share_villages_within_25km_rail"] = ar.get("rail_station_within_25km_pct")
            scorecard["share_villages_within_50km_icd"] = ar.get("icd_within_50km_pct")

    return validated_stored_record(DistrictScorecard, scorecard)


@router.get("/villages", response_model=List[VillageItem])
def query_villages(
    state: str = Query(..., description="State name, e.g. 'Haryana' or 'Sikkim'"),
    district: Optional[str] = Query(None, description="District name"),
    limit: int = Query(50, ge=1, le=500, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    store: DataStore = Depends(get_data_store)
):
    """Query settlement and village accessibility tables with pagination."""
    state = canonical_state(state)
    slug = state.lower().replace(" ", "_")
    p_access = contained_data_path(store.data_dir, "analysis", f"{slug}_village_access.csv")
    
    if not p_access.exists():
        raise HTTPException(status_code=404, detail=f"No village access data found for state '{state}'")

    df = pd.read_csv(p_access)
    # State-specific access tables historically omit this required API field.
    df["state"] = state
    if district:
        df = df[df.district.astype("string").str.lower() == district.lower()]

    page = df.iloc[offset:offset+limit]
    clean_page = page.astype(object).where(pd.notna(page), None)
    return [validated_stored_record(VillageItem, row) for row in clean_page.to_dict(orient="records")]


@router.get("/villages/geojson")
def get_villages_geojson(
    state: str = Query(..., description="State name, e.g. 'Haryana', 'Maharashtra'"),
    district: str = Query(..., description="District name, e.g. 'Ambala', 'Pune'"),
    store: DataStore = Depends(get_data_store)
):
    """Get GeoJSON FeatureCollection of all village polygons/points in a district with pre-joined accessibility metrics."""
    state = canonical_state(state)
    if not district.strip():
        raise HTTPException(status_code=422, detail="District must not be blank")
    from scripts.analyze import plot_villages
    # Validate the helper's actual root, not the unused store.data_dir.
    validate_village_map_paths(plot_villages.DATA_DIR, state)
    try:
        gdf = plot_villages.load_district_villages_gdf(state=state, district=district)
        return gdf.__geo_interface__
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Village geometry or district not found") from None


@router.get("/villages/map.html")
def get_villages_interactive_map(
    state: str = Query(..., description="State name, e.g. 'Haryana', 'Maharashtra'"),
    district: str = Query(..., description="District name, e.g. 'Ambala', 'Pune'"),
    metric: str = Query("dist_rail_station_km", description="Metric to visualize: dist_rail_station_km, dist_nh_km, dist_icd_km, dist_freight_terminal_km, dist_port_km, dist_toll_plaza_km"),
    store: DataStore = Depends(get_data_store)
):
    """Renders a standalone, responsive Leaflet.js interactive choropleth map directly in the browser."""
    from fastapi.responses import HTMLResponse
    state = canonical_state(state)
    if not district.strip():
        raise HTTPException(status_code=422, detail="District must not be blank")
    from scripts.analyze import plot_villages
    try:
        plot_villages.validate_metric(metric)
    except ValueError:
        raise HTTPException(status_code=422, detail="Unsupported accessibility metric") from None
    validate_village_map_paths(plot_villages.DATA_DIR, state)
    try:
        gdf = plot_villages.load_district_villages_gdf(state=state, district=district)
        html_code = plot_villages.generate_leaflet_html(gdf=gdf, state=state, district=district, metric=metric)
        return HTMLResponse(content=html_code, status_code=200)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Village geometry or district not found") from None
