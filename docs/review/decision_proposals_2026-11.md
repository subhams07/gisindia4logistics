# Decision proposals for the November 2026 audit findings

This file gave a recommended decision for each of the 61 findings in [`audit_findings_2026-11.md`](audit_findings_2026-11.md), for the maintainer to approve, change or reject. The proposals below are kept as written; what was actually done, and in which pull request, is in the [resolution table](#resolution-2026-11-23) at the end.

How to read the columns:
- **Run 5**: *Yes* means the change edits a Stage 4 closure file (listed in `data/analysis/producer_manifest.json` `code_hashes`) or changes a producer input, so it must ship with the run-5 regeneration. *No* means it can ship on its own.
- **Needs from you**: what only the maintainer can supply. A dash means nothing.
- **Order**: *A* = do now (no run 5, no input needed), *B* = bundle with run 5, *C* = needs maintainer input or a source first.

Closure files include every `server/` module, `scripts/analyze/{stage4_generate,nh_travel_matrix,generate_isochrones,intermodal_cost_engine,port_hinterland_model,plot_villages,...}.py`, `scripts/clean/standardize.py`, `scripts/clean/graph_builder.py` and `gisindia4logistics/__init__.py`.

## Code and API (findings 1–35)

| # | Recommendation | Why | Run 5 | Needs from you | Order |
|---|---|---|---|---|---|
| 1 | Replace the inline ternary in `routing_facade.py:89` with a per-class table, keeping today's LMV 120 and MAV_20T 340 per plaza (INR): LMV 120, LCV 195, 2_AXLE_TRUCK 280, MAV_20T 340, 7_AXLE_OVERSIZED 410. Rule: rates strictly increase with class. Keep the "modelled, not official tariff" wording. | Today LCV, 2-axle and oversized all fall to 220, so oversized pays less than MAV. The NHAI fee rules scale roughly car 1 : LCV 1.6 : 2-axle 3.3 : 4–6 axle 5.25 : 7+ axle 6.4. The current MAV/LMV ratio (2.8) is already below that, so the proposal keeps the order and the existing two anchors rather than rescaling everything; LCV and oversized use the NHAI ratios relative to their neighbours. | Yes | Approve the numbers, or ask for a full NHAI-ratio rescale | B |
| 2 | Make 0.85 / 1.65 the code default in `stage4_generate.py` and `port_hinterland_model.py`; build the limitations sentence from the run config, not a literal. | The published outputs and the decision log (2026-10-19) already use 0.85 / 1.65. Code defaults that disagree produce different results for anyone calling the function directly. | Yes | — | B |
| 3 | Standalone producer `main()` defaults to `outputs/`; refuse to write into any directory containing `producer_manifest.json` unless `--allow-manifest-dir`. Fix the USER_MANUAL instructions. | Protects the published, manifest-validated data from accidental overwrites with other parameters. | Yes | — | B |
| 4 | Delete `india_subdistrict_access.csv` and `subdistrict_access.py` now; reintroduce a sub-district product later if wanted. | Stale (138 mojibake names), uncatalogued, unread. Regenerating needs the Census sub-district tables anyway, at which point it can be rebuilt properly. | No | Say if you want to keep it | A |
| 5 | Populate `area_sqkm` and `subdistrict_count` (computable from the layers) and village `latitude`/`longitude` (representative point already in the access tables); drop `state_code` until a source exists; return station `category` by default via the join. | Always-null fields mislead API users. | Yes | — | B |
| 6 | One shared village-map implementation used by SDK and MCP; same file name pattern and result keys; error on an unsupported `output_path` suffix. | Duplicated code has already drifted. | Partly (if it touches `plot_villages.py`) | — | B |
| 7 | Unify REST errors on the structured `error_contract` shape, including FastAPI validation errors (custom handler). | Three shapes is a client burden. Changes the public API; mention in CHANGELOG. | Yes | — | B |
| 8 | Remove `share_villages_within_5km_rail` (it holds the 10 km value); keep `..._10km_...`. Update the two tests. | Mislabelled number. | Yes | — | B |
| 9 | Label the 9 border-state OSM rows `unit = habitation`; remove the unreachable district-centroid fallback; update docs. | Honest labelling; dead code. | No (`nearest_facility.py` is not a closure file), but outputs change → regenerate those 9 access tables | — | A |
| 10 | Group by state + district name + code in `allocate_population.py` so null-code MIRPUR and MUZAFFARABAD are separate. | Pooled today. Changes population input → Stage 4. | Yes (input) | Census sub-district tables | B |
| 11 | Remove `commodity_name` from REST/SDK/MCP. | Accepted but ignored is worse than absent. Breaking change, CHANGELOG. | Yes | — | B |
| 12 | Blank the six coordinates now and mark them coordinate-pending; change `build_gct_terminals.py` to exact-in-state match first and to reject any match whose state disagrees; change the catalog note from "verified". | Wrong coordinates are worse than missing ones; GCT feeds freight-terminal distances. | Yes (input) | Optional: the real site locations | B |
| 13 | Remove the self-comparing asserts now; mark the tables `quality_status: unverified` until re-sourced; re-source from PIB/IPA later and assert zone/port sums against the national totals. | Internally contradictory tables should not be labelled validated. | No | The PIB/IPA source documents, if you want them re-sourced | A then C |
| 14 | Normalise village `state` values, dtypes and drop Z coordinates in a cleaning pass over the committed files (no raw zips needed for these three). | All three are mechanical fixes of committed data. | Yes (village layers feed Stage 4) | — | B |
| 15 | Fill habitation `state_code` from the state layer and cast `district_code` to integer. | Mechanical. | Check: habitations feed access tables, not Stage 4 | — | A |
| 16 | Delete `pune_soi_villages.*`; update the audit baseline. | Duplicate rows inflate globs. | No | — | A |
| 17 | Delete `india_subdistricts_lgd.geojson`; keep the GPKG/parquet. | 21.5 MB nothing reads. | No | — | A |
| 18 | Inspect raw `SUBDIS_LGD` values; if the truncation is the cause, keep the full code. | Needs the raw archive. | Yes | Raw PAN INDIA source (or run a one-line check on your laptop) | C |
| 19 | Replace scikit-learn's `BallTree` with `scipy.spatial.cKDTree` (scipy is already a dependency). | Avoids a new dependency. | No | — | A |
| 20 | Add an `lru_cache(maxsize=4)` keyed by (state, file mtime) for the village CSV. | 0.6 s per request for UP. | Yes | — | B |
| 21 | Empty `server/__init__.py` (import the app from `server.app`). | Importing any `server.*` loads FastAPI. | Yes | — | B |
| 22 | Make `gis4logistics serve` read `HOST`/`PORT`; drop `DEBUG`. | docker-compose sets them expecting an effect. | Yes | — | B |
| 23 | Join port-gravity population on (state, district_code), falling back to (state, district name) for null codes. | Drops ~0.4 M people today. Changes outputs. | Yes | — | B |
| 24 | Single canonical label table (state, port) used by every endpoint: "Andaman and Nicobar Islands", "Jawaharlal Nehru Port (JNPT)". | Consistency. | Yes | Confirm preferred spellings | B |
| 25 | Add `custom_port_capacities` to SDK and MCP. | Parity. | Partly (`gisindia4logistics/__init__.py` is closure) | — | B |
| 26 | Add parquet-twin parity checks, remove the two duplicates, read counts from parquet metadata. | Audit gap; speed. | No | — | A |
| 27 | `reassign_village_districts.py --state X` rebuilds that state's summary and the national composite. | Silent skip. | No | — | A |
| 28 | Remove the `.gemini` fallback, guard empty zones, write to a new file instead of the input; document the base-table origin (DataMeet 2016) in `docs/sources.md`. | Tidy. | No | — | A |
| 29 | Canonical state-name lookup in `standardize.py` ("Andaman and Nicobar Islands", "Dadra and Nagar Haveli and Daman and Diu", "Jammu and Kashmir"). | Closure file. | Yes | — | B |
| 30 | Session-scoped fixtures for the highway graph and the oracle bundle. | 80–100 s saved per suite run. | No | — | A |
| 31 | Add `tests/conftest.py`; move shared helpers and the autouse fixture there; use `monkeypatch.syspath_prepend`. | Test hygiene. | No | — | A |
| 32 | Tolerance for routed distance (±0.5 km); read versions from `importlib.metadata` and the manifest; single toll-rate fixture. | Fragile pins break on legitimate changes (finding 1 will break three of them). | No | — | A (with #1) |
| 33 | Tighten the 8 weak assertions to exact exceptions / status codes. | Tests that cannot fail. | No | — | A |
| 34 | Keep one Pune→JNPT parity test, one toll matrix, one Dhule/598 check; fold `test_package.py`/`test_server.py` into existing tests. | Duplication. | No | — | A |
| 35 | Add unit tests for the GCT name join, the demographics gate and the population blend/scaling first; fetch scripts later. | Most important untested logic. | No | — | A |

## Documentation and data (findings 36–61)

| # | Recommendation | Why | Run 5 | Needs from you | Order |
|---|---|---|---|---|---|
| 36 | Extend the mojibake map with `@ → U` and `\ → I`; patch districts, sub-districts and everything keyed on the names (summaries, population). | `get_district("Dehradun")` fails today. Names feed Stage 4 outputs. | Yes | — | B |
| 37 | Delete `skills/gis4logistics/` and `plugins/gis4logistics/` if the current skill/plugin supersede them (to be confirmed by grep for references). | Stale counts and version 1.0.0. | No | Confirm nothing external installs from those paths | A |
| 38 | Correct the five ICP coordinates from LPAI pages: Mankachar (Assam side), Radhikapur, Zorinpui, Demchok (~32.70 N, 79.45 E, keep "planned"), Srimantapur. Add an audit check that every hub falls inside its stated state polygon. | ICP distances for every settlement depend on them. Mankachar/Radhikapur/Zorinpui are border posts, so the point must be on the Indian side. | Yes (input) | Approve, or supply official coordinates | B |
| 39 | Recompute `state` for all stations by point-in-polygon on the state layer; keep the source value as `source_state`; list the 176 mismatches and the two Bangladesh points for review; dedupe the 11 shared coordinates by hand. | Missing state for half the table. | Yes (stations feed Stage 4) | Review the gross mismatches list | B |
| 40 | Keep the mixed/unresolved licence labels; re-source ports from IPA and air cargo from AAI first (those are the sources `version_manifest.json` claims); replace the Instagram citation. | Weak provenance. | No (source_url only) | Time, or accept the labels as is | C |
| 41 | Keep "schematic" labelling; obtain DFCCIL alignments later; remove chainages that contradict the coordinates. | Straight lines are not alignments. | No | A real alignment source, if any | C |
| 42 | Replace the embedded attribution string with the ABDB wording from `docs/metadata/abdb/` plus the SoI boundary disclaimer; add embedded attribution to the habitation, DataMeet and Pune files. | The string still says "LGD-coded". | Yes (boundary layers feed Stage 4) | — | B |
| 43 | Drop the `reports` extra and mark ADR 006 "Not planned". | Unused dependency group. | No | Say if you want report export | A |
| 44 | Regenerate `version_manifest.json` from the catalog during run 5 (data version 2026.11, API version from the package, all 42 datasets, ABDB wording). | The API serves stale values. | Yes | — | B |
| 45 | Overwrite sub-district `district` from the district layer by (state, code); normalise `sub_district_type` case; collapse double spaces. | Mechanical. | Yes | — | B |
| 46 | Add `coord_precision` (`site` / `city`) to MMLP, ICD and IWAI rows; flag isochrones from city-level hubs in the limitations file. Geocode awarded MMLP sites later. | Users must know which points are approximate. | Yes (hub input) | Optional: site coordinates | B |
| 47 | Drop `industrial_nodes.district_code` and `freight_terminals.operator`; document the two schema families in `data_standards.md`. | Near-empty columns. | Yes if freight_terminals schema is read by producers (check) | — | B |
| 48 | Delete `census2011_lgd_crosswalk.csv` (nothing reads it; it has no LGD code). Recreate a real crosswalk with the LGD list later. | Misleading name. | No | — | A |
| 49 | Remove the Kakatiya duplicate, fix "Prayagraj" and "Raigad", drop generated-looking URLs, re-check the corridor against NICDC's list. | Data errors. | Check | Review of the 21 rows | C |
| 50 | Add `toll_id` and `osm_node_id` and per-row OSM URLs at the next toll fetch. | Stable keys. | Yes if tolls feed routing (check) | — | B |
| 51 | Extend `audit_baseline.json` with the missing datasets after run 5 (so baselines reflect the new data). | Audit coverage. | After run 5 | — | B |
| 52 | Add a `hub_code` column to the hub CSVs and align `location_aliases.yaml`; fix the header. | Aliases that match nothing. | Yes | — | B |
| 53 | Strip line breaks and collapse whitespace in village names in the access tables. | Breaks CSV readers and joins. | Check (access tables vs Stage 4 inputs) | — | B |
| 54 | Move `docs/roadmap.md` and `docs/routing_review.md` to `docs/archive/`; update `stage4_source_limitations.md` with run 5. | Historical docs presented as current. | No / Yes | — | A / B |
| 55 | Regenerate both audit totals from one run after run 5. | Two numbers for one thing. | After run 5 | — | B |
| 56 | Do not tag now (standing rule). Tidy the CHANGELOG (add 1.0.0, merge the two "Changed" blocks). Correct SECURITY.md: `v1.0.0` exists. | Accuracy. | No | Tagging decision (later) | A |
| 57 | Mark the Docker setup "evaluation only"; drop jupyter/pytest from the image, rely on the bind mount instead of baking data, fix the `outputs` permissions, remove `version:`. | Image bloat, misleading "Production" label. | No | — | A |
| 58 | Remove the Investment classifier; list only 3.11; fix or remove the `package-data` globs. | Accuracy. | No | — | A |
| 59 | Normalise `station_categories.csv` state values to the 36 canonical names; list the 134 disagreements with #39. | Joins fail on typos. | Check | Review list (with #39) | B |
| 60 | Fix FSD Niwari's coordinate to the Madhya Pradesh side and the "of India" typo. | Data errors. | Yes (hub input) | — | B |
| 61 | Reword "PoK"/"disputed" in AGENTS.md history, drift report and handoff log; add a five-point audit check (Aksai Chin → Ladakh, Gilgit → Ladakh, Muzaffarabad → J&K, Mirpur → J&K, Tawang → Arunachal Pradesh) on both state layers. | Operator ruling 2026-11-14. | No | — | A |

## Proposed sequence

1. **Batch A (now, no regeneration):** 4, 9, 13 (asserts and status only), 15, 16, 17, 19, 26, 27, 28, 30–35, 37, 43, 48, 54 (archive part), 56, 57, 58, 61.
2. **Batch B (with run 5, after the Census sub-district tables arrive):** 1, 2, 3, 5–8, 10–12, 14, 20–25, 29, 36, 38, 39, 42, 44–47, 50–53, 55, 59, 60. One regeneration, one drift note.
3. **Batch C (needs maintainer input or a source):** 13 (re-sourcing), 18, 40, 41, 49.

Items marked "Check" are resolved during implementation by grepping the producers for the file; the result is noted in the commit.

## Batch A progress (2026-11-21)

Implemented on the review branch without waiting for the Codex review, because none of these change Stage 4 closure files or published analysis numbers.

| # | Status | What was done |
|---|---|---|
| 4 | Done | `india_subdistrict_access.csv` and `subdistrict_access.py` deleted. |
| 9 | Done | The 34,954 border-state rows carry `unit = habitation`; only that column changed. The unreachable centroid fallback is removed (a state with no file now exits with an error). |
| 13 | Partly | Self-comparing asserts replaced by part-vs-total checks (script output and an audit WARN); both tables marked `quality_status: unverified` with the inconsistencies stated. Re-sourcing still needs the PIB/IPA documents. |
| 15 | Done | Habitation GeoJSON and Parquet: `state_code` from the district layer, integer `district_code`; the fetcher writes the same. |
| 16, 17 | Done | Pune extract (GeoJSON + Parquet) and the unread sub-district GeoJSON deleted; audit baseline updated. |
| 19 | Done | DBSCAN replaced by scipy connected components (`cluster_labels`, unit-tested). |
| 26 | Done | Parquet twin parity (rows and columns) for all 27 village and 9 habitation files; baseline village counts read from Parquet metadata. The two "duplicate" checks are either/or branches, not duplicates, so they stay. |
| 27 | Done | `--state` rebuilds that state's summary, then the national composite. |
| 28 | Done, with a change | `.gemini` path removed, `--source-file` added, empty zones handled, base-table origin documented. Instead of writing to a new file, the script keeps existing zone/division values and fills only gaps, so re-running it is idempotent and consumers keep reading the same path. |
| 30 | Partly | Subprocess and in-process tests that route on the committed data share one session graph cache (fingerprint-checked). The per-test oracle bundle in `test_producer_graph_oracle.py` is unchanged. |
| 31 | Partly | `tests/conftest.py` holds the component-floor fixture once (was copied in 7 files); routing and MCP helpers moved to `tests/routing_helpers.py` and `tests/mcp_helpers.py`. The `sys.path` edits in `test_district_assignment.py` remain. |
| 32 | Done | Routed distance has a ±0.5 km tolerance; package, API and data versions are read from `pyproject.toml` and `version_manifest.json`; toll rates come from one table in `tests/routing_helpers.py` (finding 1 will change only that table). |
| 33 | Done | Six weak assertions now name the exact exception or status. |
| 34 | Deferred | Removing tests reduces coverage; left for the Codex review to confirm which duplicates are safe to drop. |
| 35 | Partly | `tests/test_audit_batch_a.py` (24 tests): political-map points, habitation codes, population state-group totals and census2011 rows (ADR 007 A1–A3 now have tests), the demographics gate (`validation_checks`, extracted), freight consistency, toll clustering, GCT name normalisation, zone cleaning, twin parity, catalog statuses. GCT matching tests wait for finding 12. |
| 37 | Done | Legacy `skills/gis4logistics/` and `plugins/gis4logistics/` deleted; nothing referenced them. |
| 43 | Done | `reports` extra removed; ADR 006 "Not planned". |
| 48 | Done | Crosswalk deleted (file, catalog entry, fetcher output, data-standards line); catalog lists 41 datasets. |
| 54 | Archive part done | `roadmap.md` and `routing_review.md` in `docs/archive/`; links updated. |
| 56 | Done | CHANGELOG has a 1.0.0 section and one "Changed" block in 1.1.0; SECURITY.md names the `v1.0.0` tag. No tag created. |
| 57 | Done | Docker is evaluation-only: runtime requirements only, data excluded from the image and mounted, outputs on a named volume, no `version:` key. Not built here (no Docker in the container). |
| 58 | Done | Investment classifier, 3.10/3.12 classifiers and the empty `package-data` removed. |
| 61 | Done | Five-point political-map audit check on both state layers (10 PASS); remaining "PoK" labels reworded. The Survey of India "DISPUTED (X & Y)" inter-state sliver labels stay: they describe state-boundary disputes inside India. |

## Resolution (2026-11-23)

Pull requests are on subhams07/gisindia4logistics: #15 (batch A), #16 (batch B), #18 (batch C), #19 (batch D), #20 and #21 (village files), #22 (Wave 1), #23 (batch E). Batch F is the pull request that adds this file.

| # | Outcome |
|---|---|
| 1 | Done (#16): per-class toll table LMV 120, LCV 195, 2-axle 280, MAV 340, 7-axle 410 INR |
| 2 | Done (#16) |
| 3 | Done (#16) |
| 4 | Kept instead (maintainer): the table is regenerated and audited with every village run |
| 5 | Done (#19) |
| 6 | Done (#19) |
| 7 | Done (#19) |
| 8 | Done (#16) |
| 9 | Moot: the habitation layer was removed (#16) |
| 10 | Done (#23), together with a second pooling bug: same-name 2011 parents across states |
| 11 | Done (#16) |
| 12 | Done (#16, #19, #29): the five blanked GCTs re-located in #19; remaining 6 greenfield GCTs geocoded inside their own states in #29 (84/84 geocoded, 0 pending) |
| 13 | Done (#22, Wave 1): re-sourced from the Economic Survey and MoPSW/IPA; reconciliation is a FAIL check |
| 14 | Done (#20, #21): one attribute schema, text codes, null markers; Z = 0.0 kept by decision (history cost) |
| 15 | Moot: the habitation layer was removed (#16) |
| 16 | Done (#15) |
| 17 | Done (#15) |
| 18 | Done: raw SoI ABDB `SUBDIS_LGD` codes restored as nullable strings across GPKG, Parquet, and `india_subdistrict_access.csv` (342 alphanumeric carveouts preserved, 6,554 unique codes, 0 within-state duplicates, only the 3 genuine raw ABDB cross-state collisions `3606`/`6148`/`6479` = 6 rows, down from 577) |
| 19 | Done (#15) |
| 20 | Done (#16) |
| 21 | Done (#19) |
| 22 | Done (#19) |
| 23 | Done (#16) |
| 24 | Done (#19): ports use the ports.csv names, states the LGD spelling |
| 25 | Done (#19) |
| 26 | Done (#15) |
| 27 | Done (#15) |
| 28 | Done (#15, with a change) |
| 29 | Done (#19) |
| 30 | Done (#15, batch F): shared graph cache; oracle bundles built once per session and copied per test |
| 31 | Done (#15, batch F): conftest, shared helpers, scripts imported as a package (no sys.path edits) |
| 32 | Done (#15) |
| 33 | Done (#15) |
| 34 | Reviewed in batch F, nothing removed: the Dhule and Pune-JNPT tests each guard a different layer, and test_package/test_server are the quick check the User Manual documents |
| 35 | Done (#15, #16, batch F): demographics gate, GCT matcher, population keys, blend and state scaling (which exposed the unscaled DNH&DD) |
| 36 | Done (#18) |
| 37 | Done (#15) |
| 38 | Done (#18); Zorinpui and Srimantapur located in #19 |
| 39 | Done (#18, #29): all 189 station review rows resolved (23 OSM-verified coord fixes incl. 5 from category review, ALJ renamed, 0 shared coords, DSN/DHCA excluded per 2026-11-24 ruling, 0 pending) |
| 40 | Done for ports & air cargo (#29; FCI & PM MITRA in #27): 22/22 ports and 25/25 air cargo rows cite official port authority / AAI / operator URLs (0 Wikipedia) |
| 41 | Done (#22, Wave 1): surveyed OSM alignments |
| 42 | Done (#18) |
| 43 | Done (#15) |
| 44 | Done (#18) |
| 45 | Done (#18) |
| 46 | Done (batch F): coord_precision on the 7 transport hub files from evidence; city-level MMLP isochrones named in the catalog (the limitations sidecar schema is unchanged) |
| 47 | Done (#23) |
| 48 | Kept instead (decision when porting batch A, #15) |
| 49 | Done (#25 + follow-up): Kakatiya duplicate removed and LGD districts assigned from polygons; IMC Praghurajpur/Fatehpur replaced with IMC Prayagraj (Naini/Prayagraj, 352 acres, PRAYAGRAJ LGD 120), Orvakal corridor set to official HBIC, all per-row source URLs replaced with HTTP-200 verified PIB releases (PRIDs 2049312/2232996/1684631, relid 87421) and SPV/state portals, and `assign_node_districts.assign_file()` integrated into `build_industrial_nodes.py` and `build_agri_logistics.py` |
| 50 | Done (#29): Overpass refresh to 1,585 clusters with stable `toll_id` (preserved across refreshes by `osm_node_id`), unique `osm_node_id`, per-row OSM `source_url`, and Parquet twin parity |
| 51 | Done (#23): 142 baseline series |
| 52 | Done (batch F, #29): `hub_code` on every transport hub row, UNECE `unlocode` on all 22 ports (10 non-major filled & `location_aliases.yaml` aligned in #29) |
| 53 | Done (#18) |
| 54 | Done (#15 archive; stage4_source_limitations no longer quotes 1.2/1.5) |
| 55 | Done: the audit report is regenerated from one run in every batch |
| 56 | Done (#15) |
| 57 | Done (#15) |
| 58 | Done (#15) |
| 59 | Done (#18, #29): all 42 category review rows resolved (32 category state labels corrected, 9 station coords fixed, 1 `TDK` code collision documented, 0 pending) |
| 60 | Done (#18) |
| 61 | Done (#15) |
