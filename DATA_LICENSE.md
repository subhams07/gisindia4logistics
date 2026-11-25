# Data licensing and redistribution

The MIT license in [`LICENSE`](LICENSE) applies to repository **code only**. It does not grant rights to the datasets under `data/`, source downloads, map tiles, or third-party content.

Every dataset retains the terms of its publisher or upstream database. The authoritative project inventory is [`catalog.yaml`](catalog.yaml), with supporting lineage in [`docs/sources.md`](docs/sources.md) and policy discussion in [`docs/legal_compliance.md`](docs/legal_compliance.md).

## Main license classes

| Source class | Typical terms | Required treatment |
|---|---|---|
| Government open-data sources | GODL-India or stated portal terms | Preserve publisher, source URL, vintage, and license attribution. |
| OpenStreetMap-derived databases | ODbL | Attribute © OpenStreetMap contributors and comply with ODbL obligations for derived databases. |
| DataMeet community boundaries | CC BY 4.0 | Preserve attribution and license notice. |
| DataMeet railway station base and community station classification | Unclear per catalog; not covered by the boundary license | Record the specific source and unresolved redistribution terms; do not infer permission from attribution or public access. |
| Wikidata/Wikipedia-derived records | CC BY-SA or CC0 depending on source | Preserve the per-record source and applicable terms. |
| Survey of India products | Public government data (attributed; indicative boundaries) | Attribute Survey of India, comply with the 2021 DST Geospatial Guidelines, carry the indicative-boundary disclaimer, and remove affected files on official request. |
| Derived analytical outputs | Depends on all inputs | Cite this project and every material upstream dataset; upstream database obligations may continue to apply. |

## Survey of India distribution posture

Per the maintainer ruling (2026-11-26), all Survey of India administrative and village boundary products in this repository (ABDB states/districts/sub-districts, the Survey of India village boundary portal exports, and the National Water Data Portal SoI files) are treated as **public data available for distribution**. They remain unrestricted civilian layers published openly on official Government of India portals under the 2021 DST Geospatial Guidelines.

Required conditions on any redistribution or downstream service using these boundaries:

1. Attribute Survey of India (and National Water Data Portal / LGD where applicable);
2. Retain the indicative-boundary disclaimer on all maps and derived outputs;
3. Promptly remove or restrict any dataset if Survey of India objects.

## No single blanket data license

The committed railway station base has a 2016 DataMeet vintage, with later community classification enrichment. Neither the boundary dataset's CC BY 4.0 terms nor the ODbL terms for OSM rail-line extracts establish rights to redistribute that separate station database. The catalog records no station license file and unclear classification terms. Current operational status and legal redistribution permission remain unverified.

Do not describe the entire `data/` directory as MIT, GODL-India, ODbL, or public domain. Mixed-source outputs require dataset-level review.

## Attribution in outputs

Generated maps, exports, notebooks, API clients, and downstream products should include:

- dataset publisher and source;
- data vintage;
- applicable license;
- © OpenStreetMap contributors where OSM data is used;
- the project boundary disclaimer where administrative boundaries are displayed.

This document is a project compliance summary, not legal advice.
