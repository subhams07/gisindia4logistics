# Stage 3: canonical public routing adapters

Status: bounded stage 3 accepted. Public workers are frozen; the shared ambiguity-source correction is closed. Coordinator: 1334 full tests, stable 47 Python fingerprints, scoped Ruff/AST/diff checks; independent Opus REST/MCP acceptance and final SDK/public closure `37e329f0-0d89-4d19-97b0-23b2750761cf`. Foundation 934-pass evidence remains a separate earlier baseline. This is not release or main-merge approval.

Planning evidence: read-only Opus 5.5 session `1eae1d70-57be-4347-a415-41d667e36dc9`, process `proc_9543b1798c15`. Independent foundation closure ACCEPT: session `6d991d0e-8a9d-4541-a8f2-a7d81a73ec93`, process `proc_f71a5802e230`; reviews did not execute tests. REST/app, SDK/CLI and MCP ownership is now released in disjoint slices. Shared foundation and graph files remain frozen.

## Implemented public contract under review

- Retain the actual legacy endpoint `POST /api/v1/route/highway`, its `RouteRequest`/`RouteResponse` envelope, SDK `route_highway` signature and MCP `gis_highway_route_and_tolls` name. There is no existing `/highway/envelope` endpoint.
- Added `POST /api/v1/route/highway/plan` under the existing router namespace. The earlier plural `/routes/` sketch is superseded; no plural rich endpoint is exposed.
- Introduce `HighwayRoutePlanRequest`: discriminated `LocationReference` origin/destination, all five existing `VehicleType` values, `include_route_geometry=True`, and `geometry_detail="none"` by default. Either suppression control wins. Reject unknown request and nested location keys; pin strict numeric/boolean behavior at each public boundary.
- Return the existing `HighwayRouteResult` without toll fields. Route-matched plazas/tariffs remain stage 5, not implied by this endpoint.
- Introduce a framework-independent route facade shared by REST, SDK/CLI and MCP. It delegates to the canonical service and its coherent validated graph generation, never reimplements Dijkstra or accesses mutable store graph fields independently. Do not resolve locations or manufacture metadata twice if the service already does so; root-specific metadata must match the actual request store.
- The legacy facade retains key names, list coordinate order `[latitude, longitude]`, scalar precision and the explicitly labelled distance-based toll heuristic. Add optional `route_quality` and `metadata` to preserve warnings and limitations rather than concealing them.
- Add SDK `plan_highway_route`, CLI `route-plan` and MCP `gis_plan_highway_route`, with shared request defaults. CLI retains existing `route` flags and exposes all five vehicle types. Update package/legacy exports by identity.

## Error and serialization contract

Freeze exact domain exception classes and the graph accessor only after entry verification. HTTP mapping remains ambiguity 409, conflicting/invalid input 422, missing location 404, stored-data/graph integrity or unavailable store 503, impossible supported route 422. Classify by exception type, never string matching. A more detailed public error-code taxonomy requires matching domain types; do not invent a conflict subtype that the resolver does not expose.

SDK adapters raise domain exceptions, not FastAPI `HTTPException`. CLI distinguishes client errors (exit 2) from service/data failures (exit 1). MCP preserves equivalent safe status/code semantics and returns a fixed internal message for unexpected failures. Never print local paths from integrity or unexpected exceptions. Preserve structured ambiguity candidates only through validated serialization.

MCP rich input uses the actual Pydantic-generated nested JSON schema and strict JSON-mode validation, not a flat primitive validator. Verify enum strings, booleans, numeric strings, discriminators and nested unknown keys with real stdio calls. Startup remains data-lazy and protocol-only on stdout. Reuse shared nearest-facility missing-observation serialization instead of copying the current MCP allowlist into the SDK.

## Intentional legacy numerical changes

Canonical snapping replaces mainland-first/top-15 selection with the accepted compatible snap objective. Vehicle speed factors replace the old vehicle-independent time calculation; zero movement produces zero distance/time/tolls. Formatting follows the canonical result. The toll formula is unchanged but may produce different counts because modeled distances change. The documented Delhi detour remains a limitation; neither route accuracy nor global optimality is claimed. Explain these changes rather than preserving a second engine for numerical parity.

## Implementation sequence and ownership

1. **Entry gate:** coordinator tests and independent Opus review for graph lifecycle/integrity, configured data roots/identities, and carry-forward interface repairs. Record exact service signatures and exception classes.
2. **Foundation worker:** new facade/error-contract/shared serialization modules; rich request and nested strict models; optional legacy response diagnostics; focused facade tests. Do not collide with active resolver or graph workers. Independently review the foundation before adapter fan-out.
3. **Disjoint adapter workers:** REST owns routing router/app handlers and REST tests; SDK/CLI owns canonical/legacy SDK exports and CLI/tests; MCP owns its server/tools/transport tests. MCP map-repair ownership must be released first. Keep existing serialization regression coverage, retargeting router patches to the facade rather than deleting tests.
4. **Coordinator:** real-data and synthetic cross-surface parity, full suite, focused lint/diff checks, updated examples and independent acceptance. No commits/pushes/merges by workers.

Foundation constraints: bind service resolver and provenance explicitly to actual `store.data_dir`, using the existing injectable `RoutingService` constructor rather than its stale global singleton. Root-keyed service caching must be bounded and must not cache mutable graphs or store generations. Expose a matching root-specific metadata accessor for REST headers. Preserve same-root refresh limitations until a separate invalidation lifecycle is implemented. Keep metadata and legacy diagnostic types free of the existing `schemas`/`phase1` import cycle. Rich public numeric and boolean fields are strict, but valid JSON enum strings must remain usable. The foundation does not itself expose endpoints, SDK commands or MCP tools.

## Acceptance checklist

### Frozen adapter handoff details

- SDK `plan_highway_route(origin, destination, vehicle_type="MAV_20T", *, include_route_geometry=True, geometry_detail="none")` accepts strict location mappings or typed references and returns a strict JSON-safe rich dictionary. The facade remains the only routing gateway.
- CLI `route-plan --origin '<location JSON>' --dest '<location JSON>' --vehicle ... --geometry none|full|simplified [--no-geometry]` emits rich JSON. Existing `route` flags remain; all five vehicle types are accepted and heuristic toll counts are labelled estimated, not encountered.
- REST domain and unexpected errors use `{"detail": <shared safe error contract>}`. MCP tool errors put that same contract in their text content with `isError=True`; JSON-RPC protocol errors remain JSON-RPC errors. Client validation is 422, while validation during engine model construction is 503. Do not classify generic calculation `ValueError` as client input.
- Rich adapters call `serialize_result` before response-model/JSON serialization so calculated NaN cannot silently become null. Shared nearest observation handling is reused rather than copied.
- The REST slice also owns a narrow resolver-availability correction: unavailable source frames/required identity columns are data-integrity 503, not unknown-location 404. A valid source with no matching identity remains 404. Existing catalogued facilities without routable coordinates and missing/ambiguous representative boundary points retain their previously accepted 404 non-routable policy; do not claim universal geographic completeness. No alias/identity/root algorithms are reopened.
- Version headers must match the actual request store and successful payload metadata, including dependency-overridden custom roots. Header middleware must not initialize a store for liveness or a handshake-like request. Existing isochrone readers remain unchanged until producer stage 4.

- Sentinel facade calls prove every legacy/rich surface reaches one canonical engine; legacy router no longer imports Dijkstra/tree snapping.
- Legacy envelope types/keys and labelled toll formula hold for all five vehicles; warnings/quality/limitations survive adapters.
- Committed-data and synthetic REST/SDK/MCP results agree apart from the fresh UTC metadata timestamp. Check timestamp timezone/time window; do not add a clock-freeze API.
- Exact domain-error matrix across REST, SDK, CLI and MCP; real corruption differs from an impossible route; path-bearing integrity details never reach public output.
- Geometry default/suppression/full/simplified/zero-movement, coordinate order, vehicle scaling and root-specific provenance all exercised.
- Strict nested rich input succeeds/fails as specified over initialized stdio; startup remains lazy and errors do not break the session.
- SDK nearest-facility outputs pass `json.dumps(..., allow_nan=False)` on committed data.
- Fresh full tests, focused Ruff `E4,E7,E9,F`, diff checks and independent Opus review. Earlier 586-pass evidence is the accepted pre-entry baseline, not acceptance of these future changes.

Stage 4 planning may now proceed; producer implementation/regeneration, toll matching and corridor/export work still require separate gates. No navigation guarantee, official tariff claim, legal redistribution clearance or release approval follows from stage-3 acceptance. See `stabilization_plan.md` for the recorded nonblocking debt and exact execution/review evidence.
