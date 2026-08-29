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

## Survey of India boundary data — redistribution posture

Covers BOTH the "Village Boundary Data Base of Entire India" page
(surveyofindia.gov.in/pages/village-boundary-data-base-of-entire-india)
and the **Administrative Boundary Data Base (ABDB)** — the state/district/
sub-district layers ("PAN INDIA" archive) are SoI's own ABDB product, as
confirmed by official ISO 19115 metadata (see `docs/metadata/abdb/`):
provider "DIRECTOR, NGDR & UGID, SURVEY OF INDIA", lineage from the 1:50,000
Digital Topographical Database, harmonized with ORGI 2024–2025, published
2026-05-06, horizontal accuracy RMSE ±12.5 m.

**Official constraints (per metadata):** accessConstraints = copyright;
useConstraints = copyright; otherConstraints = "Geospatial Guidelines 2021
to be followed. Any violation of the Guidelines will be dealt under the
applicable laws." No explicit open-data license is granted.

**Current repository posture (maintainer, 2026-08):** the public repository
redistributes standardized derivatives in good faith, based on free,
login-free availability on a government portal, with attribution ("Boundary
data: Survey of India, Government of India — ABDB") embedded in files and
documentation. The metadata nevertheless states "copyright" and no written
blanket redistribution grant is recorded here. This is therefore an
**unresolved release-governance item**, not a claim of explicit permission.
Before a formal public data release, DOI deposit, or broad redistribution
campaign, the maintainer must obtain written confirmation, record a documented
legal review, replace the affected data, or move it to a user-side fetch flow.
If SoI objects, affected files will be removed promptly.

Grounds and mitigations:
- The 2021 Geospatial Data Guidelines (the stated governing constraint)
  encourage wide availability of unrestricted civilian geospatial data; this
  dataset is unrestricted-class (administrative boundaries, no negative-list
  attributes or areas), and the repo follows the Guidelines.
- No circumvention is involved: the same public URLs the scripts use are
  linked from SoI's page without registration or terms-clickthrough.
- Attribution is preserved per file; raw SoI zips/rars are NOT committed —
  only standardized derivatives (reprojected to EPSG:4326, simplified
  ≥50 m, which stays within the source's ±12.5 m RMSE at district+ scales).
- Nine border/NE states are not published by SoI at village level; treat
  that gap as deliberate and do not source those boundaries elsewhere
  without checking terms.

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
- **Survey of India village boundaries**: no explicit license stated;
  redistributed in good faith per the decision above with attribution
  ("Village boundaries: Survey of India, Government of India").

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
