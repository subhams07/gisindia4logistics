# Data Sources, Lineage & Methodology

Comprehensive, per-source documentation of datasets, provenance, licensing, lineage, and validation gates across all layers of the **GIS4Logistics India** platform.

Quality status definitions:
- **verified** — passed the repository's recorded validation gates (exact counts vs official registers, geometry checks, coordinate bounds, and join-key checks). This label describes data validation, not legal clearance or source freshness; the audit date and execution context should be recorded with releases.
- **community** — community-curated data without direct ministerial API guarantees (OpenStreetMap, DataMeet); terms vary by dataset. DataMeet boundaries have CC BY 4.0 attribution, but the railway station catalog records an unclear license (no license file), not a blanket reuse grant.
- **sample** — illustrative snapshot committed for testing and demonstration.

---

## 1. Quality Policy & Standards

1. **Official Sources First**: Prefer official Survey of India, Census of India, and Ministry portals (MoRTH, Indian Railways, IPA, CBIC, AAI, NHLML, IWAI, FCI); fall back to curated community data (DataMeet, OSM) only when official sources lack bulk open GIS formats or are restricted.
2. **Deterministic Join Keys**: All layers standardize to **Local Government Directory (LGD)** state, district, sub-district, and village codes.
3. **Validation & Integrity Gates**: Every committed dataset must pass `scripts/audit/audit_all.py` (geometry validity, coordinate bounding, exact official counts).
4. **Transparent Lineage**: Data with specific licensing (GODL-India, ODbL, CC BY 4.0, SoI copyright) is attributed below and in `catalog.yaml`. Inventory labels are not legal clearance; public availability does not establish permission. See [DATA_LICENSE.md](../DATA_LICENSE.md) for mixed terms and unresolved redistribution conditions.

---

## 2. Administrative Boundaries (5 Hierarchy Tiers)

| Hierarchy Level | Dataset & File Path | Count / Scope | Provider & Lineage | License & Governance | Quality Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Level 0 (National)** | `data/administrative/india_states_lgd.geojson` | 1 national boundary | Derived via spatial dissolve of SoI ABDB states | Copyright SoI; redistribution unresolved, not written permission (see `DATA_LICENSE.md`) | **verified** |
| **Level 1 (States/UTs - Current)** | `data/administrative/india_states_lgd.geojson` | **36 States & UTs** | Survey of India ABDB product (NGDR & UGID; 1:50k DTDB, ORGI 2024–25 harmonized) | Copyright SoI; redistribution unresolved, not written permission (see `DATA_LICENSE.md`) | **verified** |
| **Level 1 (States - 2011 Vintage)**| `data/administrative/india_states.geojson` | 36 States & UTs | DataMeet maps archive (harmonized to 2011 Census tables) | CC BY 4.0 | **verified** |
| **Level 2 (Districts - Current)** | `data/administrative/india_districts_lgd.geojson` | **781 LGD Districts** | SoI ABDB (780 official + Malegaon LGD 598 derived from 4 subdistricts; disputed slivers dropped) | Copyright SoI; redistribution unresolved, not written permission (see `DATA_LICENSE.md`) | **verified** |
| **Level 2 (Districts - 2011)** | `data/administrative/india_districts.geojson` | **640 Districts** | DataMeet maps (Census 2011 district layout, J&K placeholder dropped) | CC BY 4.0 | **verified** |
| **Level 3 (Sub-districts / Talukas)** | `data/administrative/india_subdistricts_lgd.gpkg` | **6,636 Sub-districts** | SoI ABDB product (mojibake font fixed deterministically, 3 duplicate rows dropped) | Copyright SoI; redistribution unresolved, not written permission (see `DATA_LICENSE.md`) | **verified** |
| **Level 3 (Taluka Sample Extract)** | `data/administrative/pune_taluka.geojson` | 14 talukas | OpenStreetMap admin relations (majority-containment stitched polygons) | ODbL | **verified** |
| **Level 4 (Villages - Polygons)** | `data/administrative/villages/*_soi_villages.geojson` | **543,391 Villages** (27 states/UTs) | Survey of India Village Boundary Database (national LCC reprojected to EPSG:4326) | Copyright SoI; redistribution unresolved, not written permission | **verified** |
| **Level 4 (Habitations - Points)** | `data/administrative/villages/*_habitations.geojson` | **34,954 Settlements** (9 border states) | OpenStreetMap settlement place nodes (`place=village\|hamlet\|town`) joined with 781 LGD districts | ODbL | **verified** |
| **Level 4 (Combined National Coverage)** | Total National Settlements | **578,345 Villages & Habitations** | 100% settlement coverage across all 36 Indian States & UTs | Mixed (SoI + ODbL) | **verified** |

### Additional Reference & Alternative Administrative Sources
- **ISRO Bhuvan** ([bhuvan.nrsc.gov.in](https://bhuvan.nrsc.gov.in)): National remote sensing portal for satellite imagery and alternative administrative thematic layers.
- **Local Government Directory (LGD)** ([lgdirectory.gov.in](https://lgdirectory.gov.in)): Ministry of Panchayati Raj official master register for state, district, block, sub-district, and gram panchayat codes.
- **DataMeet Maps** ([github.com/datameet/maps](https://github.com/datameet/maps)): Community open GIS boundary repository (CC BY 4.0) used for Census 2011 joins.

### Technical & Processing Notes
- **PAN INDIA ABDB Processing (`process_panindia_boundaries.py`)**: 4 inter-state disputed slivers dropped from state layer (28 from districts/subdistricts). Mojibake font artifacts fixed deterministically (`<` $\to$ a, `>` $\to$ A, `#` $\to$ u — e.g. `BR>HMAUR` $\to$ BRAHMAUR, `Bengal#ru` $\to$ BENGALURU). PoK districts (Mirpur, Muzaffarabad) retained with null LGD codes as per source. Malegaon district (LGD 598) derived by dissolving its 4 constituent subdistricts.
- **SoI Village Processing (`fetch_village_boundaries_soi.py`)**: Reprojected from national LCC to WGS 84 (`EPSG:4326`). Processed via `shapely.set_precision(1e-5)` grid-snap and `make_valid()` to eliminate GEOS topological slivers.
- **Border Habitations Ingestion (`fetch_border_habitations.py`)**: Covers the 9 states not published in SoI village boundary zips (Arunachal Pradesh, Assam, Himachal Pradesh, Jammu & Kashmir, Ladakh, Manipur, Meghalaya, Mizoram, Nagaland) using geocoded place nodes joined to LGD districts.

---

## 3. Road Network & Toll Infrastructure

| Dataset & File Path | Scope / Count | Provider & Lineage | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **National Highway Network**<br>`data/roads/india_nh_network.geojson` | **141,990 segments** (~634 NH routes) | OpenStreetMap Overpass extraction (`highway=motorway\|trunk\|primary` with `ref~NH`); simplified 0.0005° | ODbL | **verified** |
| **Clustered Toll Locations**<br>`data/roads/toll_plazas.csv` | **1,536 clusters** (model labels: 1,100 NH, 101 expressway, 335 unclassified `highway_toll`) | OpenStreetMap Overpass `barrier=toll_booth`, 150m DBSCAN; 2026-08 catalog snapshot, not independently dated here. Missing NH labels use nearest-network assignment without a distance limit; 334 unclassified records have inferred NH labels. No SH/bridge identification, official NHAI identity or verified tariffs | ODbL | **verified** (repository validation only, not official identity/tariff verification) |
| **District Road Network (Sample)**<br>`data/roads/pune_sample_roads.geojson` | 19,019 segments (Pune district) | OSM highway classes (NH, SH, MDR, ODR, Residential) classified via `scripts/fetch/fetch_roads.py` | ODbL | **verified** |

### Additional Road Pointers & Infrastructure Sources
- **NHAI / MoRTH** ([nhai.gov.in](https://nhai.gov.in), [morth.nic.in](https://morth.nic.in)): Ministry of Road Transport and Highways official registers and Basic Road Statistics.
- **NHAI Toll Information System (TIS)** ([tis.nhai.gov.in](http://tis.nhai.gov.in)): Official portal for highway fee determination, chainages, and concessionaires.
- **Geofabrik India Extract** ([download.geofabrik.de/asia/india.html](https://download.geofabrik.de/asia/india.html)): Complete daily PBF extracts of OpenStreetMap India.
- **PMGSY (OMMAS / Samarth / GeoSadak)** ([geosadak-pmgsy.nic.in](https://geosadak-pmgsy.nic.in)): Ministry of Rural Development rural road GIS network.

---

## 4. Railway Infrastructure & Freight Terminals

| Dataset & File Path | Scope / Count | Provider & Lineage | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **Dedicated Freight Corridors (DFC)**<br>`data/rail/dfc_network.geojson` | **3 Corridors** (WDFC 1,506 km, EDFC 1,337 km, Dadri-Khurja Link) | DFCCIL operational alignments | GODL-India / Public | **verified** |
| **DFC Stations & Yards**<br>`data/rail/dfc_stations.csv` | **54 Junction Stations & Crossing Yards** | DFCCIL station master roster with chainages and IR feeder interchange links | GODL-India / Public | **verified** |
| **Railway Stations**<br>`data/rail/railway_stations.csv` | **8,697 stations** | DataMeet railways (Indian Railways/erail lineage), **2016 station base** per catalog and `scripts/fetch/fetch_rail.py`; later zone/division enrichment via `railway-stations-classification.pages.dev` in `scripts/fetch/enrich_rail_stations.py`. Categories remain a separate roster; enrichment is not a station-location refresh or verification of current operational status | Unclear (no DataMeet license file); reference data, redistribution unresolved | **community** |
| **Station Categories (NSG1-6)**<br>`data/rail/station_categories.csv` | **5,938 category records; 5,493 joined station codes** | Community classification site `https://railway-stations-classification.pages.dev/` (NSG1–NSG6, passenger footfall & earnings), ~2023 catalog vintage; Ministry of Railways classification lineage, not a direct verified official feed | Unclear per catalog; reference use, cross-check before publication-grade use; public accessibility is not permission | **community** |
| **Freight Terminals (GCT)**<br>`data/rail/freight_terminals.csv` | **84 Gati Shakti Cargo Terminals** | PIB PRID 1910049 annexure (Ministry of Railways); geocoded via stations crosswalk and port fallbacks | GODL-India | **verified** |

### Additional Rail Sources & Pointers

The catalog labels the classification vintage approximately 2023, while the enrichment script's introductory comment calls it 2024. That discrepancy has not been resolved by a verified dated upstream snapshot. The script also permits fallback to an agent scrape cache when the network source is unavailable; committed records therefore cannot be certified as a dated live-source snapshot. Treat the classification vintage as approximate; neither description establishes a refreshed station-location database or current operational status.
- **Indian Railways / Railway Board** ([indianrailways.gov.in](https://indianrailways.gov.in)): Annual statistical statements, zonal divisions, and freight operating statistics.
- **Dedicated Freight Corridor Corporation (DFCCIL)** ([dfccil.com](https://dfccil.com)): WDFC and EDFC project alignments and freight feeder yards.
- **Open Government Data (OGD) Platform** ([data.gov.in](https://data.gov.in)): Indian Railways station masters and train timing datasets.

---

## 5. Multi-Modal Logistics Hubs & Industrial Clusters

| Dataset & File Path | Scope / Count | Provider & Primary Source URL | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **Industrial Nodes (NICDC)**<br>`data/logistics_hubs/industrial_nodes.csv` | **21 Smart Industrial Cities & Clusters** | National Industrial Corridor Development Corporation ([nicdc.in](https://nicdc.in)) — DMIC, CBIC, AKIC, VCIC, BMIC | GODL-India | **verified** |
| **PM MITRA Mega Textile Parks**<br>`data/logistics_hubs/pm_mitra_parks.csv` | **7 Mega Textile Parks** | Ministry of Textiles ([texmin.nic.in](https://texmin.nic.in)) | GODL-India | **verified** |
| **Major & Commercial Sea Ports**<br>`data/logistics_hubs/ports.csv` | **22 ports** (12 Major Ports) | IPA roster / public-source compilation; consult per-row URLs | Mixed official references / Wikipedia rows; per-row terms unresolved | **verified** (catalog checks only) |
| **Inland Container Depots (ICD / CFS)**<br>`data/logistics_hubs/icds.csv` | **44 ICDs / CFSs** | CBIC / CONCOR / public-source compilation; city/depot approximations | Mixed official references / Wikipedia rows; per-row terms unresolved | **community** |
| **Land Border ICPs**<br>`data/logistics_hubs/icps.csv` | **19 Integrated Check Posts** | LPAI roster / public-source compilation; consult per-row URLs | Mixed official references / Wikipedia rows; per-row terms unresolved | **verified** (catalog checks only) |
| **Air Cargo Airports**<br>`data/logistics_hubs/air_cargo.csv` | **25 Cargo Terminals** | AAI / operator sites / Wikipedia compilation; consult per-row URLs | Mixed official references / Wikipedia rows; per-row terms unresolved | **verified** (catalog checks only) |
| **Multimodal Logistics Parks**<br>`data/logistics_hubs/mmlps.csv` | **20 MMLPs** | NHLML / PIB / Wikipedia fallback; consult per-row URLs | Mixed official references / Wikipedia rows; per-row terms unresolved | **verified** (catalog checks only) |
| **Inland Waterway Terminals**<br>`data/logistics_hubs/inland_waterway_terminals.csv` | **40 Terminals** (NW-1/2/3/4/5/16) | Inland Waterways Authority of India ([iwai.nic.in](https://iwai.nic.in)) | GODL-India | **verified** |
| **Food Grain Depots**<br>`data/logistics_hubs/fci_depots.csv` | **77 Storage Depots** | FCI reference compilation; city-level coordinates, not a complete official depot register | GODL-India per catalog; per-row terms require review | **community** |
| **Cold Chain & CA Storages**<br>`data/logistics_hubs/cold_chain_storages.csv` | **15 Major Cold Logistics Hubs** | National Centre for Cold-chain Development ([nccd.gov.in](https://nccd.gov.in)) / APEDA | GODL-India | **verified** |
| **APMC e-NAM Mandis**<br>`data/logistics_hubs/enam_mandis.csv` | **16 Major Wholesale Terminal Markets** | Directorate of Marketing & Inspection ([enam.gov.in](https://enam.gov.in)) | GODL-India | **verified** |

---

## 6. Freight Flows & Transport Demand Tables

| Dataset & File Path | Series / Dimensions | Primary Anchor & Lineage | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **Rail Freight Annual Series**<br>`data/freight/rail_freight_annual.csv` | 61 series across 5 FYs (2019-20 to 2023-24) | Validated against Ministry of Railways / PIB official anchor of **1,591.0 MT** in FY24 | GODL-India | **verified** |
| **Port Throughput Annual Series**<br>`data/freight/port_throughput_annual.csv` | 65 series across 5 FYs (2019-20 to 2023-24) | Validated against Indian Ports Association official anchor of **819.0 MT** in FY24 (Paradip Port #1 at **145.38 MT**) | GODL-India | **verified** |
| **Road Network Length Indicators**<br>`data/freight/road_indicators_annual.csv` | 11 series across 5 FYs | Ministry of Road Transport and Highways (MoRTH) Basic Road Statistics | GODL-India | **verified** |

---

## 7. Demographics & Post-2011 Allocations

| Dataset & File Path | Scope / Count | Methodology & Validation | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **Census 2011 District Indicators**<br>`data/demographic/census2011_district_key_indicators.csv` | 640 districts | Total population **1,210,854,977** exactly matching official Census Primary Census Abstract (PCA) | GODL-India | **verified** |
| **781-District Population Allocation**<br>`data/demographic/district_population_estimates.csv` | **781 current districts** | 50/50 area & village count geometric overlay in `EPSG:7755`; exactly preserves Census 2011 counts for 568 unchanged districts (**1,210,846,210** population conserved) | Derived / GODL-India | **verified** |

---

## 8. Analysis Outputs & Shortest-Path Matrices

| Dataset & File Path | Scope / Resolution | Methodology & Metrics | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **Village Dual-Distance Tables**<br>`data/analysis/*_village_access.csv` | **578,345 settlements** (36 states/UTs) | Exact projected straight-line Euclidean distance in `EPSG:7755` (`sjoin_nearest`) to all infrastructure and hub targets | Derived in repo | **verified** |
| **State District Summary Rollups**<br>`data/analysis/*_district_access_summary.csv` | 36 state tables | Population-weighted catchment shares (% within 5km, 10km, 25km) and median distances | Derived in repo | **verified** |
| **National Composite Accessibility**<br>`data/analysis/india_district_access_summary.csv` | **817 rows** (781 districts + 36 state pop-weighted rows) | Unified all-India demographic-weighted accessibility indicators | Derived in repo | **verified** |
| **Highway Travel-Time Summary**<br>`data/analysis/nh_district_travel_time_summary.csv` | **781 districts** | Dijkstra shortest-path drive times (hours/min) and road distances (km) on the 289k-node highway graph | Derived in repo | **verified** |
| **District Major Port Matrix**<br>`data/analysis/nh_district_port_matrix.csv` | **781 districts $\times$ 12 major ports** | Complete origin-to-port road distance and drive time catchment matrix | Derived in repo | **verified** |
| **Multi-Modal Freight Cost Model**<br>`data/analysis/district_freight_modal_split.csv` | **781 districts** | End-to-end freight cost comparison (INR/tonne) across Road Trucking, Conventional Rail, and DFC Heavy-Haul Rail (mean 26.9% modal-shift savings) | Derived in repo | **verified** |
| **Port Hinterland Gravity Model**<br>`data/analysis/district_port_hinterland_catchment.csv` | **781 districts** | Huff / Reilly Gravity Model market share capture P(ij) and hinterland contestability across 12 Major Ports | Derived in repo | **verified** |
| **Major Ports & MMLP Isochrones**<br>`data/analysis/*_isochrones.geojson` | **108 polygons** (48 port + 60 MMLP) | 1h, 2h, 4h, 8h vector drive-time catchment polygons generated via Dijkstra network traversal | Derived in repo | **verified** |

---

## 9. Attribution & Citations

- **Administrative Boundaries**: "Administrative boundaries by Survey of India (ABDB Lineage, 2024–25 ORGI Harmonization) and DataMeet Community (CC BY 4.0)"
- **OSM Roads, Toll Booths & OSM Rail Extracts**: "© OpenStreetMap contributors (ODbL)"
- **Railway Station Base**: "DataMeet railways, 2016 (Indian Railways/erail lineage); later classification enrichment from railway-stations-classification.pages.dev". The station base is not an OSM extract and its unclear license remains unresolved; attribution alone does not grant redistribution permission.
- **Logistics Hubs**: Cite each record's publisher and source URL; the catalog records mixed official references and Wikipedia lineage. Attribution is not a blanket GODL-India grant or proof of redistribution rights.
- **Official Demographic and Statistical References**: Cite the applicable ORGI / Census of India, MoRTH, Indian Railways, IPA or other publisher and its stated terms separately from compiled hub records.


