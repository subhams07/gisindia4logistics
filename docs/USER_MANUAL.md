# GISIndia4Logistics — Complete User Manual

Welcome to **GISIndia4Logistics**, a strategic analytics platform under development for freight, supply chain, and spatial infrastructure analysis in India. Models are not operational route or tariff guarantees. Data has mixed source terms and unresolved redistribution conditions; see [DATA_LICENSE.md](../DATA_LICENSE.md).

This manual provides end-to-end guidance for data scientists, logistics professionals, software engineers, and AI developers.

---

## Table of Contents

1. [Architecture & Core Standards](#1-architecture--core-standards)
2. [Installation & Setup](#2-installation--setup)
3. [Working with Data in Python (GeoPandas & DuckDB)](#3-working-with-data-in-python-geopandas--duckdb)
4. [Analytical & Simulation Engines](#4-analytical--simulation-engines)
   - [4.1 Multi-Modal Freight Cost Optimizer](#41-multi-modal-freight-cost-optimizer)
   - [4.2 Commercial Highway Dijkstra Routing & Toll Friction](#42-commercial-highway-dijkstra-routing--toll-friction)
   - [4.3 Port Hinterland Gravity & Catchment Model](#43-port-hinterland-gravity--catchment-model)
   - [4.4 Village Accessibility Analysis](#44-village-accessibility-analysis)
   - [4.5 Drive-Time Isochrone Generation](#45-drive-time-isochrone-generation)
5. [Interactive Village Mapping & Cartography](#5-interactive-village-mapping--cartography)
6. [Deploying the FastAPI REST Service & Docker](#6-deploying-the-fastapi-rest-service--docker)
7. [AI Agent & MCP Plugin Integration (Antigravity, Codex, Claude)](#7-ai-agent--mcp-plugin-integration)
8. [Importing into Desktop GIS (QGIS & ArcGIS)](#8-importing-into-desktop-gis-qgis--arcgis)
9. [Legal Compliance & Attribution](#9-legal-compliance--attribution)

---

## 1. Architecture & Core Standards

GIS4Logistics adheres strictly to national Indian spatial standards:

* **Coordinate Reference Systems (CRS)**:
  * **Canonical Storage**: `EPSG:4326` (WGS 84, Longitude/Latitude degrees).
  * **Projected Metric Analysis**: `EPSG:7755` (India National System Framework LCC) for accurate distance and area calculations.
* **Join Keys & Standardization**:
  * **Administrative Layers**: Standardized to **Local Government Directory (LGD)** state, district, sub-district, and village codes.
  * **Demographics**: Census 2011 indicators joined via LGD crosswalks and spatial allocation across 780 current districts; the recorded national population delta is below 0.001%, not exact conservation.
  * **Railways**: Indian Railways alpha codes (`station_code`).
* **Hybrid Storage Architecture**:
  * Small layers stored in plain-text **GeoJSON & CSV** for human inspectability and web rendering.
  * Heavy layers (142k highway segments, 578k villages, 6.6k subdistricts) stored in **OGC GeoParquet** (`.parquet`) with Snappy compression for $10\times$ faster read performance.

---

## 2. Installation & Setup

### Prerequisites
* Python 3.10+ (Python 3.11 recommended)
* Git

### Step-by-Step Setup
```bash
# 1. Clone the repository
git clone https://github.com/subhams07/gisindia4logistics.git
cd gisindia4logistics

# 2. Create and activate a virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# 3. Install dependencies & package in editable mode
pip install -r requirements.txt
pip install -e .

# 4. Optional fast audit (subset only; writes the audit report)
# Do not replace full-audit evidence with a fast report for release verification.
python scripts/audit/audit_all.py --fast

# 5. Run Python SDK and server verification test suites with pytest
python -m pytest tests/test_package.py tests/test_server.py
```

### Data location

**Data location.** The ~1 GB data directory ships with the Git repository, not with the Python package. If you install non-editably (a wheel, `pip install .`, or `pip install git+https://github.com/subhams07/gisindia4logistics`), also clone the repository and point the package at its data before starting Python, the CLI, the API server or the MCP server: `export GIS4LOGISTICS_DATA_PATH=/path/to/gisindia4logistics/data` (Windows: `set GIS4LOGISTICS_DATA_PATH=C:\path\to\gisindia4logistics\data`). The variable is read once, at import time. An editable install from the clone (`pip install -e .`) finds `data/` automatically. If the data cannot be found, the SDK raises `gisindia4logistics.sdk.DataNotFoundError` (a `DataIntegrityError`), the CLI prints `error: GISIndia4Logistics data not found at <path> ...` and exits 1, and MCP tools return `isError` with code `data_not_found`.

The CLI `serve` command checks for the data and refuses to start without it, while running `uvicorn server.app:app` directly does not yet perform that check. The MCP server (`gisindia4logistics mcp`) starts without data, prints a warning on stderr, and answers `initialize` and `tools/list` normally; only tool calls fail.

Two other locations are configurable the same way and, in a non-editable install, default to folders next to the installed `server` package (inside `site-packages`), which is often read-only or unwanted: `GIS4LOGISTICS_OUTPUT_PATH` (generated maps and reports; default `outputs/`) and `GIS4LOGISTICS_CACHE_PATH` (highway-graph cache; default `outputs/cache/`). Set both to writable directories of your choice.

---

## 3. Working with Data in Python (GeoPandas & DuckDB)

### 3.1 Loading Administrative Boundaries
```python
import geopandas as gpd
import pandas as pd

# Load 780 Current LGD Districts (GeoParquet or GeoJSON)
districts_gdf = gpd.read_parquet("data/administrative/india_districts_lgd.parquet")
# or: districts_gdf = gpd.read_file("data/administrative/india_districts_lgd.geojson")

# Load 6,636 LGD Sub-districts (Talukas/Tehsils)
subdistricts_gdf = gpd.read_parquet("data/administrative/india_subdistricts_lgd.parquet")

# Load Village Polygons for a State (e.g. Haryana)
haryana_villages_gdf = gpd.read_parquet("data/administrative/villages_parquet/haryana_soi_villages.parquet")
```

### 3.2 Loading Highway Network & Toll Plazas
```python
# Load 141,990 National Highway segments (~634 NH routes)
nh_gdf = gpd.read_parquet("data/roads/india_nh_network.parquet")

# Filter NH-48 (Delhi-Mumbai-Chennai Golden Quadrilateral corridor)
nh48_gdf = nh_gdf[nh_gdf["ref"].str.contains("NH48", na=False)]

# Load 1,536 Clustered Toll Plazas
tolls_df = pd.read_parquet("data/roads/toll_plazas.parquet")
```

### 3.3 Querying Multi-Modal Logistics Hubs with DuckDB
```python
import duckdb

# Query major ports (no throughput threshold is applied here)
con = duckdb.connect()
res = con.execute("""
    SELECT name, state, city, latitude, longitude, capacity_notes
    FROM 'data/logistics_hubs/ports.csv'
    WHERE hub_type = 'major_port'
""").df()
print(res)
```

---

## 4. Analytical & Simulation Engines

### 4.1 Multi-Modal Freight Cost Optimizer
Compare generalized freight cost (₹/tonne) and transit time across **Road Trucking**, **Conventional Rail**, and **Dedicated Freight Corridor (DFC)**.

```bash
# Run simulation with default rates for all 780 districts
python scripts/analyze/intermodal_cost_engine.py

# Run with custom economic parameter overrides
python scripts/analyze/intermodal_cost_engine.py \
  --road-linehaul-rate 3.80 \
  --toll-cost-per-plaza 400.0 \
  --rail-base-rate 1.40 \
  --dfc-linehaul-rate 1.05 \
  --inventory-holding-rate 8.50 \
  --output-csv data/analysis/custom_freight_modal_split.csv
```

#### Python API Usage:
```python
import gisindia4logistics as gis

# Public SDK; payload_tons is shipment weight, not truck capacity.
result = gis.calculate_freight_cost(
    origin_district="Indore", payload_tons=24.0,
    road_linehaul_rate=3.80, toll_cost_per_plaza=400.0,
    dfc_linehaul_rate=1.05,
)
print(f"Optimal Mode: {result['optimal_mode']}")
print(f"Cost Road: ₹{result['road']['cost_per_ton_inr']:.2f}/t")
if result.get("dfc_rail") is not None:
    print(f"Cost DFC: ₹{result['dfc_rail']['cost_per_ton_inr']:.2f}/t")
print(f"Savings vs Road: {result['modal_shift_savings_pct']:.1f}%")
```

---

### 4.2 Commercial Highway Dijkstra Routing & Toll Friction
Computes strategic highway-network distance and modelled driving hours and toll expense. Snapping, synthetic connectivity and source coverage limit operational accuracy; tolls are not verified official FASTag tariffs.

```python
import gisindia4logistics as gis

# Route from Pune (18.5204, 73.8567) to JNPT Port (18.9500, 72.9500)
route = gis.route_highway(
    origin=(18.5204, 73.8567),
    destination=(18.9500, 72.9500),
    vehicle_type="MAV_20T"
)
print(f"Distance: {route['distance_km']:.1f} km")
print(f"Drive Time: {route['drive_time_formatted']} ({route['drive_time_hours']:.2f} hrs)")
print(f"Modelled Toll Count: {route['tolls_encountered_count']}")
print(f"Estimated Toll Outlay: ₹{route['estimated_toll_cost_inr']:.2f}")
```

For the rich result, use `gis.plan_highway_route(origin={"type": "district", "district_code": 521}, destination={"type": "hub", "hub_type": "port", "name": "JNPT"}, vehicle_type="LCV", geometry_detail="simplified")`. The equivalent REST endpoint is `/api/v1/route/highway/plan`, CLI command is `route-plan`, and MCP tool is `gis_plan_highway_route`. Each delegates to the same service. Coordinate references use `{"type": "coordinate", "latitude": ..., "longitude": ...}`; display GeoJSON uses `[longitude, latitude]`. The district reference resolves a district representative, not necessarily its city centre.

Rich geometry defaults to `none`; `include_route_geometry=False` suppresses it even when a detail is requested. Inspect `route_quality` warnings/limitations and `metadata`; synthetic connectivity, minimum-feeder snapping and ignored direction restrictions prevent navigation guarantees. Rich results do not contain tolls. The legacy `tolls_encountered_count` key remains for compatibility but represents a distance-based heuristic, not observed passage.

---

### 4.3 Port Hinterland Gravity & Catchment Model
Models market capture probability $P_{ij}$ and contestability across India's 12 Major Commercial Ports using the Huff/Reilly gravity formulation:
$$P_{ij} = \frac{S_j^\alpha \cdot T_{ij}^{-\beta}}{\sum_{k} S_k^\alpha \cdot T_{ik}^{-\beta}}$$

```bash
# Run simulation with custom sensitivity exponents
python scripts/analyze/port_hinterland_model.py --alpha 0.90 --beta 1.70
```

---

### 4.4 Village Accessibility Analysis
Village access tables calculate projected straight-line proximity to infrastructure. District highway travel-time tables are separate network outputs; village proximity must not be presented as a verified road journey.

```bash
# Run accessibility for a specific state
python scripts/analyze/nearest_facility.py --state Haryana

# Run accessibility across all 36 States and UTs
python scripts/analyze/nearest_facility.py --all
```

---

### 4.5 Drive-Time Isochrone Generation
Generates 1-hour, 2-hour, 4-hour, and 8-hour drive-time vector catchment polygons around infrastructure assets.

```bash
# Generate isochrones for 12 Major Ports and 20 MMLPs
python scripts/analyze/generate_isochrones.py
```
Outputs saved to `data/analysis/major_ports_isochrones.geojson` and `data/analysis/mmlp_isochrones.geojson`.

---

## 5. Interactive Village Mapping & Cartography

Generate interactive Leaflet web maps or 300 DPI publication maps for any district:

```bash
# 1. Interactive Leaflet HTML Map
python scripts/analyze/plot_villages.py --state Haryana --district Ambala --metric dist_rail_station_km --format html

# 2. Publication-Quality Static PNG Map
python scripts/analyze/plot_villages.py --state Maharashtra --district Pune --metric dist_nh_km --format png

# 3. Both Formats
python scripts/analyze/plot_villages.py --state Gujarat --district Surat --metric dist_icd_km --format both
```

### Visual Metrics Available:
* `dist_rail_station_km`: Distance to closest railway station.
* `dist_nh_km`: Distance to closest National Highway.
* `dist_icd_km`: Distance to closest Inland Container Depot.
* `dist_freight_terminal_km`: Distance to closest Gati Shakti Cargo Terminal (GCT).
* `dist_port_km`: Distance to closest commercial port.
* `dist_toll_plaza_km`: Projected straight-line distance to the closest clustered OSM toll location; FASTag status and official tariffs are not verified.

---

## 6. Deploying the FastAPI REST Service & Docker

### 6.1 Direct Local Launch
```bash
uvicorn server.app:app --host 0.0.0.0 --port 8000 --reload
```
* **Interactive OpenAPI/Swagger Docs**: [`http://localhost:8000/docs`](http://localhost:8000/docs)
* **ReDoc Documentation**: [`http://localhost:8000/redoc`](http://localhost:8000/redoc)

### 6.2 Docker Compose Evaluation Deployment
```bash
# Build and run containerized server in the background
docker-compose up --build -d

# Check service logs
docker-compose logs -f

# Stop service
docker-compose down
```

### 6.3 REST API Endpoints Quick Reference

| Endpoint | Method | Params | Description |
| :--- | :--- | :--- | :--- |
| `/api/v1/admin/states` | `GET` | — | List 36 States/UTs with LGD codes and district counts |
| `/api/v1/admin/districts` | `GET` | `state`, `search` | List 780 current LGD districts with population estimates |
| `/api/v1/admin/districts/{name_or_code}` | `GET` | — | Comprehensive District Logistics Scorecard |
| `/api/v1/admin/villages/map.html` | `GET` | `state`, `district`, `metric` | Interactive Leaflet HTML Map |
| `/api/v1/admin/villages/geojson` | `GET` | `state`, `district` | Vector GeoJSON of district villages |
| `/api/v1/hubs` | `GET` | `hub_type`, `state` | Query multi-modal hubs |
| `/api/v1/hubs/nearest` | `GET` | `latitude`, `longitude`, `top_k` | Closest infrastructure across all categories |
| `/api/v1/hubs/rail/stations` | `GET` | `search`, `zone`, `category` | Query 8,697 railway stations |
| `/api/v1/hubs/rail/dfc` | `GET` | — | 3 DFC corridors & 54 junction yards |
| `/api/v1/hubs/roads/toll-plazas` | `GET` | `state`, `nh_number` | Query 1,536 toll plazas |
| `/api/v1/route/highway` | `POST` | `origin`, `destination`, `vehicle_type` | Legacy strategic highway routing & modelled toll heuristic |
| `/api/v1/route/highway/plan` | `POST` | Strict location references, vehicle, geometry controls | Canonical route geometry, quality and provenance; no toll fields |
| `/api/v1/simulate/freight-cost` | `POST` | `origin_district`, `custom_parameters` | Intermodal freight cost optimization |
| `/api/v1/simulate/port-gravity` | `POST` | `alpha`, `beta` | National port hinterland gravity capture |

This table describes existing public entrypoints, not a completed decision workbench. SDK `get_district(name_or_code)` returns a dictionary, not an object with attribute access. SDK `find_nearest(latitude, longitude, top_k=3)` returns category-keyed results and has no `facility_types` argument. Highway SDK results use the legacy dictionary envelope shown in section 4.2.

Public highway routing is implemented for review; the [stabilization plan](stabilization_plan.md) records its current acceptance gate. Other Phase 1 schemas and `docs/contracts/` examples do not prove implemented corridor decisions, district comparison, matched official toll tariffs or PDF/XLSX export. Producer unification, toll matching, corridor workflow and secure exports require separate verification gates.

---

## 7. AI Agent & MCP Plugin Integration

GIS4Logistics includes a native **Model Context Protocol (MCP)** server enabling AI assistants in Antigravity, Codex, Cursor, and Claude Desktop to execute spatial tools directly.

### Configuration (`mcp_config.json`)
Add the server definition to your workspace configuration:
```json
{
  "mcpServers": {
    "gis4logistics": {
      "command": "python",
      "args": ["-m", "mcp_server.server"]
    }
  }
}
```

### The 6 Agent Tools:
1. `gis_get_district_scorecard`: Retrieves full demographic, nearest NH, rail, ICD, and port connectivity.
2. `gis_calculate_intermodal_freight_cost`: Simulates Road vs Rail vs DFC freight rates and modal shift savings.
3. `gis_find_nearest_facilities`: Finds closest infrastructure to any latitude/longitude coordinate.
4. `gis_highway_route_and_tolls`: Calculates strategic driving distance, modelled hours and distance-based toll estimates, not verified FASTag/NHAI tariffs.
5. `gis_simulate_port_catchment`: Models port market contestability under the Huff gravity model.
6. `gis_plot_villages_map`: Generates interactive Leaflet HTML or PNG maps for a district.

#### Example Agent Prompt:
> *"Compare the freight cost of shipping 25 tonnes of automotive parts from Pune to JNPT using Road trucking vs DFC Rail assuming a trucking rate of ₹3.80/tonne-km."*

---

## 8. Importing into Desktop GIS (QGIS & ArcGIS)

### 8.1 QGIS (3.28+)
1. **Adding GeoParquet / GPKG Layers**:
   - Drag and drop `data/administrative/india_districts_lgd.parquet` or `india_subdistricts_lgd.parquet` directly onto the QGIS canvas.
2. **Adding Highway Network**:
   - Open `data/roads/india_nh_network.parquet` or `data/roads/india_nh_network.geojson`.
3. **Styling by Attribute**:
   - Right-click Layer $\to$ *Properties* $\to$ *Symbology* $\to$ *Graduated* $\to$ Select `dist_rail_station_km` $\to$ Color ramp `RdYlGn_r`.

### 8.2 ArcGIS Pro
1. Use the **JSON To Features** tool for GeoJSON files or the **Parquet / Arrow** spatial connector in ArcGIS Pro 3.1+.
2. For SQLite/GPKG: Drag `data/administrative/india_subdistricts_lgd.gpkg` directly into the Contents pane.

---

## 9. Legal Compliance & Attribution

### Governing Framework
The **Department of Science and Technology (DST) Guidelines for Acquiring and Producing Geospatial Data and Geospatial Data Services including Maps (15 February 2021)** are a policy reference, not a certification of compliance or redistribution permission:
* **Intended Scope**: Civilian strategic logistics analytics under development; assess the specific dataset and intended use independently.
* **Source Terms**: MIT applies to code only. OSM-derived roads and toll booths are ODbL; other sources retain their own terms. Public access or simplified precision is not permission. See [DATA_LICENSE.md](../DATA_LICENSE.md) and [sources](sources.md).
* **Unresolved Rights**: Survey of India redistribution and DataMeet railway station licensing remain unresolved. Station locations have a 2016 DataMeet base with later classification enrichment, not a verified 2024–25 refresh. No written permission is asserted.
* **Boundary Disclaimer**: Administrative boundaries are indicative analytical derivatives (DataMeet / Survey of India ABDB lineage) published for logistics and economic modeling, not official Survey of India boundary certifications.

### Required Citations
When using GIS4Logistics in research, reports, or commercial products, please cite:
```bibtex
@misc{gis4logistics_india_2026,
  author = {GIS4Logistics Contributors},
  title = {GIS4Logistics India: Curated Geospatial Data & Analytical Platform for Indian Logistics},
  year = {2026},
  publisher = {GitHub},
  url = {https://github.com/subhams07/gisindia4logistics}
}
```
