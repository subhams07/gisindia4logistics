# ADR 001: Phase 1 Service Boundaries and Pure Domain Layer

## Status
Accepted

## Context
In previous releases, significant analytical logic (e.g. Dijkstra graph traversal, toll heuristics, freight modal calculations) resided directly inside FastAPI router functions (`server/routers/`). This created code duplication and forced non-HTTP consumers (Python SDK, CLI, and MCP tool server) to either re-implement logic, import router functions with dummy FastAPI dependencies, or mock HTTP requests.

## Decision
We extract all business, routing, simulation, comparison, and reporting logic into pure or mostly pure Python services under `server/services/`:
- `location_resolver.py`: Deterministic location parsing, validation, and alias matching.
- `routing_service.py`: Canonical National Highway Dijkstra shortest path, component snapping, and predecessor path reconstruction.
- `toll_service.py`: Spatial indexing, route buffer search, confidence scoring, and route-ordered toll plaza matching.
- `corridor_service.py`: Multi-modal corridor orchestration (Road, Rail, DFC), recommendation explanations, and warnings.
- `comparison_service.py`: Multi-criteria district scoring, percentile rank normalization, weight distribution, and strength/gap identification.
- `report_service.py`: Sandboxed PDF, Excel, and HTML export generation.
- `metadata_service.py`: Centralized version manifest loading and response provenance.

### Responsibilities
- **Routers**: Thin adapters that parse HTTP requests, invoke services, map domain exceptions to HTTP status codes, and return Pydantic models.
- **Services**: Pure Python domain logic accepting explicit dependencies (e.g. `DataStore`), returning Pydantic domain models, and raising domain exceptions (`LocationNotFoundError`, `AmbiguousLocationError`, `RouteNotAvailableError`, `ReportGenerationError`).
- **SDK / CLI / MCP**: Direct consumers of `server/services/`, ensuring 100% logic and contract parity across all surfaces.

## Consequences
- Single source of truth for all analytical calculations.
- Zero FastAPI dependency mocking required for unit testing core logic.
- Domain models are separated from HTTP transport envelopes.
