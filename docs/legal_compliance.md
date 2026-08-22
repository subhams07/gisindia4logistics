# Legal & Policy Compliance (India Geospatial)

This repository is published from India by Indian persons/entities under the
self-certification regime of the **Guidelines for Acquiring and Producing
Geospatial Data and Geospatial Data Services including Maps** (Department of
Science & Technology, 15 February 2021).

**This document is a good-faith compliance summary, not legal advice.**
For authoritative texts see the links at the end.

## Why this repo is in the unrestricted category

| Guideline rule | This repo's status |
|---|---|
| Civilian geospatial data freely producable/publishable by Indian entities | All datasets are civilian: boundaries, roads, rail, ports, ICDs/ICPs, airports, census |
| Accuracy thresholds — regulated if finer than ~1 m horizontal / 3 m vertical (DEM/terrain), 1 milli-gal gravity | No DEM/terrain/gravity datasets. Committed geometry is simplified to ~50–100 m; coordinate precision ~1 m. Fetch scripts return OSM road/rail geometry of similar class |
| Negative List of Attributes (defence/security installations) must not be marked | No defence/military features collected or accepted (enforced via CONTRIBUTING checklist) |
| Negative List of Areas — ground survey/verification needs SoI permission | No ground truthing performed; repo only aggregates openly published data |
| Foreign entities — license only for high-accuracy/restricted data | All data here is unrestricted class; publication on global platforms is permitted |

## Boundary depiction (important)

The boundaries in this repo (DataMeet-derived) are **indicative, not
authoritative**. They are not Survey of India products and must not be used as
an official depiction of India's external or disputed boundaries
(LoC, Arunachal Pradesh, Aksai Chin). Authoritative depiction is the
prerogative of the Survey of India. Maps and notebooks in this repo carry this
disclaimer, and derived works should too.

## Survey of India village boundaries — specific posture

The SoI "Village Boundary Data Base of Entire India"
(surveyofindia.gov.in/pages/village-boundary-data-base-of-entire-india)
publishes free, login-free per-state downloads of official LGD-coded village
polygons for 27 states/UTs. The page states no open-data license (site footer:
"all rights reserved"). Accordingly:

- `scripts/fetch/fetch_village_boundaries_soi.py` fetches and standardizes to
  local `data/administrative/villages/` for **your own use** — this is what the
  Guidelines permit and what SoI's free publication intends.
- These files are **gitignored and never committed**; redistribution (e.g., in
  this repo or a public mirror) only after written terms from SoI.
- Nine border/NE states are not published by SoI at village level; treat that
  gap as deliberate and do not source those boundaries from elsewhere without
  checking terms.

## License obligations per dataset

Maintained per dataset in `catalog.yaml` (`license` field) and
`docs/sources.md`. Key rules when redistributing:

- **OSM-derived files** (e.g., `data/roads/pune_sample_roads.geojson`,
  anything produced by `fetch_roads.py` / `fetch_rail.py`): ODbL —
  attribute "© OpenStreetMap contributors" and keep derived databases
  under ODbL.
- **Census data**: Government Open Data License — India (GODL-India);
  attribute "Government of India, Census 2011".
- **DataMeet boundaries**: CC BY 4.0 — attribute "India boundaries by DataMeet
  India community".
- **Hub coordinates sourced from Wikipedia/Wikidata**: CC BY-SA 4.0
  (per-row `source_url` attribution is included).

## Rules for future contributions (enforced via CONTRIBUTING checklist)

1. No defence, military, intelligence, or strategic-installation features —
   cross-check additions against the DST negative list of sensitive attributes.
2. No DEM/terrain/gravity data finer than the 2021 thresholds without
   documented SoI clearance.
3. No ground-truthing/survey inside notified Negative List of Areas.
4. Every dataset documents source, license, and vintage; ambiguous-license
   data is not committed.
5. Maps must carry the boundary-disclaimer footer.

## References

- Guidelines (full text, SoI): https://onlinemaps.surveyofindia.gov.in/GeospatialGuidelines.aspx
- PIB press release (Feb 2021): https://www.pib.gov.in/PressReleasePage.aspx?PRID=1814067
- DST — List of Features/Installations and their sensitive attributes:
  https://dst.gov.in/news/list-featuresinstallations-and-their-sensitive-attributes-reference-guidelines-acquiring-and
- DST Geospatial Division: https://dst.gov.in
