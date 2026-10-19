"""Canonical highway routing, threshold-aware snapping and internal network paths."""

import logging
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal, Optional

import numpy as np
import pyproj
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString

from scripts.analyze import highway_pair_kernel
from server.dependencies import DataStore, get_data_store
from server.models.phase1 import (
    HighwayRouteResult,
    LocationReference,
    ResolvedLocation,
    RoutePoint,
    RouteQuality,
    VehicleType,
)
from server.services.exceptions import DataIntegrityError, LocationNotFoundError, RouteNotAvailableError
from server.services.graph_snapshot import graph_for_request
from server.services.location_resolver import LocationResolver, get_location_resolver
from server.services.metadata_service import MetadataService, get_metadata_service

LOGGER = logging.getLogger(__name__)
PROJ_EPSG = 7755
WGS84_EPSG = 4326
ACCESS_SPEED_KMH = 35.0
MAX_FEEDER_SNAP_KM = 150.0
# Diagnostic only: >3x avoids flagging modest feeder detours. Compare unrounded
# projected metres, exclude zero movement, and never infer direct-path routability.
FEEDER_EXCESS_RATIO = 3.0
SIMPLIFY_TOLERANCE_M = 50.0
VEHICLE_SPEED_FACTORS: dict[VehicleType, float] = {
    VehicleType.LMV: 1.0, VehicleType.LCV: 0.95, VehicleType.TRUCK_2AXLE: 0.85,
    VehicleType.MAV_20T: 0.80, VehicleType.OVERSIZED_7AXLE: 0.65,
}


@dataclass(frozen=True)
class RoutePath:
    """Internal EPSG:7755 network vertices and edge kinds, separate from feeders.

    Edge kinds align with adjacent node IDs. Display simplification never changes
    this path. A zero-movement path contains the checked snap node, no traversals.
    """
    node_ids: tuple[int, ...]
    projected_coordinates: tuple[tuple[float, float], ...]
    edge_kinds: tuple[Literal["physical", "bridge"], ...]
    origin_feeder: tuple[tuple[float, float], ...]
    destination_feeder: tuple[tuple[float, float], ...]
    crs_epsg: int = PROJ_EPSG


class RoutingService:
    _instance: Optional["RoutingService"] = None

    def __init__(self, resolver: LocationResolver | None = None,
                 metadata_service: MetadataService | None = None):
        self.resolver = resolver or get_location_resolver()
        self.metadata_service = metadata_service or get_metadata_service()
        self.to_proj = pyproj.Transformer.from_crs(WGS84_EPSG, PROJ_EPSG, always_xy=True)
        self.to_wgs84 = pyproj.Transformer.from_crs(PROJ_EPSG, WGS84_EPSG, always_xy=True)

    @classmethod
    def get_instance(cls) -> "RoutingService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def calculate_highway_route(
        self, origin: LocationReference | ResolvedLocation,
        destination: LocationReference | ResolvedLocation,
        vehicle_type: VehicleType = VehicleType.MAV_20T,
        include_route_geometry: bool = True,
        geometry_detail: Literal["simplified", "full", "none"] = "simplified",
        store: DataStore | None = None,
    ) -> HighwayRouteResult:
        """Compatibility adapter: the public result model is unchanged."""
        return self.calculate_highway_route_with_path(
            origin, destination, vehicle_type, include_route_geometry, geometry_detail, store
        )[0]

    def calculate_highway_route_with_path(
        self, origin: LocationReference | ResolvedLocation,
        destination: LocationReference | ResolvedLocation,
        vehicle_type: VehicleType = VehicleType.MAV_20T,
        include_route_geometry: bool = True,
        geometry_detail: Literal["simplified", "full", "none"] = "simplified",
        store: DataStore | None = None,
    ) -> tuple[HighwayRouteResult, RoutePath]:
        """Minimize compatible feeder snap sum, then fastest graph path for that pair.

        All nodes within 150km per endpoint are considered; nearest per component
        suffices for this snap objective. This is NOT global door-to-door route
        optimization. No mainland preference or minimum component size is applied.
        """
        if geometry_detail not in ("full", "simplified", "none"):
            raise ValueError("Unsupported geometry_detail")
        store = store or get_data_store()
        graph = graph_for_request(store)
        orig = origin if isinstance(origin, ResolvedLocation) else self.resolver.resolve(origin, store)
        dest = destination if isinstance(destination, ResolvedLocation) else self.resolver.resolve(destination, store)
        for loc in (orig, dest):
            if loc.latitude is None or loc.longitude is None:
                raise LocationNotFoundError(f"Location '{loc.canonical_name}' lacks geographic coordinates")
            if (not np.isfinite([loc.longitude, loc.latitude]).all()
                    or not -180 <= loc.longitude <= 180 or not -90 <= loc.latitude <= 90):
                raise LocationNotFoundError("Location coordinates must be finite and within WGS84 bounds")
        o = np.array(self.to_proj.transform(orig.longitude, orig.latitude))
        d = np.array(self.to_proj.transform(dest.longitude, dest.latitude))
        if not np.isfinite([o, d]).all():
            raise LocationNotFoundError("Location cannot be projected to EPSG:7755")
        zero = orig.latitude == dest.latitude and orig.longitude == dest.longitude
        oi, od = self._radius_candidates(o, graph)
        di, dd = self._radius_candidates(d, graph)
        pair = self._find_compatible_component_pair(od, oi, dd, di, graph.nh_comp_labels)
        if pair is None:
            raise RouteNotAvailableError(
                f"No continuous highway network route available between '{orig.canonical_name}' "
                f"and '{dest.canonical_name}' within {MAX_FEEDER_SNAP_KM}km per-endpoint snap limit"
            )
        u, v, om, dm, comp = pair
        okm, dkm = om / 1000, dm / 1000
        if u == v:
            nodes, raw_hrs = [u], 0.0
        else:
            distances, predecessors = dijkstra(graph.nh_graph, directed=False, indices=u,
                                                return_predecessors=True)
            raw_hrs = float(distances[v])
            if not np.isfinite(raw_hrs):
                raise DataIntegrityError("Same-component destination is unreachable on the highway graph")
            nodes = self._reconstruct_path(u, v, predecessors)
        network_km, bridge_count, bridge_km, max_bridge = self._analyze_path_edges(nodes, graph)
        coords = tuple(tuple(float(x) for x in graph.nh_node_xy[n]) for n in nodes)
        path = RoutePath(
            node_ids=tuple(nodes), projected_coordinates=coords,
            edge_kinds=tuple("bridge" if graph.nh_bridge_mask[a, b] else "physical"
                             for a, b in pairwise(nodes)),
            origin_feeder=() if zero else (tuple(float(x) for x in o), coords[0]),
            destination_feeder=() if zero else (coords[-1], tuple(float(x) for x in d)),
        )
        factor = VEHICLE_SPEED_FACTORS.get(vehicle_type, .80)
        total_km = 0.0 if zero else round(network_km + okm + dkm, 2)
        total_hrs = 0.0 if zero else round(raw_hrs / factor + (okm + dkm) / (ACCESS_SPEED_KMH * factor), 2)
        warnings: list[str] = []
        if zero:
            warnings.append("Zero movement: geometry is null; snap distances describe network proximity, not travel.")
        elif u == v:
            warnings.append("Distinct endpoints selected the same snap node: no network traversal; "
                            "feeder-only via snap node, not an established highway route.")
        direct_m = float(np.linalg.norm(d - o))
        if not zero and om + dm > FEEDER_EXCESS_RATIO * direct_m:
            warnings.append("Feeder sum exceeds 3x direct projected endpoint distance; straight-line feeders "
                            "may dominate this estimate. Direct separation is diagnostic only, not a routable alternative.")
        if bridge_count:
            warnings.append(f"Route traverses {bridge_count} synthetic topological junction bridge(s) ({bridge_km:.2f}km total)")
        for name, km in (("Origin", okm), ("Destination", dkm)):
            if km > 25:
                warnings.append(f"{name} is {km:.1f}km feeder distance from the selected compatible National Highway node")
        if bridge_count == 0 and okm <= 25 and dkm <= 25:
            tier = "network_exact"
        elif max_bridge <= 350 and okm <= 50 and dkm <= 50:
            tier = "modelled_connectivity"
        else:
            tier = "low_confidence"
        if not zero and u == v:
            tier = "low_confidence"
        if graph.nh_graph.indptr[u + 1] == graph.nh_graph.indptr[u]:
            tier = "low_confidence"
            warnings.append("Selected snap node is isolated; no physical network connectivity is established.")
        geometry = None
        simplified = False
        if not zero and include_route_geometry and geometry_detail != "none":
            display = self._build_route_coordinates(nodes, orig, dest, graph,
                                                     simplify=geometry_detail == "simplified")
            if len(display) >= 2 and len(set(map(tuple, display))) >= 2:
                geometry = {"type": "LineString", "coordinates": display}
                simplified = geometry_detail == "simplified"
            else:
                warnings.append("Display geometry collapsed at coordinate precision; geometry is null.")
        quality = RouteQuality(
            network_scope="national_highway", origin_snap_distance_km=round(okm, 2),
            destination_snap_distance_km=round(dkm, 2), connected_component=comp,
            synthetic_bridge_count=bridge_count, synthetic_bridge_distance_km=round(bridge_km, 2),
            maximum_synthetic_bridge_m=round(max_bridge, 1), geometry_simplified=simplified,
            geometry_tolerance_m=SIMPLIFY_TOLERANCE_M if simplified else None,
            quality=tier, warnings=warnings,
        )
        def snapped(n):
            lon, lat = self.to_wgs84.transform(*graph.nh_node_xy[n])
            return RoutePoint(latitude=round(lat, 5), longitude=round(lon, 5))
        minutes = round(total_hrs * 60)
        result = HighwayRouteResult(
            origin=orig, destination=dest, distance_km=total_km, drive_time_hours=total_hrs,
            drive_time_formatted=f"{minutes // 60} hrs {minutes % 60} min",
            origin_access_distance_km=0.0 if zero else round(okm, 2),
            destination_access_distance_km=0.0 if zero else round(dkm, 2),
            network_distance_km=round(network_km, 2), origin_snapped=snapped(u),
            destination_snapped=snapped(v), route_geometry=geometry, route_quality=quality,
            metadata=self.metadata_service.get_metadata(custom_limitations=[
                "National Highway fastest graph path after minimum compatible feeder snap-sum selection",
                "Snap model: all nodes within 150km per endpoint; straight-line feeders, no global door-to-door optimum",
                "Topology model: 15m grid nodes may merge grade-separated roads; all-endpoint junction bridges within 350m are heuristic",
                "The undirected highway graph ignores one-way restrictions",
                "network_exact denotes no marked synthetic bridges and each feeder <=25km, not physical ground truth; same-snap distinct endpoints are low_confidence",
                "Feeder excess diagnostic: warn only when feeder sum >3x direct projected endpoint distance; direct separation does not establish a routable path",
                "Travel times reflect free-flow speed limits scaled by commercial vehicle class",
            ]),
        )
        return result, path

    def _radius_candidates(self, point, store):
        return highway_pair_kernel.radius_candidates(
            point, store, max_distance_m=MAX_FEEDER_SNAP_KM * 1000)

    def _find_compatible_component_pair(self, dists_orig, idxs_orig, dists_dest,
                                        idxs_dest, comp_labels):
        """Threshold first, then nearest per component; linear memory, stable ties."""
        return highway_pair_kernel.select_compatible_pair(
            dists_orig, idxs_orig, dists_dest, idxs_dest, comp_labels,
            max_distance_m=MAX_FEEDER_SNAP_KM * 1000)

    def _reconstruct_path(self, orig_node_idx, dest_node_idx, predecessors):
        return highway_pair_kernel.reconstruct_path(orig_node_idx, dest_node_idx, predecessors)

    def _analyze_path_edges(self, path_nodes, store):
        return highway_pair_kernel.analyze_path_edges(path_nodes, store)

    def _build_route_coordinates(self, path_nodes, origin, destination, store, simplify=True):
        """Simplify EPSG:7755 in metres before WGS84 output rounding."""
        projected = [self.to_proj.transform(origin.longitude, origin.latitude)]
        projected.extend(tuple(store.nh_node_xy[n]) for n in path_nodes)
        projected.append(self.to_proj.transform(destination.longitude, destination.latitude))
        if simplify:
            projected = list(LineString(projected).simplify(SIMPLIFY_TOLERANCE_M,
                                                            preserve_topology=True).coords)
        wgs = [self.to_wgs84.transform(*p) for p in projected]
        wgs[0] = (origin.longitude, origin.latitude)
        wgs[-1] = (destination.longitude, destination.latitude)
        output = []
        for lon, lat in wgs:
            pt = [round(lon, 5), round(lat, 5)]
            if not output or output[-1] != pt:
                output.append(pt)
        return output


def get_routing_service() -> RoutingService:
    return RoutingService.get_instance()
