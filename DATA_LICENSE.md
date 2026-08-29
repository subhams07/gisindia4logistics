# Data licensing and redistribution

The MIT license in [`LICENSE`](LICENSE) applies to repository **code only**. It does not grant rights to the datasets under `data/`, source downloads, map tiles, or third-party content.

Every dataset retains the terms of its publisher or upstream database. The authoritative project inventory is [`catalog.yaml`](catalog.yaml), with supporting lineage in [`docs/sources.md`](docs/sources.md) and policy discussion in [`docs/legal_compliance.md`](docs/legal_compliance.md).

## Main license classes

| Source class | Typical terms | Required treatment |
|---|---|---|
| Government open-data sources | GODL-India or stated portal terms | Preserve publisher, source URL, vintage, and license attribution. |
| OpenStreetMap-derived databases | ODbL | Attribute © OpenStreetMap contributors and comply with ODbL obligations for derived databases. |
| DataMeet community boundaries | CC BY 4.0 | Preserve attribution and license notice. |
| Wikidata/Wikipedia-derived records | CC BY-SA or CC0 depending on source | Preserve the per-record source and applicable terms. |
| Survey of India products | Copyright; no blanket open-data license asserted here | See the unresolved redistribution posture below. |
| Derived analytical outputs | Depends on all inputs | Cite this project and every material upstream dataset; upstream database obligations may continue to apply. |

## Survey of India redistribution posture

Survey of India ABDB and village-boundary products are publicly accessible but their metadata states copyright and does not provide an explicit blanket redistribution license. The repository currently documents a good-faith interpretation under the 2021 Geospatial Guidelines; that interpretation is **not equivalent to written permission**.

Before a formal public data release, DOI deposit, or broad redistribution campaign, the maintainer must record one of the following decisions:

1. written permission or authoritative terms permitting redistribution;
2. a documented legal review supporting the chosen distribution model;
3. replacement with clearly redistributable data; or
4. removal of affected files and provision of reproducible user-side fetch/processing instructions.

Until then, users must independently assess whether their intended redistribution is permitted.

## No single blanket data license

Do not describe the entire `data/` directory as MIT, GODL-India, ODbL, or public domain. Mixed-source outputs require dataset-level review.

## Attribution in outputs

Generated maps, exports, notebooks, API clients, and downstream products should include:

- dataset publisher and source;
- data vintage;
- applicable license;
- © OpenStreetMap contributors where OSM data is used;
- the project boundary disclaimer where administrative boundaries are displayed.

This document is a project compliance summary, not legal advice.
