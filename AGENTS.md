# AGENTS.md — Project Context & Build Log

Context file for AI agents (and humans) working on GIS4Logistics India.
Read this before making changes. Last updated: 2026-11-22.

## What this repository is

An open, curated GIS data collection and freight analytics platform for logistics/transport analysis in
India, published at https://github.com/subhams07/gisindia4logistics (PUBLIC canonical repository). It is a **hybrid** repo: small
and medium datasets committed directly (~460 MB total), larger/regenerable
datasets produced by fetch scripts.

**Core standards (do not violate):**
- CRS: EPSG:4326 (WGS 84). Join keys: LGD codes (state/district/sub-district/village).
- Attribute naming: snake_case. `catalog.yaml` is the machine-readable catalog — every dataset has an entry with source, license, vintage, quality_status.
- Data quality bar: committed datasets must pass validation (counts vs official totals, geometry validity, coordinate sanity). "High quality only" is a maintainer requirement.
- **India's official political map is the reference** (operator ruling 2026-11-14): all boundaries, maps and territorial wording follow the Survey of India depiction. Aksai Chin and Gilgit-Baltistan are in Ladakh; Mirpur and Muzaffarabad are districts of Jammu and Kashmir; Arunachal Pradesh is shown in full. Never take national or state outlines from sources that draw India differently (OSM admin boundaries, Natural Earth, GADM), and never describe such areas as outside India.
- India geospatial law: 2021 DST Guidelines regime; no defence features, no sub-threshold DEM/gravity data, boundary disclaimers on maps. See `docs/legal_compliance.md`.

## Repository contents (as of 2026-11-22)

| Layer | What | Count | Source | License |
|---|---|---|---|---|
| States (current) | polygons (name only) | 36 | SoI ABDB (provenance via docs/metadata/abdb/) | copyright per metadata; good-faith redistribution |
| Districts (current) | official LGD codes (abdb_district_code, census2011_code kept) | 780 | SoI ABDB + LGD directory | same |
| Sub-districts (current) | GPKG + Parquet, district names from the district layer | 6,636 | SoI ABDB | same |
| LGD district directory | official snapshot + ABDB→LGD crosswalk | 790 / 780 | lgdirectory.gov.in | GODL-India |
| States + districts (2011) | joins to Census tables | 36 / 640 | DataMeet maps | CC BY 4.0 |
| Villages | polygons, all 36 states/UTs (GeoJSON + Parquet) | 658,248 | Survey of India (27 via SoI portal, 9 via NWDP) | same ABDB constraints / GODL (NWDP) |
| Talukas (OSM sample) | Pune district | 14 | OSM | ODbL |
| Roads — NH network | numbered NH routes | 141,990 segments | OSM/Overpass | ODbL |
| Toll plazas | clustered OSM toll points | 1,536 | OSM | ODbL |
| Rail stations | codes/names/coords; state from polygons (source_state kept) | 8,697 | DataMeet railways (2016) | none declared (reference) |
| Station categories | NSG1-6 (+P variants) | 5,938 | community (railway-stations-classification.pages.dev) | unclear — flagged |
| Freight terminals | Gati Shakti GCTs, 78 geocoded (state-checked; 6 greenfield pending) | 84 | PIB PRID 1910049 | GODL-India |
| Logistics hubs | ports 22, ICPs 19 (2 approximate: Zorinpui village, Srimantapur town), ICDs/CFSs 44, air cargo 25, MMLPs 20, IWAI 40, FCI 77, e-NAM 16, cold chain 15, industrial nodes 21, PM MITRA 7 | 306 points | IPA/LPAI/CBIC/AAI/NHLML/IWAI/FCI + others | mixed, per-row source_url |
| Demographics | Census 2011 district indicators; current-district population estimates | 640 / 780 | community digitization (validated) / derived | GODL-India |
| Analysis | village access (geodesic), district/sub-district summaries, Stage 4 (geodesic NH routing, port matrix, freight modal split, hinterland, isochrones) | national | derived in-repo | mixed upstream |

Key paths: `data/` (committed data incl. `data/analysis/`), `scripts/fetch/`
(downloaders), `scripts/clean/` (standardization + shared utils in
`standardize.py`), `scripts/analyze/` (accessibility toolkit),
`scripts/make_demo.py` (end-to-end district pipeline → GeoPackage + map PNG),
`examples/` (notebook), `docs/` (sources.md, data_standards.md,
legal_compliance.md, metadata/abdb/, review/), `catalog.yaml` (44 datasets).

## Build history — steps taken, in order

### Phase 1: scaffold + core datasets
1. Planned hybrid repo (catalog + scripts + committed data), MIT for code,
   per-dataset data licenses. Wrote README, CONTRIBUTING, LICENSE,
   .gitattributes, requirements.txt (geopandas/shapely/pyproj/requests/pyyaml/pandas/matplotlib/mapclassify), docs.
2. **Administrative boundaries**: downloaded DataMeet maps master zip
   (https://github.com/datameet/maps/archive/refs/heads/master.zip, ~230 MB, CC BY 4.0).
   Processed by `scripts/fetch/fetch_admin_boundaries.py`: 36 states;
   districts are Census-2011 vintage — **641 features raw, one is a "DATA
   NOT AVAILABLE" placeholder in J&K (dropped → 640)**. Name normalization
   via `STATE_NAME_FIXES` (datameet spellings: "Arunanchal Pradesh",
   "Dadara & Nagar Havelli", etc.). Committed simplified (0.001°) with
   coordinates rounded to 5 decimals (`write_geojson` in standardize.py).
3. **Census 2011 demographics**: CSV from
   https://github.com/RajaBhavesh/India-Census-2011-Analysis-Using_Python
   (Project5/file.csv). **Validation gate** in `fetch_demographics.py`:
   640 districts, total population exactly 1,210,854,977, male+female=total,
   UP/Maharashtra totals match — script refuses to write on mismatch.
   Note: 2011 vintage → Telangana inside AP; DNH+DD merged to current UT name.
4. **Rail stations**: DataMeet railways stations.json → CSV
   (`data/rail/railway_stations.csv`), 8,697 rows with coords; zone ~45%
   filled; vintage 2016; no license declared (flagged in catalog).
5. **Logistics hubs**: compiled via web research into 4 CSVs (ports/icps/icds/
   air_cargo) with columns name, hub_type, state, city, latitude, longitude,
   operator, capacity_notes, source_url. Validated: in-India bbox, no dups.
   ICD/ICP coords are city approximations (flagged per row).
6. **Roads fetcher** (`fetch_roads.py`): Overpass API, OSM highway tags →
   Indian classes via `classify_osm_road` (ref=NH… takes precedence;
   `_link` suffix stripped). Committed sample: Pune district major classes,
   19,019 segments, simplified 0.0005° → 5.6 MB.
7. **Rail lines fetcher** (`fetch_rail.py`) + village boundaries fetcher
   (`fetch_village_boundaries.py`, OSM admin relations).
8. **Demo** (`make_demo.py` + notebook): one district → boundary + roads +
   rail + stations + hubs → GeoPackage + PNG. Ran for Pune; map visually
   verified. Census row joined and printed.
9. Git init (main), commits. `.zcode/` artifacts gitignored.

### Phase 2: quality hardening + legal
10. Maintainer directive: high-quality datasets only → added validation gates
    everywhere; census passed exactly.
11. **India geospatial legal**: researched 2021 DST Guidelines (liberalized;
    negative lists for attributes/areas; accuracy thresholds ~1 m horizontal /
    3 m vertical DEM / 1 milli-gal gravity — all far from anything here).
    Wrote `docs/legal_compliance.md`, README legal section, map disclaimer
    footers (boundary data is indicative, not SoI-authoritative).

### Phase 3: talukas (OSM)
12. OSM admin_level numbering **varies by state**: Maharashtra district=5,
    taluka=6; some states use 6/7. Script auto-probes levels 6+7 and filters
    by majority-containment (>50% area inside district polygon — plain
    `intersects` keeps neighbors' border slivers). Proper multipolygon
    assembly: member ways stitched into closed rings (`stitch_rings`),
    inner rings subtracted. Pune: 14/14 talukas, area sum = 100.0% of
    district. **Village polygons in OSM India ≈ zero** (villages are place
    nodes) — documented dead end.

### Phase 4: Survey of India villages (the big one)
13. Maintainer pointed to
    https://surveyofindia.gov.in/pages/village-boundary-data-base-of-entire-india
    — free, login-free per-state zips, shapefile in national LCC (WGS84),
    **LGD codes at every level** + village name + Rural/Urban category.
    27 states/UTs published; **9 absent: Assam, Arunachal, HP, J&K, Ladakh,
    Manipur, Meghalaya, Mizoram, Nagaland** (border states — treat as
    deliberate).
14. Wrote `fetch_village_boundaries_soi.py`: download → extract → reproject
    LCC→4326 → rename to snake_case schema → optional district filter →
    simplify → validate → GeoJSON per state in
    `data/administrative/villages/{state}_soi_villages.geojson`.
15. **Maintainer decision: treat SoI data as redistributable** (openly
    downloadable), attributed per file, remove-on-request — documented in
    legal_compliance.md. Raw zips stay gitignored (`data/raw/soi_villages/`).
16. Batch-fetched all 27 states. **SoI CDN throttles/breaks large downloads**
    (502s, ChunkedEncodingError on >100 MB files) → fetcher now validates zip
    magic + `zipfile.is_zipfile`, deletes corrupt partials, retries 4× with
    30-90 s backoff. Parallel workers (4×) helped; user hand-delivered 6 zips
    via browser (UP, WB, Uttarakhand, Tripura, Telangana, Rajasthan) which
    bypassed the flaky CDN (place zips in `data/raw/soi_villages/` with the
    exact names and the script uses them as cache).
17. **Per-state schema quirks handled**: Telangana ships mixed-case columns
    (Dist_LGD, Vill_cat) → columns uppercased before rename. Some UTs
    (Puducherry) lack sub-district columns → validation is tolerant.
    Zip slugs are irregular: "PUNDUCHERRY" (typo), "Dadra Nagar Haveli &
    Daman Diu" (spaces & ampersand) — see STATE_ZIPS map.
18. **Geometry validity pipeline (hard-won)**: naive simplify+round produced
    invalid geometries that only appeared on GeoJSON re-read. Final approach:
    per-feature `simplify(tol)` → `shapely.set_precision(1e-5)` grid-snap →
    `make_valid()`, with fallbacks (buffer(0), then largest-polygon) because
    Karnataka has free-hole shells that crash GEOS vectorized ops with
    TopologyException. ~11 zero-area villages exist in SoI's own source
    (e.g. Dhaond, Burhanpur MP) → dropped and reported. All 27 files
    re-validated valid. Grid-snap 1e-5 matches write_geojson's 5-decimal
    rounding, so what is validated is what is written.
19. State-level spot checks passed: Sikkim 447/6/18, Pune 1,884 villages/14
    sub-districts (= OSM taluka count), Telangana 33 districts, UP 75.

### Phase 5: PAN INDIA LGD boundaries (SoI ABDB)
20. User supplied `State_District_Subdistrict_PAN INDIA.rar` (202 MB).
    No 7-Zip on the machine — **Windows' built-in bsdtar
    (`C:\Windows\System32\tar.exe`) reads RAR fine**. Extracted to
    `data/raw/panindia/`.
20b. **Provenance resolved later**: user supplied SoI's official ISO 19115
    metadata (`docs/metadata/abdb/*.xlsx`) — the archive is SoI's ABDB
    product (provider: DIRECTOR, NGDR & UGID, Survey of India; lineage:
    1:50,000 DTDB, ORGI-harmonized 2024–25; published 2026-05-06; horizontal
    RMSE ±12.5 m; constraints: copyright, governed by Geospatial Guidelines
    2021 — good-faith redistribution posture documented in
    legal_compliance.md; re-evaluate before making the repo public).
21. Processed by `scripts/clean/process_panindia_boundaries.py`:
    - States 40 → **36** after dropping 4 "DISPUTED (X & Y)" inter-state
      slivers.
    - Districts 808 → **780** after dropping 28 disputed slivers (rows with
      NaN state + REMARKS containing DISPUTED). Mirpur & Muzaffarabad (J&K)
      kept with null LGD codes ("NOT AVAILABLE" in source).
    - Sub-districts 6,667 → 6,639. 89 rows have null LGD codes in source;
      one genuine code collision (3606 shared by Bari Pattan J&K / Khirkiya MP).
    - **Mojibake fix**: provider's legacy font replaced chars — deterministic
      map `{<:a, >:A, #:u}` (BR>HMAUR→BRAHMAUR, Dh<rw<d→DHARWAD,
      Bengal#ru→BENGALURU); 169 rows affected.
    - Outputs: `india_states_lgd.geojson` (2.3 MB), `india_districts_lgd.geojson`
      (9.3 MB), `india_subdistricts_lgd.gpkg` (22 MB).
22. `standardize.load_districts()/load_states()` prefer the LGD (current)
    files; make_demo and taluka fetcher switched to them. Census-2011 files
    kept for census joins.

### Phase 6: publishing
23. **File-size policy relaxed by maintainer**: no 10 MB cap; commit
    everything; >20 MB preferably LFS-routed formats (but see LFS lesson).
24. Installed GitHub CLI via `winget install GitHub.cli`; user ran
    `gh auth login` (browser). Canonical public repo:
    https://github.com/subhams07/gisindia4logistics
25. **LFS lesson**: account's LFS budget was exhausted → push blocked on the
    single LFS-routed gpkg. Fixed by `git lfs untrack` + commit as regular
    blob + **`git lfs migrate export --everything --include=<path>`** to
    rewrite history (pointers in old commits block pushes even after
    untracking the tip). Repo now uses zero LFS.
26. Push succeeded. GitHub warns (only warns) about 2 files >50 MB
    recommended size: UP villages 72.7 MB, MP villages 52.3 MB. Hard limit
    is 100 MB — all files below it.

### Phase 7: analysis toolkit + national NH network (2026-08-23)
27. **Accessibility analysis**: `scripts/analyze/nearest_facility.py` — per-
    village straight-line distance to nearest facility of each type
    (rail_station/icd/port/air_cargo/icp/mmlp/iw_terminal/fci_depot — any
    CSV present in data/logistics_hubs or data/rail). EPSG:7755
    `sjoin_nearest`; representative_point() for village polygons. Outputs:
    per-village CSV + district summary with `__STATE__` population-weighted
    row (Census 2011; post-2011 districts carry no population by design —
    e.g. Haryana's Charki Dadri). Example outputs committed: Sikkim, Haryana
    (headline: 92.5% of Haryana villages within 25 km of a rail station).
    Gotcha: hub CSVs use `name`, stations table uses `station_name` —
    loader normalizes to `fac_name`.
28. **NH network**: `scripts/fetch/fetch_nh_network.py` →
    `data/roads/india_nh_network.geojson` (35 MB). One India-bbox Overpass
    query for ways with ref~NH on motorway/trunk/primary; 145k segments,
    ~700 distinct NH numbers; simplify 0.0005 + make_valid. **Caveat**:
    summed length ~198k km overcounts official ~146k km (dual carriageways
    are separate ways) — fine for geometry/routing, not length stats.
    Per-state fallback loop exists if the big query ever fails.

29. **Rail upgrade** (2026-08-23):
    - `data/rail/station_categories.csv` — 5,941 stations NSG1-6 (+NSG*P
      suburban variants) from community site
      railway-stations-classification.pages.dev (license unclear — flagged);
      92.5% code-join to railway_stations.csv; NSG1=22 matches official.
    - `data/rail/freight_terminals.csv` — 84 Gati Shakti Cargo Terminals,
      transcribed from PIB PRID 1910049 annexure (GODL-India).
      `scripts/fetch/build_gct_terminals.py` reproduces the coordinate join:
      exact -> substring -> difflib fuzzy (cutoff 0.82) against stations
      table, then hub-CSV fallback for port GCTs (Krishnapatnam, Paradeep).
      70/84 geocoded; 14 greenfield sites coordinate-pending (flagged).
    - Note: agents died twice mid-research with a model "no text returned"
      error — the station-categories file was salvaged from the partial run;
      GCT compilation was done in-session afterward.
30. **Hub gap datasets** (2026-08-23, agent-compiled then validated):
    mmlps.csv (20 — NHLML awarded roster vs original 35-site identification,
    status flagged per row), inland_waterway_terminals.csv (40 — NW-1/2/3/4/
    5/16 with official capacities), fci_depots.csv (77 — city-level coords;
    FCI publishes no complete register). All consumed automatically by
    nearest_facility.py via FACILITY_SOURCES.
31. **catalog.yaml had a latent YAML syntax error** (unquoted `3606: ` inside
    a quality_notes scalar) — caught when adding a `yaml.safe_load` check.
    Rule: run `python -c "import yaml; yaml.safe_load(open('catalog.yaml'))"`
    after every catalog edit.

## Critical gotchas for future agents

- **Overpass API rejects clients without a User-Agent header** (HTTP 406) and
  502s transiently → use `http_session()` from standardize.py + retries.
- **SoI downloads throttle**: big states break mid-download; use the cached
  zips or browser-download into `data/raw/soi_villages/`.
- Shapefiles must be extracted to disk before `gpd.read_file` (sidecars).
- Always re-read written GeoJSON and check `is_valid` — rounding can break
  geometries; use the set_precision(1e-5)+make_valid pattern.
- `district_bbox()`/demo use the **current 780-district** file; census joins
  use the **2011 640-district** file — do not mix them up.
- Telangana/Puducherry schema quirks (above) are handled in the SoI fetcher;
  new states may need similar handling.
- Windows quirks: bsdtar for rar; OneDrive path (C:\Users\Shubham\OneDrive\
  Documents\GIS4logistics); git bash; CRLF warnings are noise.

## Known gaps / debt

1. ~~9 border/NE states have no village boundaries~~ — resolved in Phase J:
   SoI's own files from the National Water Data Portal (NWIC).
2. Rail stations base table: 2016 vintage, zone 45% filled. Categories now
   covered (5,941 NSG via community source — data.gov.in remains the
   authoritative upgrade). Freight terminals cover the 2023 GCT annexure
   only (306 approved / 118+ commissioned since then); 14 GCT sites lack
   coordinates.
3. ICD/ICP/FCI coordinates are approximations (flagged in capacity_notes).
4. Districts-by-decade: census 2011 ↔ current 780 mapping (census2011_lgd_
   crosswalk.csv helps; new-district splits after 2011 need LGD parentage).
5. Roads: PMGSY/NHAI official networks not integrated (documented pointers);
   NH network length stats unreliable (dual carriageways double-counted).
6. Two village files >50 MB (GitHub warning): UP 72.7 MB, MP 52.3 MB.
   Options: LFS (needs quota), per-district splits, stronger simplification.
7. No CI. A GitHub Action running `yaml.safe_load(catalog.yaml)` + the
   validation checks (census totals, boundary counts, geometry validity)
   would enforce the quality bar.
8. Accessibility is straight-line only (EPSG:7755); road-network travel time
   via OSRM/Valhalla is the planned upgrade.
9. ~~The 9 border/NE states' accessibility uses district centroids~~ —
   resolved in Phase J (village level everywhere).

### Phase 8: all-India accessibility (2026-08-23)
32. `nearest_facility.py --all` covers ALL 36 states/UTs. 27 at village
    level (SoI polygons); the 9 border/NE states run on district centroids
    (`unit` column = district_centroid; villages_geojson_path now returns
    None instead of exiting). Committed outputs: per-village CSVs,
    per-district summaries, and `data/analysis/india_district_access_
    summary.csv` (817 rows incl. 36 `__STATE__` rows). National headline:
    population-weighted mean distance to a rail station 13.6 km, to an ICD
    121 km across the 27 village-level states (island UTs 300-1200 km).
    Three bugs fixed during the run:
    (a) sjoin_nearest equidistant ties duplicate rows -> dedupe index
        before assigning values;
    (b) MP's SoI file has a typo'd `Villl_name` column (three L's) ->
        alias map in the loader (the old MP output silently lacked village
        names; regenerated 58,686 villages);
    (c) PAN INDIA district names carried stray `\r` (e.g. "CHIRANG\r") ->
        strip() in process_panindia_boundaries; all three LGD layers
        regenerated.
    Maharashtra initially fell back to district centroids because only the
    Pune extract existed — full state file (44,659 villages) generated and
    the analysis rerun at village level.

### Phase 9: full data audit (2026-08-23)
33. `scripts/audit/audit_all.py` — 212 checks across every dataset:
    completeness vs verified references, schema, geometry validity on
    re-read, coordinate sanity, cross-layer LGD joins, analysis
    reproducibility (recomputes sample distances), catalog-vs-disk. Run it
    after ANY data change; exits non-zero on FAIL. Report at
    `docs/audit_report.md`.
34. Three evaluate-fix-evaluate cycles: 28 FAIL -> 4 FAIL -> 0 FAIL
    (201 PASS / 11 documented WARN). Real bugs found & fixed:
    - ~~**Malegaon (LGD 598) missing from districts**~~ — WRONG diagnosis,
      reverted in item 41: the "missing" district was Dhule's own 4
      talukas mis-coded 598 in the source; the dissolve duplicated Dhule
      (781 districts). Correct count is 780.
    - TN/Puducherry `Subdist` column alias (sub-districts restored).
    - MP `Villl_name` typo (names restored).
    - 3 exact-dup subdistrict rows; NH junk refs ("Old NH7") + 2 degenerate
      geometries; stray CR in district names (earlier).
    - Reference table was stale vs 2024-25 district formations (Arunachal
      27 incl. Bichom/Keyi Panyor, Chhattisgarh 33, Goa 3 incl. Kushavati).
    Accepted WARNs: ~1k blank village names (SoI source), revenue-vs-census
    village counting for Delhi/Chandigarh, documented gaps (Ladakh 2,
    Puducherry 2/4, J&K Mirpur/Muzaffarabad rows, Delhi Nazul).
    **Audit-script lesson**: naive line-counting breaks on multiline CSV
    names; NaN != "" comparisons don't filter; glob patterns in catalog
    paths need expansion; quote YAML values with colons — THREE separate
    audit-script bugs produced false FAILs that looked like data bugs.

### Phase B: post-2011 population allocation & analysis update (2026-08-23)
35. **Population allocation for all 781 current districts** (`scripts/analyze/allocate_population.py`):
    - Geometrically overlays 640 Census 2011 parent districts with 781 current LGD districts in EPSG:7755.
    - Blends area-share with village-count share (50/50) to prevent urban carve-out bias.
    - Preserves exact census counts for unchanged districts; scales jointly by state groups (AP+TG, DNH+DD, J&K+Ladakh) to match official totals.
    - Output: `data/demographic/district_population_estimates.csv` (781 rows: 568 census2011, 134 blended, 79 area_share; sum 1,210,846,210, delta <0.001%).
36. **All-India accessibility rerun with 100% population coverage**:
    - `nearest_facility.py`: updated population lookup to state-scoped code and name matching with difflib fuzzy fallback.
    - Added `build_national_summary()` automatic aggregation in `nearest_facility.py`.
    - Regenerated all 36 state district summaries + national composite `data/analysis/india_district_access_summary.csv` (817 rows, 780/781 non-null pop weights; only unpopulated Disputed (Jharkhand) sliver has NaN).
### Phase C: station enrichment & freight flow series (2026-08-23)
37. **Railway station & category enrichment (Initiative 6a)** (`scripts/fetch/enrich_rail_stations.py`):
    - Upgraded `data/rail/station_categories.csv` (5,938 stations with category, zone, division, state, annual passenger footfall, and earnings).
    - Enriched `data/rail/railway_stations.csv`: filled missing zones, increasing coverage from 48% (4,171) to 79.3% (6,896) and added railway divisions.
38. **Freight-flow & transport demand tables (Initiative 4)** (`scripts/fetch/build_freight_tables.py`):
    - `data/freight/rail_freight_annual.csv`: 61 series across 5 FYs (2019-20 to 2023-24) by commodity group and originating zones; validated against PIB anchor of 1,591 MT in FY24.
    - `data/freight/port_throughput_annual.csv`: 65 series across 5 FYs for all 12 major ports; validated against IPA anchor of 819 MT in FY24 (Paradip #1 at 145.4 MT).
    - `data/freight/road_indicators_annual.csv`: 11 series of MoRTH classified road network lengths (NH, SH, MDR, Rural).
    - Added `audit_freight()` to `scripts/audit/audit_all.py` (57 checks | 57 PASS | 0 FAIL).

### Phase D: national highway travel-time engine (Initiative 3a) (2026-08-23)
39. **National Highway drive-time & shortest-path engine** (`scripts/analyze/nh_travel_matrix.py`):
    - Converts 141,990 NH LineStrings into a topological sparse CSR graph in `EPSG:7755` with speed-weighted edges (Motorway 90 km/h, Trunk 70 km/h, Primary 55 km/h) and 350m junction gap bridging.
    - Mainland connected component contains 289,458 nodes (91.3% of vertices), providing continuous routing across all 777 mainland districts.
    - Computes exact Dijkstra shortest-path drive times (hours/min) and road distances (km) to all logistics hubs and infrastructure targets (Ports, ICDs, MMLPs, Air Cargo, ICPs, Railway Stations, GCT Freight Terminals, and Expressways).
    - Outputs:
      - `data/analysis/nh_district_travel_time_summary.csv` (781 rows: nearest hub names, straight-line km, road distance km, and drive times).
      - `data/analysis/nh_district_port_matrix.csv` (781 rows x 12 major commercial ports drive-time matrix).
    - Validated National Benchmarks (777 mainland districts):
      - Nearest NH (First-Mile Access): straight-line 5.4 km (9 min access time)
      - Nearest Railway Station: straight-line 16.2 km | road 23.3 km | drive time 0.47 hrs (28 min)
      - Nearest Freight Terminal (GCT): straight-line 113.7 km | road 204.6 km | drive time 3.09 hrs (186 min)
      - Nearest ICD / CFS: straight-line 153.8 km | road 248.5 km | drive time 3.74 hrs (225 min)
      - Nearest Air Cargo Airport: straight-line 184.2 km | road 299.9 km | drive time 4.49 hrs (269 min)
      - Nearest MMLP: straight-line 196.9 km | road 346.2 km | drive time 5.09 hrs (305 min)
      - Nearest Expressway (Motorway): straight-line 244.1 km | road 390.7 km | drive time 5.77 hrs (346 min)
      - Nearest Major / Commercial Sea Port: straight-line 511.8 km | road 839.3 km | drive time 12.18 hrs (731 min)

### Phase E: multi-modal connectivity & dual-distance accessibility (2026-08-23)
40. **All-India village & district accessibility with road + rail infrastructure**:
    - `scripts/analyze/nearest_facility.py`: added `nh` and `expressway` spatial road layers alongside `rail_station` (8,697 IR stations), `freight_terminal` (84 GCT freight handling terminals), and all multi-modal hubs (`port`, `icd`, `mmlp`, `air_cargo`, `icp`, `iw_terminal`, `fci_depot`).
    - Regenerated all 27 village accessibility datasets (543,391 village polygons), all 36 state district summaries, and national composite `data/analysis/india_district_access_summary.csv` (817 rows, 781 districts + 36 state pop-weighted rows).
    - Enables multi-threshold feeder and catchment metrics (`nh_within_5km_pct`, `nh_within_10km_pct`, `expressway_within_25km_pct`, `rail_station_within_10km_pct`, `freight_terminal_within_25km_pct`).
    - Full deep audit suite passed: **227 checks | 216 PASS | 11 WARN | 0 FAIL**.

### Phase F: Dhule / 598 source-code correction (2026-10-19)
41. The derived "Malegaon (LGD 598)" district was a duplicate DHULE polygon
    (6,929 km², ~100% overlap with DHULE 498). Cause: the source
    sub-district layer tags Dhule's 4 talukas (Dhule 3959, Sakri 3958,
    Shirpur 3956, Sindkhede 3957) with district code 598, which is Kerala's
    Alappuzha (LGD codes run per state: Maharashtra 497-531, Kerala 588-601).
    The source has no Malegaon district — Malegaon taluka (4146) is in
    Nashik (516). Fix: `SUBDISTRICT_DISTRICT_CODE_FIXES` in
    process_panindia_boundaries corrects 598 -> 498, and
    `reconcile_districts()` now raises instead of deriving when a
    "missing" code's name already exists in the state or the code belongs
    to another state. Committed layers patched (raw source is not in the
    repo); districts 781 -> 780, Maharashtra 37 -> 36.
    Downstream: the duplicate also double-counted Dhule's ~2.04 M people in
    `district_population_estimates.csv`, which the Maharashtra state
    scaling then took out of every other Maharashtra district (~1.8% each).
    Regenerated population estimates, Maharashtra/national access
    summaries, and all Stage 4 outputs; the producer_limitations
    source-identity disclosures are now empty.

### Phase G: village-access district identity (2026-10-20, rebased 2026-11-21)
42. **Village-access outputs now carry current LGD districts.** The SoI village
    files use an older district-code scheme and legacy-font names (Gujarat
    Devbhumi Dwarka 674 vs LGD 656; Telangana 697 = Yadadri in the village file
    but Mancherial in the LGD layer; `DEVBHUMI DW>RKA`, `DEVBHUMI DWARKAl`).
    `nearest_facility.assign_current_districts()` assigns each village's
    representative point to the same-state polygon of
    `india_districts_lgd.parquet` containing it (EPSG:4326 `within`).
    Multi-polygon hits (sliver overlaps of the simplified layer) resolve by largest village-polygon
    overlap, then original over subdistrict-derived polygon, then the file's
    own code, then lowest code. 134 points (coastal generalisation; 13 border
    villages whose point lies metres into a neighbouring state) fall in no
    same-state polygon and take the nearest one (`district_assignment=nearest`).
    `source_district` / `source_district_code` keep the SoI values.
    - Summaries group by (district, district_code): 780 district rows + 36
      `__STATE__`; population joins exactly on (state, district_code) (name
      only for the 3 null-code districts). The old fuzzy name join had
      mis-weighted 12 states (e.g. West Bengal -7.4 M, Tamil Nadu -6.4 M);
      every state row's population now equals the estimates file's total.
    - `india_district_access_summary.parquet` is written with the CSV;
      `india_subdistrict_access.csv` gains `district_code` (same assignment).
    - `plot_villages.load_district_villages_gdf` aligns access rows to geometry
      by row order (village_code is not unique within states) and accepts the
      current or the source district name; it is in the producer code closure,
      so the Stage 4 producers were regenerated (run `linux-2026-11-21-run1`,
      on top of the Phase F Dhule fix).
    - `make_readme_figures.py` (README maps) now joins the access summary on
      (state, district_code) like the travel table; its fuzzy village-name
      grouping workaround is gone.
    - Audit: `audit_access_districts()` re-checks point-in-polygon for all
      621,105 rows, one summary row per district, parquet == CSV, subdistrict
      districts. Haryana reproducibility check now aligns by row order.
    - Residual: the LGD layer still has 4 legacy names (`B\dar`,
      `DEHRAD@N`, `BIRBH@M`, `SOUTH 24PARGANAS`); fixing them changes pinned
      producer inputs.

### Phase H: geodesic village distances (2026-11-21)
43. A comparison with saketlab/bharatviz exposed that EPSG:7755 (Lambert
    conformal, standard parallels 12.47°/35.17°N) is not distance- or
    area-preserving: point scale is 0.980 at 24°N and ~1.03 at 6.7°N, so
    7755 areas sum India to 3.179 M km² vs 3.270 M km² geodesic, and
    distances were ~2% short across central/north India.
    `nearest_facility.nearest_geodesic()` keeps 7755 only to collect
    candidates (everything within 1.06× the projected nearest distance) and
    picks the smallest WGS84 geodesic distance (pyproj.Geod); for lines the
    closest point is found in 7755 and measured geodesically. Matches a
    brute-force geodesic search exactly on samples at 8–28°N. Used by
    nearest_facility.py and subdistrict_access.py; all 36 states regenerated
    (~4 min). Rule: never report km or km² measured in EPSG:7755; use
    pyproj.Geod (distances/lengths) or an equal-area CRS such as ESRI:102028
    (areas). The Stage 4 / routing side followed in Phase I (area shares)
    and Phase K (highway graph).

### Phase I: official LGD district codes (2026-11-21)
44. While importing NWDP village files (which carry real LGD codes) we found
    the boundary layer's `district_code` was mostly NOT the LGD code: the
    ABDB DIST_LGD column holds the Census 2011 code for 635 older districts
    and its own numbering for 142 newer ones (only 10 real LGD codes). The
    LGD web service is public and answers POST requests:
    `POST https://lgdirectory.gov.in/webservices/lgdws/districtList` with
    form field `stateCode` (the download page itself is CAPTCHA-protected;
    do not script it). `scripts/fetch/fetch_lgd_directory.py` snapshots all
    790 districts to `data/administrative/lgd_districts.csv`;
    `scripts/clean/apply_lgd_codes.py` maps the 780 polygons (Census code ->
    exact name -> close name -> MANUAL) and re-keys the district and
    sub-district layers in place (GeoJSON edited property-by-property so the
    geometry text is untouched), keeping `abdb_district_code` and adding
    `census2011_code`. Idempotent. DNH&DD state code 26 -> 38.
    Consequences: the "village files use an older scheme" diagnosis in
    Phase G was backwards for new districts — the SoI village files carry
    real LGD codes for many of them (Devbhumi Dwarka 674, Yadadri 697); the
    village files' codes are still a mix of LGD, Census and a shifted
    numbering (Andhra's 2022 districts), so district identity stays spatial.
    allocate_population.py had keyed village counts by those mixed codes;
    it now counts villages by point-in-polygon and measures area in
    ESRI:102028. Rule: never treat a numeric district code as LGD without
    checking it against lgd_districts.csv; LGD codes are unique nationally,
    Census codes are not the same numbers (Karaikal is LGD 598, Alappuzha is
    Census 598). Remaining gaps vs LGD: Ladakh's 2025 districts, Delhi's
    2025 reorganisation, a few other 2025 formations (catalog notes).

### Phase J: village polygons for the 9 border/NE states (2026-11-21)
45. The maintainer downloaded SoI's "Village Boundary" files for Arunachal,
    Assam, HP, J&K, Ladakh, Manipur, Meghalaya, Mizoram and Nagaland (plus
    Puducherry, unused) from the National Water Data Portal (NWIC, Ministry
    of Jal Shakti; nwdp.nwic.gov.in — its TLS certificate was expired, so no
    scripted download) into `data/raw/nwdp_villages/vb_soi_<ab>_geojson.zip`
    (gitignored). `scripts/fetch/fetch_village_boundaries_nwdp.py`
    standardizes them to the SoI village schema (EPSG:7755 -> 4326, same
    simplify/grid-snap/make_valid pipeline via the shared `robust_prep`),
    adds `population_2011` / `households_2011`, nulls code 999999, and
    writes `<state>_soi_villages.geojson` + Parquet. 72,097 villages; their
    populations sum to the Census 2011 state totals (exact for 5 states).
    NWDP column names end in "\n" — strip them. A third-party re-host
    (bharatviz) exists but is simplified and lacks district codes; do not use
    it. Consequences: all 36 states are now at village level (658,248
    villages); allocate_population.py sums village populations per current
    district where the files carry them (`village_census`, 153 districts),
    which also fixes Leh and Srinagar. Audit expects village files for all
    36 states and checks NWDP population sums against Census.

### Phase K: geodesic highway graph (2026-11-22)
46. The NH graph's edge lengths were EPSG:7755 Euclidean norms (~2% short in
    central/north India). `graph_builder.geodesic_m(a, b)` (EPSG:7755 points ->
    WGS84 metres, thread-local Transformer) and `edge_lengths_km(coords, rows,
    cols)` (measured lower->higher vertex ID so the matrix stays bit-symmetric)
    now give every edge length; time = length / class speed. `validate_graph_
    metadata` recomputes geodesic lengths; CACHE_VERSION 6 -> 7 (the
    fingerprint's key set is pinned by producer_manifest, so do not add keys).
    `kernel.radius_candidates` collects nodes within SCALE_MARGIN (1.06) x the
    radius in projected metres and keeps those within the geodesic radius;
    `kernel.nearest_node_distance_m` does the same for highway access;
    `*_straight_km`, the oracle and the routing feeder-excess check use
    geodesic_m. **Junction bridges are still chosen by projected separation
    <= 350 m** — a geodesic threshold dropped 1,198 bridges in the north and
    disconnected real junctions (Katihar->Jogbani ICP 124 -> 277 km); bridge
    lengths are geodesic (<= 350 x SCALE_MARGIN, which the kernel's defensive
    check and the routing tier now use). Isochrone 8 km buffers stay
    projected (error below the 500 m simplification). Stage 4 regenerated
    (`linux-2026-11-22-run2`): median +1.85% on road km / drive time.
    Tests: fixtures now build geodesic edge lengths; tests whose synthetic
    coordinates sit near the EPSG:7755 false origin (~14S, where scale is
    ~1.2) and that check arithmetic/ties use the `planar` fixture in
    tests/conftest.py; metric behaviour is tested on Indian coordinates.

### Phase L: audit batches A and B (2026-11-22)
47. Ported the November audit fixes from the `veukfs` review branch (forked
    at PR #8, so every commit was adapted to the LGD codes, geodesic metrics
    and NWDP villages of #9-#13). Batch A (#15) touched no Stage 4 closure
    file; batch B (this phase) did and regenerated Stage 4
    (`linux-2026-11-22-run3`) plus the village access tables. Lessons:
    - GCT matching must check the row's state polygon: nationwide name and
      hub-file fallbacks put five terminals in other states and Wadi at the
      wrong Wadi. The PIB annexure gives some locations under their railway
      division's state (Lajkura, Nipania, Samba); `state` is the place's own.
    - Live API and batch producers must share joins
      (`producer_io.district_population`); the API's code-only population
      map silently dropped the 4 uncoded districts.
    - Standalone producer CLIs refuse the manifest directory; regenerate
      `data/analysis/` only through `stage4_generate`.
    - Do not run a script with `--help` to test it unless it has argparse:
      `apply_lgd_codes.py` has none and rewrote its outputs (idempotently).
    - The parquet LFS rule is gone; parquet commits as plain binary.
    Still open from the proposals: Census sub-district tables, ICP and station
    coordinate fixes, DFC alignments, freight-table re-sourcing.

### Phase M: audit batch C — names and locations (2026-11-22)
48. Decision items 36, 38, 39, 42, 44, 45, 53, 59, 60 (Stage 4 run
    `linux-2026-11-22-run4`). New scripts: `scripts/clean/patch_boundary_names.py`
    (applies `fix_mojibake`'s @/backslash decoding to the committed layers),
    `assign_station_states.py` (station state from polygons, `source_state`
    kept, review lists in `docs/review/`), `refresh_version_manifest.py`. Hub
    fixes used Nominatim town/station nodes checked inside the state polygon;
    unverifiable points are blanked and marked "coordinate pending" (the
    audit requires that note). Rules:
    - A hub or terminal point must lie in its own state (audit, 5 km coastal
      tolerance); border posts must be on the Indian side.
    - (Superseded in Phase O: a properties-only rewrite of the village files
      costs ~20 MB of history, not the full file size.)
    Still open after this phase: see Phase N.

### Phase N: audit batch D — API consistency and last locations (2026-11-22)
49. Decision items 5, 6, 7, 21, 22, 24, 25, 29 (Stage 4 run
    `linux-2026-11-22-run5`) plus the five pending GCTs and two ICPs. Rules:
    - `server/__init__.py` stays empty: anything it imports joins the Stage 4
      closure (the app, routers and plot_villages left it in this batch).
    - Canonical labels: states via `standardize.clean_state_name` (LGD
      spelling, lower-case "and"); major ports are the `ports.csv` names, with
      request aliases in `standardize.PORT_NAME_ALIASES`. Never compare
      against a title-cased literal ("Andaman And Nicobar Islands").
    - All REST errors are `{"detail": {"status", "code", "message"}}`
      (`server/services/error_contract.py`; handlers in `server/app.py`).
    - Village maps go through `plot_villages.render_village_map` only.
    - Areas served by the API are measured in ESRI:102028
      (`server/dependencies.district_attributes`).
    Still open: Census sub-district tables (#10), DFC alignments,
    freight-table re-sourcing, the 6 greenfield GCT sites.

### Phase O: village file attribute schema (2026-11-22)
50. Audit item 14, decided after a measured scratch-clone benchmark (another
    agent, on main 70f5a0e): `scripts/clean/normalize_village_properties.py`
    gives all 36 village files one schema - `state` = LGD spelling on every
    row (Chandigarh had no column, 14 Maharashtra rows were null, Delhi had
    'DELHI\r\n', spellings differed per file), the four code columns as text
    (Goa district_code, Tripura state_code and MP sub_district_code were
    integers), and whitespace collapsed in every text value. 26 files changed.
    Rules:
    - Edit village GeoJSON properties only: the files round-trip
      byte-identically through `json.dumps(separators=(",", ":"))`, so the
      geometry text stays identical and git delta-compresses the rewrite
      (benchmark: +21 MB history for all 36 files incl. Parquet twins).
      Never rewrite the geometry (dropping Z = 0.0 measured +152 MB).
    - Regenerate a Parquet twin with gpd.read_file -> to_parquet (snappy) and
      check its geometry WKB is unchanged (the script refuses otherwise).
    - pandas 3 reads GeoJSON text as StringDtype, not object: a check that
      filters `dtype == object` silently skips every column. Use
      `pd.api.types.is_string_dtype(s) or s.dtype == object`.
    - Code must give the same result on pandas 2 and 3 (requirements allow
      >=2.0): use `.astype("string")`, never `.astype(str)`, on columns that
      can be missing (pandas 2 writes 'nan'); read the village access CSVs
      with `village_access_io.read_village_access_csv` (default read_csv
      drops the leading zeros of all-digit village codes, returns them as floats where blanks occur, and turns 'NA'/
      'null' into missing).
    - "Not available" markers (NA, Null, <Null>, Not available) are stored
      as null in the village files; the audit fails on any that return.
    The village access tables and Stage 4 are unaffected (they do not read
    `state`; codes go through `pd.to_numeric`/`clean_text`).

### Phase P: real OSM DFC alignments and verified freight tables (2026-11-23)
51. Replaced schematic DFC polylines (`data/rail/dfc_network.geojson` + `.parquet`)
    with surveyed OpenStreetMap alignments fetched via the OSM REST relation API
    (`12145132` WDFC 1,460.0 km, `11154478` EDFC 1,256.6 km + 43.3 km Dadri–Khurja
    link; ODbL, `quality_status: verified`). Re-sourced `data/freight/` from
    Economic Survey Statistical Appendix Tables 1.27A & 1.28 (`indiabudget.gov.in`)
    and MoPSW/IPA Basic Port Statistics (`rail_freight_annual.csv` 66 series FY20–FY25
    excl. KRCL, `port_throughput_annual.csv` 65 series FY20–FY24,
    `road_indicators_annual.csv` 11 series), removing the unverified zone rows and
    wrong PIB PRIDs (`2016942`/`2017001`). All commodity and port parts reconcile to
    national totals (<0.05%); `audit_all.py` now enforces freight reconciliation as
    `FAIL` (upgraded from `WARN`) and verifies `dfc_network.parquet` parity. Zero
    Stage 4 closure files touched. Still open: Census sub-district tables (#10),
    the station state review lists in `docs/review/` (from PR #18), the 6
    greenfield GCT sites. (Audit #14, the village properties, was done in
    Phase O.)

## Enhancement backlog (suggested next steps)

- Per-state GeoPackage bundles (boundaries + villages + analysis layers, one
  file per state) for QGIS users (Initiative 5).
- Choropleth visualization and catchment map generation.
- Official population projections (NCP/MoHFW) to complement Census 2011
  weights in accessibility summaries.
- Maintainer roadmap: keep canonical public repo https://github.com/subhams07/gisindia4logistics synchronized; consider Zenodo DOI release for datasets.

## Environment

Windows 11, Git Bash, Python 3.11 (geopandas, shapely 2, pyproj, requests,
pyyaml, pandas, matplotlib, mapclassify — see requirements.txt), git 2.x +
git-lfs 3.7.1 (installed but unused), GitHub CLI 2.98 (`/c/Program Files/
GitHub CLI/gh.exe`, authenticated as subhams07), winget available.
