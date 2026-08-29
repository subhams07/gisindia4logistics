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
    LocationReference,
    ResolvedLocation,
    RouteQuality,
    RoutePoint,
    HighwayRouteResult,
    RouteTollPlaza,
    TollSummary,
    ModalScenario,
    CorridorPlanRequest,
    CorridorPlanResponse,
    DistrictReference,
    DistrictComparisonRequest,
    DistrictMetricResult,
    DistrictComparisonItem,
    DistrictComparisonResponse,
    GeneratedReport,
)
from server.services.metadata_service import MetadataService
from server.app import app

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "docs" / "contracts"


def test_metadata_service_loads_manifest():
    """Verifies that MetadataService loads data/version_manifest.json and builds ResponseMetadata."""
    svc = MetadataService.get_instance()
    assert svc.api_version == "1.0.0"
    assert svc.data_version == "2026.08"
    assert svc.model_version == "phase1-decision-workbench"

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


def test_location_reference_discriminated_union():
    """Verifies discriminated union parsing for coordinate, district, and hub references."""
    # 1. Coordinate
    coord = CoordinateLocation(latitude=18.5204, longitude=73.8567, label="Pune City")
    assert coord.type == "coordinate"

    # Out of bounds latitude
    with pytest.raises(ValidationError):
        CoordinateLocation(latitude=95.0, longitude=73.0)

    # 2. District
    dist = DistrictLocation(state="Maharashtra", district="Pune", district_code=505)
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
