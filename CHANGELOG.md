# Changelog

All notable code, API, data-schema, and documentation changes will be recorded here. The project follows [Semantic Versioning](https://semver.org/) for code interfaces; dataset vintages are versioned separately.

## [Unreleased]

### Fixed — Dhule duplicate / district code 598

- `india_districts_lgd` had two Maharashtra "DHULE" polygons (codes 498 and 598) covering the same ~6,929 km². The source sub-district layer tags Dhule's four talukas (Dhule, Sakri, Shirpur, Sindkhede) with district code 598, which is Kerala's Alappuzha; `reconcile_districts()` took the unknown (Maharashtra, 598) pair for a new "Malegaon" district and dissolved those talukas into a second Dhule. The source contains no Malegaon district (Malegaon taluka is in Nashik, 516). `process_panindia_boundaries.py` now corrects the code to 498 (`SUBDISTRICT_DISTRICT_CODE_FIXES`), and `reconcile_districts()` raises instead of deriving when a name already exists in the state or a code belongs to another state.
- Districts: 781 → **780** (Maharashtra 37 → 36). Sub-district layers (GPKG, GeoJSON, Parquet) carry 498 for Dhule. Code 598 now means only Alappuzha.
- `district_population_estimates.csv` regenerated. The duplicate counted Dhule's ~2.04 M people twice, and Maharashtra's state-total scaling then took that from every other Maharashtra district (about 1.8% each). Only Maharashtra rows change; the national total is 1,210,846,213.
- Maharashtra and national district access summaries (CSV/Parquet) regenerated: population weights and the Maharashtra `__STATE__` means change; village distances do not.
- Stage 4 outputs regenerated (run `linux-2026-10-19-run4`): 780 rows; the DHULE/598 row is gone and Maharashtra `pop_2011` changes; isochrones and all routing values are unchanged. Hinterland: Highly Contested 688 (was 689). `producer_limitations.json` now has no source-identity disclosures.
- API: `/api/v1/admin/districts/Dhule?state=Maharashtra` resolves (previously 409 ambiguous) and code 598 resolves to Alappuzha without a state.
- National audit on the published tree: 5,359 checks, 0 FAIL, 12 WARN (the four Alappuzha/Dhule disclosure WARNs are gone).

## [1.1.0] - 2026-10-19

### Data — national analysis refresh

- Regenerated all seven Stage 4 analysis outputs in `data/analysis/` (district travel-time summary CSV/Parquet, district–port matrix, freight modal split, port hinterland catchment, major-port and MMLP isochrones) with the reviewed producers. Added `producer_manifest.json` (input/code hashes, configuration, library versions) and `producer_limitations.json`, produced at the 1.1.0 code (run `linux-2026-10-19-run3`). No input data changed; all differences come from reviewed code and parameters. National audit (including code-closure validation): 5,367 checks, 0 FAIL, 16 WARN (15 source disclosures plus 1 informational note). Details: `docs/stage4_drift_linux-2026-10-18-run1.md`.
- Highway access, distances and drive times now use canonical, component-aware routing. Highway access decreases in 62 districts (largest: the Andaman islands); road distances shift by a median of about 0.1%. Drive times are stored to 0.1 minute.
- Isochrone thresholds are now 4/8/12/24 h (major ports) and 4/8/12 h (MMLPs), previously 1/2/4/8 h and 1/2/4 h. Features carry provenance properties (`status`, `coverage_method`, `snap_feeder_km`, `snap_component_node_count`, …).
- Port hinterland populations come from `data/demographic/district_population_estimates.csv` for every district. This fixes a code-collision bug (Alappuzha) and zero populations for uncoded rows.

### Fixed

- Isochrone hubs now snap to a supported highway component (at least 1,000 nodes) within 150 km instead of the nearest vertex of any fragment. JNPT, Mumbai, Chennai and Visakhapatnam ports, plus six MMLPs including Jammu, now have real catchments instead of fragment-sized or missing ones.

### Changed

- Package classifier is now `Development Status :: 4 - Beta`; `tabulate` added to the `dev` extra (needed by the audit report writer). `catalog.yaml` analysis entries updated (vintage 2026-10, isochrone bands, hinterland counts, disclosures).
- The freight modal-split CSV has the same columns in a different order. `pop_2011` is now float64 in the analysis tables.
- Recurring push/PR CI (`ci.yml`) and the weekly deep-audit workflow are removed. Tests and the national audit were run locally for this release (3,508 tests passed; audit 0 FAIL).

### Known limitations (disclosed)

- The gravity model uses α = 0.85, β = 1.65, unchanged from earlier releases and the live API default. These values are **uncalibrated**; a sensitivity table is in the drift report.
- MIRPUR and MUZAFFARABAD (PoK) and NAZUL populations are `area_share` estimates from the source population file.
- 372 district–hub pairs have road distance more than 3× the straight-line distance, a pre-existing graph-fragmentation issue under investigation.
- Highway-access values for Lakshadweep and Nicobars are over-sea distances above the 150 km snap limit.
- Isochrone hub snapping (nearest highway component of at least 1,000 nodes within 150 km) is a modelling policy, not a measured property. Hubs whose nearby components are all smaller (e.g. Andaman, parts of Arunachal) would be reported as unsupported; no current hub is affected. API responses do not yet carry routing warnings at the point of use; see `producer_limitations.json`.

### Added

- GitHub community, security, support, citation, and review templates.
- Regression tests for API semantics, validation, geodesic distance, MCP lifecycle, and map metric safety.
- MCP initialization and ping support.
- Configurable CORS origins and generated-output directory.
- Distance columns in the district-to-port matrix.

### Changed

- Nearest-facility calculations use WGS84 geodesic distance instead of degree-distance approximation.
- Requested target ports now select their own matrix-backed distance and drive time.
- Highway route distance is summed from path edges rather than inferred from a fixed average speed.
- Map-generation inputs and embedded values are validated and escaped.
- The 10 km rail-access score is exposed under a matching field name.

### Security

- MCP errors no longer return Python tracebacks.
- Generated MCP maps are written outside the committed data tree.
- Wildcard credentialed CORS is no longer the default.

> Earlier repository history predates this changelog. A tagged baseline release should document those changes before publication.
