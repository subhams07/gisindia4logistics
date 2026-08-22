# AGENTS.md — Project Context & Build Log

Context file for AI agents (and humans) working on GIS4Logistics India.
Read this before making changes. Last updated: 2026-08-23.

## What this repository is

An open, curated GIS data collection for logistics/transport analysis in
India, published at https://github.com/subhams07/GIS4logistics (PRIVATE as of
2026-08-23; intended to go public eventually). It is a **hybrid** repo: small
and medium datasets committed directly (~460 MB total), larger/regenerable
datasets produced by fetch scripts.

**Core standards (do not violate):**
- CRS: EPSG:4326 (WGS 84). Join keys: LGD codes (state/district/sub-district/village).
- Attribute naming: snake_case. `catalog.yaml` is the machine-readable catalog — every dataset has an entry with source, license, vintage, quality_status.
- Data quality bar: committed datasets must pass validation (counts vs official totals, geometry validity, coordinate sanity). "High quality only" is a maintainer requirement.
- India geospatial law: 2021 DST Guidelines regime; no defence features, no sub-threshold DEM/gravity data, boundary disclaimers on maps. See `docs/legal_compliance.md`.

## Repository contents (as of 2026-08-23, after phase 7)

| Layer | What | Count | Source | License |
|---|---|---|---|---|
| States (current) | LGD-coded polygons | 36 | SoI ABDB (PAN INDIA archive; provenance via docs/metadata/abdb/) | copyright per metadata; good-faith redistribution |
| Districts (current) | LGD-coded | 780 | SoI ABDB | same |
| Sub-districts (current) | LGD-coded, GPKG | 6,639 | SoI ABDB | same |
| States + districts (2011) | joins to Census tables | 36 / 640 | DataMeet maps | CC BY 4.0 |
| Villages | LGD-coded polygons, per state | 543,391 across 27 states | Survey of India | same ABDB constraints |
| Talukas (OSM sample) | Pune district | 14 | OSM | ODbL |
| Roads — NH network | all numbered NH routes | 145k segments, ~700 routes | OSM/Overpass | ODbL |
| Roads — district fetcher | class-mapped (NH/SH/MDR/ODR) | Pune sample 19k | OSM/Overpass | ODbL |
| Rail stations | codes/names/coords | 8,697 | DataMeet railways (2016) | none declared (reference) |
| Station categories | NSG1-6 (+P variants) | 5,941 | community (railway-stations-classification.pages.dev) | unclear — flagged |
| Freight terminals | Gati Shakti GCTs, 70 geocoded | 84 | PIB PRID 1910049 | GODL-India |
| Rail lines fetcher | any district | — | OSM/Overpass | ODbL |
| Logistics hubs | ports 22, ICPs 19, ICDs/CFSs 44, air cargo 25, MMLPs 20, IWAI terminals 40, FCI depots 77 | 247 total points | IPA/LPAI/CBIC/AAI/NHLML/IWAI/FCI + Wikipedia | GODL / CC BY-SA, per-row source_url |
| Demographics | Census 2011 district indicators | 640 | community digitization, validated vs official | GODL-India |
| Analysis | village accessibility (nearest facility) | Sikkim + Haryana examples | derived in-repo | mixed upstream |

Key paths: `data/` (committed data incl. `data/analysis/`), `scripts/fetch/`
(downloaders), `scripts/clean/` (standardization + shared utils in
`standardize.py`), `scripts/analyze/` (accessibility toolkit),
`scripts/make_demo.py` (end-to-end district pipeline → GeoPackage + map PNG),
`examples/` (notebook), `docs/` (sources.md, data_standards.md,
legal_compliance.md, metadata/abdb/), `catalog.yaml` (25 datasets).

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
      NaN state + REMARKS containing DISPUTED). Mirpur & Muzaffarabad (PoK)
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
    `gh auth login` (browser). Repo created PRIVATE:
    https://github.com/subhams07/GIS4logistics
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

1. 9 border/NE states have no village boundaries (SoI doesn't publish; do not
   source elsewhere without checking terms — deliberate gap).
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

## Enhancement backlog (suggested next steps)

- Road-network travel time (OSRM/Valhalla on the OSM extracts) as upgrade to
  straight-line distances in nearest_facility.py; then accessibility maps
  (district choropleths, population-beyond-threshold tables).
- Run nearest_facility.py for all 27 states; commit the small district
  summaries, keep per-village CSVs generated on demand.
- Freight-flow data: Railway Board origin-destination tables, IPA port
  throughput — enables corridor modelling on top of the NH/rail layers.
- data.gov.in integration for authoritative station categories (API key via
  env var) to replace the community NSG compilation.
- Per-state GeoPackage bundles (boundaries + villages + analysis layers, one
  file per state) for QGIS users.
- CI workflow: catalog lint + dataset validation on push (gap 7).
- Official population projections (NCP/MoHFW) to complement Census 2011
  weights in accessibility summaries.
- When making public: `gh repo edit subhams07/GIS4logistics --visibility
  public`; FIRST re-evaluate SoI/ABDB redistribution posture (metadata says
  copyright — see legal_compliance.md); consider Zenodo DOI release.

## Environment

Windows 11, Git Bash, Python 3.11 (geopandas, shapely 2, pyproj, requests,
pyyaml, pandas, matplotlib, mapclassify — see requirements.txt), git 2.x +
git-lfs 3.7.1 (installed but unused), GitHub CLI 2.98 (`/c/Program Files/
GitHub CLI/gh.exe`, authenticated as subhams07), winget available.
