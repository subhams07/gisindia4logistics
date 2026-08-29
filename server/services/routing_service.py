"""
server/services/routing_service.py
Canonical Highway Routing Service for GISIndia4Logistics Decision Workbench.
Provides shortest-path route calculation, predecessor geometry extraction,
component-compatible node snapping, synthetic bridge diagnostics, and vehicle profiling.
"""

import logging
from typing import Optional, Dict, Any, List, Tuple, Union, Literal
import numpy as np
import pyproj
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString

from server.dependencies import DataStore, get_data_store
from server.models.phase1 import (
    LocationReference,
    ResolvedLocation,
    VehicleType,
    RoutePoint,
    RouteQuality,
    HighwayRouteResult,
)
from server.services.location_resolver import LocationResolver, get_location_resolver
from server.services.metadata_service import MetadataService, get_metadata_service
from server.services.exceptions import (
    RouteNotAvailableError,
    LocationNotFoundError,
)

LOGGER = logging.getLogger(__name__)

PROJ_EPSG = 7755
WGS84_EPSG = 4326
ACCESS_SPEED_KMH = 35.0
MAX_FEEDER_SNAP_KM = 150.0
SIMPLIFY_TOLERANCE_DEG = 0.0005  # ~50m simplification tolerance

VEHICLE_SPEED_FACTORS: Dict[VehicleType, float] = {
    VehicleType.LMV: 1.0,
    VehicleType.LCV: 0.95,
    VehicleType.TRUCK_2AXLE: 0.85,
    VehicleType.MAV_20T: 0.80,
    VehicleType.OVERSIZED_7AXLE: 0.65,
}


class RoutingService:
    """Canonical service for National Highway pathfinding, geometry assembly, and route quality diagnostics."""
    _instance: Optional["RoutingService"] = None

    def __init__(
        self,
        resolver: Optional[LocationResolver] = None,
        metadata_service: Optional[MetadataService] = None,
    ):
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
        self,
        origin: Union[LocationReference, ResolvedLocation],
        destination: Union[LocationReference, ResolvedLocation],
        vehicle_type: VehicleType = VehicleType.MAV_20T,
        include_route_geometry: bool = True,
        geometry_detail: Literal["simplified", "full"] = "simplified",
        store: Optional[DataStore] = None,
    ) -> HighwayRouteResult:
        """
        Calculates a canonical National Highway route between origin and destination.
        Returns complete metrics, route geometry, and topological diagnostics.
        """
        store = store or get_data_store()
        if store.nh_graph is None or store.nh_tree is None or store.nh_node_xy is None:
            raise RouteNotAvailableError("National Highway routing graph is not initialized in DataStore")

        # 1. Resolve Locations if needed
        resolved_orig = (
            origin if isinstance(origin, ResolvedLocation)
            else self.resolver.resolve(origin, store)
        )
        resolved_dest = (
            destination if isinstance(destination, ResolvedLocation)
            else self.resolver.resolve(destination, store)
        )

        if resolved_orig.latitude is None or resolved_orig.longitude is None:
            raise LocationNotFoundError(f"Origin location '{resolved_orig.canonical_name}' lacks geographic coordinates")
        if resolved_dest.latitude is None or resolved_dest.longitude is None:
            raise LocationNotFoundError(f"Destination location '{resolved_dest.canonical_name}' lacks geographic coordinates")

        # 2. Check if origin and destination are geographically identical
        if (
            abs(resolved_orig.latitude - resolved_dest.latitude) < 1e-6
            and abs(resolved_orig.longitude - resolved_dest.longitude) < 1e-6
        ):
            origin_pt = RoutePoint(latitude=round(resolved_orig.latitude, 5), longitude=round(resolved_orig.longitude, 5))
            coords = [[round(resolved_orig.longitude, 5), round(resolved_orig.latitude, 5)]]
            route_geom = {"type": "LineString", "coordinates": coords} if include_route_geometry else None
            r_quality = RouteQuality(
                network_scope="national_highway",
                origin_snap_distance_km=0.0,
                destination_snap_distance_km=0.0,
                connected_component=store.main_comp_label,
                synthetic_bridge_count=0,
                synthetic_bridge_distance_km=0.0,
                maximum_synthetic_bridge_m=0.0,
                geometry_simplified=False,
                geometry_tolerance_m=None,
                quality="network_exact",
                warnings=[],
            )
            meta = self.metadata_service.get_metadata()
            return HighwayRouteResult(
                origin=resolved_orig,
                destination=resolved_dest,
                distance_km=0.0,
                drive_time_hours=0.0,
                drive_time_formatted="0 hrs 0 min",
                origin_access_distance_km=0.0,
                destination_access_distance_km=0.0,
                network_distance_km=0.0,
                origin_snapped=origin_pt,
                destination_snapped=origin_pt,
                route_geometry=route_geom,
                route_quality=r_quality,
                metadata=meta,
            )

        # 3. Project coordinates to EPSG:7755
        orig_x, orig_y = self.to_proj.transform(resolved_orig.longitude, resolved_orig.latitude)
        dest_x, dest_y = self.to_proj.transform(resolved_dest.longitude, resolved_dest.latitude)

        # 4. Component-Compatible Snapping (k-nearest search)
        k_search = min(150, store.nh_node_xy.shape[0])
        dists_orig, idxs_orig = store.nh_tree.query([orig_x, orig_y], k=k_search)
        dists_dest, idxs_dest = store.nh_tree.query([dest_x, dest_y], k=k_search)

        if np.isscalar(dists_orig):
            dists_orig, idxs_orig = np.array([dists_orig]), np.array([idxs_orig])
        if np.isscalar(dists_dest):
            dists_dest, idxs_dest = np.array([dists_dest]), np.array([idxs_dest])

        best_pair = self._find_compatible_component_pair(
            dists_orig, idxs_orig, dists_dest, idxs_dest, store.nh_comp_labels
        )

        if best_pair is None:
            raise RouteNotAvailableError(
                f"No continuous highway network route available between '{resolved_orig.canonical_name}' "
                f"and '{resolved_dest.canonical_name}' (disconnected network components)"
            )

        orig_node_idx, dest_node_idx, orig_snap_m, dest_snap_m, comp_id = best_pair

        orig_snap_km = float(orig_snap_m / 1000.0)
        dest_snap_km = float(dest_snap_m / 1000.0)

        if orig_snap_km > MAX_FEEDER_SNAP_KM or dest_snap_km > MAX_FEEDER_SNAP_KM:
            raise RouteNotAvailableError(
                f"Location is too remote from the National Highway network "
                f"(origin snap {orig_snap_km:.1f}km, destination snap {dest_snap_km:.1f}km > {MAX_FEEDER_SNAP_KM}km limit)"
            )

        # Snapped Entry/Exit Node Coordinates in WGS 84
        orig_node_pt = store.nh_node_xy[orig_node_idx]
        dest_node_pt = store.nh_node_xy[dest_node_idx]
        orig_snap_lon, orig_snap_lat = self.to_wgs84.transform(orig_node_pt[0], orig_node_pt[1])
        dest_snap_lon, dest_snap_lat = self.to_wgs84.transform(dest_node_pt[0], dest_node_pt[1])

        origin_snapped = RoutePoint(latitude=round(orig_snap_lat, 5), longitude=round(orig_snap_lon, 5))
        destination_snapped = RoutePoint(latitude=round(dest_snap_lat, 5), longitude=round(dest_snap_lon, 5))

        # 4. Dijkstra Pathfinding with Predecessor Geometry Extraction
        dist_matrix, predecessors = dijkstra(
            store.nh_graph,
            directed=False,
            indices=[orig_node_idx],
            return_predecessors=True,
        )

        raw_network_hrs = float(dist_matrix[0, dest_node_idx])
        if np.isinf(raw_network_hrs):
            raise RouteNotAvailableError(
                f"Destination '{resolved_dest.canonical_name}' is unreachable on the highway graph from '{resolved_orig.canonical_name}'"
            )

        # Vehicle Speed Scaling
        speed_factor = VEHICLE_SPEED_FACTORS.get(vehicle_type, 0.80)
        scaled_network_hrs = raw_network_hrs / speed_factor

        # 5. Predecessor Path Reconstruction & Synthetic Bridge Analysis
        path_nodes = self._reconstruct_path(orig_node_idx, dest_node_idx, predecessors[0])

        network_distance_km, bridge_count, bridge_dist_km, max_bridge_m = self._analyze_path_edges(
            path_nodes, store
        )

        # Feeder Access Metrics
        access_time_hrs = (orig_snap_km + dest_snap_km) / (ACCESS_SPEED_KMH * speed_factor)
        total_distance_km = round(network_distance_km + orig_snap_km + dest_snap_km, 2)
        total_drive_time_hrs = round(scaled_network_hrs + access_time_hrs, 2)

        # Formatted Duration String (e.g. '1 hrs 57 min')
        total_minutes = int(round(total_drive_time_hrs * 60))
        hrs = total_minutes // 60
        mins = total_minutes % 60
        drive_time_formatted = f"{hrs} hrs {mins} min"

        # 6. Route Quality Classification
        warnings: List[str] = []
        if bridge_count > 0:
            warnings.append(f"Route traverses {bridge_count} synthetic topological junction bridge(s) ({bridge_dist_km:.2f}km total)")
        if orig_snap_km > 25.0:
            warnings.append(f"Origin is {orig_snap_km:.1f}km feeder distance from the nearest National Highway node")
        if dest_snap_km > 25.0:
            warnings.append(f"Destination is {dest_snap_km:.1f}km feeder distance from the nearest National Highway node")

        if bridge_count == 0 and orig_snap_km <= 25.0 and dest_snap_km <= 25.0:
            quality_tier: Literal["network_exact", "modelled_connectivity", "low_confidence"] = "network_exact"
        elif max_bridge_m <= 350.0 and orig_snap_km <= 50.0 and dest_snap_km <= 50.0:
            quality_tier = "modelled_connectivity"
        else:
            quality_tier = "low_confidence"

        # 7. GeoJSON Vector LineString Assembly
        route_geometry: Optional[Dict[str, Any]] = None
        is_simplified = False
        tolerance_m: Optional[float] = None

        if include_route_geometry:
            coords = self._build_route_coordinates(
                path_nodes,
                resolved_orig,
                resolved_dest,
                store,
                simplify=(geometry_detail == "simplified")
            )
            route_geometry = {
                "type": "LineString",
                "coordinates": coords
            }
            if geometry_detail == "simplified":
                is_simplified = True
                tolerance_m = 50.0

        route_quality = RouteQuality(
            network_scope="national_highway",
            origin_snap_distance_km=round(orig_snap_km, 2),
            destination_snap_distance_km=round(dest_snap_km, 2),
            connected_component=comp_id,
            synthetic_bridge_count=bridge_count,
            synthetic_bridge_distance_km=round(bridge_dist_km, 2),
            maximum_synthetic_bridge_m=round(max_bridge_m, 1),
            geometry_simplified=is_simplified,
            geometry_tolerance_m=tolerance_m,
            quality=quality_tier,
            warnings=warnings,
        )

        metadata = self.metadata_service.get_metadata(
            custom_limitations=[
                "National Highway shortest path routing with modeled feeder connections",
                "Travel times reflect free-flow speed limits scaled by commercial vehicle class",
            ]
        )

        return HighwayRouteResult(
            origin=resolved_orig,
            destination=resolved_dest,
            distance_km=total_distance_km,
            drive_time_hours=total_drive_time_hrs,
            drive_time_formatted=drive_time_formatted,
            origin_access_distance_km=round(orig_snap_km, 2),
            destination_access_distance_km=round(dest_snap_km, 2),
            network_distance_km=round(network_distance_km, 2),
            origin_snapped=origin_snapped,
            destination_snapped=destination_snapped,
            route_geometry=route_geometry,
            route_quality=route_quality,
            metadata=metadata,
        )

    def _find_compatible_component_pair(
        self,
        dists_orig: np.ndarray,
        idxs_orig: np.ndarray,
        dists_dest: np.ndarray,
        idxs_dest: np.ndarray,
        comp_labels: np.ndarray,
    ) -> Optional[Tuple[int, int, float, float, int]]:
        """
        Finds the pair of origin and destination candidate nodes that share a common
        connected component while minimizing the total snapping distance.
        """
        comps_o = comp_labels[idxs_orig]
        comps_d = comp_labels[idxs_dest]

        match_matrix = (comps_o[:, None] == comps_d[None, :])
        if not np.any(match_matrix):
            return None

        cost_matrix = dists_orig[:, None] + dists_dest[None, :]
        cost_matrix[~match_matrix] = np.inf

        best_idx = np.unravel_index(np.argmin(cost_matrix), cost_matrix.shape)
        best_o_k, best_d_k = best_idx[0], best_idx[1]

        return (
            int(idxs_orig[best_o_k]),
            int(idxs_dest[best_d_k]),
            float(dists_orig[best_o_k]),
            float(dists_dest[best_d_k]),
            int(comps_o[best_o_k]),
        )

    def _reconstruct_path(
        self,
        orig_node_idx: int,
        dest_node_idx: int,
        predecessors: np.ndarray,
    ) -> List[int]:
        """Reconstructs the node sequence from destination back to origin using predecessors."""
        if orig_node_idx == dest_node_idx:
            return [orig_node_idx]

        path: List[int] = []
        curr = dest_node_idx
        visited = set()

        while curr != -9999 and curr not in visited:
            visited.add(curr)
            path.append(curr)
            if curr == orig_node_idx:
                break
            curr = predecessors[curr]

        if not path or path[-1] != orig_node_idx:
            raise RouteNotAvailableError("Predecessor chain broken during shortest path reconstruction")

        path.reverse()
        return path

    def _analyze_path_edges(
        self,
        path_nodes: List[int],
        store: DataStore,
    ) -> Tuple[float, int, float, float]:
        """Calculates exact network distance, synthetic bridge count, bridge distance, and max bridge length."""
        total_dist_km = 0.0
        bridge_count = 0
        bridge_dist_km = 0.0
        max_bridge_m = 0.0

        if len(path_nodes) <= 1:
            return 0.0, 0, 0.0, 0.0

        for i in range(len(path_nodes) - 1):
            u, v = path_nodes[i], path_nodes[i + 1]

            # 1. Edge Distance
            d_km = 0.0
            if store.nh_distance_graph is not None:
                d_km = float(store.nh_distance_graph[u, v])

            if d_km <= 0.0 and store.nh_node_xy is not None:
                pt_u = store.nh_node_xy[u]
                pt_v = store.nh_node_xy[v]
                d_km = float(np.hypot(pt_u[0] - pt_v[0], pt_u[1] - pt_v[1]) / 1000.0)

            total_dist_km += d_km

            # 2. Bridge Mask Check
            if store.nh_bridge_mask is not None and bool(store.nh_bridge_mask[u, v] > 0):
                bridge_count += 1
                bridge_dist_km += d_km
                bridge_m = d_km * 1000.0
                if bridge_m > max_bridge_m:
                    max_bridge_m = bridge_m

        return total_dist_km, bridge_count, bridge_dist_km, max_bridge_m

    def _build_route_coordinates(
        self,
        path_nodes: List[int],
        origin: ResolvedLocation,
        destination: ResolvedLocation,
        store: DataStore,
        simplify: bool = True,
    ) -> List[List[float]]:
        """
        Builds EPSG:4326 [[lon, lat], ...] coordinates connecting origin,
        network vertices, and destination with endpoint preservation.
        """
        raw_coords: List[Tuple[float, float]] = []

        # 1. Origin Feeder Point
        raw_coords.append((round(origin.longitude, 5), round(origin.latitude, 5)))

        # 2. Highway Graph Vertices
        if store.nh_node_xy is not None:
            for node_idx in path_nodes:
                pt_proj = store.nh_node_xy[node_idx]
                lon, lat = self.to_wgs84.transform(pt_proj[0], pt_proj[1])
                raw_coords.append((round(lon, 5), round(lat, 5)))

        # 3. Destination Feeder Point
        raw_coords.append((round(destination.longitude, 5), round(destination.latitude, 5)))

        # 4. Deduplicate consecutive identical points
        deduped: List[Tuple[float, float]] = []
        for pt in raw_coords:
            if not deduped or deduped[-1] != pt:
                deduped.append(pt)

        if len(deduped) < 2:
            return [[p[0], p[1]] for p in deduped]

        # 5. Douglas-Peucker Simplification
        if simplify and len(deduped) > 2:
            orig_first = deduped[0]
            orig_last = deduped[-1]
            line = LineString(deduped)
            simplified_line = line.simplify(tolerance=SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
            simp_coords = list(simplified_line.coords)

            # Ensure exact endpoints are conserved
            simp_coords[0] = orig_first
            simp_coords[-1] = orig_last
            return [[round(p[0], 5), round(p[1], 5)] for p in simp_coords]

        return [[p[0], p[1]] for p in deduped]


def get_routing_service() -> RoutingService:
    """Dependency injector for singleton RoutingService."""
    return RoutingService.get_instance()
