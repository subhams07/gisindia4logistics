# Changelog

All notable code, API, data-schema, and documentation changes will be recorded here. The project follows [Semantic Versioning](https://semver.org/) for code interfaces; dataset vintages are versioned separately.

## [Unreleased]

### Fixed & Changed — PR #29: railway stations review, ports/airports/GCTs verification, and toll-plaza refresh (Stage 4 regeneration deferred to Linux)

- **Railway stations & categories (PR #18 review resolved; `data/rail/railway_stations.csv` + `.parquet`, `data/rail/station_categories.csv`, `scripts/clean/assign_station_states.py`, `docs/review/`):**
  - **23** station coordinates fixed to OSM `railway=station`/`halt` nodes (18 from `railway_station_state_review.csv` + 5 from `station_category_state_review.csv`), **0** left pending; `ALJ` renamed `ALAI` → `ALIGANJ`; **0** duplicate `(latitude, longitude)` pairs remain across all 8,697 rows; `source_state` preserved verbatim; `railway_stations.parquet` regenerated.
  - **32** category state labels fixed in `station_categories.csv` (all 42 category review rows resolved: 32 `label_fixed`, 9 `coords_fixed`, 1 documented `code_collision` `TDK` Tadoki CG vs Taduku AP, 0 unresolved).
  - **2** international stations (`DSN` Darsana and `DHCA` Dhaka Cantt., Bangladesh Maitree Express) explicitly registered in `INTERNATIONAL_STATIONS`, kept with blank `state`, and excluded from India bbox/polygon checks; all 189 station review rows resolved (169 `label_ok_polygon_correct`, 18 `coords_fixed`, 2 `international_excluded`, 0 pending).
- **Gati Shakti cargo terminals (`data/rail/freight_terminals.csv`):**
  - **6** remaining greenfield GCTs geocoded via OpenStreetMap (`Vadalapudi`, `Ramnagar FCI GCT Terminal`, `Phulbasiya`, `Ghuggus`, `Moorsa`, `Dinegaon`) → **84/84 geocoded, 0 pending**, all 84 verified inside their own state polygon.
- **Ports & air cargo (`data/logistics_hubs/ports.csv`, `air_cargo.csv`, `data/aliases/location_aliases.yaml`, `scripts/clean/add_hub_codes_precision.py`, `docs/review/hub_site_check.csv`):**
  - **22/22** ports and **25/25** air cargo terminals now cite official non-Wikipedia URLs (**0** Wikipedia URLs remain).
  - **10** non-major port UN/LOCODEs populated from the UNECE list → **22/22** unique UN/LOCODEs (`location_aliases.yaml` corrected for Hazira `INHZA` and Dhamra `INDMQ`).
  - **2** Andhra Pradesh ports relocated onto verified OSM `industrial=port` features (`PRT-010` Visakhapatnam, `PRT-018` Gangavaram).
  - **8** ports (at existing coordinates, plus the 2 relocated → **17/22** `site`, 5 `unverified`) and **4** airports (`BLR`, `HYD`, `AMD`, `LKO` → **25/25** `site`) upgraded to `coord_precision = site`, with Overpass evidence recorded in `docs/review/hub_site_check.csv`.
- **Toll plazas (`data/roads/toll_plazas.csv` + `.parquet`, `scripts/fetch/fetch_toll_plazas.py`, `server/models/schemas.py`):**
  - Overpass refresh **1,536 → 1,585** clusters (`1,120 national_highway`, `102 expressway_toll`, `363 highway_toll`), upgraded to the 12-column schema adding deterministic `toll_id` (`TPL-0001`..`TPL-1585`), unique representative `osm_node_id`, and per-row OSM `source_url` (`https://www.openstreetmap.org/(node|way)/<osm_node_id>`); `--from-cache` CLI option added; `TollPlazaItem` schema updated; `data/audit_baseline.json` updated (`roads:toll_plazas`: `1536 → 1585`).
- **Audit (`scripts/audit/audit_all.py`):** enforces the 12-column toll schema, `toll_id`/`osm_node_id`/`source_url` invariants and `>= 1,500` rows; checks `DSN`/`DHCA` international exclusion and zero duplicate station coordinates; enforces 84/84 geocoded GCTs, 22/22 port UN/LOCODEs, and zero Wikipedia URLs in `ports.csv` and `air_cargo.csv`.
- **Stage 4 & village access regeneration (pending on Linux):** villages whose rail-station distance changed = **NOT YET MEASURED**, because Stage 4 and village/sub-district access regeneration is deferred to Linux. Until regenerated on Linux, `data/analysis/producer_manifest.json` carries stale SHA-256 input hashes for `rail/railway_stations.csv`, `rail/freight_terminals.csv`, `logistics_hubs/ports.csv`, `logistics_hubs/air_cargo.csv`, and `roads/toll_plazas.csv`.

### Fixed — industrial nodes verification (audit item 49, complete); builder/audit hardening & unit tests

- **`industrial_nodes.csv` (20 rows, now `verified` in `catalog.yaml`):**
  - Replaced "Integrated Manufacturing Cluster (IMC) Praghurajpur / Fatehpur" with **Integrated Manufacturing Cluster (IMC) Prayagraj** (Naini / Prayagraj, `25.37867, 81.87312` — flagged as Naini industrial-area approximation, `PRAYAGRAJ` LGD code `120`, 352 acres; cites PIB PRID 2232996).
  - Updated Zaheerabad (`12,600 → 3,245`), JPMIA (`6,500 → 1,578`), Kopparthy (`6,700 → 2,596`), and Orvakal (`5,000 → 2,621`) `area_acres` to match Cabinet-approved Phase-1 areas in PIB PRID 2232996 and cited PRID 2232996 on each row.
  - Corrected Orvakal Industrial Node corridor from `VCIC / HBIC` to official **`HBIC`** (per PIB PRID 2232996 S. No. 9).
  - Replaced all 19 unverified/broken `nicdc.in/project/...` and `gidb.org/mbsir` URLs with content-verified HTTP-200 official SPV/state portals (`dholera.gujarat.gov.in`, `auric.city`, `vikramudyogpuriujjain.com`, `iitgnl.com`, `gidb.gujarat.gov.in`) and PIB releases (PRIDs 2049312, 2232996, 1684631, relid 87421).
- **Builders & audit hardening:** `scripts/fetch/build_industrial_nodes.py` and `scripts/fetch/build_agri_logistics.py` now call `assign_node_districts.assign_file()` automatically so rebuilds pass audit (`Bengaluru Rural` → `Bengaluru (Rural)` fixed in `build_agri_logistics.py`). `scripts/audit/audit_all.py` loads `india_districts_lgd.parquet` once across all four node tables and handles invalid coordinates safely.
- **Tests:** added `tests/test_node_districts.py` covering LGD district assignment, idempotency, invalid/out-of-state coordinate rejection, audit mismatch checks, and end-to-end builder outputs.
- **No Stage 4 regeneration required:** none of the four node tables or edited scripts are in Stage 4's input or code closure.

### Fixed — industrial nodes review (audit item 49, partly); LGD districts for the node files

- `industrial_nodes.csv` loses the "Kakatiya Mega Textile Park (Warangal)" row (21 → 20). Its own status read "PM MITRA Node", and it sits 2.9 km from the PM MITRA Warangal park, which `pm_mitra_parks.csv` already lists.
- `industrial_nodes`, `pm_mitra_parks`, `cold_chain_storages` and `enam_mandis` now carry the current LGD `district` and `district_code` of the polygon each point falls in (`scripts/clean/assign_node_districts.py`). 7 district names change:
  - Dighi Port: Raigarh (a Chhattisgarh district) → Raigad.
  - Neemrana: Alwar → Kotputli-Behror (formed 2023).
  - AURIC: Aurangabad → Chhatrapati Sambhaji Nagar.
  - Bengaluru Rural Cold Hub (Hosakote): Bengaluru Rural → Bengaluru (Rural).
  - Three spelling variants: Tiruvallur, Y.S.R. and Kamrup Metro.
  - The audit checks every row.
- Not checked against NICDC, because nicdc.in fails certificate verification from the build environment. The per-row project URLs, the corridor labels and the "IMC Praghurajpur / Fatehpur" row are unchanged; no place of that name could be found. The catalog now marks the file `unverified`.

### Changed — batch F: hub codes and coordinate precision, test tidying, audit archive (Stage 4 run `linux-2026-11-23-run7`)

- **Hub identifiers (#52).** The 7 transport hub files gain `hub_code`, a stable ID assigned by the repository (`PRT-001`, `ICD-017`, …; not an official code).
  - `ports.csv` gains `unlocode` for the 12 major ports. The 10 non-major ports are blank until checked against the UNECE list.
  - Locations resolve by `hub_code` or UN/LOCODE (e.g. `{"hub_type": "port", "code": "INNSA"}`).
  - `location_aliases.yaml`: Haldia now carries INCCU (one row covers Kolkata and Haldia), and Mumbai Port/Bombay Port aliases are added.
- **Coordinate precision (#46).** `coord_precision` says what each point is, and is set from evidence only (`scripts/clean/add_hub_codes_precision.py`):
  - `site`: 7 ports and 21 airports, each confirmed against an OSM port/harbour or aerodrome feature within 3 km (`docs/review/hub_site_check.csv`).
  - `station` / `village` / `town`: the OSM node a row was re-located to.
  - `city`: an approximate point. That covers 76 of 77 FCI depots, 23 of 44 ICDs, and 15 of 20 MMLPs, which share a city-centre point with another hub.
  - `unverified` otherwise, and `pending` where coordinates are missing.
  - The catalog names the 15 MMLP isochrones that start from a city point.
- **Population: DNH&DD was never scaled.** The Census table already uses the merged UT name, which never matched the state-group label, so Dadra and Nagar Haveli and Daman and Diu stayed at 575,790 instead of its 2011 total of 586,956. The national total now matches Census 2011 to rounding (1,210,854,974 vs 1,210,854,977). Found while extracting the blend and scaling steps into tested functions.
- **Tests.**
  - The oracle bundles are built once per session (73 s → 35 s for that file).
  - The audit-batch tests import scripts as a package, with no `sys.path` edits.
  - The population helpers have unit tests.
  - The proposed trimming of duplicate tests was reviewed and not done, because each test guards a different layer.
- **Audit archive.** `docs/review/` now holds the November 2026 audit findings and decision proposals, with a resolution table for all 61 items.

### Fixed — population allocation keys; empty hub columns; audit baseline (Stage 4 run `linux-2026-11-23-run6`)

- **Population estimates: two pooling bugs in `allocate_population.py`.** State totals are unchanged.
  - **Uncoded districts were pooled.** Every grouping on (state, district_code) treated all of a state's uncoded districts as one district: Delhi's Nazul and Shahdara, and J&K's Mirpur and Muzaffarabad. Their villages were also never counted. Each current district now has its own key: the LGD code, or state + name where there is no code.
  - **2011 parents with the same name were pooled across states.** The 2011 parent key was the district name alone, and 10 names belong to districts of two states: Aurangabad, Bijapur, Bilaspur, Hamirpur, Pratapgarh and Raigarh, and Delhi's and Sikkim's East, North, South and West. Their village shares were split across both states, and the parent-population merge counted each pair twice. The key now includes the state.
  - **Sikkim now sums back to its 2011 parents.** Mangan was 12,481 and is now 41,404 (North Sikkim: 43,709). Gangtok + Pakyong is 280,313 (East: 283,583).
  - **Other changes:** Delhi's districts move by up to ±50% (they had been pooled with Sikkim), with smaller shifts in Chhattisgarh, Maharashtra and UP.
  - **Mirpur and Muzaffarabad** now take their J&K villages' Census 2011 population, which is 0: Census 2011 did not enumerate those areas.
  - **Counts by method:** census2011 485, village_census 155, blended 140.
- **Empty hub columns dropped:** `freight_terminals.operator` (empty in all 84 rows) and `industrial_nodes.district_code` (filled in 1 of 21 rows, with a non-LGD float). `docs/data_standards.md` now documents the hub file schemas.
- **Audit baseline:** grows from 96 to 142 series. It now covers toll plazas, DFC stations and network, the NH network, all district and sub-district access summaries, modal split, hinterland and both isochrone files. The new counts are read from file metadata, so no geometry is loaded.
- **Regenerated:** the access summaries (population weights only; 7 states) and Stage 4 run 6. Only `pop_2011` changes (186 districts); routing, the hinterland categories and primary ports, modal split and isochrones are identical.

### Changed — real OSM DFC alignments & verified freight tables (Wave 1)

- **Dedicated Freight Corridors (`data/rail/dfc_network.geojson` + `.parquet`).** Replaced the schematic straight-line station-to-station polylines with real surveyed single-track centerlines extracted from OpenStreetMap relations `12145132` (WDFC, 1,460.0 km geodesic vs 1,506 km official) and `11154478` (EDFC, 1,256.6 km geodesic vs 1,337 km official, plus the 43.3 km Dadri–Khurja link). Validated on re-read in both GeoJSON and GeoParquet (`quality_status: verified`, `license: ODbL`).
- **Annual freight flow tables (`data/freight/`).** Re-sourced all three freight tables from authoritative Government of India publications, replacing earlier hand-compiled tables and incorrect PIB PRIDs:
  - `rail_freight_annual.csv` (66 series, FY 2019-20 to 2024-25): Ministry of Railways revenue-earning originating freight loading by 10 commodity groups from Economic Survey Statistical Appendix Table 1.27A (excl. KRCL; 1,588.06 MT in FY23-24, 1,614.9 MT provisional in FY24-25). Removed the 16 unverified/inconsistent zone rows.
  - `port_throughput_annual.csv` (65 series, FY 2019-20 to 2023-24): MoPSW Basic Port Statistics / IPA 12 major ports series (784.31 MT in FY22-23, 819.23 MT in FY23-24; Paradip #1 at 145.38 MT).
  - `road_indicators_annual.csv` (11 series, FY 2019-20 to 2023-24): cross-checked against Economic Survey Table 1.28 and MoRTH Annual Report NH totals (146,145 km in FY23-24).
  - Every commodity and port breakdown reconciles to its national total within 0.05% (`build_freight_tables.py` enforces a hard gate; `audit_all.py` upgrades freight consistency from `WARN` to `FAIL`; `catalog.yaml` flips both tables from `unverified` to `verified`).
- **No Stage 4 regeneration required:** neither `dfc_network` nor `data/freight/*` is in Stage 4's input or code closure.

### Fixed — reading village codes and text the same way on pandas 2 and 3

- **API village codes lost their leading zeros.** `/admin/villages` (and the map's code fallback) read the village access CSVs with pandas' defaults. Where a state's codes were all digits, pandas parsed them as numbers, as floats wherever the column also had blanks. Zero-padded codes therefore came back wrong: `071955` was returned as `71955`. That is 27,754 codes: Himachal Pradesh 20,680, J&K 6,425, Uttarakhand 343 (its mixed column was parsed chunk by chunk), Ladakh 241, Delhi 54, Kerala 8 and Lakshadweep 3. `scripts/analyze/village_access_io.read_village_access_csv` now reads these tables with explicit dtypes: text as `string`, district codes as `Int64`, and only empty cells as missing.
- **"Not available" markers stored as text are now null** in the village files, the way the NWDP files store `999999`. The markers are `NA` (1,929 MP village codes, 8 Haryana categories), `Null` / `<Null>` (Odisha, Gujarat, Puducherry, Uttarakhand, West Bengal) and `Not available` (Karnataka codes; 17 village names in J&K, Karnataka and Uttarakhand): 2,534 values in 9 files. pandas read `NA` and `null` as missing from CSV but as text from Parquet, so the two formats disagreed. Similar-looking real names (Punjab's `NAN` and `NONE`, Hapur's `Nan`) are kept. The audit fails on any marker that comes back.
- **`.astype(str)` → `.astype("string")`** in the NWDP village fetcher, the station-category enrichment and one audit check. On pandas 2, `.astype(str)` writes a missing value as the text `"nan"`; on pandas 3 it stays missing.
- The village access tables are regenerated for the 9 states. Only `village`/`village_code` change; distances and summaries are unchanged.

### Fixed — village files: one attribute schema (audit item 14)

- All 36 village GeoJSONs and their GeoParquet twins now share one attribute schema (`scripts/clean/normalize_village_properties.py`). 26 files changed:
  - **`state`:** the LGD state name on every row. Before, Chandigarh's file had no `state` column, 14 Maharashtra rows were empty, one Delhi row read `'DELHI\r\n'`, and spellings differed per file (`MAHARASHTRA`, `Lakshadweep-UT`, `DADRA & NAGAR HAVELI & DAMAN & DIU`).
  - **Codes:** `state_code`, `district_code`, `sub_district_code` and `village_code` are text in every file. Goa's `district_code`, Tripura's `state_code` and Madhya Pradesh's `sub_district_code` were integers, so combining states mixed integers and strings.
  - **Text values:** line breaks and repeated spaces are collapsed in all of them, including 1,339 village names and 179 village codes (the same values batch C cleaned in the access tables).
- Geometry is unchanged byte for byte, in the GeoJSON text and in the Parquet WKB; 15 states keep the source's Z = 0.0. The REST village GeoJSON/map endpoints and anyone reading the files directly now see the corrected values.
- The audit checks all three properties for every file, in both formats.
- The village access tables and the Stage 4 outputs are unaffected.

### Changed — audit batch D: API consistency, server packaging, last hub locations (Stage 4 run `linux-2026-11-22-run5`)

- **Breaking for REST clients: one error body.** Router errors and unknown paths now answer `{"detail": {"status", "code", "message"}}`, like domain and validation errors (`candidates` added on a 409). Read `detail.message` where `detail` used to be a string.
- **Breaking for SDK users: one village-map implementation.**
  - `gis.plot_villages`, the MCP tool `gis_plot_villages_map` and `scripts/analyze/plot_villages.py` share `render_village_map`.
  - File names follow `<state>_<district>_village_<metric>_map.<ext>` under `outputs/maps`.
  - The result is `{..., "metric", "outputs": {"html_map_path", "png_map_path"}}`; the SDK's `files: {html, png}` is gone.
  - An `output_path` with a suffix other than `.html`/`.png`, or one the format does not write, is an error instead of being ignored.
  - The CLI no longer writes into `data/analysis`.
- **Labels.**
  - States use the LGD spellings everywhere. `clean_state_name` returned "Andaman And Nicobar Islands"; the Census 2011 state/district layers and the key-indicator table now use the same three spellings as the current layers (state values only).
  - Major ports use the `ports.csv` names, which the port matrix and `nearest_port_name` already used: "Jawaharlal Nehru Port (JNPT/Navi Mumbai)" and "Kolkata Port (Syama Prasad Mookerjee Port) incl. Haldia Dock Complex" replace the gravity model's labels in the hinterland output.
  - The old labels, "Nhava Sheva" and "Mumbai" are accepted as request aliases. An exact name beats a substring match, so "Mumbai" is Mumbai Port.
- **Filled fields.**
  - `state_code` (LGD) on `/admin/states` and `/admin/districts`.
  - District `area_sqkm` (ESRI:102028; India 3.27 M km²) and `subdistrict_count`.
  - Village `latitude`/`longitude`: the village access tables gain the representative point the distances are measured from.
  - Station NSG `category` by default (5,491 stations).
- **`custom_port_capacities`** on `sdk.simulate_port_catchment` and the MCP tool, as on REST.
- **Server packaging.**
  - `server/__init__.py` no longer imports the app, so the app, routers and `plot_villages` leave the Stage 4 code closure.
  - `gisindia4logistics serve` reads `GIS4LOGISTICS_HOST`/`GIS4LOGISTICS_PORT`, and the Dockerfile starts through it, so docker-compose's values apply and the data check runs. The unused `DEBUG` setting is removed.
- **Last hub locations.**
  - Five GCTs are placed on OpenStreetMap points inside their state: Kamalajari, Shivpur-Godawari and Ras Babra at their stations; New Makardhokda and Barabani at village level. 78 of 84 GCTs now have coordinates; the 6 greenfield sites remain pending.
  - Zorinpui ICP is at Hmawngbuchhuah village. Srimantapur ICP is approximated by Sonamura town.
  - Effects: 52,888 village freight-terminal distances fall (median −22 km) and 1,176 ICP distances fall (median −111 km). 58 districts get a new nearest GCT (median drive time 3.24 → 3.20 h) and 7 a new nearest ICP. 315 districts get a lower DFC cost in the modal split. The hinterland output is unchanged apart from the port labels.
- **Still open** (need a source): Census sub-district tables, real DFC alignments, re-sourced freight tables, the 6 greenfield GCT sites.

### Fixed — audit batch C: names, hub and station locations (Stage 4 run `linux-2026-11-22-run4`)

- **District and sub-district names.** The ABDB export's `@` (u) and backslash (i) legacy-font characters are decoded:
  - DEHRAD@N → DEHRADUN, BIRBH@M → BIRBHUM, `B\dar` → Bidar and about 100 sub-district names; `SOUTH 24PARGANAS` → SOUTH 24 PARGANAS.
  - The sub-district layer's district names now come from the district layer by code (295 rows were spelled differently, e.g. AIZWAL, RAYGAD).
  - `scripts/clean/patch_boundary_names.py` applied this to the committed layers, the population estimates, the code crosswalk and the toll table. Lookups such as `get_district("Dehradun")` now work.
- **ICPs and other hubs.** Several land-border ICPs sat outside India or in the wrong state:
  - Rupaidiha (in Nepal), Radhikapur (Bangladesh) and Mankachar (Meghalaya) take OpenStreetMap station or town points inside their state, and Demchok moves about 185 km south to the village.
  - Zorinpui (in Myanmar) and Srimantapur (which repeated Agartala) have no verified location and are coordinate-pending.
  - The Badarpur waterway terminal and FCI Niwari move into their own state. A typo in the Surat airport operator name is fixed.
  - The audit now checks every hub point against its state (5 km tolerance for coastal points). 228,053 village ICP distances change (median −8.5 km).
- **Railway stations.**
  - `state` is now the state polygon containing the station (8,695 of 8,697), and the old label is kept as `source_state`. The old labels had left 4,513 stations without a state and 176 in the wrong one, including 83 Telangana stations labelled Andhra Pradesh.
  - Station-category state spellings are normalised to the 36 canonical names.
  - Review lists are in `docs/review/`: 189 station rows and 42 category rows.
- **Access tables:** village names and codes no longer contain line breaks (1,339 names, 179 codes), and the audit fails on any that come back.
- **Metadata.**
  - The boundary GeoJSONs name Survey of India's ABDB, the LGD code source and the boundary disclaimer.
  - `data/version_manifest.json` is refreshed (`scripts/clean/refresh_version_manifest.py`): data version 2026.11, API version 1.1.0 (the package version), current feature counts and the village layer.
- **Stage 4 run 4:** the four renamed districts carry their new names.
  - The median drive time to an ICP is 9.44 → 9.46 h.
  - High-circuity pairs go from 368 to 370.
  - Hinterland and modal split are unchanged.
- **Not done here:** #14 (the village files' `state` column and dtypes); done later, see "village files: one attribute schema".

### Changed — audit batch B (Stage 4 regenerated, run `linux-2026-11-22-run3`)

- **Gati Shakti terminal coordinates.** Five GCTs had coordinates in another state: Kamalajari (Assam) at an Odisha station, Shivpur-Godawari (Jharkhand) in Madhya Pradesh, New Makardhokda (Maharashtra) at a Karnataka port, Ras Babra (Rajasthan) in Kolkata, Barabani (West Bengal) at Barabanki, UP. These are now blank and coordinate-pending. Wadi (Karnataka) moves from a Chitradurga station to Wadi junction (Kalaburagi). Lajkura, Nipania and Samba carry the station's own state rather than the railway division's. 73 of 84 GCTs have coordinates. `build_gct_terminals.py` and `geocode_gcts.py` accept only candidates inside the row's state polygon, and the audit checks every GCT coordinate. Effects:
  - 86,107 villages (13.1%) get a new freight-terminal distance (median +51 km); the removed points had acted as false nearby terminals.
  - 73 districts get a new nearest GCT. The median district drive time to a GCT is 3.14 → 3.24 h.
  - High-circuity district–hub pairs go from 372 to 368.
- **Modelled tolls per plaza** rise strictly with vehicle class: LMV 120, LCV 195, 2-axle 280, MAV 340, 7-axle 410 INR. LCV, 2-axle and 7-axle previously all paid 220, so a 7-axle paid less than a MAV.
- **Gravity defaults** in code are now 0.85 / 1.65, the published and live values (they were 1.2 / 1.5). The limitations sidecar no longer contrasts two parameter sets.
- **Port-gravity simulation** (REST and MCP) joins population like the batch producer: (state, district_code), then (state, district). The four uncoded districts (Nazul, Shahdara, Mirpur, Muzaffarabad; 2.26 M people) were captured by no port before.
- **Breaking API changes:** `share_villages_within_5km_rail` is removed from scorecards (it carried the 10 km value), and `commodity_name` is removed from the freight-cost SDK and MCP tool (it was ignored).
- **Standalone producer CLIs** (`nh_travel_matrix`, `generate_isochrones`, `port_hinterland_model`, `intermodal_cost_engine`) write to `$GIS4LOGISTICS_OUTPUT_PATH` or `outputs/`. They refuse a directory holding `producer_manifest.json` unless given `--allow-manifest-dir`.
- **Server:**
  - Unloaded tables return 503, not 500.
  - The toll-plaza NH filter normalises `nh 44` / `NH44` / `44` and no longer matches NH 444.
  - `DataStore.get_instance` initialises once under a lock.
  - The store starts without population estimates.
  - `/admin/villages` caches the last four state tables per file modification time.
- **CLI and scripts:** `plot-villages` reports user errors without a traceback. `nearest_facility.py --all` does not rebuild the national composite after a failed state.
- **Audit** adds checks for `hub_type` values per hub file, catalog parse failure, vanished baseline series and GCT state agreement.
- **Removed:** the 34,954 OSM habitation points and `fetch_border_habitations.py` (superseded by the NWDP village polygons since #12).

### Changed — project-audit fixes (no Stage 4 regeneration)

- SDK, CLI and MCP raise a clear `DataNotFoundError` when the data directory is missing; the MCP `serverInfo.version` is the package version.
- India's official political map is the stated reference for all boundaries and territorial wording. The audit checks five reference points (Aksai Chin and Gilgit in Ladakh, Muzaffarabad and Mirpur in Jammu and Kashmir, Tawang in Arunachal Pradesh) on both state layers. "PoK" labels are reworded.
- Removed: `india_subdistricts_lgd.geojson` (20.7 MB; the GPKG and Parquet layers remain), the duplicate Pune village extract, `data/rail/stations_raw.csv`, the Ambala village map, unused images, the tracked `*.egg-info/` directories and the legacy `gis4logistics` skill and plugin. Finished planning documents move to `docs/archive/`.
- The freight tables are `unverified` in the catalog: their parts do not add up to the national rows. `build_freight_tables.py` reports this instead of asserting its own anchors, and the audit adds a WARN check. The DFC layers are `sample` (schematic station-to-station lines). The hand-compiled hub files carry mixed licence terms.
- Fetch and clean scripts take `--help` instead of running on bare invocation. The toll fetcher uses scipy instead of scikit-learn. `enrich_rail_stations.py` drops a hard-coded cache path. `make_demo.py` matches the census row by state as well as name.
- Packaging and Docker: no LFS rule for parquet, PEP 639 licence metadata, no unused `reports` extra, PyPI publishing is manual-only, and Docker mounts `data/` instead of baking it into the image.
- Tests: the AST pin hashes `ast.unparse` (stable on Python 3.13), the CLI test does not depend on `PATH`, MCP maps go to `tmp_path`, routing fixtures are shared in `tests/routing_helpers.py`, one highway-graph cache serves all subprocess tests, and the audit checks that every village GeoParquet twin matches its GeoJSON.
- Docs: the user manual, skill examples (official LGD codes), ADR status notes, contract examples, catalog, sources, data standards and legal text match the current data.

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
