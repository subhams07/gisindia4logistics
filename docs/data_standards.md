# Data Standards

All datasets in this repository follow these conventions.

## Geometry & CRS

- Canonical CRS: **EPSG:4326 (WGS 84)**.
- Derived/analysis outputs may additionally use EPSG:7755 (India NSF LCC) or
  relevant UTM zones, named with a `_ projected` suffix.
- Formats: GeoJSON (committed small vector), CSV (attribute tables),
  GeoPackage (multi-layer analysis outputs), Parquet (large tabular).

## Join keys

- **LGD codes** (Local Government Directory, lgdirectory.gov.in) are the
  canonical identifiers:
  - `state_code` — 2-digit LGD state code
  - `district_code` — LGD district code (unique nationally)
  - `lgd_village_code` — 6-digit village code where available
- Census 2011 uses its own state/district codes; a crosswalk table is provided
  in `data/administrative/census2011_lgd_crosswalk.csv`.
- Railway stations join on `station_code` (Indian Railways alpha codes).

## Attribute naming

- snake_case, ASCII, no spaces.
- Names of places keep official spellings (e.g., `Puducherry`).

## Road classification

OSM `highway` tags mapped to Indian road-type classes:

| OSM highway | Indian class |
|---|---|
| motorway / trunk | National Highway (NH) |
| primary | State Highway (SH) |
| secondary | Major District Road (MDR) |
| tertiary | Other District Road (ODR) |
| unclassified / residential | Village road / street |

Ref-tagged roads (`ref=NH…`, `ref=SH…`) take precedence over the generic class.

## Rail categories

Station categories per Indian Railways (NSG1–NSG7, formerly A1/A/B/C/D/E/F),
plus a boolean `freight_terminal` flag for goods terminals/sidings where known.

## Hub types

`hub_type` ∈ {major_port, minor_port, icd, cfs, icp, air_cargo_terminal,
logistics_park, warehouse_cluster, dry_port, mmlp}

## File size policy

- Committed files < 10 MB (prefer simplified geometry for boundaries —
  tolerance 0.001° ≈ 100 m — plus full-resolution via fetch script).
- Larger files via fetch scripts or Git LFS (`.gitattributes` patterns).

## Metadata

Every dataset has an entry in `catalog.yaml`:
`name, category, description, source_url, license, vintage, resolution,
provider, access (committed|script), path, fetch_script`.
