# Data Standards

All datasets in this repository follow these conventions.

## Geometry & CRS

- Canonical CRS: **EPSG:4326 (WGS 84)**.
- Analysis uses EPSG:7755 (India NSF LCC) for metric work; committed outputs are
  in EPSG:4326.
- Formats: GeoJSON, CSV, GeoPackage (sub-districts) and GeoParquet twins of the
  large layers.

## Join keys

- **Survey of India ABDB codes** carried by the boundary layers are the
  canonical identifiers:
  - `state` — state/UT name (the current state layer carries no state code)
  - `district_code` — Survey of India ABDB district code. It equals the LGD
    (Local Government Directory, lgdirectory.gov.in) and Census 2011 code for
    districts that existed in 2011 but uses a separate numbering for about 140
    newer districts; a dedicated LGD code column is planned. Join on
    `(state, district_code)`; the three null-code polygons (Delhi Nazul, Mirpur,
    Muzaffarabad) join on `(state, district)`.
  - `village_code` — the Survey of India village code (LGD) where available
- Village and settlement rows are assigned to the district polygon that contains
  them (within their own state). `district` / `district_code` hold that polygon's
  values; the village source's own LGD district name and code are kept in
  `source_district` / `source_district_code`.
- Census 2011 uses its own state/district codes; join Census tables to the 2011
  district layer (`india_districts.geojson`) on `district_key`.
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

Station categories per Indian Railways: NSG1–NSG6 and suburban NSG1P–NSG5P
(`data/rail/station_categories.csv`). Freight terminals are a separate table
(`data/rail/freight_terminals.csv`).

## Hub types

`hub_type` ∈ {major_port, minor_port, icd, cfs, icp, air_cargo, mmlp,
iw_terminal, foodgodown}. Seven hub files share the columns `name, hub_type,
state, city, latitude, longitude, operator, capacity_notes, source_url`; the
cold-chain, e-NAM, industrial-node and PM MITRA files have their own columns.

## GeoParquet & Hybrid Storage Architecture

To provide both human-readable inspectability and high-throughput analytical query speeds, GISIndia4Logistics uses a **dual-tier hybrid pattern**:

1. **Human & Web Tier (`.geojson`, `.csv`)**:
   - Small administrative boundaries, points of interest, and demo outputs are stored in standard GeoJSON and CSV for direct browser/Leaflet rendering, `jq`/`curl` inspectability, and Git line diffs.
2. **Analytical & Big Data Tier (`.parquet`)**:
   - Heavy spatial datasets (142k National Highway segments, 586k village polygons, 6.6k subdistricts) are exported to **OGC GeoParquet** with Snappy compression using `scripts/clean/export_geoparquet.py`.
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
provider, access (committed|script), path, fetch_script, quality_status,
quality_notes`.

`quality_status` values:
- `verified`: checked against an official total or reference by the audit.
- `community`: from a community source; plausible but not officially verified.
- `sample`: a partial extract or schematic, for demonstration.
- `unverified`: compiled by hand and known or suspected to be inconsistent; re-source before use.
