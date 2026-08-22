# GIS4Logistics — India

Open, curated GIS data collection for logistics and transport analysis in India. Covers
administrative boundaries (India / state / district / taluka / village), roads by type,
railways (stations, freight), logistics hubs (ports, ICDs, ICPs, air cargo) and
demographic data.

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
| Administrative | India outline, States (36), Districts (~780) | National | GeoJSON | Committed in `data/administrative/` |
| Administrative | Talukas — any district (OSM) | Sub-district | GeoJSON | `scripts/fetch/fetch_village_boundaries.py` (Pune sample committed) |
| Administrative | Villages | Village | GeoJSON | OSM polygon coverage near-zero; use Bhuvan (see `docs/sources.md`) |
| Roads | Road network classified by type (NH/SH/MDR/ODR/village) | National | OSM PBF / GeoJSON | `scripts/fetch/fetch_roads.py` |
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
