# Changelog

All notable code, API, data-schema, and documentation changes will be recorded here. The project follows [Semantic Versioning](https://semver.org/) for code interfaces; dataset vintages are versioned separately.

## [Unreleased]

### Fixed — geodesic highway lengths and drive times

- The National Highway graph measured every edge as a Euclidean length in EPSG:7755. That projection is conformal: its scale is ~0.98 around 24°N, so highway lengths and drive times were ~2% short across central and north India, and up to ~3% long in the far south. Edge lengths are now WGS84 geodesics (`graph_builder.geodesic_m` / `edge_lengths_km`) and drive times are those lengths over the same class speeds. The same applies to the snap feeders (hub and district to the highway, inclusive 150 km), highway access distance, `*_straight_km`, and the routing API's feeder-excess diagnostic. EPSG:7755 is still used for snapping, KD-tree search (with a 1.06 margin, then the geodesic test) and geometry.
- Topology is unchanged: the 350 m junction-bridge heuristic still selects endpoint pairs by projected separation (932,722 directed edges, as before). A geodesic 350 m threshold would have dropped 1,198 bridges in north India and disconnected real junctions; for example, Katihar to Jogbani ICP went from 124 km to 277 km. Bridge lengths are reported geodesically, so they can measure up to ~357 m.
- Graph cache version 6 → 7; cached graphs are rebuilt.
- Stage 4 outputs regenerated (run `linux-2026-11-22-run2`). District road distances and drive times change by a median +1.85% (1st–99th percentile about −1.3% to +2.0%, positive between the projection's standard parallels at 12.5°N and 35.2°N, negative south of 12.5°N). Mean road distance to the nearest port is 877 → 893 km. Nearest-hub identities change in 14 of 7,020 district–category cells. Median district drive time to a major port is 12.2 → 12.4 h; to an ICD 3.67 → 3.72 h; to a Gati Shakti terminal 3.09 → 3.14 h.
- Port hinterland: Highly Contested 688 → 690, Dominant 59 → 57; captive districts stay at 29. Freight modal split: Rail 324 → 323, Road 172 → 173; mean modal-shift saving 27.0% → 27.3%. The 372 high-circuity district–hub pairs are unchanged in number. Isochrone polygons change because reachable node sets shrink by ~2%; all 108 are still `ok`.
- Pune → JNPT (LCV, REST/SDK/MCP/CLI parity test): 131.19 → 133.25 km.
- Not changed: isochrone buffers stay 8 km in EPSG:7755. That is 7.8–8.2 km on the ground, below the 500 m simplification tolerance.

### Added — village polygons for the 9 border/NE states

- Survey of India village polygons for Arunachal Pradesh, Assam, Himachal Pradesh, Jammu & Kashmir, Ladakh, Manipur, Meghalaya, Mizoram and Nagaland (72,097 villages), from SoI's files on the National Water Data Portal (NWIC, Ministry of Jal Shakti), standardized by `scripts/fetch/fetch_village_boundaries_nwdp.py` into the same `*_soi_villages` schema plus Census 2011 `population_2011` and `households_2011`. Village populations sum to the Census 2011 state totals. All 36 states/UTs are now analysed at village level (658,248 villages); the OSM habitation points remain in the repo but are no longer used.
- Population estimates: in these 9 states a district's population is now the sum of its villages' Census 2011 populations (`village_census`, 153 districts), replacing area/village-count blends that were far off (e.g. Srinagar 0.32 M -> 1.23 M, Leh 186 k -> 133 k; Census 2011: 1.24 M and 133 k).
- Headlines now cover all 36 states: population-weighted mean distance to a rail station 15.9 km and to an ICD 131.2 km (previously quoted for 27 states); village medians 6.6 km to a National Highway and 13.4 km to a railway station.
- Audit: expects village files for all 36 states and checks NWDP village populations against Census 2011 state totals; district coverage is counted from the spatial assignment.

### Fixed — district codes are now official LGD codes

- `district_code` in the current boundary layers was not the LGD code: the SoI ABDB source numbers 635 of 780 districts by their Census 2011 code and 142 newer ones by a scheme of its own; only 10 carried their real LGD code. Checked against the Local Government Directory's own district list (790 districts, lgdirectory.gov.in web service). Example: Pune is LGD 490 (Census 521); Himachal's Bilaspur is LGD 15 (Census 30); Puducherry's Karaikal is LGD 598, the number the old layer gave Kerala's Alappuzha (Census 598, LGD 554).
- New `data/administrative/lgd_districts.csv` (official snapshot, `scripts/fetch/fetch_lgd_directory.py`) and `data/administrative/district_code_crosswalk.csv` (`scripts/clean/apply_lgd_codes.py`: 638 districts by Census code, 123 by name, 11 by spelling variant, 8 by hand). `district_code` is now the LGD code in the district and sub-district layers (GeoJSON, Parquet, GPKG) and everything derived from them; the source value is kept as `abdb_district_code`, and the district layer adds `census2011_code`. LGD codes are unique nationally.
- Dadra & Nagar Haveli and Daman & Diu move to LGD state code 38 (was 26, the pre-2020 Dadra & Nagar Haveli code).
- Four districts have no LGD code: Mirpur and Muzaffarabad (Jammu and Kashmir), Delhi's Nazul, and Shahdara, which the LGD directory no longer lists after Delhi's 2025 reorganisation.
- Population estimates: villages are now counted per current district by point-in-polygon instead of by the village files' own district codes, which mix Census, LGD and older numbering and had credited villages to the wrong district (e.g. 1,075 villages to Assam's Tamulpur, for which no SoI village file exists). Area shares are measured in an equal-area projection (ESRI:102028) instead of EPSG:7755. 101 districts change by more than 5%; state totals still equal the Census 2011 totals.
- API examples and schema descriptions now cite Pune as LGD 490.
- Audit: new checks that every district code is an official LGD code of its state, that `census2011_code` matches the directory, that codes are unique, and that only the four districts above lack one.

### Fixed — geodesic village distances

- Village and sub-district access distances (`dist_*_km` in `data/analysis/*_village_access.csv`, the district summaries and `india_subdistrict_access.csv`) are now geodesic on the WGS84 ellipsoid. They were measured in EPSG:7755, a conformal projection whose scale runs from ~0.980 (24°N) to ~1.03 (6.7°N), so distances were up to ~2% short in central and north India and up to ~2% long in the far south. Median change +1.9% (1st–99th percentile −0.7% to +2.0%). EPSG:7755 now only narrows the candidates; the nearest facility is the one with the smallest geodesic distance, which changes the named facility for 0.02–0.25% of villages per type.
- Headline figures: population-weighted mean distance to a rail station 14.8 → 15.1 km and to an ICD 125.4 → 127.4 km (27 village-level states); median village distance to a National Highway 6.6 → 6.7 km and to a railway station 13.3 → 13.5 km.
- Audit: the Haryana reproducibility check is replaced by a brute-force geodesic recomputation of rail-station and port distances for 40 villages each in Haryana, Kerala, Andaman & Nicobar and Sikkim (tolerance 0.006 km).
- Highway routing, drive times and the other Stage 4 outputs still measure in EPSG:7755; they are corrected separately.

### Fixed — Dhule duplicate / district code 598

- `india_districts_lgd` had two Maharashtra "DHULE" polygons (codes 498 and 598) covering the same ~6,929 km². The source sub-district layer tags Dhule's four talukas (Dhule, Sakri, Shirpur, Sindkhede) with district code 598, which is Kerala's Alappuzha; `reconcile_districts()` took the unknown (Maharashtra, 598) pair for a new "Malegaon" district and dissolved those talukas into a second Dhule. The source contains no Malegaon district (Malegaon taluka is in Nashik, 516). `process_panindia_boundaries.py` now corrects the code to 498 (`SUBDISTRICT_DISTRICT_CODE_FIXES`), and `reconcile_districts()` raises instead of deriving when a name already exists in the state or a code belongs to another state.
- Districts: 781 → **780** (Maharashtra 37 → 36). Sub-district layers (GPKG, GeoJSON, Parquet) carry 498 for Dhule. Code 598 now means only Alappuzha.
- `district_population_estimates.csv` regenerated. The duplicate counted Dhule's ~2.04 M people twice, and Maharashtra's state-total scaling then took that from every other Maharashtra district (about 1.8% each). Only Maharashtra rows change; the national total is 1,210,846,213.
- Maharashtra and national district access summaries (CSV/Parquet) regenerated: population weights and the Maharashtra `__STATE__` means change; village distances do not.
- Stage 4 outputs regenerated (run `linux-2026-10-19-run4`): 780 rows; the DHULE/598 row is gone and Maharashtra `pop_2011` changes; isochrones and all routing values are unchanged. Hinterland: Highly Contested 688 (was 689). `producer_limitations.json` now has no source-identity disclosures.
- API: `/api/v1/admin/districts/Dhule?state=Maharashtra` resolves (previously 409 ambiguous) and code 598 resolves to Alappuzha without a state.
- National audit on the published tree: 5,359 checks, 0 FAIL, 12 WARN (the four Alappuzha/Dhule disclosure WARNs are gone).

### Fixed — village-access district identity

- Village-access tables (`data/analysis/*_village_access.csv`), per-state and national district summaries (`india_district_access_summary.csv` / `.parquet`) and `india_subdistrict_access.csv` now carry current LGD district codes and names, assigned by spatial join of each village's representative point to `data/administrative/india_districts_lgd.parquet`. Previously the village files' older SoI codes and legacy-font names were copied through, so 91 districts did not join on (state, district_code), 14 joined to the wrong district, and some districts were split across several summary rows. Distances are unchanged.
- District summaries have one row per current district (780 + 36 `__STATE__` rows), a new `district_code` column, and population weights joined exactly on (state, district_code). The previous fuzzy name match mis-weighted 12 state rows.
- `scripts/analyze/make_readme_figures.py` joins the rail map directly on (state, district_code) from the national access summary instead of re-grouping village rows by fuzzy district name (removed ~130 lines); it now stops if a code-matched row names a different district. All 780 districts have a value.
- The village map loader (`plot_villages`) aligns access rows by row order and accepts current or source district names. Stage 4 producer outputs were regenerated for the code-closure change (run `linux-2026-11-21-run1`).

### Changed — village-access tables

- Village-access CSVs gain `district_assignment` (`within`/`nearest`/`district_centroid`), `source_district` and `source_district_code`; `district_code` is written as an integer.

## [1.1.0] - 2026-10-19

### Data — national analysis refresh

- Regenerated all seven Stage 4 analysis outputs in `data/analysis/` (district travel-time summary CSV/Parquet, district–port matrix, freight modal split, port hinterland catchment, major-port and MMLP isochrones) with the reviewed producers. Added `producer_manifest.json` (input/code hashes, configuration, library versions) and `producer_limitations.json`, produced at the 1.1.0 code (run `linux-2026-10-19-run3`). No input data changed; all differences come from reviewed code and parameters. National audit (including code-closure validation): 5,367 checks, 0 FAIL, 16 WARN (15 source disclosures plus 1 informational note). Details: `docs/stage4_drift_linux-2026-10-18-run1.md`.
- Highway access, distances and drive times now use canonical, component-aware routing. Highway access decreases in 62 districts (largest: the Andaman islands); road distances shift by a median of about 0.1%. Drive times are stored to 0.1 minute.
- Isochrone thresholds are now 4/8/12/24 h (major ports) and 4/8/12 h (MMLPs), previously 1/2/4/8 h and 1/2/4 h. Features carry provenance properties (`status`, `coverage_method`, `snap_feeder_km`, `snap_component_node_count`, …).
- Port hinterland populations come from `data/demographic/district_population_estimates.csv` for every district. This fixes a code-collision bug (Alappuzha) and zero populations for uncoded rows.

### Fixed

- Isochrone hubs now snap to a supported highway component (at least 1,000 nodes) within 150 km instead of the nearest vertex of any fragment. JNPT, Mumbai, Chennai and Visakhapatnam ports, plus six MMLPs including Jammu, now have real catchments instead of fragment-sized or missing ones.


### Known limitations (disclosed)

- The gravity model uses α = 0.85, β = 1.65, unchanged from earlier releases and the live API default. These values are **uncalibrated**; a sensitivity table is in the drift report.
- MIRPUR and MUZAFFARABAD (Jammu and Kashmir; no LGD code and no Census 2011 enumeration) and NAZUL populations are `area_share` estimates from the source population file.
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

- Package classifier is now `Development Status :: 4 - Beta`; `tabulate` added to the `dev` extra (needed by the audit report writer). `catalog.yaml` analysis entries updated (vintage 2026-10, isochrone bands, hinterland counts, disclosures).
- The freight modal-split CSV has the same columns in a different order. `pop_2011` is now float64 in the analysis tables.
- Recurring push/PR CI (`ci.yml`) and the weekly deep-audit workflow are removed. Tests and the national audit were run locally for this release (3,508 tests passed; audit 0 FAIL).
- Nearest-facility calculations use WGS84 geodesic distance instead of degree-distance approximation.
- Requested target ports now select their own matrix-backed distance and drive time.
- Highway route distance is summed from path edges rather than inferred from a fixed average speed.
- Map-generation inputs and embedded values are validated and escaped.
- The 10 km rail-access score is exposed under a matching field name.

### Security

- MCP errors no longer return Python tracebacks.
- Generated MCP maps are written outside the committed data tree.
- Wildcard credentialed CORS is no longer the default.

## [1.0.0] - 2026-08-29

Tagged baseline (`v1.0.0`). It is the state of the repository after the August 2026 build: administrative boundaries (Survey of India ABDB and DataMeet 2011), Survey of India village boundaries for 27 states/UTs, the national highway network, rail stations and categories, Gati Shakti cargo terminals, logistics hubs, Census 2011 demographics, freight tables, all-India village accessibility, the national highway travel-time engine, the REST API, SDK, CLI and MCP server. This changelog was started after the tag; the build history is in `AGENTS.md` (phases 1–E).
