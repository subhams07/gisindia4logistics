"""Framework-independent safe route failures and observational serialization."""
import json

import pytest
from pydantic import ValidationError

from server.models.phase1 import HighwayRoutePlanRequest
from server.services.error_contract import domain_error_from_status, route_error_contract
from server.services.exceptions import (
    AmbiguousLocationError, InvalidLocationError, RouteNotAvailableError,
    LocationNotFoundError, DataIntegrityError, UnsupportedScenarioError,
)
from server.services.serialization import serialize_nearest_facilities


@pytest.mark.parametrize("cls,status,code", [
    (AmbiguousLocationError, 409, "ambiguous_location"),
    (InvalidLocationError, 422, "invalid_location"),
    (RouteNotAvailableError, 422, "route_not_available"),
    (LocationNotFoundError, 404, "location_not_found"),
    (DataIntegrityError, 503, "data_integrity"),
    (UnsupportedScenarioError, 422, "unsupported_scenario"),
    (RuntimeError, 500, "internal_error"), (ValueError, 500, "internal_error"),
])
def test_matrix_safe_messages(cls, status, code):
    result = route_error_contract(cls("C:/secret/path graph invalid"))
    assert result["status"] == status
    assert result["code"] == code
    assert "secret" not in json.dumps(result, allow_nan=False)


def test_candidates_allowlist():
    result = route_error_contract(AmbiguousLocationError("private", candidates=[
        dict(district="Dhule", district_code=498, state="Maharashtra", path="C:/secret")]))
    assert result["candidates"] == [dict(district="Dhule", district_code=498, state="Maharashtra")]


def test_request_validation_error_has_safe_client_contract():
    with pytest.raises(ValidationError) as caught:
        HighwayRoutePlanRequest.model_validate(dict(
            origin=dict(type="coordinate", latitude="C:/secret/path", longitude=78),
            destination=dict(type="district", district_code=521)))
    result = route_error_contract(caught.value)
    assert result == dict(status=422, code="invalid_request", message="Request input is invalid.")
    assert "secret" not in json.dumps(result, allow_nan=False)


def test_candidate_extreme_integer_fails_closed():
    result = route_error_contract(AmbiguousLocationError("private", candidates=[
        dict(latitude=10 ** 1000, name="invalid coordinate")]))
    assert result["candidates"] == []
    json.dumps(result, allow_nan=False)


def test_hub_candidate_allowlist():
    candidate = dict(name="JNPT", code="INNSA", hub_type="port", source_dataset="ports.csv",
                     latitude=18.95, longitude=72.95, path="C:/secret")
    result = route_error_contract(AmbiguousLocationError("private", candidates=[candidate]))
    assert result["candidates"] == [{k: v for k, v in candidate.items() if k != "path"}]


@pytest.mark.parametrize("candidate", [dict(latitude=float("nan")), dict(district_code=True),
                                       dict(name={"path": "secret"})])
def test_invalid_candidate_fails_closed(candidate):
    result = route_error_contract(AmbiguousLocationError("private", candidates=[candidate]))
    assert result["candidates"] == []
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("status,cls", [(409, AmbiguousLocationError), (422, InvalidLocationError),
                                        (400, InvalidLocationError), (404, LocationNotFoundError),
                                        (503, DataIntegrityError), (500, RuntimeError)])
def test_bridge(status, cls):
    error = domain_error_from_status(status)
    assert isinstance(error, cls)


def test_nearest_policy():
    fields = ("name", "state", "city", "operator", "nh_number", "toll_type")
    result = serialize_nearest_facilities({"ports": [{k: float("nan") for k in fields}]})
    assert all(value is None for value in result["ports"][0].values())
    json.dumps(result, allow_nan=False)
    with pytest.raises(ValueError):
        serialize_nearest_facilities({"distance_km": float("nan")})
    with pytest.raises(ValueError):
        serialize_nearest_facilities({"name": float("inf")})
