"""
server/models/phase1.py
Frozen Domain Models and API Schemas for GISIndia4Logistics Decision Workbench (Phase 1).
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Literal, Optional, List, Dict, Any, Annotated, Union
from pydantic import BaseModel, Field, ConfigDict, model_validator

from server.models.schemas import CostParametersOverride


# ==========================================
# 1. Response Metadata & Provenance
# ==========================================

class ResponseMetadata(BaseModel):
    """Provenance and audit metadata returned with every Decision Workbench analytical payload."""
    model_config = ConfigDict(extra="forbid")

    api_version: str = Field(..., description="Semantic API version, e.g. '1.0.0'")
    package_version: str = Field(..., description="Python package version, e.g. '1.0.0'")
    model_version: str = Field(..., description="Analytical model version, e.g. 'phase1-decision-workbench'")
    data_version: str = Field(..., description="Data release version, e.g. '2026.08'")
    generated_at_utc: datetime = Field(..., description="ISO 8601 UTC timestamp of response generation")

    road_network_vintage: Optional[str] = Field(None, description="National Highway network layer vintage")
    port_capacity_vintage: Optional[str] = Field(None, description="Port throughput reference vintage")
    population_vintage: Optional[str] = Field(None, description="Population allocation reference vintage")

    assumptions_url: str = Field(..., description="Link to analytical methodology and parameter assumptions")
    sources_url: str = Field(..., description="Link to upstream data sources and citations")
    limitations: List[str] = Field(default_factory=list, description="Explicit modeling caveats and boundary disclaimers")


# ==========================================
# 2. Strict Location Resolution Models
# ==========================================

class CoordinateLocation(BaseModel):
    """Explicit coordinate location reference [latitude, longitude]."""
    type: Literal["coordinate"] = "coordinate"
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees WGS 84")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees WGS 84")
    label: Optional[str] = Field(None, description="Optional human-readable label")


class DistrictLocation(BaseModel):
    """District administrative location reference."""
    type: Literal["district"] = "district"
    district: Optional[str] = Field(None, description="District name, e.g. 'Pune' or 'Indore'")
    district_code: Optional[int] = Field(None, ge=1, description="Official LGD district code, e.g. 521")
    state: Optional[str] = Field(None, description="State or UT name, e.g. 'Maharashtra'")

    @model_validator(mode="after")
    def validate_district_presence(self) -> "DistrictLocation":
        if not (self.district and self.district.strip()) and self.district_code is None:
            raise ValueError("Either 'district' (name) or 'district_code' (LGD integer) must be provided")
        return self


class HubLocation(BaseModel):
    """Logistics facility hub location reference."""
    type: Literal["hub"] = "hub"
    hub_type: Literal[
        "port",
        "icd",
        "mmlp",
        "freight_terminal",
        "rail_station",
        "air_cargo",
        "iw_terminal",
        "icp",
        "fci_depot",
        "cold_chain",
        "mandi"
    ] = Field(..., description="Category of logistics hub")
    name: Optional[str] = Field(None, description="Name or abbreviation of hub, e.g. 'JNPT' or 'Dadri ICD'")
    code: Optional[str] = Field(None, description="Official station or facility code, e.g. 'PUNE' or 'INNSA'")

    @model_validator(mode="after")
    def validate_hub_presence(self) -> "HubLocation":
        if not (self.name and self.name.strip()) and not (self.code and self.code.strip()):
            raise ValueError("Either 'name' or 'code' must be provided for hub location reference")
        return self


LocationReference = Annotated[
    Union[CoordinateLocation, DistrictLocation, HubLocation],
    Field(discriminator="type")
]


class ResolvedLocation(BaseModel):
    """Canonical resolved location with validated coordinates and administrative context."""
    type: str = Field(..., description="'coordinate', 'district', or 'hub'")
    canonical_name: str = Field(..., description="Standardized canonical name of the resolved entity")
    state: Optional[str] = Field(None, description="State or UT name if applicable")
    district: Optional[str] = Field(None, description="District name if applicable")
    district_code: Optional[int] = Field(None, ge=1, description="LGD district code if applicable")
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Resolved latitude WGS 84")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Resolved longitude WGS 84")
    source_dataset: str = Field(..., description="Source dataset used for resolution")
    match_method: Literal["exact_code", "exact_name", "alias_lookup", "coordinate"] = Field(
        ..., description="Method used to resolve the location"
    )


# ==========================================
# 3. Route Geometry & Quality Diagnostics
# ==========================================

class VehicleType(str, Enum):
    LMV = "LMV"
    LCV = "LCV"
    TRUCK_2AXLE = "2_AXLE_TRUCK"
    MAV_20T = "MAV_20T"
    OVERSIZED_7AXLE = "7_AXLE_OVERSIZED"


class RoutePoint(BaseModel):
    """Single geographic point with named coordinates."""
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)


class RouteQuality(BaseModel):
    """Diagnostic indicators detailing the topological quality of the modeled route."""
    network_scope: Literal["national_highway"] = "national_highway"
    origin_snap_distance_km: float = Field(..., ge=0.0, description="Feeder access distance from origin coordinate to network (km)")
    destination_snap_distance_km: float = Field(..., ge=0.0, description="Feeder egress distance from network to destination coordinate (km)")
    connected_component: int = Field(..., ge=0, description="ID of the graph connected component traversed")
    synthetic_bridge_count: int = Field(..., ge=0, description="Count of synthetic topological junction bridges traversed")
    synthetic_bridge_distance_km: float = Field(..., ge=0.0, description="Total length over synthetic topological bridges (km)")
    maximum_synthetic_bridge_m: float = Field(..., ge=0.0, description="Maximum span of a single synthetic bridge (m)")
    geometry_simplified: bool = Field(..., description="Whether Douglas-Peucker simplification was applied")
    geometry_tolerance_m: Optional[float] = Field(None, ge=0.0, description="Simplification tolerance in meters")
    quality: Literal["network_exact", "modelled_connectivity", "low_confidence"] = Field(
        ..., description="Overall routing confidence classification"
    )
    warnings: List[str] = Field(default_factory=list, description="Diagnostic warnings regarding route feasibility")


class HighwayRouteResult(BaseModel):
    """Complete strategic highway route calculation with vector geometry and diagnostics."""
    origin: ResolvedLocation
    destination: ResolvedLocation

    distance_km: float = Field(..., ge=0.0, description="Total road distance including modeled feeder connections (km)")
    drive_time_hours: float = Field(..., ge=0.0, description="Total driving duration (hours)")
    drive_time_formatted: str = Field(..., description="Formatted duration string, e.g. '1 hrs 57 min'")

    origin_access_distance_km: float = Field(..., ge=0.0, description="Feeder distance from origin to NH entry node (km)")
    destination_access_distance_km: float = Field(..., ge=0.0, description="Feeder distance from NH exit node to destination (km)")
    network_distance_km: float = Field(..., ge=0.0, description="Distance traversed strictly on the National Highway network (km)")

    origin_snapped: RoutePoint = Field(..., description="Coordinates of the network entry node")
    destination_snapped: RoutePoint = Field(..., description="Coordinates of the network exit node")

    route_geometry: Optional[Dict[str, Any]] = Field(
        None,
        description="GeoJSON LineString with coordinates [[lon, lat], ...] in EPSG:4326"
    )

    route_quality: RouteQuality = Field(..., description="Topological diagnostics and synthetic bridge disclosure")
    routing_scope: str = Field(
        "strategic National Highway graph with modeled feeder access; not turn-by-turn navigation",
        description="Operational scope disclaimer"
    )
    metadata: ResponseMetadata


# ==========================================
# 4. Route-Matched Toll Plazas
# ==========================================

class RouteTollPlaza(BaseModel):
    """Individual toll plaza matched along the route corridor."""
    toll_plaza_id: str = Field(..., description="Deterministic unique identifier for the toll plaza")
    name: str = Field(..., description="Toll plaza name")
    state: Optional[str] = Field(None, description="State containing the plaza")
    district: Optional[str] = Field(None, description="District containing the plaza")
    nh_number: Optional[str] = Field(None, description="National Highway number, e.g. 'NH48'")

    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)

    distance_from_route_m: float = Field(..., ge=0.0, description="Perpendicular distance from route centerline (meters)")
    distance_along_route_km: float = Field(..., ge=0.0, description="Distance along route from origin (km)")

    match_confidence: Literal["high", "medium", "low"] = Field(..., description="Spatial matching confidence tier")
    match_score: float = Field(..., ge=0.0, le=100.0, description="Calculated matching score (0-100)")

    tariff_inr: Optional[float] = Field(None, ge=0.0, description="Applicable toll rate for the requested vehicle type (INR)")
    tariff_status: Literal["official", "stale_official", "modelled", "unknown"] = Field(
        ..., description="Verification status of the applied tariff rate"
    )
    tariff_source_url: Optional[str] = Field(None, description="Source URL for official tariff schedules if available")

    @model_validator(mode="after")
    def validate_tariff_consistency(self) -> "RouteTollPlaza":
        if self.tariff_status in ("official", "stale_official", "modelled") and self.tariff_inr is None:
            raise ValueError(f"tariff_inr must be non-null when tariff_status is '{self.tariff_status}'")
        return self


class TollSummary(BaseModel):
    """Summary of all toll plazas matched along the calculated highway route."""
    matched_plaza_count: int = Field(..., ge=0, description="Number of unique toll plazas matched along the route")
    matched_plazas: List[RouteTollPlaza] = Field(default_factory=list, description="Ordered sequence of plazas from origin to destination")

    estimated_total_inr: float = Field(..., ge=0.0, description="Total estimated toll fee for the shipment (INR)")
    official_total_inr: Optional[float] = Field(None, ge=0.0, description="Sum of strictly verified official tariffs if available (INR)")

    tariff_method: str = Field(
        "route-matched toll plazas multiplied by standard vehicle-class tariff model; not official quotation",
        description="Calculation methodology disclosure"
    )
    unmatched_tariff_count: int = Field(0, ge=0, description="Count of matched plazas lacking official published rates")
    warnings: List[str] = Field(default_factory=list)


# ==========================================
# 5. Multimodal Corridor Planner Models
# ==========================================

class ModalCostBreakdown(BaseModel):
    cost_per_ton_inr: float = Field(..., ge=0.0)
    total_shipment_cost_inr: float = Field(..., ge=0.0)
    transit_time_hours: float = Field(..., ge=0.0)


class ModalScenario(BaseModel):
    """Comparative scenario result for a specific transport mode."""
    mode: Literal["road", "conventional_rail", "dfc"] = Field(..., description="Transport mode")
    feasible: bool = Field(..., description="Whether this mode is operationally feasible for the corridor")
    infeasibility_reason: Optional[str] = Field(None, description="Explanation if the mode is not feasible")

    distance_km: Optional[float] = Field(None, ge=0.0, description="Mode-specific distance (km)")
    transit_time_hours: Optional[float] = Field(None, ge=0.0, description="Total door-to-door transit time (hours)")

    cost_per_ton_inr: Optional[float] = Field(None, ge=0.0, description="Cost per metric tonne (INR)")
    total_shipment_cost_inr: Optional[float] = Field(None, ge=0.0, description="Total shipment expense (INR)")

    first_mile_km: Optional[float] = Field(None, ge=0.0, description="First-mile road access distance (km)")
    last_mile_km: Optional[float] = Field(None, ge=0.0, description="Last-mile road egress distance (km)")

    transfer_count: int = Field(0, ge=0, description="Number of intermodal transshipment / siding transfers required")
    assumptions: List[str] = Field(default_factory=list, description="Mode-specific operational assumptions")
    warnings: List[str] = Field(default_factory=list)


class CorridorPlanRequest(BaseModel):
    """High-level corridor planning request orchestrating routing, tolls, and intermodal modal shift."""
    origin: LocationReference = Field(..., description="Origin location reference (coordinate, district, or hub)")
    destination: LocationReference = Field(..., description="Destination location reference (coordinate, district, or hub)")

    vehicle_type: VehicleType = Field(VehicleType.MAV_20T, description="Commercial vehicle type for road leg")
    payload_tons: float = Field(20.0, gt=0.0, le=1000.0, description="Shipment payload in metric tonnes")
    commodity: str = Field("General Merchandise", description="Cargo commodity classification")

    cost_parameters: Optional[CostParametersOverride] = Field(
        None, description="Optional overrides for modal transport cost and operational parameters"
    )

    include_road: bool = Field(True, description="Evaluate road trucking scenario")
    include_rail: bool = Field(True, description="Evaluate conventional rail freight scenario")
    include_dfc: bool = Field(True, description="Evaluate Dedicated Freight Corridor scenario if corridor eligible")

    include_route_geometry: bool = Field(True, description="Include GeoJSON LineString in response")
    geometry_detail: Literal["full", "simplified", "none"] = Field("simplified", description="Level of geometry detail")


class CorridorPlanResponse(BaseModel):
    """Comprehensive decision-ready corridor comparison response."""
    origin: ResolvedLocation
    destination: ResolvedLocation

    road_route: Optional[HighwayRouteResult] = Field(None, description="Highway route result if road evaluated")
    tolls: Optional[TollSummary] = Field(None, description="Route-matched toll plaza summary")

    modal_scenarios: List[ModalScenario] = Field(..., description="Comparative evaluations across transport modes")

    recommended_mode: Optional[str] = Field(None, description="Recommended least-cost or optimal mode")
    recommendation_reason: str = Field(..., description="Transparent rationale balancing cost, duration, and transfers")

    metadata: ResponseMetadata


# ==========================================
# 6. Multi-Criteria District Comparison
# ==========================================

class MetricDefinition(BaseModel):
    """Definition of an allowable district comparison metric in the registry."""
    key: str = Field(..., description="Metric identifier, e.g. 'port_drive_time_hours'")
    label: str = Field(..., description="Human-readable title")
    unit: str = Field(..., description="Measurement unit, e.g. 'hours' or 'km'")
    direction: Literal["lower_is_better", "higher_is_better"] = Field(..., description="Scoring directionality")
    source_column: str = Field(..., description="Underlying dataset column name")
    default_weight: float = Field(..., ge=0.0, le=1.0, description="Default scoring weight (0-1)")
    description: str = Field(..., description="Metric methodology explanation")


class DistrictReference(BaseModel):
    """Reference identifying a single district for batch comparison."""
    state: Optional[str] = Field(None, description="State name, e.g. 'Maharashtra'")
    district: Optional[str] = Field(None, description="District name, e.g. 'Pune'")
    district_code: Optional[int] = Field(None, ge=1, description="LGD district code, e.g. 521")

    @model_validator(mode="after")
    def validate_district_reference(self) -> "DistrictReference":
        if not (self.district and self.district.strip()) and self.district_code is None:
            raise ValueError("Either 'district' (name) or 'district_code' (LGD integer) must be provided")
        return self


class DistrictComparisonRequest(BaseModel):
    """Request to rank and compare 2 to 50 districts across standardized logistics metrics."""
    districts: List[DistrictReference] = Field(
        ..., min_length=2, max_length=50, description="List of 2 to 50 districts to compare"
    )
    metrics: Optional[List[str]] = Field(
        None, description="List of registered metric keys to include. Defaults to standard baseline set."
    )
    weights: Optional[Dict[str, float]] = Field(
        None, description="Custom weights per metric. Automatically normalized to sum to 1.0."
    )
    missing_value_policy: Literal[
        "exclude_metric",
        "worst_score",
        "state_median",
        "national_median"
    ] = Field("exclude_metric", description="Treatment policy for missing indicators")


class DistrictMetricResult(BaseModel):
    """Evaluated indicator value and normalized percentiles for a single district."""
    metric: str
    label: str
    raw_value: Optional[float]
    unit: str
    national_percentile: Optional[float] = Field(None, ge=0.0, le=100.0)
    state_percentile: Optional[float] = Field(None, ge=0.0, le=100.0)
    score: Optional[float] = Field(None, ge=0.0, le=100.0, description="Direction-adjusted score (0-100, higher is better)")


class DistrictComparisonItem(BaseModel):
    """Ranked comparison scorecard for an individual district."""
    state: str
    district: str
    district_code: Optional[int] = Field(None, ge=1)

    overall_score: Optional[float] = Field(None, ge=0.0, le=100.0, description="Weighted composite logistics score")
    rank: Optional[int] = Field(None, ge=1, description="Rank among the compared cohort (1 is best)")

    metrics: List[DistrictMetricResult] = Field(default_factory=list)
    strengths: List[str] = Field(default_factory=list, description="Top performing metrics in the cohort")
    gaps: List[str] = Field(default_factory=list, description="Lowest performing metrics requiring intervention")


class DistrictComparisonResponse(BaseModel):
    """Ranked district cohort comparison response."""
    districts: List[DistrictComparisonItem] = Field(..., description="Ranked district list")
    weights_used: Dict[str, float] = Field(..., description="Normalized metric weights applied")
    missing_value_policy: str
    cohort_size: int = Field(..., ge=2, le=50, description="Number of districts successfully compared")
    metadata: ResponseMetadata


# ==========================================
# 7. Downloadable Report Models
# ==========================================

class GeneratedReport(BaseModel):
    """Metadata detailing a generated report document ready for download."""
    report_id: str = Field(..., description="Unique UUID identifier for the report")
    report_type: Literal["corridor_report", "district_comparison_report"] = Field(..., description="Report document type")
    formats_available: Dict[str, str] = Field(
        ..., description="Map of available format ('html', 'pdf', 'xlsx') to relative download URL"
    )
    created_at_utc: datetime = Field(..., description="UTC creation timestamp")
    file_size_bytes: Optional[Dict[str, int]] = Field(None, description="File sizes per format")
    metadata: ResponseMetadata
