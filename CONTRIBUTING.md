# Contributing to GIS4Logistics — India

Thanks for helping build an open GIS data collection for Indian logistics!

## Ways to contribute

- **New data sources** — point us to open, redistributable datasets (government
  portals, OSM, research datasets).
- **Corrections** — bad coordinates, outdated hub lists, boundary errors.
- **Fetch scripts** — new sources that are too large to commit directly.
- **Docs** — license clarifications, data quality notes.

## Data standards (required for new datasets)

1. **CRS**: EPSG:4326 (WGS 84). Projected outputs may additionally be provided
   in EPSG:7755 or UTM zones, but 4326 is the canonical format.
2. **Join keys**: use LGD (Local Government Directory) state/district codes
   where applicable; attribute names snake_case.
3. **Metadata**: every dataset needs a `catalog.yaml` entry with: name,
   category, source_url, license, vintage, resolution, and how it is obtained.
4. **Size**: committed files < 10 MB. Anything larger goes through a fetch
   script (or Git LFS if derived and essential).
5. **License**: only commit data that is openly licensed or redistributable.
   Ambiguous-license sources are documented in `docs/sources.md` with a fetch
   script, not committed.

## Adding a dataset — checklist

- [ ] Source URL and license verified and recorded in `catalog.yaml` + `docs/sources.md`
- [ ] Data standardized per `docs/data_standards.md`
- [ ] Fetch script added under `scripts/fetch/` (if not committed directly)
- [ ] README catalog table updated

## Reporting data errors

Open an issue with: dataset name, affected feature(s), expected vs actual value,
and a source supporting the correction.
