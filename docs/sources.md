# Data Sources

Per-source documentation: what we use, where it comes from, its license, and
its quality status. Quality statuses:

- **verified** — passes automated checks in this repo (counts/totals reconcile
  with official figures, geometries valid, coordinates in India bbox, no dup keys)
- **community** — maintained open data without official guarantees (OSM, DataMeet);
  best available open option, use with attribution
- **sample** — illustrative snapshot committed for demo purposes

## Quality policy

1. Prefer official sources; fall back to curated community data only when the
   official source is not practically accessible (API keys, no bulk export,
   restrictive license).
2. Every committed dataset records validation results here and in `catalog.yaml`.
3. Data with unclear licensing is documented, not silently redistributed.

## Administrative boundaries

| Source | URL | License | Use |
|---|---|---|---|
| Survey of India ABDB (state/district/sub-district) | surveyofindia.gov.in — ISO 19115 metadata in `docs/metadata/abdb/` | copyright per metadata; governed by Geospatial Guidelines 2021 (good-faith posture in legal_compliance.md) | **Current** states (36) / districts (780) / sub-districts (6,639), LGD-coded — committed (PAN INDIA archive, provider confirmed: NGDR & UGID, SoI; 1:50k DTDB lineage, ORGI-harmonized 2024–25, published 2026-05-06, RMSE ±12.5 m) |
| DataMeet maps | github.com/datameet/maps | CC BY 4.0 | States + Census-2011 districts (640) used for census joins — committed |
| Survey of India villages | surveyofindia.gov.in/pages/village-boundary-data-base-of-entire-india | same ABDB constraints | **Official village boundaries** (LGD-coded, 27 states/UTs) — committed |
| ISRO Bhuvan | bhuvan.nrsc.gov.in | Free registration; check terms per layer | Alternative official boundaries — manual |
| LGD | lgdirectory.gov.in | Government open data | Canonical state/district codes |

**PAN INDIA LGD dataset notes**: processed by `scripts/clean/process_panindia_boundaries.py`.
Data-quality fixes applied: 4 inter-state "DISPUTED" slivers dropped from the
states layer (28 from district/subdistrict layers); legacy-font mojibake in
names fixed deterministically (`<`→a, `>`→A, `#`→u — e.g. `BR>HMAUR`→
BRAHMAUR, `Bengal#ru`→BENGALURU); Mirpur and Muzaffarabad (PoK) kept with
null LGD codes; 89 subdistrict rows carry null codes in the source.

**SoI village database notes**: direct per-state zips (no login), shapefile in
the national LCC projection, attributes include LGD codes for state, district,
sub-district and village plus village name and Rural/Urban category. Reprojects
cleanly to EPSG:4326 (`fetch_village_boundaries_soi.py`). Published for 27
states/UTs — Assam, Arunachal Pradesh, Himachal Pradesh, Jammu & Kashmir,
Ladakh, Manipur, Meghalaya, Mizoram and Nagaland are absent. Redistribution
posture (good-faith, with attribution, remove-on-request) is documented in
`docs/legal_compliance.md`. Files in `data/administrative/villages/` are
standardized derivatives: simplified ~50 m, rounded coordinates, LGD schema;
state files over the 10 MB commit policy are regenerated via the script
(use `--district` for per-district extracts of large states).

Caveat: committed district boundaries are Census 2011 vintage (640 districts).
India now has ~780 districts; new districts since 2011 must come from Bhuvan/
GADM or state portals. Telangana is inside Andhra Pradesh in this vintage; the
2011 demographic table matches it.

## Roads

| Source | URL | License | Use |
|---|---|---|---|
| OpenStreetMap | openstreetmap.org / overpass-api.de | ODbL | Road network by class — fetch script |
| Geofabrik India extract | download.geofabrik.de/asia/india.html | ODbL | Nation-wide PBF for heavy use |
| PMGSY (OMAS/Samarth) | omerms.nic.in | GODL-India | Official rural roads; no clean bulk export — documented pointer |
| NHAI / MoRTH | nhai.gov.in, morth.nic.in | Government open data | NH network reports — pointer |

Road class mapping (OSM → Indian classification) in `docs/data_standards.md`.

## Rail

| Source | URL | License | Use |
|---|---|---|---|
| DataMeet railways | github.com/datameet/railways | none declared (reference use) | Stations table — committed (2016) |
| data.gov.in | data.gov.in (Indian Railways resources) | GODL-India; API key needed | Official station/category lists — pointer |
| Indian Railways / Railway Board | indianrailways.gov.in | Government open data | Freight statistics reports — pointer |
| OSM | openstreetmap.org | ODbL | Rail line geometry — fetch script |

Known gaps in the committed stations table: zone ~45% filled, category
(NSG1–7) missing, vintage 2016. Joining data.gov.in's station-category list is
the recommended upgrade path.

## Logistics hubs

| Source | URL | License | Use |
|---|---|---|---|
| Indian Ports Association | ipa.nic.in | GODL-India | 12 major ports roster |
| Land Ports Authority of India | lpai.gov.in | GODL-India | ICP roster (site blocks scraping; roster cross-checked) |
| CBIC notified ICD/CFS list | cbic.gov.in | GODL-India | ICD/CFS roster |
| CONCOR | concorindia.co.in | site terms | ICD network details |
| AAI | aai.aero | GODL-India | Air cargo terminals |
| Wikipedia/Wikidata | wikipedia.org | CC BY-SA 4.0 | Coordinates fallback (per-row source_url) |

Hub CSVs carry a `source_url` per row for attribution. ICD/ICP coordinates are
city/depot approximations where operators don't publish exact locations —
flagged in `capacity_notes`; spot-check before publication-grade use.

## Demographics

| Source | URL | License | Use |
|---|---|---|---|
| Census of India 2011 PCA | censusindia.gov.in | GODL-India | Official tables |
| Community digitization | github.com/RajaBhavesh/India-Census-2011-Analysis-Using-Python | GODL-India (data) | District CSV — committed, validated against official totals |

Validation performed (`scripts/fetch/fetch_demographics.py`): 640 districts;
total population 1,210,854,977 exactly; male+female = total; state totals
match (UP 199,812,341; Maharashtra 112,374,333).

## Attribution

- "India boundaries by DataMeet India community (CC BY 4.0)"
- "© OpenStreetMap contributors (ODbL)"
- Census data: Government of India, Census 2011 (GODL-India)
