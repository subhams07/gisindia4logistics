# Decision Workbench Stabilization Plan

Status: stages 1–3 and bounded provenance documentation accepted. Stage 4 foundations, S1–S5 integration and N1a–N1c accepted; N1d is implemented and reviewed but remains unaccepted pending a bounded test/helper repair. National output regeneration and stages 5–6 remain gated. This is a review checkpoint, not a release or production-readiness claim.
Baseline: `00b21b8` (open PR3), canonical repository `subhams07/gisindia4logistics`.
Review branch: `quality/decision-workbench-stabilization`. The maintainer has authorized a checkpoint commit and push on this branch; no main merge or release is authorized. The chronological evidence below retains older statuses and authorizations; the current checkpoint takes precedence.

## Current checkpoint

| Milestone | Current boundary |
|---|---|
| Stages 1–3 | Accepted within the interface, internal-routing and canonical public-adapter scopes recorded below; not operational navigation certification. |
| Stage 4 engineering | Shared producers, orchestration and safeguards accepted through N1c. N1d admission/coherence logic is reviewed; its acceptance remains open. |
| Stage 4 national outputs | Seven baseline outputs unchanged. No national regeneration, output acceptance or publication is claimed. |
| Stage 5 | Route-matched tolls not started. Existing toll estimates are not verified tariffs. |
| Stage 6 | Corridor journey and secure HTML export not started. PDF/XLSX and comparison remain separate scope. |

N1d's remaining repair is confined to its semantic test file: persist a real-CRS nonfinite-area regression, normalize specified retained-surface failures in the provenance-rebuilding test helper, confirm the existing schema-v2 diagnostic, and persist the closed set of related prerequisite/feeder/disclosure controls. No production logic change is required by the reviewed findings.

Existing verification of the current implementation: coordinator and independent reviewer each completed the full repository collection with **3,473 passed and 5 skipped**. These are separate, overlapping executions, not a summed test total. The skips are Windows alias/broken-link capability gaps, not covered behavior. Five unchanged exporter lint findings remain explicitly qualified. These results establish code evidence, not national output correctness.

### Next steps and plan adjustments

Keep the six-stage business scope. Change the execution process:

1. Close the single, finite N1d repair after short plan critique, designated implementation and independent closure. For this single-test-file repair, use affected regressions, immutable reproductions and the whole semantic file, plus scoped lint, preservation and integrity checks. No automatic 11-file or full-suite reruns; broader execution requires a stated production/shared-fixture/dependency risk.
2. Plan resource profiling and read-only inventory-audit scope discovery early, alongside the repair where dependencies permit. These are risk-discovery plans, not permission to execute national workloads. Working-set observations do not establish private-commit headroom.
3. After the required operational admission ruling, progress through resource measurement, staged generation, unpatched audits, explained drift, fresh consumer checks and guarded publication, with explicit gates. No national execution or publication is authorized by this checkpoint.
4. Only after Stage 4 output acceptance, plan route-matched tolls and then the corridor/export journey.

Check each approved acceptance row against persisted tests before expensive verification. Reuse authenticated evidence for unchanged dependencies, keep handoffs concise, consolidate findings into one bounded repair, and checkpoint regularly on review branches. Do not reopen accepted foundations for optional polish.

Historical private review transcripts, scratch probes, caches and generated artifacts are excluded from this checkpoint. Later N1d fixes will be separate commits; pushing this snapshot does not accept them in advance.

## Roles

- Claude Opus 5.5 (`claude-opus-5-5`, medium effort): evaluation, bounded planning and final alignment.
- GPT 6.1 Sol (`gpt-6.1-sol`, medium reasoning): coordination, independent plan critique and implementation verification.
- Claude Sonnet 5.5 (`claude-sonnet-5-5`, medium effort): designated scoped implementation after plan reconciliation. Earlier worker allocations below are historical.

CI and PyPI distribution are out of scope. No new required dependencies, fabricated upstream records, or silent source-vintage upgrades.

## Architectural decisions

1. `payload_tons` denotes shipment quantity; `truck_payload_tons` denotes vehicle capacity. No implicit copying. Preserve the existing continuous per-tonne cost model; discrete truck-trip rounding is a separate future model change.
2. Inputs used in numerical models must be finite and satisfy domain bounds. Missing observational data may serialize to null; invalid calculated financial values must not silently become successful null results.
3. MCP stdout contains only strict JSON-RPC. Log on stderr and initialize analytical data only when a tool needs it. Serialize nested Pydantic objects using JSON mode, including datetimes; normalize missing observational scalars explicitly.
4. Conflicting location identifiers are errors, never fallback permission. Ambiguous names/codes require disambiguation. Freeze the HTTP mapping now: ambiguity 409; conflicting supplied identifiers 422; not found 404; invalid client input 422; invalid stored numerical source data 503. A supported request for an impossible mainland/island land route remains 422. SDK domain-error conversion is a separate stage-3 gate; MCP must preserve equivalent semantics without leaking local paths.
5. Retain the legacy highway response envelope through a compatibility adapter. Expose the richer result separately and route every surface through the service.
6. Zero-movement geometry is null with an explanatory warning. Do not claim mainland network membership without checking connectivity. Confirm the zero-movement/snap-distance semantics in durable tests.
7. Geometry suppression wins when either `include_route_geometry=False` or `geometry_detail=none`. Simplify projected coordinates in metres. Retain full internal network path, segment kinds and node IDs for analytical matching independent of display geometry.
8. Enforce per-endpoint snap thresholds before pair selection. Remove the arbitrary top-150 completeness claim. Do not adopt an uncalibrated minimum component size or mainland preference that rejects legitimate regional/island routes.
9. One graph builder and source-loading policy for server and analytical generators. Physical edges take precedence; sparse duplicate edge entries must not sum alternative costs.
10. Toll matching uses full projected physical network geometry, not simplified display lines, synthetic bridges or straight-line feeders. Missing direction evidence cannot receive a direction bonus. Deterministic IDs and normalization must not imply an official plaza identity.
    Source warning: 334 of 335 unclassified `highway_toll` records have NH labels inferred by nearest-network assignment without a distance limit. Such labels are not trusted plaza identity or passage evidence. Stage 5 must distinguish source tags from derived attribution, or withhold the NH-label confidence contribution when lineage cannot be recovered.
11. Without verified tariff data, tariffs are modelled/unknown and official totals remain absent. Confidence is heuristic, not proof of real-world passage; do not promise elimination of all parallel-road false positives.
12. One freight calculation core for live and batch consumers, with explicit assumptions. Rail and DFC scenarios remain strategic approximations, not operationally verified routes.
13. Corridor workflow and secure HTML export form the first end-to-end slice. PDF/XLSX and district comparison are separate acceptance gates, not implied complete by example-schema tests.
14. Legal redistribution permission remains a maintainer decision; correct claims but do not invent clearance or remove datasets without authorization.
15. Manifest version strings identify contract/model revisions, not milestone acceptance. The existing `phase1-decision-workbench` model identifier does not certify public corridor, comparison or export delivery.

## Priority stages and acceptance

### 1. Public-interface repairs — ACCEPTED (BOUNDED SCOPE)
- MCP wrappers, nested serialization, strict stdout, subprocess handshake and successful calls.
- Village listing schema, allowlisted state/path containment, literal district search, rail category filter.
- Shipment/capacity parity, identifier conflicts, finite costs, zero baseline behavior.
- Legacy SDK module delegates instead of maintaining a divergent implementation.
Gate: new regressions pass, supported successful MCP calls serialize, REST/SDK/CLI/MCP equivalent requests agree, full suite and focused lint pass.

### 2. PR3 routing remediation — ACCEPTED (INTERNAL SERVICE SCOPE)
- Valid geometry/null zero movement, none mode, metric simplification.
- Compatible threshold-aware snapping, truthful quality, sparse bridge mask.
- Exact synthetic bridge and geometry tests, reviewed real corridors.
Gate: no false connectivity claims or malformed geometry; synthetic and real-data tests pass.

### 3. Canonical integration — ACCEPTED (BOUNDED PUBLIC SCOPE)
- Entry gate: separate graph/data integrity errors (503) from genuinely impossible supported routes (422), including inconsistent component reachability, broken predecessor chains and invalid edge distances. Never classify by message text.
- Entry gate: full validation at a stable, immutable or invalidation-aware graph-store lifecycle; cheap request checks, no unchecked memoization of mutable graphs. Preserve coherent local graph references through a request and test mutation/replacement behavior.
- Entry gate: resolver aliases/boundaries and metadata honor the configured data root, never retain a previous root's centroid/manifest cache or silently use repository data.
- Thin REST compatibility/rich adapters, SDK/CLI/MCP parity, shared location resolution.
- Configured data path honored by resolver/provenance services.
- Carry-forward public-interface debt to correct before stage-3 acceptance: port-gravity overflow/unknown capacity keys, MCP map state aliases and same-name cross-state output collisions, freight missing required source text yielding 500, SDK HTTPException leakage and nearest-facility NaN values. These were not represented as fixed by bounded stage 1.
Gate: one route implementation, consistent errors, rich outputs available publicly, legacy compatibility verified.

### 4. Analytical producer unification — INITIAL FOUNDATION IMPLEMENTATION; ACCEPTANCE PENDING
- Isochrone graph and source/cache policy, pure freight core, live/batch parity.
- Regenerate affected outputs with source/model fingerprints and before/after statistics.
- Register omitted road/toll audit checks; expand semantic checks beyond geometry validity.
Gate: measured and explained artifact drift; full audit and physical invariants pass.

### 5. Toll matching — PENDING (requires routing/analytical gates)
- Candidate search, deterministic identity, full-path ordering/deduplication, evidence-limited scoring, transparent tariffs.
Gate: exact synthetic fixtures and independently reviewed corridor references; honest precision/recall where labelled ground truth exists. No invented expected plazas.

### 6. Corridor journey and exports — PENDING (requires stages 3–5)
- Resolve -> route -> tolls -> explicitly modelled scenarios -> explanation -> secure HTML export.
Gate: end-to-end public-interface parity, escaped inputs, UUID paths/containment, download safety; no claim of full workbench completion until each export/comparison feature is separately exercised.

### Parallel provenance/documentation — ACCEPTED (ACCURATE WARNINGS ONLY)
- Reconcile rail/toll sources and vintages with catalog/fetch evidence.
- Correct audit badge and production/legal overclaims; repair stale examples.
- Preserve mixed licensing and unresolved source conditions.

## Verification baseline and evidence policy

Baseline: 76 tests pass; configured full audit 279 checks = 268 PASS / 11 WARN / 0 FAIL. Road/toll audit has 8 additional passing checks omitted from the main entrypoint. Broader lint has known legacy findings; no broad cleanup is folded into functional fixes.

Each stage records actual commands/results and files changed. Parent runs integration tests independently rather than accepting worker summaries. Full audit must not be overwritten by a fast report. Generated artifacts must be checked together with producers and consumers. No main merges or remote writes occur automatically.

## Current review gate

- **Coordinator GO for the bounded shared adapter foundation.** Fresh entry-closure suite: **810 passed in 97.50s**, with inherited `PYTHONPATH`/`PYTHONHOME` removed, bytecode and pytest cache disabled. SHA-256 checks of all 31 modified/new Python source/test files found no changes during the run. Focused Ruff `E4,E7,E9,F` and AST syntax checks passed across all 31 files; `git diff --check` passed. Independent entry closures below are accepted. This 810-pass baseline does not certify later foundation or public adapter edits; stages 4–6 remain unaccepted. The prior 586-pass result remains the accepted pre-entry baseline.
- Independent Opus closures: priority-1 join corrections **ACCEPT**, session `60036fb0-e237-4e3e-9c8a-cb465bec5fe8`; bounded internal routing **ACCEPT**, session `9a099af2-48c1-4b0a-9d37-ac34e879b9ac`; provenance warnings **ACCEPT**, session `7d234a89-e44f-4fd3-8088-04b4136d204c`. Reviews were read-only and did not run tests; execution evidence is the coordinator's.
- The scorecard join regressions reproduced **6 failed / 1 passed** before the shared helper fix. Known non-null codes use keyed travel rows; missing codes use name/state only when the primary identity is unique. Unkeyed Dhule access shares are withheld for both identities, not assigned using population coincidence. Real NAZUL/MIRPUR/MUZAFFARABAD travel enrichment is retained. Fresh admin/routing/cache regression run: **162 passed in 14.74s** plus focused lint/diff success.
- Previous evidence below is historical, not an unresolved rejection after the closure reviews. No source datasets were regenerated, no upstream rights obtained and no Git staging/commit/push/merge performed.

### Active stage-3 work and ownership

The 586-pass result certifies the accepted stage-1/2 baseline. The 810-pass result and independent closures certify the bounded entry changes below, not the unimplemented shared foundation or public adapters.

- Sol medium `proc_43944d270b3a`: graph lifecycle and integrity exceptions; owns dependencies, routing service, exception/service exports, new graph snapshot/lifecycle tests and routing tests. Must preserve coherent immutable/invalidation-aware generations and verify mutation/replacement behavior.
- Sol medium `proc_3c779ec7ba0e`: configured resolver/metadata data roots and identity constraints; owns those services, existing resolver/contract tests and new root/identity regressions. Must not cross-attach centroids by repeated name/state or ignore supplied names/codes.
- Sol medium `proc_e6ad1d2d88cb`: carry-forward port-gravity, required freight source-text and MCP map alias/filename repairs; owns simulation router, MCP tools and a new interface-debt test file. Does not integrate routing adapters.
- Opus high `proc_9543b1798c15`: read-only public-adapter planning completed, session `1eae1d70-57be-4347-a415-41d667e36dc9`. Coordinator reconciled the proposal in `docs/stage3_adapter_plan.md`: retain `/api/v1/route/highway`, proposed rich `/api/v1/route/highway/plan`, one facade, strict nested inputs, domain-error/JSON parity and sequenced ownership. This is not entry-gate acceptance or implementation authorization for public adapters.
- Coordinator owns documentation and consolidated verification. No worker may stage/commit/push or modify another worker's files. Public adapter implementation waits for verified entry corrections and independent review; toll/producer/workbench work remains blocked by its prerequisite gates.

### Stage-3 accepted entry verification and closure reviews

- All three Sol entry workers exited successfully; their owned implementation files are no longer under active worker edits. Coordinator first independently ran the four new entry regression files: **112 passed in 6.86s**. The subsequent full suite and lint/diff results are recorded above.
- Graph lifecycle now publishes detached, immutable generations and distinguishes `DataIntegrityError` from `RouteNotAvailableError`. Mutable raw fixture stores remain fully validated per call. Publication/reload and public array/CSR/tree mutation behavior have durable regressions. Public HTTP/domain mapping is still a separate adapter gate.
- Resolver/metadata defaults honor configured roots; resolver aliases and boundary centroids follow actual `store.data_dir`. All supplied identifiers constrain the entity; code/state boundary points prevent repeated Dhule names overwriting each other. Same-root files remain per-instance snapshots, requiring recreation to refresh. Missing/malformed manifest containers degrade to unknown provenance with an explicit limitation, not repository-source fallback; independent review will determine any further validation needed.
- Carry-forward fixes cover port-gravity numerical-range failures/unknown capacity keys, required freight source text and canonical-state map filenames. Extreme legacy arithmetic is rejected rather than replaced with a new log model. Repeated exports of the same state/district/metric still reuse the same filename.
- Coordinator independently probed the national graph: **316,930 nodes / 932,722 directed edges**. Three warm Pune-code-521 to JNPT calls had raw-validation median **0.373769s** with three full validations versus published-generation median **0.077536s** with zero full validations. Analytical responses agreed after excluding the generated timestamp: **131.19km / 2.37h**, vehicle `2_AXLE_TRUCK`, five marked synthetic bridges. This is local latency evidence, not concurrency throughput, physical corridor accuracy or deployment approval. Scratch probe: `gis_stage3_graph_probe.py`.
- Initial read-only Opus review accepted graph/integrity, session `6d4cb435-b113-4fbf-b213-af7658341f85`, and interface debt, session `9fc29b4d-6e6d-4d37-a488-2113017e9649`. Root/identity review initially required two corrections: real station names must beat foreign-category aliases; incorrectly typed manifest values must not fabricate version evidence or crash metadata construction.
- Coordinator added B1/B2 regressions (**61 failed / 38 deselected** before correction). Exact committed Dadri/Ennore/Haldia/Karaikal/Whitefield names and codes now resolve without weakening same-row constraints. Known manifest scalars are validated without stringification; invalid provenance degrades to unknown with withheld vintages and a limitation. The initial post-fix run exposed four incorrect uppercase expectations in the new tests; expectations were corrected to preserve exact committed source names. Focused resolver/contracts run: **127 passed in 7.49s**, Ruff/diff clean. Independent Opus closure **ACCEPT**, session `40e4f33f-b52f-419b-9fb9-3019f29af087`.
- Coordinator also reproduced missing population-source behavior: two malformed-source cases silently returned successful zero populations, and a missing `pop_2011` column returned 500. Required source columns/availability now give safe 503 without changing valid formulas or duplicate handling. Red **3 failed / 73 deselected**, green interface-debt suite **76 passed in 5.86s**; MCP non-finite population assertions added. Independent Opus delta closure **ACCEPT**, session `700ab0e1-d9c1-4f10-aefa-00bb7c7b6339`.
- Coordinator reproduced all four sparse read-compatibility failures identified by the graph reviewer. Sol correction freezes published CSR instances only; derived arithmetic/comparison results are mutable independent matrices, and format validation checks a detached copy. Component selection now uses unique-label counts rather than allocation proportional to the largest label. Coordinator graph/cache/edge rerun: **104 passed in 2.24s**, Ruff/diff clean. Independent Opus delta closure **ACCEPT**, session `2a970755-8d44-4175-9a20-5eca852bdd45`.
- Accepted limits: published CSR generic Python copy/pickle/process-pool support is not promised; derived NumPy arrays retain structural-lock behavior; private/native tampering and concurrent mutation of raw fixture inputs are unsupported. Same-root data file refresh requires recreation. Legacy adapters still use their old graph-reading path until canonical integration. Public metadata must bind to actual request `store.data_dir`, and HTTP/domain mapping and strict nested MCP inputs remain foundation/adapter gates.
- Final entry execution evidence: scratch `gis-stage3-entry-closure-evidence.json`; **810 passed in 97.50s**, all 31 Python fingerprints unchanged during execution, scoped Ruff/AST/diff clean. Earlier 736-pass runs preceded the closure corrections and are historical, not the current acceptance total.

### Shared foundation verification — ACCEPTED (BOUNDED SCOPE)

- Sol medium foundation worker `proc_39c1b977ad15`, session `20261003_154912_f0d79d`, completed the shared facade, error contract, request/legacy diagnostic models, serialization and three new test files. Its reported 281 focused passes are worker evidence, not coordinator acceptance.
- Coordinator reproduced a typed-instance validation bypass: Pydantic trusts existing model instances after top-level/nested mutation, and legacy coordinate-list mutation can bypass assignment validation. Nine new regressions failed before correction. Both facade boundaries now extract Python-mode field values and revalidate them before acquiring a default store or calling the engine, preserving bool/string distinctions rather than coercing via JSON. Valid model instances remain supported; direct Pydantic model-instance revalidation itself is not claimed to be strict.
- Fresh coordinator foundation tests: **112 passed in 8.70s**. Fresh full suite: **922 passed in 96.79s**, with all **36** changed/new Python fingerprints unchanged. AST syntax checks and scoped Ruff `E4,E7,E9,F` passed across those 36 files; `git diff --check` passed. Evidence: scratch `gis-stage3-foundation-closure-evidence.json`.
- Initial Opus foundation review required mapping-nested instance revalidation and safe 422 handling of request `ValidationError`. Coordinator expanded regressions: **8 failed / 6 passed / 65 deselected**, also exposing huge-coordinate-integer candidate overflow and calculated-envelope misclassification. Mapping-nested values are rebuilt and revalidated, candidate bounds short-circuit before float conversion, and calculated-envelope model validation is wrapped as data-integrity 503. Intermediate full suite: **931 passed in 108.41s**, source fingerprints stable.
- Opus closure then identified model validation during engine/resolver calculation still becoming a client 422. Coordinator red **3 failed / 57 deselected** reproduced both rich/legacy `RoutePoint` construction and an actual synthetic station row with `district_code=0`. Request validation remains outside the engine-only catch; engine `ValidationError` now becomes fixed `DataIntegrityError` (503), while domain exceptions and ordinary `ValueError` remain unchanged.
- Final fresh foundation suite: **124 passed in 7.47s**; full suite **934 passed in 103.00s**. All **36** changed/new Python fingerprints stayed unchanged; AST, scoped Ruff and diff checks passed. Read-only independent Opus final closure **ACCEPT**, session `6d991d0e-8a9d-4541-a8f2-a7d81a73ec93`, process `proc_f71a5802e230`. Intermediate 922/931 totals are historical and do not certify the final correction.
- Public adapter fan-out is authorized with disjoint REST/app/resolver-availability, SDK/CLI and MCP ownership, using frozen details in `docs/stage3_adapter_plan.md`. The narrow resolver source-availability error correction remains a public gate; accepted non-routable/missing representative-point 404 behavior is retained and documented. No worker owns foundation/graph files. Final public parity and independent review are still required before stage-3 acceptance or dependent stages 4–6.

### Public adapter verification — ACCEPTED (BOUNDED SCOPE)

Launch ownership/red notes below are historical; the final gate following them supersedes their pending status.

- Sol medium REST/app/resolver-availability: `proc_83644f3e3561`, PID 23668. Owns routing router/app, narrow resolver source-availability classification and dedicated REST/availability tests; no graph/foundation or SDK/MCP edits.
- Sol medium SDK/CLI: `proc_4828aa54a458`, PID 23880. Owns canonical/legacy package SDK exports, CLI and its adapter tests, with only necessary old HTTP-exception assertions retargeted.
- Sol medium MCP: `proc_ac93ab38ca28`, PID 18736. Owns MCP tool/server/transport tests and new MCP routing tests, retaining existing recursive serialization and protocol/lazy-startup coverage.
- Coordinator owns `tests/test_route_adapter_parity.py`, documentation and final verification. Initial cross-surface regression is red: **1 failed / 30 deselected**, demonstrating the rich HTTP endpoint is still absent before adapter implementation. The new tests require rich geometry/vehicle parity, legacy diagnostics/toll-label parity, safe domain-error parity, fresh UTC timestamps and request-root headers across REST/SDK/MCP. They are not represented as passing yet.
- All three workers were verified running with the explicit `openai-codex` / `gpt-6.1-sol` / medium command. Stage-3 acceptance requires workers frozen, coordinator full execution and independent Opus public review; stages 4–6 remain pending. Original checkout was reverified clean at `00b21b89ff0d1301e958e689876fb941bf7899ef` after launch.

### Final public gate and remaining debt

- All public workers finished and frozen: REST session `20261003_213559_3d6379`, SDK/CLI `20261003_213559_f6a5af`, MCP `20261003_213559_14013c`. Their respective reported 309/566/311 focused passes are worker evidence, not substituted for the coordinator's gate.
- Independent read-only Opus accepted REST (`04534c1d-66da-4a32-9296-bafec89f7044`) and MCP (`5ee8a504-7815-47b9-bcda-a7da1a0e4cb1`). SDK review `a1ba62f0-85de-4a74-9930-6c5cca281ed4` required preserving committed district ambiguity candidates: CSV nulls promote codes to float, which the strict transport allowlist correctly withheld. Coordinator red: **1 failed / 1 passed in 22.87s**.
- Sol medium root correction `proc_35da61c0c8d2`, session `20261003_221748_7588c5`, normalizes finite positive whole-number stored float codes only after record extraction in `unique_district_row`. The shared allowlist remains unchanged; no SDK-only workaround, identifier coercion or enrichment change. REST/SDK/MCP now preserve identical Bilaspur candidates (406 and 30 as Python integers). Invalid bool/string/fractional/nonfinite/negative values are not legitimized, and the input frame is unchanged.
- The initial frozen public full run had **1313 passed / 3 failed in 200.49s**, with 45 Python fingerprints stable. The failures were stale expectations for CLI HTTP-status text, unstructured MCP error prefixes and six rather than seven tools. Assertions now pin the recorded safe contract and all exact legacy tool names; no production behavior was changed to satisfy those obsolete expectations. Fresh retargeted regression/parity run: **35 passed in 25.10s**.
- Coordinator cross-surface tests include all vehicle/geometry controls and warning propagation on a marked short synthetic bridge. A committed Pune district 521 to JNPT port request with LCV/simplified geometry agrees across REST, SDK, MCP and a real CLI subprocess apart from independently fresh UTC timestamps. The pinned 131.19km/five-bridge values are accepted model diagnostics, not corridor ground truth.
- Final fresh coordinator focused gate: **50 passed in 54.90s**. Full suite: **1334 passed in 237.32s**. All **47** modified/new Python fingerprints stayed unchanged; AST syntax checks, scoped Ruff `E4,E7,E9,F` and `git diff --check` passed. Separate evidence: scratch `gis-stage3-public-adapter-final-evidence.json`; the foundation and initial failed public evidence are retained separately.
- Final read-only Opus closure **ACCEPT** for the root correction, SDK closure and bounded public integration: session `37e329f0-0d89-4d19-97b0-23b2750761cf`, process `proc_c0f9a6387c51`. Reviews did not execute tests; the fresh execution above is the coordinator's. **Coordinator GO for stage 4 planning**, not toll/export implementation or release/main merge.
- Nonblocking follow-up debt remains explicit: cold-load concurrency and server logging, map lookup input classified as 500, eager plotting on SDK import, duplicate JSON keys, older non-routing status-only bridges and nonfinite administrative ambiguity codes failing REST serialization. Same-root source refresh needs recreation. Routing still has synthetic connectivity/undirected/closest-compatible-snap limitations and the documented Delhi detour. No navigation accuracy, operational readiness, official tariff or legal redistribution clearance is implied.
- README/manual now describe the implemented rich entrypoints and compatibility changes. Original checkout remains clean at `00b21b89ff0d1301e958e689876fb941bf7899ef`; review changes remain uncommitted on `quality/decision-workbench-stabilization`. No staging, commit, push or merge occurred.

### Stage 4 planning handoff

- The first Opus planning launch `proc_648e5605b602` exited 2 before Claude started: a prompt apostrophe broke shell quoting. Relaunched read-only Opus 5.5 as `proc_20fa48fa8a4e`, PID 26644, using the scratch prompt file `gis-stage4-planning-prompt.txt` passed as a quoted shell variable. Scope: canonical graph/source/cache policy for isochrones and NH matrices, time/distance path coherence, a shared pure freight core, invoked road/semantic audits and actual derivative-regeneration evidence. It may inspect code only, not edit, run generators or change Git state; no planning result is accepted yet.
- Opus planning session `cfa6f8e3-b5ac-4f8d-9bd4-44a5bbf6c584` completed read-only. Coordinator checked actual signatures and collected B0 baseline evidence before authorizing only shared-source loader W1-a and pure freight-core W2-a. `docs/stage4_producer_plan.md` records corrections to the proposed shell, island-column assertion, reachability constraints, historical speed claim, promotion atomicity and persistent provenance.
- B0: seven derivative artifacts (five tables of 781 rows; isochrones 48/60 features), 21 source hashes, live graph 316930/932722, equal NH source fingerprints and stable P3 source/full-audit hashes. Matched current hour/distance speeds do not reproduce the historical ~2346 km/h figure. Summary CSV and parquet have equal identities/columns but numerical differences, including up to 4.1 km for ICP road distance. Durable baseline: `outputs/stage4/initial-baseline/baseline_evidence.json`.
- Sol medium workers launched: W1-a `proc_84c995edb0c0`, PID 24848 (source helper, DataStore delegation and source-policy tests); W2-a `proc_f83520536c27`, PID 9996 (pure freight core, dataclass/tariff re-exports and core tests). No producer rewrite, live freight-body integration, national regeneration/promotion or P5/P6 work is authorized until the initial foundation is independently accepted.
- Initial foundations subsequently accepted: parent **1728 passed in 210.57s**, stable **51** Python files, AST/diff clean, **50** scoped-lint-clean files plus engine's independently confirmed unchanged two baseline findings. Generic verifier exit1 is disclosed as baseline lint debt, not hidden. Independent Opus W1-a/W2-a ACCEPT sessions `b400f7d2-9ebd-4a00-92c1-0477aebdfbde` and `571485f6-9389-43f7-a6f4-38e83e4b2446`. Sources, derivatives and full audit hash remain unchanged. Durable evidence `outputs/stage4/foundation-acceptance/foundations_evidence.json`.
- Only next bounded slices launched after acceptance: W1-b shared pair kernel/live helper delegation `proc_709458d15bdf`, PID 32672; W2-b live/batch freight-core delegation `proc_1330e1928a00`, PID 12500; both Sol 6.1 medium. Their edits are **not** covered by the 1728 baseline. Fresh execution/stability and independent Opus acceptance required before producer integration/regeneration; P5/P6 remain gated. Details in `docs/stage4_producer_plan.md`.
- Neither planning nor synthetic tests count as regenerated-artifact acceptance. Record before/after statistics, source/model fingerprints, null/island/identity semantics and physical invariants; preserve full versus fast audit evidence. Stages 5–6 remain pending.

### Historical review evidence

- Opus 5.5 read-only reviews require corrections before priority 1 acceptance: real Dhule enrichment must use district codes before checking ambiguity; missing real village names must serialize as null without dropping records; SDK and MCP must expose state disambiguation; MCP freight must expose district code, state and explicit truck capacity. Error mapping above is the target, not a claim that every adapter already implements it.
- Sol workers are correcting the admin/freight and MCP slices with disjoint file ownership. Routing work is preparatory only; it does not authorize toll implementation or milestone acceptance before the routing review gate passes.
- Coordinator verification: contracts/resolver tests passed (`28 passed`) using the existing Python 3.11 environment. The initial run failed collection because inherited `PYTHONPATH` selected another environment's compiled packages; removing `PYTHONPATH`/`PYTHONHOME` from the test process resolved it without installing or changing dependencies.
- Final acceptance requires fresh full-suite, public-interface parity and real-data regressions against the consolidated changes, followed by independent Opus review. Earlier test totals do not certify the current working tree.
- Consolidated coordinator run initially returned **561 passed / 1 failed**: a legacy unknown-hub test expected HTTP 400 rather than the explicitly adopted invalid-input 422. Updated that assertion to the recorded contract. Fresh priority-1/nonrouting suite then returned **515 passed in 189.85s**, with focused Ruff `E4,E7,E9,F` and `git diff --check` passing. Opus rereview remains pending; this is not full-suite or milestone acceptance.
- Routing worker finished; coordinator independently reran `tests/test_routing_service.py`, `tests/test_graph_cache.py` and `tests/test_routing_edge_cases.py`: **47 passed in 12.81s**. Focused Ruff `E4,E7,E9,F` passed across the five routing files. Opus review is pending; these results are not stage acceptance.
- National graph performance probe: 316,930 nodes / 932,722 directed edges. Across three validation/route measurements, median full graph validation was 0.4714s and median Pune-to-JNPT route calculation was 0.5840s (first route 1.5520s). Revalidating the entire graph each request is a known latency cost, not a production throughput benchmark. The probe reproduced 131.19km with five synthetic bridges; it does not establish real-world corridor accuracy.
- Opus stage-2 review found a feeder-only confidence bug and requested corridor explanation. Sol is correcting distinct points that share one snap node. The coordinator traced the Delhi detour to the closest-compatible-snap objective: the current 379.47km model path has a 289.3270025267359km alternative in the same graph using a slightly farther origin snap and the same destination snap. See `docs/routing_review.md`; neither is labelled real-world ground truth. Graph integrity error separation and validated-store lifecycle optimization are mandatory stage-3 entry work, explicitly deferred from bounded stage 2.
- Provenance review corrections include unclassified toll-label warnings, unresolved SoI terms, accurate category joins and qualified mixed hub licensing. Fresh YAML/JSON and bounded provenance invariant checks passed; independent documentation rereview remains pending. Source snapshots were not regenerated, and no legal clearance is asserted.
