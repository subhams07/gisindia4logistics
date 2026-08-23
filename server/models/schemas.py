"""
server/models/schemas.py
Pydantic data validation schemas for GIS4Logistics API.
"""

from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field


# --- Administrative Schemas ---
class StateSummary(BaseModel):
    state: str
    state_code: Optional[int] = None
    district_count: int
    village_count: int
    pop_2011_total: Optional[int] = None


class DistrictSummary(BaseModel):
    state: str
    state_code: Optional[int] = None
    district: str
    district_code: Optional[int] = None
    pop_2011: Optional[int] = None
    area_sqkm: Optional[float] = None
    subdistrict_count: Optional[int] = None


class DistrictScorecard(BaseModel):
    state: str
    district: str
    district_code: Optional[int] = None
    pop_2011: Optional[int] = None
    is_island: bool = False
    
    # Proximity metrics
    nearest_highway_km: Optional[float] = None
    nearest_toll_plaza: Optional[Dict[str, Any]] = None
    nearest_rail_station: Optional[Dict[str, Any]] = None
    nearest_freight_terminal: Optional[Dict[str, Any]] = None
    nearest_port: Optional[Dict[str, Any]] = None
    nearest_icd: Optional[Dict[str, Any]] = None
    nearest_mmlp: Optional[Dict[str, Any]] = None
    
    # Catchment shares
    share_villages_within_5km_rail: Optional[float] = None
    share_villages_within_25km_rail: Optional[float] = None
    share_villages_within_50km_icd: Optional[float] = None


class VillageItem(BaseModel):
    state: str
    district: str
    village: str
    village_code: Optional[Union[int, str]] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    dist_rail_station_km: Optional[float] = None
    dist_icd_km: Optional[float] = None
    dist_port_km: Optional[float] = None
    dist_air_cargo_km: Optional[float] = None


# --- Hub Schemas ---
class HubItem(BaseModel):
    name: str
    hub_type: str
    state: str
    city: str
    latitude: float
    longitude: float
    operator: Optional[str] = None
    capacity_notes: Optional[str] = None
    source_url: Optional[str] = None


class RailStationItem(BaseModel):
    station_code: str
    station_name: str
    state: Optional[str] = None
    zone: Optional[str] = None
    division: Optional[str] = None
    category: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class TollPlazaItem(BaseModel):
    name: str
    toll_type: str
    nh_number: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    latitude: float
    longitude: float
    booth_count: Optional[int] = None


# --- Routing Schemas ---
class RouteRequest(BaseModel):
    origin: List[float] = Field(..., description="[latitude, longitude] of origin point", min_length=2, max_length=2)
    destination: List[float] = Field(..., description="[latitude, longitude] of destination point", min_length=2, max_length=2)
    vehicle_type: Optional[str] = Field("MAV_20T", description="Vehicle type: LMV, LCV, 2_AXLE_TRUCK, MAV_20T, 7_AXLE_OVERSIZED")


class RouteResponse(BaseModel):
    distance_km: float
    drive_time_hours: float
    drive_time_formatted: str
    tolls_encountered_count: int
    estimated_toll_cost_inr: float
    origin_snapped: List[float]
    destination_snapped: List[float]


# --- Simulation Schemas ---
class CostParametersOverride(BaseModel):
    road_linehaul_rate: Optional[float] = Field(3.30, description="Road linehaul rate INR/tonne-km")
    road_handling_cost: Optional[float] = Field(140.0, description="Road handling INR/tonne")
    toll_cost_per_plaza: Optional[float] = Field(340.0, description="Toll fee per plaza INR")
    truck_payload_tons: Optional[float] = Field(20.0, description="Commercial payload in MT")
    toll_spacing_km: Optional[float] = Field(65.0, description="Average km between tolls")
    
    rail_base_class_rate: Optional[float] = Field(1.55, description="IR Base class rate factor")
    rail_first_mile_rate: Optional[float] = Field(4.20, description="Feeder trucking rate INR/tonne-km")
    rail_first_mile_handling: Optional[float] = Field(120.0, description="First-mile goods shed handling INR/tonne")
    rail_handling_and_siding: Optional[float] = Field(220.0, description="Rail terminal handling INR/tonne")
    rail_commercial_speed_kmh: Optional[float] = Field(25.0, description="Average rail speed km/h")
    rail_yard_detention_hours: Optional[float] = Field(12.0, description="Rail marshalling delay hours")
    
    dfc_linehaul_rate: Optional[float] = Field(1.12, description="DFC linehaul rate INR/tonne-km")
    dfc_handling_cost: Optional[float] = Field(180.0, description="DFC terminal handling INR/tonne")
    dfc_commercial_speed_kmh: Optional[float] = Field(60.0, description="DFC timetable speed km/h")
    dfc_yard_transfer_hours: Optional[float] = Field(3.0, description="DFC transfer delay hours")
    
    inventory_holding_rate: Optional[float] = Field(7.50, description="Working capital delay cost INR/tonne-hour")


class FreightCostSimulationRequest(BaseModel):
    origin_district: Optional[str] = Field(None, description="Origin district name, e.g. 'Pune' or 'Indore'")
    origin_district_code: Optional[int] = Field(None, description="LGD District Code, e.g. 521")
    target_port: Optional[str] = Field(None, description="Optional target port name; defaults to nearest major port")
    payload_tons: Optional[float] = Field(20.0, description="Shipment weight in metric tonnes")
    commodity_name: Optional[str] = Field("General Merchandise", description="Commodity description")
    custom_parameters: Optional[CostParametersOverride] = None


class ModalCostBreakdown(BaseModel):
    cost_per_ton_inr: float
    total_shipment_cost_inr: float
    transit_time_hours: float


class FreightCostSimulationResponse(BaseModel):
    origin_district: str
    state: str
    target_port: str
    road_distance_km: float
    road: ModalCostBreakdown
    conventional_rail: ModalCostBreakdown
    dfc_rail: Optional[ModalCostBreakdown] = None
    optimal_mode: str
    modal_shift_savings_per_ton_inr: float
    modal_shift_savings_total_inr: float
    modal_shift_savings_pct: float
    break_even_distance_km: float


class PortGravitySimulationRequest(BaseModel):
    alpha: Optional[float] = Field(0.85, description="Port capacity sensitivity exponent")
    beta: Optional[float] = Field(1.65, description="Distance decay friction exponent")
    custom_port_capacities: Optional[Dict[str, float]] = Field(None, description="Custom capacity in MT for specific ports")


class PortMarketShareItem(BaseModel):
    port_name: str
    captured_districts_count: int
    market_share_districts_pct: float
    captured_population_cr: float
