# ADR 003: Highway Route Geometry and Quality Diagnostics Contract

## Status
Accepted

## Context
Previous iterations provided scalar road distances and travel times without vector geometries. Users and visualization tools require exact route geometries to render maps, inspect corridor alignments, and perform downstream spatial intersections (such as matching toll plazas and freight terminals).

## Decision
1. **Predecessor Graph Traversal**:
   We execute `scipy.sparse.csgraph.dijkstra` with `return_predecessors=True`. The exact sequence of network vertices is reconstructed from destination back to origin.
2. **Coordinate Standards**:
   - Internal graph computations are performed in `EPSG:7755` (India NSF LCC) $[x, y]$ in meters.
   - Serialized GeoJSON geometry is strictly standard `[longitude, latitude]` $[X, Y]$ in WGS 84 (`EPSG:4326`).
   - Domain schemas use explicit `latitude` and `longitude` fields to prevent inversion bugs.
3. **Component-Compatible Snapping**:
   - Rather than forcing all points to component 0 (mainland), the routing engine queries top-$k$ candidate nodes for origin and destination and selects the closest pair belonging to the **same valid connected component**.
   - If no common component exists within a documented snap threshold, `RouteNotAvailableError` is raised.
4. **Transparent Route Quality Model (`RouteQuality`)**:
   Every route result returns diagnostic metadata:
   - `synthetic_bridge_count`: Number of topological bridge edges traversed.
   - `synthetic_bridge_distance_km`: Total distance over synthetic bridge links.
   - `maximum_synthetic_bridge_m`: Longest single bridge span.
   - `quality`: `"network_exact"` | `"modelled_connectivity"` | `"low_confidence"`.
5. **Geometry Simplification**:
   - Supports `geometry_detail: "full" | "simplified" | "none"`.
   - Default is `"simplified"` using Douglas-Peucker simplification while conserving path endpoints and lengths.

## Consequences
- Clean, map-renderable GeoJSON LineStrings with validated coordinate ordering.
- Transparent reporting of modeled synthetic bridges, protecting against assuming turn-by-turn navigation accuracy.
- Support for regional or island networks without artificial mainland snapping.
