# GISIndia4Logistics — Complete User Manual

Welcome to **GISIndia4Logistics**, an open, standardized geospatial data collection and analytical platform engineered for freight, supply chain, and spatial infrastructure analysis in India.

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
  * **Demographics**: Census 2011 indicators joined via official LGD crosswalks with 100% population conservation across all 781 current districts.
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
git clone https://github.com/subhams07/GIS4logistics.git
cd GIS4logistics

# 2. Create and activate a virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# 3. Install dependencies & package in editable mode
pip install -r requirements.txt
pip install -e .

# 4. Verify system integrity (80 automated checks)
python scripts/audit/audit_all.py --fast

# 5. Run Python SDK and server verification test suites
python tests/test_package.py
python tests/test_server.py
```

---

## 3. Working with Data in Python (GeoPandas & DuckDB)

### 3.1 Loading Administrative Boundaries
```python
import geopandas as gpd
import pandas as pd

# Load 781 Current LGD Districts (GeoParquet or GeoJSON)
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

# Query ports with container throughput > 50 MT
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
# Run simulation with default rates for all 781 districts
python scripts/analyze/intermodal_cost_engine.py

# Run with custom economic parameter overrides
python scripts/analyze/intermodal_cost_engine.py \
  --road-linehaul-rate 3.80 \
  --toll-cost-per-plaza 400.0 \
  --rail-base-rate 1.40 \
  --dfc-linehaul-rate 1.05 \
  --inventory-holding-rate 8.50 \
  --output data/analysis/custom_freight_modal_split.csv
```

#### Python API Usage:
```python
from scripts.analyze.intermodal_cost_engine import calculate_freight_modal_cost, FreightCostConfig

config = FreightCostConfig(
    road_linehaul_rate_inr_per_tkm=3.80,
    toll_cost_per_plaza_inr=400.0,
    dfc_linehaul_rate_inr_per_tkm=1.05
)

result = calculate_freight_modal_cost("Indore", config=config, payload_tons=24.0)
print(f"Optimal Mode: {result['optimal_mode']}")
print(f"Cost Road: ₹{result['road']['cost_per_ton_inr']:.2f}/t | DFC: ₹{result['dfc_rail']['cost_per_ton_inr']:.2f}/t")
print(f"Savings by DFC: {result['modal_shift_savings_pct']:.1f}%")
```

---

### 4.2 Commercial Highway Dijkstra Routing & Toll Friction
Computes exact shortest-path commercial road driving distance, driving hours, and FASTag toll expense between any two points in India using the 289,000-node highway graph.

```python
from server.dependencies import DataStore
from server.routers.routing import calculate_highway_route, RouteRequest

store = DataStore.get_instance()

# Route from Pune (18.5204, 73.8567) to JNPT Port (18.9500, 72.9500)
req = RouteRequest(
    origin=[18.5204, 73.8567],
    destination=[18.9500, 72.9500],
    vehicle_type="MAV_20T"
)

route = calculate_highway_route(req, store=store)
print(f"Distance: {route.distance_km:.1f} km")
print(f"Drive Time: {route.formatted_drive_time} ({route.drive_time_hours:.2f} hrs)")
print(f"Toll Plazas Encountered: {route.toll_plazas_count}")
print(f"Estimated Toll Outlay: ₹{route.estimated_toll_cost_inr:.2f}")
```

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
Calculates straight-line and road-network distance for all 578,345 settlements to the nearest railway station, ICD, port, air cargo terminal, MMLP, and toll plaza.

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
* `dist_toll_plaza_km`: Distance to closest FASTag toll plaza.

---

## 6. Deploying the FastAPI REST Service & Docker

### 6.1 Direct Local Launch
```bash
uvicorn server.app:app --host 0.0.0.0 --port 8000 --reload
```
* **Interactive OpenAPI/Swagger Docs**: [`http://localhost:8000/docs`](http://localhost:8000/docs)
* **ReDoc Documentation**: [`http://localhost:8000/redoc`](http://localhost:8000/redoc)

### 6.2 Docker Compose Production Deployment
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
| `/api/v1/admin/districts` | `GET` | `state`, `search` | List 781 current LGD districts with population estimates |
| `/api/v1/admin/districts/{name_or_code}` | `GET` | — | Comprehensive District Logistics Scorecard |
| `/api/v1/admin/villages/map.html` | `GET` | `state`, `district`, `metric` | Interactive Leaflet HTML Map |
| `/api/v1/admin/villages/geojson` | `GET` | `state`, `district` | Vector GeoJSON of district villages |
| `/api/v1/hubs` | `GET` | `hub_type`, `state` | Query multi-modal hubs |
| `/api/v1/hubs/nearest` | `GET` | `latitude`, `longitude`, `top_k` | Closest infrastructure across all categories |
| `/api/v1/hubs/rail/stations` | `GET` | `search`, `zone`, `category` | Query 8,697 railway stations |
| `/api/v1/hubs/rail/dfc` | `GET` | — | 3 DFC corridors & 54 junction yards |
| `/api/v1/hubs/roads/toll-plazas` | `GET` | `state`, `nh_number` | Query 1,536 toll plazas |
| `/api/v1/route/highway` | `POST` | `origin`, `destination`, `vehicle_type` | Commercial highway shortest-path routing & tolls |
| `/api/v1/simulate/freight-cost` | `POST` | `origin_district`, `custom_parameters` | Intermodal freight cost optimization |
| `/api/v1/simulate/port-gravity` | `POST` | `alpha`, `beta` | National port hinterland gravity capture |

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
4. `gis_highway_route_and_tolls`: Calculates driving distance, hours, and FASTag toll expense.
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
This repository complies with the **Department of Science and Technology (DST) Guidelines for Acquiring and Producing Geospatial Data and Geospatial Data Services including Maps (15 February 2021)**:
* **Civilian Scope**: Unrestricted civilian spatial data; no defence or strategic installations included.
* **Accuracy Thresholds**: Data simplified to $\sim 50\text{--}100\text{ m}$; well above the regulated $1\text{ m}$ threshold.
* **Boundary Disclaimer**: Administrative boundaries are indicative analytical derivatives (DataMeet / Survey of India ABDB lineage) published for logistics and economic modeling, not official Survey of India boundary certifications.

### Required Citations
When using GIS4Logistics in research, reports, or commercial products, please cite:
```bibtex
@misc{gis4logistics_india_2026,
  author = {GIS4Logistics Contributors},
  title = {GIS4Logistics India: Curated Geospatial Data & Analytical Platform for Indian Logistics},
  year = {2026},
  publisher = {GitHub},
  url = {https://github.com/subhams07/GIS4logistics}
}
```
