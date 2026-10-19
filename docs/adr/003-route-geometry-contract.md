# ADR 003: Highway Route Geometry and Quality Diagnostics Contract

## Status
Design contract accepted; stabilization implementation and public integration remain under review.

## Context
Previous iterations provided scalar road distances and travel times without vector geometries. Users need modelled graph-path geometries for maps and analytical intersections. These are not operationally verified road alignments or turn-by-turn navigation.

## Decision
1. **Predecessor Graph Traversal**:
   We execute `scipy.sparse.csgraph.dijkstra` with `return_predecessors=True`. The exact sequence of network vertices is reconstructed from destination back to origin.
2. **Coordinate Standards**:
   - Internal graph computations are performed in `EPSG:7755` (India NSF LCC) $[x, y]$ in meters.
   - Serialized GeoJSON geometry is strictly standard `[longitude, latitude]` $[X, Y]$ in WGS 84 (`EPSG:4326`).
   - Domain schemas use explicit `latitude` and `longitude` fields to prevent inversion bugs.
3. **Component-Compatible Snapping**:
   - Consider all nodes within a 150 km projected radius of each endpoint, apply that threshold before pair selection, and retain the nearest node per component. Select the compatible pair with minimum combined snap distance, with deterministic ties. Do not force mainland membership or impose an uncalibrated minimum component size.
   - Then compute the fastest graph path between those selected nodes. This objective is not the globally fastest/shortest door-to-door modelled route: a slightly farther snap node can produce a materially shorter corridor. See [review evidence](../routing_review.md).
   - If no common component exists within a documented snap threshold, `RouteNotAvailableError` is raised.
4. **Transparent Route Quality Model (`RouteQuality`)**:
   Every route result returns diagnostic metadata:
   - `synthetic_bridge_count`: Number of topological bridge edges traversed.
   - `synthetic_bridge_distance_km`: Total distance over synthetic bridge links.
   - `maximum_synthetic_bridge_m`: Longest single bridge span.
   - `quality`: `"network_exact"` | `"modelled_connectivity"` | `"low_confidence"`.
   - `network_exact` is a legacy model tier: no marked synthetic bridges and each snap distance at most 25 km. It does not certify physical road connectivity or exact ground distances. `modelled_connectivity` permits marked bridges and each snap distance at most 50 km; longer feeders, isolated nodes and nonzero feeder-only routes require `low_confidence`.
   - Exactly equal resolved coordinates produce zero travel, null geometry and a warning after graph/coordinate/snap validation. Proximity diagnostics remain truthful; access-distance fields are zero because no journey occurs. Distinct points sharing one snap node must disclose that no network edge is traversed.
5. **Geometry Simplification**:
   - Supports `geometry_detail: "full" | "simplified" | "none"`.
   - Default is `"simplified"`: Douglas-Peucker in EPSG:7755 with a 50 projected-metre tolerance, preserving endpoints but not length. Serialize in WGS84 rounded to five decimal places; rounding adds error beyond the simplification tolerance. Tests allow 52 projected metres between full and simplified display lines.
   - `geometry_detail="none"` or `include_route_geometry=False` suppresses display geometry. Null geometry is also valid for zero movement or output collapsed at coordinate precision; never emit a one-position LineString.
6. **Synthetic Bridge Graph Metadata**:
   - The builder/cache exposes a sparse `bridge_mask` containing only marked bridge entries (no explicit zeros); physical duplicate edges retain their fastest weight and take precedence over synthetic links.
   - Internal `RoutePath` retains full grid-snapped EPSG:7755 vertices, node IDs and physical/bridge edge kinds, with straight-line feeders stored separately. Toll analysis must not use simplified display geometry, feeders or bridge segments as physical-passage evidence.

## Consequences
- Clean, map-renderable GeoJSON LineStrings with validated coordinate ordering.
- Transparent reporting of modeled synthetic bridges, protecting against assuming turn-by-turn navigation accuracy.
- Support for regional or island networks without artificial mainland snapping.

## Mandatory limitations and next-stage gates

- The graph is undirected and ignores one-way restrictions. Its 15 m grid can merge nearby vertices, including grade-separated roads, without marking those merges as synthetic bridges. Endpoint bridges within 350 m are heuristic and can connect parallel carriageways or switchbacks. Neither zero marked bridges nor a valid LineString proves real-world passage.
- Feeders are straight lines at a modelled 35 km/h scaled by vehicle class, not verified access roads. Speeds are free-flow assumptions, not traffic forecasts. Small fragments may win the snap objective on short journeys.
- Before public canonical integration, distinguish corrupt graph/data failures (503) from impossible land routes (422), and move expensive full graph validation to a stable validated store lifecycle without allowing stale checks on mutated arrays. Current per-request validation is deliberately retained while that lifecycle is unresolved.
- Corridor examples are model regression probes, not labelled routing ground truth. The NDLS-to-Jaipur district detour is a documented model limitation; it prevents claiming dependable door-to-door routing merely because tests pass.
