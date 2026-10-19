"""Shared route foundation, not a public transport adapter.

Only resolver/provenance service instances are cached (at most eight roots), never
stores or graph generations. Same-root alias/boundary/manifest file changes require
clear_routing_facade_cache() or process recreation. RoutingService acquires the
coherent graph snapshot separately on every request. Explicit stores never trigger
DataStore creation; raw historical fixtures without data_dir use settings only.
"""
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from server.config import settings
from server.dependencies import DataStore, get_data_store
from server.models.phase1 import (
    CoordinateLocation, HighwayRoutePlanRequest, HighwayRouteResult, ResponseMetadata,
)
from server.models.schemas import RouteRequest, RouteResponse
from server.services.location_resolver import LocationResolver
from server.services.exceptions import DataIntegrityError
from server.services.metadata_service import MetadataService
from server.services.routing_service import RoutingService
from server.services.serialization import serialize_result


@lru_cache(maxsize=8)
def _service_for_root(root: Path) -> RoutingService:
    return RoutingService(resolver=LocationResolver(data_dir=root),
                          metadata_service=MetadataService(manifest_path=root / "version_manifest.json"))


def _service_for_store(store: DataStore) -> RoutingService:
    root = Path(getattr(store, "data_dir", settings.DATA_PATH)).resolve()
    return _service_for_root(root)


def clear_routing_facade_cache() -> None:
    """Recreate root-scoped resolver/provenance snapshots on next use."""
    _service_for_root.cache_clear()


def metadata_for_store(store: DataStore | None = None) -> ResponseMetadata:
    """Fresh metadata from the same root-scoped provider used in route payloads."""
    if store is None:
        store = get_data_store()
    return _service_for_store(store).metadata_service.get_metadata()


def plan_highway_route(
    request: HighwayRoutePlanRequest, store: DataStore | None = None,
) -> HighwayRouteResult:
    """Delegate once; resolution, graph lifecycle and provenance belong to engine."""
    if not isinstance(request, HighwayRoutePlanRequest):
        request = HighwayRoutePlanRequest.model_validate(request)
    # Pydantic trusts existing model instances, including mutated nested fields.
    request = HighwayRoutePlanRequest.model_validate(request.model_dump(mode="python", warnings=False))
    if store is None:
        store = get_data_store()
    try:
        return _service_for_store(store).calculate_highway_route(
            origin=request.origin, destination=request.destination, vehicle_type=request.vehicle_type,
            include_route_geometry=request.include_route_geometry,
            geometry_detail=request.geometry_detail, store=store,
        )
    except ValidationError:
        raise DataIntegrityError("Calculated highway route is invalid.") from None


def legacy_highway_route(req: RouteRequest, store: DataStore | None = None) -> dict[str, Any]:
    """Strict JSON legacy envelope; tolls are a distance-based model, not tariffs."""
    if isinstance(req, RouteRequest):
        req = req.model_dump(mode="python", warnings=False)
    req = RouteRequest.model_validate(req)
    result = plan_highway_route(HighwayRoutePlanRequest(
        origin=CoordinateLocation(latitude=req.origin[0], longitude=req.origin[1]),
        destination=CoordinateLocation(latitude=req.destination[0], longitude=req.destination[1]),
        vehicle_type=req.vehicle_type,
    ), store=store)
    # Check canonical objects before rounding or model JSON serializers can hide NaN.
    encoded = serialize_result(result)
    try:
        canonical = HighwayRouteResult.model_validate(encoded)
    except ValidationError:
        raise DataIntegrityError("Calculated highway route is invalid.") from None
    distance = canonical.distance_km
    count = int(max(round(distance / 65.0), 1)) if distance >= 40.0 else 0
    rate = 340.0 if req.vehicle_type == "MAV_20T" else 120.0 if req.vehicle_type == "LMV" else 220.0
    envelope = RouteResponse(
        distance_km=round(distance, 1), drive_time_hours=round(canonical.drive_time_hours, 2),
        drive_time_formatted=canonical.drive_time_formatted, tolls_encountered_count=count,
        estimated_toll_cost_inr=round(count * rate, 2),
        toll_estimation_method="distance-based estimate using average 65 km toll spacing; not route-plaza intersection; modelled vehicle rates, not official tariffs",
        routing_scope=canonical.routing_scope,
        origin_snapped=[canonical.origin_snapped.latitude, canonical.origin_snapped.longitude],
        destination_snapped=[canonical.destination_snapped.latitude, canonical.destination_snapped.longitude],
        route_quality=serialize_result(canonical.route_quality),
        metadata=serialize_result(canonical.metadata),
    )
    return serialize_result(envelope)
