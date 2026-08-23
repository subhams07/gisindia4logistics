# GIS4Logistics — India

[![CI](https://github.com/subhams07/GIS4logistics/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

Open, curated GIS data collection for logistics and transport analysis in India. Covers
administrative boundaries (India / state / district / taluka / village), roads by type,
railways (stations, freight), logistics hubs (ports, ICDs, ICPs, air cargo) and
demographic data — plus a village-to-facility **accessibility analysis** covering
all 36 states/UTs.

![Accessibility](docs/img/accessibility_map.png)

## Quickstart (no network needed)

```python
import geopandas as gpd
districts = gpd.read_file("data/administrative/india_districts_lgd.geojson")   # 781 current districts
villages  = gpd.read_file("data/administrative/villages/sikkim_soi_villages.geojson")
access    = gpd.read_file  # see data/analysis/india_district_access_summary.csv
```

With network: `python scripts/analyze/nearest_facility.py --state Haryana`
computes per-village distances to the nearest rail station / ICD / port /
airport / ICP. Headline: across the 27 village-level states, the
population-weighted mean distance to a railway station is **13.6 km**, to an
ICD **121 km**.

## Repository philosophy

This is a **hybrid** repository:

- **Small datasets are committed directly** (state/district boundaries, hub point
  locations, station lists, district demographic tables).
- **Large datasets (village boundaries, full road/rail networks) are fetched by
  scripts** into a local `data/` folder via Git LFS-compatible formats, because
  village-level India-wide data runs into gigabytes.

All data is standardized to **EPSG:4326 (WGS 84)** and uses **LGD (Local Government
Directory) codes** as the join key across datasets.

## Data catalog

| Category | Dataset | Resolution | Format | How to get it |
|---|---|---|---|---|
| Administrative | **India / State / District / Taluka — current, LGD-coded** | National → sub-district | GeoJSON/GPKG | Committed in `data/administrative/` (36 states, 780 districts, 6,639 sub-districts) |
| Administrative | Districts (Census 2011, for census joins) | District | GeoJSON | Committed in `data/administrative/` |
| Administrative | **Villages — official (Survey of India, LGD-coded)** | Village | GeoJSON | Committed in `data/administrative/villages/` for all 27 published states; regenerable via `scripts/fetch/fetch_village_boundaries_soi.py` |
| Roads | **National Highway network (all numbered NH routes)** | National | GeoJSON | Committed in `data/roads/india_nh_network.geojson` (OSM) |
| Roads | Road network classified by type (NH/SH/MDR/ODR/village) | National | OSM PBF / GeoJSON | `scripts/fetch/fetch_roads.py` (Pune sample committed) |
| Analysis | **Village accessibility (nearest station/ICD/port/airport/ICP)** | Village/district | CSV | `scripts/analyze/nearest_facility.py --state X` (Sikkim + Haryana examples committed) |
| Rail | Railway stations (~8,000, code/name/zone/category) | National | CSV/GeoJSON | Committed in `data/rail/` |
| Rail | Rail lines & freight sidings | National | GeoJSON | `scripts/fetch/fetch_rail.py` |
| Logistics hubs | Major/minor ports, ICDs/CFSs, ICPs, air-cargo terminals | National points | CSV/GeoJSON | Committed in `data/logistics_hubs/` |
| Demographics | Census 2011 key indicators by district | District | CSV | Committed in `data/demographic/` |

The machine-readable version of this catalog is [`catalog.yaml`](catalog.yaml).
Detailed per-source documentation (URL, license, vintage, update frequency) is in
[`docs/sources.md`](docs/sources.md).

## Quickstart

```bash
pip install -r requirements.txt

# Fetch a district road network from OSM (roads classified NH/SH/MDR/ODR)
python scripts/fetch/fetch_roads.py --district Pune --state Maharashtra --name pune_sample --simplify 0.0005

# Build the end-to-end demo: one district, all layers merged into a GeoPackage + map
python scripts/make_demo.py --district Pune --state Maharashtra
```

Demo notebook: [`examples/district_logistics_demo.ipynb`](examples/district_logistics_demo.ipynb).

![Pune demo](docs/img/pune_demo_map.png)

## Repository layout

```
data/            # Committed small datasets (GeoJSON/CSV)
scripts/fetch/   # Download scripts, one per source
scripts/clean/   # Standardization utilities (CRS, codes, schemas)
examples/        # Notebooks
docs/            # Source documentation and data standards
catalog.yaml     # Machine-readable data catalog
```

## Licensing & legal

- **Code** in this repository: MIT (see [LICENSE](LICENSE)).
- **Data**: each dataset remains under its source's license — see the `license`
  field in `catalog.yaml` and [`docs/sources.md`](docs/sources.md). Sources with
  restrictive or unclear licensing (e.g., Survey of India village boundaries) are
  provided as fetch scripts only, not committed data.
- **India geospatial law**: this repository complies with the 2021 DST
  Geospatial Data Guidelines (unrestricted civilian data, self-certification
  regime). See [`docs/legal_compliance.md`](docs/legal_compliance.md).
- **Boundary disclaimer**: boundaries here are indicative community data
  (DataMeet), not Survey of India products, and must not be treated as an
  official depiction of India's external or disputed boundaries.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). New datasets must document source, license,
vintage and pass the standardization checks in `scripts/clean/`.
