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
districts = gpd.read_file("data/administrative/india_districts_lgd.geojson")   # 781 current LGD districts
subdists  = gpd.read_file("data/administrative/india_subdistricts_lgd.gpkg")   # 6,636 LGD subdistricts
villages  = gpd.read_file("data/administrative/villages/sikkim_soi_villages.geojson")
access    = pd.read_csv("data/analysis/india_district_access_summary.csv")     # 817 rows (all 36 states)
travel    = pd.read_csv("data/analysis/nh_district_travel_time_summary.csv")   # 781-district highway matrix
```

## Data catalog

| Category | Dataset | Resolution | Format | Scope / How to get it |
|---|---|---|---|---|
| Administrative | **India / State / District / Sub-district — current, LGD-coded** | National → sub-district | GeoJSON/GPKG | Committed in `data/administrative/` (**36 states, 781 districts, 6,636 sub-districts**) |
| Administrative | Districts (Census 2011, for census joins) | District | GeoJSON | Committed in `data/administrative/india_districts.geojson` (640 districts) |
| Administrative | **Villages & Habitations — 100% 36 States/UTs Coverage** | Village / Settlement | GeoJSON | Committed in `data/administrative/villages/` (**578,345 settlements**: 543k SoI polygons + 35k border habitations) |
| Roads | **National Highway network (all numbered NH routes)** | National | GeoJSON | Committed in `data/roads/india_nh_network.geojson` (141,990 segments, ~634 routes) |
| Roads | **National Toll Plazas (FASTag & Highway Plazas)** | National points | CSV | Committed in `data/roads/toll_plazas.csv` (**1,536 clustered Toll Plazas** under NHAI TIS model) |
| Roads | Road network classified by type (NH/SH/MDR/ODR/village) | National | OSM PBF / GeoJSON | `scripts/fetch/fetch_roads.py` (Pune sample committed) |
| Rail | **Railway stations (~8,700 stations & 5,938 categorized NSG1-6)** | National | CSV | Committed in `data/rail/` (79.3% zone coverage, operating divisions) |
| Rail | **Freight Terminals (Gati Shakti Cargo Terminals - GCT)** | National | CSV | Committed in `data/rail/freight_terminals.csv` (84 GCT freight handling terminals) |
| Logistics hubs | **Ports, ICDs, ICPs, Air Cargo, MMLPs, IWAI, FCI Depots** | National points | CSV | Committed in `data/logistics_hubs/` (247 multi-modal points) |
| Freight Flows | **Annual Multi-Year Freight Series (Rail, Port, Road)** | 5 FYs (2019–24) | CSV | Committed in `data/freight/` (137 series validated vs PIB/IPA anchors) |
| Demographics | **Census 2011 & 781-District Population Allocation** | District | CSV | Committed in `data/demographic/` (1,210,846,210 population conserved) |
| Analysis | **Village Dual-Distance Accessibility Engine (all 36 States)** | Village/district | CSV | Committed in `data/analysis/` (578k village CSVs + `india_district_access_summary.csv`) |
| Analysis | **Highway Shortest-Path Drive-Time & Catchment Matrix** | 781 Districts | CSV | Committed in `data/analysis/nh_district_travel_time_summary.csv` and `nh_district_port_matrix.csv` |

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
