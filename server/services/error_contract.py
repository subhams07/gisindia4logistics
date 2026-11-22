"""Framework-independent safe routing errors; no HTTP handlers installed here."""
import json
import math
from typing import Any

from pydantic import ValidationError

from server.services.exceptions import (
    AmbiguousLocationError, DataIntegrityError, InvalidLocationError,
    LocationNotFoundError, RouteNotAvailableError, UnsupportedScenarioError,
)

_ERROR_RULES = (
    (ValidationError, 422, "invalid_request", "Request input is invalid."),
    (AmbiguousLocationError, 409, "ambiguous_location", "Location is ambiguous; supply distinguishing identifiers."),
    (InvalidLocationError, 422, "invalid_location", "Location input is invalid or conflicting."),
    (RouteNotAvailableError, 422, "route_not_available", "No supported highway route is available between these locations."),
    (LocationNotFoundError, 404, "location_not_found", "Location was not found."),
    (DataIntegrityError, 503, "data_integrity", "Routing data is unavailable or invalid."),
    (UnsupportedScenarioError, 422, "unsupported_scenario", "The requested scenario is unsupported."),
)
_CANDIDATE_TEXT_FIELDS = frozenset({
    "name", "canonical_name", "district", "state", "code", "hub_type", "source_dataset",
})


def _safe_candidates(candidates: Any) -> list[dict[str, Any]]:
    """Allowlisted scalar candidates only; malformed candidates are withheld."""
    output = []
    if not isinstance(candidates, list):
        return output
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        safe = {}
        valid = True
        for field, value in candidate.items():
            if field in _CANDIDATE_TEXT_FIELDS:
                valid = valid and (value is None or isinstance(value, str))
                safe[field] = value
            elif field == "district_code":
                valid = valid and (value is None or type(value) is int and value >= 1)
                safe[field] = value
            elif field in ("latitude", "longitude"):
                bound = 90 if field == "latitude" else 180
                valid = valid and (value is None or type(value) in (float, int)
                                   and -bound <= value <= bound and math.isfinite(value))
                safe[field] = value
        if valid and safe:
            # A final strict-JSON check protects future allowlist changes.
            json.dumps(safe, allow_nan=False)
            output.append(safe)
    return output


def route_error_contract(error: Exception) -> dict[str, Any]:
    """Return status/code/fixed safe message, classifying solely by domain type."""
    result = dict(status=500, code="internal_error", message="An internal routing error occurred.")
    for cls, status, code, message in _ERROR_RULES:
        if isinstance(error, cls):
            result = dict(status=status, code=code, message=message)
            break
    if isinstance(error, AmbiguousLocationError):
        result["candidates"] = _safe_candidates(error.candidates)
    return result


def domain_error_from_status(status: int) -> Exception:
    """Safe lossy bridge for old HTTP errors; never inspect or echo their detail.

    A 422 cannot distinguish impossible route from invalid input without a domain
    type, so the legacy bridge returns InvalidLocationError. Canonical adapters
    should retain domain exceptions rather than round-trip through this bridge.
    """
    cls = {400: InvalidLocationError, 409: AmbiguousLocationError,
           422: InvalidLocationError, 404: LocationNotFoundError,
           503: DataIntegrityError}.get(status, RuntimeError)
    return cls(route_error_contract(cls(""))["message"])


_HTTP_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 409: "ambiguous_location",
               422: "invalid_request", 503: "data_unavailable"}


def http_error_contract(status: int, detail: Any) -> dict[str, Any]:
    """The structured error body for an HTTPException raised by a router.

    Same keys as route_error_contract: status, code, message (the router's own
    fixed text), plus allowlisted candidates for a 409 that carries them.
    """
    result = dict(status=status, code=_HTTP_CODES.get(status, "http_error" if status < 500 else "internal_error"))
    if isinstance(detail, str) and detail:
        result["message"] = detail
    elif isinstance(detail, dict) and isinstance(detail.get("message"), str):
        result["message"] = detail["message"]
    else:
        result["message"] = "Request failed." if status < 500 else "The service is unavailable."
    if isinstance(detail, dict) and "candidates" in detail:
        result["candidates"] = _safe_candidates(detail["candidates"])
    return result
