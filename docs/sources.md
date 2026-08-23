# Data Sources & Lineage

Comprehensive, per-source documentation of datasets, provenance, licensing, lineage, and validation gates across all layers of the **GIS4Logistics India** platform.

Quality status definitions:
- **verified** — passes automated CI validation gates (exact counts vs official registers, zero invalid geometries, coordinates bounded strictly within India CRS `EPSG:7755` / `EPSG:4326`, unique join keys).
- **community** — curated open data without direct ministerial API guarantees (OpenStreetMap, DataMeet); best available open spatial geometries, redistributed with attribution under ODbL / CC BY 4.0.
- **sample** — illustrative snapshot committed for testing and demonstration.

---

## 1. Administrative Boundaries (5 Hierarchy Tiers)

| Hierarchy Level | Dataset & File Path | Count / Scope | Provider & Lineage | License & Governance | Quality Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Level 0 (National)** | `data/administrative/india_states_lgd.geojson` | 1 national boundary | Derived via spatial dissolve of SoI ABDB states | Copyright Survey of India; DST Guidelines 2021 | **verified** |
| **Level 1 (States/UTs)** | `data/administrative/india_states_lgd.geojson` | **36 States & UTs** | Survey of India ABDB product (NGDR & UGID; 1:50k DTDB, ORGI 2024–25 harmonized) | Copyright SoI; good-faith redistribution posture (see `docs/legal_compliance.md`) | **verified** |
| **Level 1 (2011 Vintage)**| `data/administrative/india_states.geojson` | 36 States & UTs | DataMeet maps archive (harmonized to 2011 Census tables) | CC BY 4.0 | **verified** |
| **Level 2 (Districts - Current)** | `data/administrative/india_districts_lgd.geojson` | **781 LGD Districts** | SoI ABDB (780 official + Malegaon LGD 598 derived from 4 subdistricts; disputed slivers dropped) | Copyright SoI; DST Guidelines 2021 | **verified** |
| **Level 2 (Districts - 2011)** | `data/administrative/india_districts.geojson` | **640 Districts** | DataMeet maps (Census 2011 district layout, J&K placeholder dropped) | CC BY 4.0 | **verified** |
| **Level 3 (Sub-districts / Talukas)** | `data/administrative/india_subdistricts_lgd.gpkg` | **6,636 Sub-districts** | SoI ABDB product (mojibake font fixed deterministically, 3 duplicate rows dropped) | Copyright SoI; DST Guidelines 2021 | **verified** |
| **Level 3 (Taluka Sample)** | `data/administrative/pune_taluka.geojson` | 14 talukas | OpenStreetMap admin relations (majority-containment stitched polygons) | ODbL | **verified** |
| **Level 4 (Villages - Polygons)** | `data/administrative/villages/*_soi_villages.geojson` | **543,391 Villages** (27 states/UTs) | Survey of India Village Boundary Database (national LCC reprojected to EPSG:4326) | Copyright SoI; good-faith redistribution | **verified** |
| **Level 4 (Habitations - Points)** | `data/administrative/villages/*_habitations.geojson` | **34,954 Settlements** (9 border states) | OpenStreetMap settlement place nodes (`place=village\|hamlet\|town`) joined with 781 LGD districts | ODbL | **verified** |
| **Level 4 (National Combined)** | Total National Settlements | **578,345 Villages & Habitations** | 100% settlement coverage across all 36 Indian States & UTs | Mixed (SoI + ODbL) | **verified** |

---

## 2. Road Network & Toll Infrastructure

| Dataset & File Path | Scope / Count | Provider & Lineage | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **National Highway Network**<br>`data/roads/india_nh_network.geojson` | **141,990 segments** (~634 NH routes) | OpenStreetMap Overpass extraction (`highway=motorway\|trunk\|primary` with `ref~NH`); simplified 0.0005° | ODbL | **verified** |
| **National Toll Plazas**<br>`data/roads/toll_plazas.csv` | **1,536 clustered Toll Plazas** (1,100 NH, 101 Expressway, 335 SH/Bridge) | OSM `barrier=toll_booth` clustered via 150m DBSCAN; snapped to NH network for route fee modeling under the **NHAI Toll Information System (TIS)** conceptual model | ODbL / Public TIS structure | **verified** |
| **District Road Network (Sample)**<br>`data/roads/pune_sample_roads.geojson` | 19,019 segments (Pune district) | OSM highway classes (NH, SH, MDR, ODR, Residential) classified via `scripts/fetch/fetch_roads.py` | ODbL | **verified** |

---

## 3. Railway Infrastructure & Freight Terminals

| Dataset & File Path | Scope / Count | Provider & Lineage | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **Railway Stations**<br>`data/rail/railway_stations.csv` | **8,697 stations** | DataMeet Railways + Indian Railways operational enrichment; 79.3% zone coverage (6,896 stations) + divisions (5,493 stations) | Reference / Open Community | **verified** |
| **Station Categories (NSG1-6)**<br>`data/rail/station_categories.csv` | **5,938 categorized stations** | Ministry of Railways classification roster (NSG1–NSG6, passenger footfall & earnings) | Public Roster / Reference | **verified** |
| **Freight Terminals (GCT)**<br>`data/rail/freight_terminals.csv` | **84 Gati Shakti Cargo Terminals** | PIB PRID 1910049 annexure (Ministry of Railways); geocoded via stations crosswalk and port fallbacks | GODL-India | **verified** |

---

## 4. Multi-Modal Logistics Hubs

| Dataset & File Path | Scope / Count | Provider & Primary Source URL | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **Major & Commercial Sea Ports**<br>`data/logistics_hubs/ports.csv` | **22 ports** (12 Major Ports) | Indian Ports Association ([ipa.nic.in](https://ipa.nic.in)) | GODL-India | **verified** |
| **Inland Container Depots (ICD / CFS)**<br>`data/logistics_hubs/icds.csv` | **44 ICDs / CFSs** | Central Board of Indirect Taxes and Customs ([cbic.gov.in](https://cbic.gov.in)) + CONCOR | GODL-India | **verified** |
| **Land Border ICPs**<br>`data/logistics_hubs/icps.csv` | **19 Integrated Check Posts** | Land Ports Authority of India ([lpai.gov.in](https://lpai.gov.in)) | GODL-India | **verified** |
| **Air Cargo Airports**<br>`data/logistics_hubs/air_cargo.csv` | **25 Cargo Terminals** | Airports Authority of India ([aai.aero](https://aai.aero)) | GODL-India | **verified** |
| **Multimodal Logistics Parks**<br>`data/logistics_hubs/mmlps.csv` | **20 MMLPs** | National Highways Logistics Management Limited ([nhlml.co.in](https://nhlml.co.in)) | GODL-India | **verified** |
| **Inland Waterway Terminals**<br>`data/logistics_hubs/inland_waterway_terminals.csv` | **40 Terminals** (NW-1/2/3/4/5/16) | Inland Waterways Authority of India ([iwai.nic.in](https://iwai.nic.in)) | GODL-India | **verified** |
| **Food Grain Depots**<br>`data/logistics_hubs/fci_depots.csv` | **77 Storage Depots** | Food Corporation of India ([fci.gov.in](https://fci.gov.in)) | GODL-India | **verified** |

---

## 5. Freight Flows & Transport Demand Tables

| Dataset & File Path | Series / Dimensions | Primary Anchor & Lineage | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **Rail Freight Annual Series**<br>`data/freight/rail_freight_annual.csv` | 61 series across 5 FYs (2019-20 to 2023-24) | Validated against Ministry of Railways / PIB official anchor of **1,591.0 MT** in FY24 | GODL-India | **verified** |
| **Port Throughput Annual Series**<br>`data/freight/port_throughput_annual.csv` | 65 series across 5 FYs (2019-20 to 2023-24) | Validated against Indian Ports Association official anchor of **819.0 MT** in FY24 (Paradip Port #1 at **145.38 MT**) | GODL-India | **verified** |
| **Road Network Length Indicators**<br>`data/freight/road_indicators_annual.csv` | 11 series across 5 FYs | Ministry of Road Transport and Highways (MoRTH) Basic Road Statistics | GODL-India | **verified** |

---

## 6. Demographics & Post-2011 Allocations

| Dataset & File Path | Scope / Count | Methodology & Validation | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **Census 2011 District Indicators**<br>`data/demographic/census2011_district_key_indicators.csv` | 640 districts | Total population **1,210,854,977** exactly matching official Census Primary Census Abstract (PCA) | GODL-India | **verified** |
| **781-District Population Allocation**<br>`data/demographic/district_population_estimates.csv` | **781 current districts** | 50/50 area & village count geometric overlay in `EPSG:7755`; exactly preserves Census 2011 counts for 568 unchanged districts (**1,210,846,210** population conserved) | Derived / GODL-India | **verified** |

---

## 7. Analysis Outputs & Shortest-Path Matrices

| Dataset & File Path | Scope / Resolution | Methodology & Metrics | License | Quality Status |
| :--- | :--- | :--- | :--- | :--- |
| **Village Dual-Distance Tables**<br>`data/analysis/*_village_access.csv` | **578,345 settlements** (36 states/UTs) | Exact projected straight-line Euclidean distance in `EPSG:7755` (`sjoin_nearest`) to all infrastructure and hub targets | Derived in repo | **verified** |
| **State District Summary Rollups**<br>`data/analysis/*_district_access_summary.csv` | 36 state tables | Population-weighted catchment shares (% within 5km, 10km, 25km) and median distances | Derived in repo | **verified** |
| **National Composite Accessibility**<br>`data/analysis/india_district_access_summary.csv` | **817 rows** (781 districts + 36 state pop-weighted rows) | Unified all-India demographic-weighted accessibility indicators | Derived in repo | **verified** |
| **Highway Travel-Time Summary**<br>`data/analysis/nh_district_travel_time_summary.csv` | **781 districts** | Dijkstra shortest-path drive times (hours/min) and road distances (km) on the 289k-node highway graph | Derived in repo | **verified** |
| **District Major Port Matrix**<br>`data/analysis/nh_district_port_matrix.csv` | **781 districts $\times$ 12 major ports** | Complete origin-to-port road distance and drive time catchment matrix | Derived in repo | **verified** |

---

## 8. Attribution & Citations

- **Administrative Boundaries**: "Administrative boundaries by Survey of India (ABDB Lineage, 2024–25 ORGI Harmonization) and DataMeet Community (CC BY 4.0)"
- **Road & Rail Network**: "© OpenStreetMap contributors (ODbL)"
- **Logistics Hubs & Demographics**: "Government of India (GODL-India) — IPA, CBIC, AAI, NHLML, LPAI, IWAI, FCI, MoRTH, Indian Railways, Census of India 2011"

