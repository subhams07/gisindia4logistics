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

## GeoParquet & Hybrid Storage Architecture

To provide both human-readable inspectability and high-throughput analytical query speeds, GIS4Logistics uses a **dual-tier hybrid pattern**:

1. **Human & Web Tier (`.geojson`, `.csv`)**:
   - Small administrative boundaries, points of interest, and demo outputs are stored in standard GeoJSON and CSV for direct browser/Leaflet rendering, `jq`/`curl` inspectability, and Git line diffs.
2. **Analytical & Big Data Tier (`.parquet`)**:
   - Heavy spatial datasets (142k National Highway segments, 578k village polygons, 6.6k subdistricts) are exported to **OGC GeoParquet** with Snappy compression using `scripts/clean/export_geoparquet.py`.
   - GeoParquet delivers **65%–80% smaller file sizes** and **10× faster load speeds** with column and bounding box predicate pushdown.

### Python GeoParquet Usage:
```python
import geopandas as gpd

# Blazing-fast reading of national highway network
nh_gdf = gpd.read_parquet("data/roads/india_nh_network.parquet")

# Direct reading of state village boundaries
villages_gdf = gpd.read_parquet("data/administrative/villages_parquet/haryana_soi_villages.parquet")
```

## Metadata

Every dataset has an entry in `catalog.yaml`:
`name, category, description, source_url, license, vintage, resolution,
provider, access (committed|script), path, fetch_script`.
