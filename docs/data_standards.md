# Data Standards

All datasets in this repository follow these conventions.

## Geometry & CRS

- Canonical CRS: **EPSG:4326 (WGS 84)**.
- Derived/analysis outputs may additionally use EPSG:7755 (India NSF LCC) or
  relevant UTM zones, named with a `_ projected` suffix.
- EPSG:7755 is conformal, not equidistant or equal-area (scale 0.98-1.03 over
  India). Report distances and lengths as WGS84 geodesics (`pyproj.Geod`;
  `scripts/clean/graph_builder.geodesic_m` for EPSG:7755 points) and areas in
  an equal-area CRS such as ESRI:102028; use EPSG:7755 only for indexing,
  snapping and geometry construction.
- Formats: GeoJSON (committed small vector), CSV (attribute tables),
  GeoPackage (multi-layer analysis outputs), Parquet (large tabular).

## Join keys

- **LGD codes** (Local Government Directory, lgdirectory.gov.in) are the
  canonical identifiers:
  - `state_code` — 2-digit LGD state code
  - `district_code` — LGD district code (unique nationally), from the official
    directory snapshot `data/administrative/lgd_districts.csv`
    (`scripts/fetch/fetch_lgd_directory.py`). The SoI ABDB source numbers
    districts differently (Census 2011 codes for older districts, its own scheme
    for newer ones); the boundary layers keep that value as `abdb_district_code`,
    and the district layer also carries `census2011_code`. The mapping is
    `data/administrative/district_code_crosswalk.csv`
    (`scripts/clean/apply_lgd_codes.py`).
  - `village_code` — the Survey of India village code (LGD) where available
  - In the village files every code column is text (as the source pads them,
    e.g. `"055"`, `"05614"`), and `state` is the LGD state name on every row
    (`scripts/clean/normalize_village_properties.py`; the audit enforces both).
    A value that is not available is null, never the text `NA` or `Null`.
    Read the village access CSVs with
    `scripts/analyze/village_access_io.read_village_access_csv`, or with
    `dtype=str`, so zero-padded codes keep their leading zeros.
- Census 2011 uses its own state/district codes; a crosswalk table is provided
  in `data/administrative/census2011_lgd_crosswalk.csv`.
- Village rows are assigned to the district polygon that contains them
  (within their own state). `district` / `district_code` hold that polygon's
  values; the village source's own district name and code are kept in
  `source_district` / `source_district_code`.
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

## Hub file schemas

The point files come in two families:

- **Transport hubs** (`data/logistics_hubs/` ports, icps, icds, air_cargo,
  mmlps, inland_waterway_terminals, fci_depots): exactly
  `hub_code, name, hub_type, state, city, latitude, longitude, coord_precision, operator, capacity_notes, source_url`,
  with `unlocode` after `hub_code` in ports.csv. The audit enforces these
  column lists. `hub_type` takes the values listed under "Hub types". A row
  without coordinates says "coordinate pending" in `capacity_notes`.
  - `hub_code` is assigned by the repository (`PRT`, `ICP`, `ICD`, `AIR`,
    `MMLP`, `IWT`, `FCI` + three digits), never reused or renumbered; it is
    not an official code. `unlocode` holds the UN/LOCODE of the 12 major
    ports only (the others are not yet checked against the UNECE list).
  - `coord_precision` says what the point is: `site` (confirmed against an
    OSM port/harbour or aerodrome feature within 3 km;
    `docs/review/hub_site_check.csv`), `station`, `village` or `town` (the
    OSM node the row was re-located to), `city` (approximate: a city centre,
    or another facility's point), `unverified`, or `pending` (no
    coordinates). Set by `scripts/clean/add_hub_codes_precision.py`.
- **Economic and agri nodes** (industrial_nodes, pm_mitra_parks,
  cold_chain_storages, enam_mandis): `name, state, district, city, latitude,
  longitude, ..., source_url`, with type-specific columns in between
  (`corridor`/`node_type`, `area_acres`, `status`, `capacity_mt`,
  `commodities`, `market_type`, ...). They carry the district name, not a code.

Gati Shakti cargo terminals (`data/rail/freight_terminals.csv`) have their own
schema: `name, terminal_type, zone, division, state, city, latitude, longitude,
capacity_notes, source_url, matched_station_code`.

Every point lies in its own state (the audit allows 5 km on the coast).

## GeoParquet & Hybrid Storage Architecture

To provide both human-readable inspectability and high-throughput analytical query speeds, GISIndia4Logistics uses a **dual-tier hybrid pattern**:

1. **Human & Web Tier (`.geojson`, `.csv`)**:
   - Small administrative boundaries, points of interest, and demo outputs are stored in standard GeoJSON and CSV for direct browser/Leaflet rendering, `jq`/`curl` inspectability, and Git line diffs.
2. **Analytical & Big Data Tier (`.parquet`)**:
   - Heavy spatial datasets (142k National Highway segments, 658k village polygons, 6.6k subdistricts) are exported to **OGC GeoParquet** with Snappy compression using `scripts/clean/export_geoparquet.py`.
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
