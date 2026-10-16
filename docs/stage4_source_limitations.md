# Stage 4 source limitations and sidecar (schema 1)

Status: S2-core, **provisional** until the S2-final real-repository closure test passes. This document describes the contract of `producer_limitations.json`; it makes no producer or national-output acceptance claim.

## What the sidecar is

`producer_limitations.json` accompanies the seven derived artifacts and `producer_manifest.json`. It is bound to the manifest by `run_id` and the seven artifact SHA-256 values, without changing manifest schema 1. The publication set is the seven artifacts, then the sidecar, then the manifest last.

Exact top-level keys, no others:

`schema_version`, `run_id`, `artifact_sha256`, `column_classification`, `source_identity_disclosures`, `oracle_independence`, `resource_measurement_method`, `limitations`.

## Column classification

`column_classification` is the only classification. For each of the five tabular artifacts it is an ordered list of `[column, class]` pairs in exact header order; each GeoJSON artifact is the string `"population_independent"`.

| Class | Columns |
|---|---|
| `identity` | `state`, `district`, `district_code` |
| `population` | `pop_2011` |
| `flag` | `is_island` |
| `population_independent` | every other column |

`population_independent` means the numeric or model value does not depend on the population input. It is not a statement about the trustworthiness of labels or of any aggregate built from them.

## Source identity disclosures

Disclosures are derived generically from the declared district GeoJSON and the population CSV; no code is hard-coded. All identities are validated first. Same-state duplicate codes and malformed identities are errors, not disclosures.

- `cross_state_shared_code`: one district code appears under more than one state.
- `same_state_label_multiple_codes`: one state and district label carries more than one code. Pairwise overlap fractions of the retained geometries are measured in EPSG:7755 and rounded to 10 decimal places (compared with tolerance 1e-9 on validation). If a geometry cannot be measured, no fraction is invented and the build fails.

Rows keep the literal labels, ascending source row and integer codes. Population and method are copied verbatim only for a unique exact literal `(state, district, district_code)` match in the population CSV; otherwise both are null. This is a disclosure, not an alternative population policy.

Fixed notes: the cross-state note states an upstream source condition with rows and codes preserved literally and cause not determined. The same-state note reads: "Retained rows; overlapping geometries can make row-count or population-weighted aggregation double-represent the area; Kerala/Alappuzha/598 is a separate cross-state code condition; cause not determined."

## Fixed limitations

1. No navigation or door-to-door claim.
2. No official tariff.
3. No rail routing.
4. No claim of national or district population accuracy; conservation of totals is not accuracy.
5. No cause is asserted for source conditions and no official identifier is corrected.
6. Recomputation does not upgrade source vintage, copyright or licensing claims.
7. Isochrones are approximate coverage.
8. The graph oracle is a bounded sample.
9. Live default gravity parameters 0.85/1.65 differ from batch parameters 1.2/1.5.

## Oracle independence and resources

`oracle_independence` is the constant: producer-independent, with shared components `highway_pair_kernel`, `graph_builder`, `freight_cost_core`, the W0 `district_population` policy, and `build_port_hinterland` (stored-output consistency, not independent model validation); sample at most 12 rows; two valid hubs per isochrone type.

`resource_measurement_method` must equal one of two supported constants (Windows peak working set, or Linux `ru_maxrss` in KiB times 1024). Measurements themselves are recorded in the manifest, never invented here.

## Validation

`validate_limitations(path, manifest, *, source_root=None)` reads strict JSON (duplicate keys, NaN and Infinity rejected), checks exact keys, types and constants, the manifest `run_id` and hashes, the classification against the manifest columns, disclosure entry shape and order, and scans for absolute or private paths. With `source_root` it also recomputes the disclosures from the declared sources. Source-grounded validation is required for national full audits and for the publication validator.
