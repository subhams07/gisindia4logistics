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

Not yet read line by line: `tests/`, documentation, and the data tree (covered so far by scripted checks only).
