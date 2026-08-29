"""
tests/test_phase1_contracts.py
Unit and contract validation test suite for Phase 1 Decision Workbench models and metadata service.
"""

import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from server.models.phase1 import (
    ResponseMetadata,
    CoordinateLocation,
    DistrictLocation,
    HubLocation,
    VehicleType,
    RouteTollPlaza,
    CorridorPlanRequest,
    CorridorPlanResponse,
    DistrictReference,
    DistrictComparisonRequest,
    DistrictComparisonResponse,
)
from server.models.schemas import CostParametersOverride
from server.services.metadata_service import MetadataService
from server.app import app

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = REPO_ROOT / "docs" / "contracts"


def test_all_version_manifest_paths_exist():
    """Validates that every dataset path declared in version_manifest.json exists on disk and is non-empty."""
    manifest_file = REPO_ROOT / "data" / "version_manifest.json"
    assert manifest_file.exists(), "data/version_manifest.json does not exist"

    with open(manifest_file, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    datasets = manifest.get("datasets", {})
    assert len(datasets) >= 15, "Manifest must contain all core datasets"

    required_keys = {"vintage", "source", "path", "feature_count"}
    for name, item in datasets.items():
        assert isinstance(item, dict), f"Dataset entry '{name}' must be an object"
        missing = required_keys - set(item.keys())
        assert not missing, f"Dataset entry '{name}' is missing required fields: {missing}"

        rel_path = item["path"]
        abs_path = REPO_ROOT / rel_path
        assert abs_path.exists(), f"Declared dataset path '{rel_path}' for '{name}' does not exist on disk"
        assert abs_path.stat().st_size > 0, f"Declared dataset file '{rel_path}' is empty"


def test_metadata_service_loads_manifest():
    """Verifies that MetadataService loads data/version_manifest.json and builds ResponseMetadata."""
    svc = MetadataService.get_instance()
    assert svc.api_version == "1.0.0"
    assert svc.data_version == "2026.08"
    assert svc.model_version == "phase1-decision-workbench"
    assert svc.package_version is not None

    meta = svc.get_metadata()
    assert isinstance(meta, ResponseMetadata)
    assert meta.api_version == "1.0.0"
    assert meta.data_version == "2026.08"
    assert meta.road_network_vintage == "2026-08"
    assert meta.port_capacity_vintage == "FY 2019-20 to FY 2023-24"
    assert len(meta.limitations) >= 1


def test_metadata_service_fallback_on_missing_manifest(tmp_path: Path):
    """Verifies that MetadataService handles missing manifest gracefully with defaults."""
    non_existent = tmp_path / "missing_manifest.json"
    svc = MetadataService(manifest_path=non_existent)
    meta = svc.get_metadata()
    assert meta.api_version == "1.0.0"
    assert meta.data_version == "2026.08"


def test_canonical_vehicle_types():
    """Verifies canonical vehicle enum supports existing and Phase 1 vehicle classes."""
    assert VehicleType.LMV == "LMV"
    assert VehicleType.LCV == "LCV"
    assert VehicleType.TRUCK_2AXLE == "2_AXLE_TRUCK"
    assert VehicleType.MAV_20T == "MAV_20T"
    assert VehicleType.OVERSIZED_7AXLE == "7_AXLE_OVERSIZED"


def test_input_models_reject_empty_references():
    """Verifies that input models reject structurally empty references at model validation time."""
    # Empty DistrictLocation
    with pytest.raises(ValidationError):
        DistrictLocation()

    with pytest.raises(ValidationError):
        DistrictLocation(district="")

    # Valid DistrictLocation
    d1 = DistrictLocation(district="Pune")
    assert d1.district == "Pune"
    d2 = DistrictLocation(district_code=521)
    assert d2.district_code == 521

    # Empty HubLocation
    with pytest.raises(ValidationError):
        HubLocation(hub_type="port")

    with pytest.raises(ValidationError):
        HubLocation(hub_type="port", name="")

    # Valid HubLocation
    h1 = HubLocation(hub_type="port", name="JNPT")
    assert h1.name == "JNPT"
    h2 = HubLocation(hub_type="port", code="INNSA")
    assert h2.code == "INNSA"

    # Empty DistrictReference
    with pytest.raises(ValidationError):
        DistrictReference()

    with pytest.raises(ValidationError):
        DistrictReference(district="")

    # Valid DistrictReference
    ref = DistrictReference(district="Pune")
    assert ref.district == "Pune"


def test_toll_tariff_consistency():
    """Verifies tariff rate consistency with tariff_status."""
    # Modelled status with non-null tariff_inr passes
    p1 = RouteTollPlaza(
        toll_plaza_id="toll_1",
        name="Plaza 1",
        latitude=18.5,
        longitude=73.5,
        distance_from_route_m=50.0,
        distance_along_route_km=25.0,
        match_confidence="high",
        match_score=90.0,
        tariff_inr=340.0,
        tariff_status="modelled"
    )
    assert p1.tariff_inr == 340.0

    # Modelled status with None tariff_inr fails validation
    with pytest.raises(ValidationError):
        RouteTollPlaza(
            toll_plaza_id="toll_2",
            name="Plaza 2",
            latitude=18.5,
            longitude=73.5,
            distance_from_route_m=50.0,
            distance_along_route_km=25.0,
            match_confidence="high",
            match_score=90.0,
            tariff_inr=None,
            tariff_status="modelled"
        )

    # Unknown status with None tariff_inr passes cleanly
    p3 = RouteTollPlaza(
        toll_plaza_id="toll_3",
        name="Plaza 3",
        latitude=18.5,
        longitude=73.5,
        distance_from_route_m=50.0,
        distance_along_route_km=25.0,
        match_confidence="medium",
        match_score=70.0,
        tariff_inr=None,
        tariff_status="unknown"
    )
    assert p3.tariff_inr is None


def test_numeric_bounds_validation():
    """Verifies non-negative constraints and score bounds on domain models."""
    # Negative match_score fails
    with pytest.raises(ValidationError):
        RouteTollPlaza(
            toll_plaza_id="toll_1",
            name="Plaza",
            latitude=18.5,
            longitude=73.5,
            distance_from_route_m=-10.0,
            distance_along_route_km=10.0,
            match_confidence="high",
            match_score=105.0,  # >100 fails
            tariff_inr=100.0,
            tariff_status="modelled"
        )


def test_corridor_plan_request_supports_cost_overrides():
    """Verifies CorridorPlanRequest accepts optional cost_parameters overrides."""
    req = CorridorPlanRequest(
        origin=DistrictLocation(district="Pune", state="Maharashtra"),
        destination=HubLocation(hub_type="port", name="JNPT"),
        cost_parameters=CostParametersOverride(road_linehaul_rate=3.50)
    )
    assert req.cost_parameters is not None
    assert req.cost_parameters.road_linehaul_rate == 3.50


def test_location_reference_discriminated_union():
    """Verifies discriminated union parsing for coordinate, district, and hub references."""
    # 1. Coordinate
    coord = CoordinateLocation(latitude=18.5204, longitude=73.8567, label="Pune City")
    assert coord.type == "coordinate"

    # Out of bounds latitude
    with pytest.raises(ValidationError):
        CoordinateLocation(latitude=95.0, longitude=73.0)

    # 2. District
    dist = DistrictLocation(state="Maharashtra", district="Pune", district_code=521)
    assert dist.type == "district"

    # 3. Hub
    hub = HubLocation(hub_type="port", name="JNPT", code="INNSA")
    assert hub.type == "hub"

    # Invalid hub category
    with pytest.raises(ValidationError):
        HubLocation(hub_type="invalid_category", name="Unknown")


def test_district_comparison_request_bounds():
    """Verifies min and max bounds on district comparison request (2 to 50 districts)."""
    # 1 district fails (min 2)
    with pytest.raises(ValidationError):
        DistrictComparisonRequest(districts=[DistrictReference(district="Pune", state="Maharashtra")])

    # 2 districts pass
    req = DistrictComparisonRequest(districts=[
        DistrictReference(district="Pune", state="Maharashtra"),
        DistrictReference(district="Indore", state="Madhya Pradesh")
    ])
    assert len(req.districts) == 2

    # >50 districts fail
    fifty_one = [DistrictReference(district=f"Dist_{i}", state="State") for i in range(51)]
    with pytest.raises(ValidationError):
        DistrictComparisonRequest(districts=fifty_one)


def test_frozen_contracts_parse_cleanly():
    """Validates that all committed JSON contract examples deserialize into Pydantic models."""
    # 1. Response Metadata
    with open(CONTRACTS_DIR / "response_metadata.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        meta = ResponseMetadata.model_validate(data)
        assert meta.api_version == "1.0.0"

    # 2. Corridor Plan Request
    with open(CONTRACTS_DIR / "corridor_plan_request.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        req = CorridorPlanRequest.model_validate(data)
        assert req.payload_tons == 25.0
        assert req.origin.type == "district"
        assert req.destination.type == "hub"

    # 3. Corridor Plan Response
    with open(CONTRACTS_DIR / "corridor_plan_response.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        resp = CorridorPlanResponse.model_validate(data)
        assert resp.recommended_mode == "road"
        assert resp.tolls.matched_plaza_count == 2
        assert len(resp.modal_scenarios) == 3

    # 4. District Comparison Request
    with open(CONTRACTS_DIR / "district_comparison_request.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        d_req = DistrictComparisonRequest.model_validate(data)
        assert len(d_req.districts) == 3

    # 5. District Comparison Response
    with open(CONTRACTS_DIR / "district_comparison_response.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        d_resp = DistrictComparisonResponse.model_validate(data)
        assert len(d_resp.districts) == 3
        assert d_resp.districts[0].rank == 1


def test_fastapi_version_headers():
    """Verifies that FastAPI returns X-GIS4L version headers on HTTP responses."""
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers.get("X-GIS4L-API-Version") == "1.0.0"
    assert resp.headers.get("X-GIS4L-Data-Version") == "2026.08"
    assert resp.headers.get("X-GIS4L-Model-Version") == "phase1-decision-workbench"
