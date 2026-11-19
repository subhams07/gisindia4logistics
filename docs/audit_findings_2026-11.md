# Project audit, November 2026: open findings

A line-by-line read of the server and packages (31 files), the analysis and audit scripts (21 files) and the fetch and clean scripts (23 files). Clear defects were fixed in commits `45cbeec`, `66bfcfb`, `6f858c4` and the commit that adds this file. The items below change published numbers, the public API or source data, so they wait for the maintainer's decision. Most belong with the next national regeneration (run 5).

| # | Area | Finding | Suggested action |
|---|---|---|---|
| 1 | Tolls | `routing_facade.py` prices `7_AXLE_OVERSIZED` below `MAV_20T` (5,500 vs 8,500 on Pune–Delhi). | Explicit rate per vehicle class; maintainer picks the numbers. |
| 2 | Gravity | Code defaults are α 1.2 / β 1.5 (`stage4_generate.py`, `port_hinterland_model.py`); published data uses 0.85 / 1.65. A hard-coded limitations sentence states the wrong pair. | Default to 0.85 / 1.65 everywhere; build the sentence from the actual config. |
| 3 | Producers | Standalone `main()` in `nh_travel_matrix.py`, `generate_isochrones.py`, `intermodal_cost_engine.py`, `port_hinterland_model.py` writes into the manifest-bound `data/analysis/` with arbitrary parameters; USER_MANUAL tells users to run them. | Default output to `outputs/`; refuse a directory holding `producer_manifest.json`. |
| 4 | Data | `india_subdistrict_access.csv` is stale (138 mojibake district names), uncatalogued, unaudited and unread. | Regenerate + catalog + audit, or delete file and script. |
| 5 | API | Always-null fields: `state_code`, `area_sqkm`, `subdistrict_count`, village `latitude`/`longitude`; station `category` null unless filtered. | Populate what the data supports; drop the rest. |
| 6 | API | Village-map request gives different file names and result keys on SDK and MCP (duplicated code); SDK ignores `output_path` with an unexpected suffix. | One shared implementation. |
| 7 | API | Three REST error body shapes. | Unify on the structured contract. |
| 8 | API | `share_villages_within_5km_rail` holds the 10 km value. | Remove the field (two tests pin it). |
| 9 | Data | The 9 border states' OSM points carry `unit = village`; the district-centroid fallback in `nearest_facility.py` is unreachable. | Label `habitation`; remove the fallback. |
| 10 | Population | `allocate_population.py` pools the null-code MIRPUR and MUZAFFARABAD polygons (groups by state + code). | Group by state + district + code, with the Census rebuild. |
| 11 | API | `commodity_name` is accepted on REST, SDK and MCP and does nothing. | Remove or echo as a label. |
| 12 | Data | Six GCT coordinates are wrong (`build_gct_terminals.py` matching order): Kamalajari, Shivpur-Godawari, Barabani, Wadi, New Makardhokda, Ras Babra. Catalog says "verified". | Blank the six; exact-in-state match first; require state agreement. |
| 13 | Data | `build_freight_tables.py` tables contradict themselves (FY24 zones sum 1,697 MT vs national 1,591; ports 822.9 vs 819); its asserts compare literals with themselves. | Re-source from PIB/IPA; assert sums against totals. |
| 14 | Data | SoI village files: inconsistent `state` values (stray CR/LF, mixed case, missing for Chandigarh), mixed dtypes, 3D coordinates in Goa. | Normalise at the next regeneration (needs raw zips). |
| 15 | Data | Habitation files: `state_code` entirely null; `district_code` float. | Fill from the district layer. |
| 16 | Data | Leftover `pune_soi_villages.*` extract inflates globs to 28 files / 588,035 rows. | Delete; update audit baseline. |
| 17 | Data | `india_subdistricts_lgd.geojson` (21.5 MB) is read by nothing. | Delete. |
| 18 | Data | 577 sub-district rows share a `sub_district_code`; `process_panindia_boundaries.py` keeps only leading digits of the source code. | Check raw `SUBDIS_LGD` values (maintainer has the source). |
| 19 | Deps | `fetch_toll_plazas.py` imports scikit-learn, which is not a declared dependency. | Replace with a scipy equivalent or declare it. |
| 20 | Server | `/admin/villages` re-reads the whole state CSV per request (0.6 s for Uttar Pradesh). | Small bounded cache. |
| 21 | Server | `server/__init__.py` imports the FastAPI app, so any `server.*` import loads everything. | Empty the package init. |
| 22 | Server | Unused `HOST`/`PORT`/`DEBUG` settings; docker-compose sets two of them to no effect. | Remove, or make `serve` read them. |
| 23 | Server | Port-gravity population joins on `district_code` only, dropping the three null-code districts (~0.4 M people). | Fall back to state + district. |
| 24 | Server | State and port labels differ by endpoint ("Andaman and/And Nicobar", two JNPT spellings). | One canonical label per entity. |
| 25 | SDK/MCP | `custom_port_capacities` exists only on REST. | Add to SDK and MCP. |
| 26 | Audit | Parquet twins of the village layers are not checked; two duplicate checks; village counts re-read 476 MB of GeoJSON. | Add twin parity; use parquet metadata. |
| 27 | Scripts | `reassign_village_districts.py --state X` skips the summary rebuild silently. | Rebuild or warn. |
| 28 | Scripts | `enrich_rail_stations.py`: `.gemini` cache fallback path, crash on empty zone, overwrites its own input; the base stations table has no script. | Tidy; document the base-table origin. |
| 29 | Scripts | `clean_state_name` title-cases "And", so 2011 layers and current layers disagree for three state names (closure file). | Canonical lookup, with run 5. |

| 30 | Tests | ~80–100 s of the suite is cold rebuilds of the national highway graph (`test_mcp_transport_regressions.py`, `test_route_adapter_parity.py`, `test_adapter_ambiguity_candidates.py`); `test_producer_graph_oracle.py` rebuilds its bundle per test. | Session-scoped cache/template fixtures. |
| 31 | Tests | No `tests/conftest.py`: helpers are imported from other test modules and one autouse fixture is copied into 7 files; `test_district_assignment.py` leaves `scripts/clean` and `scripts/analyze` on `sys.path`. | Add a conftest; use package imports. |
| 32 | Tests | Fragile pins: exact routed distance `131.19` km, data version `"2026.08"`, package version literal, toll rates repeated in three files. | Tolerance band; derive versions from the manifest/package. |
| 33 | Tests | Weak assertions (`pytest.raises(Exception)`, `status in (200, 400)`, `returncode in (0, 1)`, two bounds in one call) in 8 places. | Tighten. |
| 34 | Tests | Duplicates: Pune→JNPT parity computed in 8 places, the toll matrix twice, the Dhule/598 data check five times; `test_package.py`/`test_server.py` are older smoke tests mostly covered elsewhere. | Trim. |
| 35 | Tests | No tests at all for the 16 fetch scripts, `subdistrict_access.py`, `make_readme_figures.py`, `make_demo.py`, the notebook, the `audit` CLI command, `/health/ready`, or the SDK `plot_villages` success path; `allocate_population.py` logic is tested only through its committed output. | Add unit tests for the pure helpers first (GCT name join, demographics gate, population blend/scaling). |

## Documentation and data findings (read 2026-11-14)

| # | Area | Finding | Suggested action |
|---|---|---|---|
| 36 | Data | Three district names keep legacy-font mojibake: `B\dar` (Bidar), `DEHRAD@N` (Dehradun), `BIRBH@M` (Birbhum); `get_district("Dehradun")` fails. 97 sub-district rows and 9 sub-district names are also affected. The mojibake map misses `@` (ū) and `\` (ī). | Extend the map in `process_panindia_boundaries.py`, patch the layers and dependants, with run 5. |
| 37 | Skills | Legacy `skills/gis4logistics/` and `plugins/gis4logistics/` are stale (old counts, FASTag wording, version 1.0.0). | Delete, or align with the current skill and plugin. |
| 38 | Data | `icps.csv`: Mankachar falls in Meghalaya (12.9 km from Assam); Radhikapur is 11.3 km outside India; Zorinpui 4.0 km outside Mizoram; the "Demchok (planned)" row (34.35 N, 79.30 E) is inside Ladakh but about 185 km north of Demchok (~32.70 N, 79.45 E); Srimantapur repeats the Agartala coordinate. These feed ICP distances for every settlement. | Correct the coordinates from LPAI/official sources, with run 5. |
| 39 | Data | `railway_stations.csv`: state missing for 4,513 of 8,697 rows; 176 labelled rows fall outside their state (83 Telangana stations labelled Andhra Pradesh; gross errors such as Kavi (Gujarat) at Kolkata); two stations are in Bangladesh; 11 rows share coordinates. | Recompute `state` from the polygons; review the gross mismatches by hand. |
| 40 | Data | Hub rows cite weak sources: e-NAM 16/16 and cold chain 15/15 home pages; FCI 67 home page + 10 one Instagram post; ICPs 7 and ICDs 21 root URLs; ports 22/22 and air cargo 25/25 Wikipedia only (`version_manifest.json` names IPA and AAI). | Re-source the rows; until then licences are labelled mixed/unresolved. |
| 41 | Data | DFC "alignments" are straight lines between stations (1,359 / 1,162 / 42 km against stated 1,506 / 1,337 / 46 km); chainages do not match the coordinates. | Obtain real alignments; until then described as schematic. |
| 42 | Data | Embedded attribution in the ABDB GeoJSONs says "LGD-coded … schema consistent with Survey of India / LGD"; habitation, DataMeet and Pune files embed none. | Correct the string at the next boundary regeneration. |
| 43 | Packaging | The `reports` extra (reportlab, openpyxl) is imported by nothing; ADR 006 report export was never built. | Drop the extra or build the feature. |
| 44 | Data | `data/version_manifest.json` says data version 2026.08 (data regenerated in October), API version 1.0.0, "LGD" vintages; lists 17 of 42 datasets. The API serves these values. | Update with run 5. |
| 45 | Data | Sub-district layer: 87 (state, district) pairs not in the district layer (case and spelling differences); `sub_district_type` mixes `Tehsil`/`TEHSIL` and is 55% null; double spaces in three names. | Overwrite `district` from the district layer by code; normalise at the next regeneration. |
| 46 | Data | Many MMLPs, ICDs and IWAI terminals sit on shared city-centre coordinates with no "approximate" flag (e.g. MMLP Pune = FCI Pune; MMLP Visakhapatnam = the port = FCI); MMLP isochrones are computed from them. | Flag city-level rows or geocode the awarded sites. |
| 47 | Data | Hub CSVs come in two schema families; `industrial_nodes.csv` `district_code` filled for 1 of 21 rows; `freight_terminals.csv` `operator` 100% null. | Drop or fill the near-empty columns; document the schemas. |
| 48 | Data | `census2011_lgd_crosswalk.csv` has no LGD or current-district code, `district_key` has 10 duplicates, and nothing reads it. | Rename and describe it, or delete it. |
| 49 | Data | `industrial_nodes.csv`: Kakatiya Mega Textile Park duplicates the PM MITRA Warangal park; corridor outside the six NICDC corridors; "Praghurajpur" typo; district `Raigarh` for Raigad; project URLs look generated. | Review the rows against official lists. |
| 50 | Data | `toll_plazas.csv`: 675 distinct names for 1,536 rows, no ID column, `source_url` the OSM home page for every row. | Add a stable `toll_id` and keep the OSM node id at the next fetch. |
| 51 | Audit | `audit_baseline.json` has no keys for habitations, toll plazas, DFC stations, district summaries, hinterland, modal split or isochrones. | Extend the baseline. |
| 52 | Data | `location_aliases.yaml` port codes match nothing (hub CSVs have no code column); Kolkata/Haldia carry two codes; Mumbai Port has no alias; header still says "Decision Workbench". | Tidy when adding hub codes. |
| 53 | Data | 119 village names contain line breaks and 1,280 have double spaces in the village-access tables. | Strip and collapse whitespace at the next rebuild. |
| 54 | Docs | `docs/roadmap.md` is a historical plan still linked as the Roadmap (781 districts, removed CI, PyPI publishing); `docs/routing_review.md` is pre-merge evidence; `docs/stage4_source_limitations.md` repeats the 1.2/1.5 parameters. | Archive the first two; update the third with run 5. |
| 55 | Docs | README cites audit totals 5,367 / 16 WARN while `docs/audit_report.md` reads 5,359 / 12 WARN. | Update both with run 5. |
| 56 | Release | Tag `v1.0.0` exists, no `v1.1.0` tag; CHANGELOG has no 1.0.0 section and two "Changed" blocks in 1.1.0; SECURITY says the project has no tagged releases. | Decide on tagging; tidy the CHANGELOG. |
| 57 | Docker | The image installs jupyter and pytest, bakes in the 1 GB data and is then bind-mounted over; the `outputs` mount may be unwritable for the unprivileged user; obsolete `version:` key; header says "Production". | Slim the image and fix the mounts, or mark it evaluation-only. |
| 58 | Packaging | Unrelated classifier `Office/Business :: Financial :: Investment`; classifiers list 3.10 and 3.12 (only 3.11 tested); `package-data` globs match nothing. | Tidy `pyproject.toml`. |
| 59 | Data | `station_categories.csv` state values have embedded line breaks and typos (`ARUNACHALPRADES\n H`, `Gujrat`, `WEST BEBGAL`, `MANIP UR`); 134 joined stations disagree with `railway_stations.csv` on state. | Normalise to the 36 canonical names; review the disagreements. |
| 60 | Data | `fci_depots.csv` FSD Niwari (Madhya Pradesh) falls 10.2 km inside Uttar Pradesh; `air_cargo.csv` Surat operator typo "ofIndia". | Correct, with run 5. |
| 61 | Policy | India's official political map is the reference (operator ruling 2026-11-14). Both boundary layers comply, but older text still uses labels such as "PoK" and "disputed" (AGENTS.md build history, drift report, handoff log), and nothing in the audit guards the rule. | Reword at the next edit; add an audit check that five reference points (Aksai Chin, Gilgit, Muzaffarabad, Mirpur, Tawang) resolve to the expected states. |

The documentation and the data tree were read on 2026-11-14; the five largest producer test files were covered by fixture reads and searches only.
