# 🇮🇳 GISIndia4Logistics

[![GitHub Release](https://img.shields.io/github/v/release/subhams07/gisindia4logistics?color=blue&logo=github)](https://github.com/subhams07/gisindia4logistics/releases)
[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg?logo=python)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Data Audit](https://img.shields.io/badge/Data%20Audit-279%20Checks%20Passed-brightgreen.svg)](docs/audit_report.md)
[![Compliance](https://img.shields.io/badge/DST%20Geospatial%202021-Compliant-success.svg)](docs/legal_compliance.md)
[![User Manual](https://img.shields.io/badge/Docs-User%20Manual-orange.svg)](docs/USER_MANUAL.md)

> **The Open-Source Geospatial & Multimodal Freight Analytics Platform for India.**
> From nationwide supply chain strategy down to village-level infrastructure accessibility — answered in seconds.

---

## 🌟 Why GISIndia4Logistics?

Analyzing supply chain, infrastructure connectivity, and logistics costs in India has historically meant wrestling with fragmented shapefiles, unstandardized district names, mismatched Census boundaries, and expensive proprietary routing engines.

**GISIndia4Logistics** changes that. It provides a unified, production-ready spatial data warehouse and analytics engine combining:
- **781 Current LGD Districts & 578,000+ Villages/Settlements** across all 36 States/UTs.
- **Topological National Highway Network** (141,990 segments in EPSG:7755) with speed-weighted Dijkstra routing and FASTag toll estimation.
- **Dedicated Freight Corridors (DFC) & Railway Infrastructure** (8,697 stations, 5,938 NSG categories, 84 Gati Shakti Cargo Terminals).
- **250+ Logistics Hubs** (Major Ports, ICDs/CFSs, MMLPs, Land Border ICPs, Air Cargo, FCI Silos, Cold Chains, Agri Mandis).
- **Intermodal Cost Optimization & Gravity Port Catchment Models** out-of-the-box.
- **Multi-Surface Access**: Python SDK, Command-Line Tool, FastAPI REST Server, and Model Context Protocol (MCP) tool server for AI agents.

---

## 💡 What Questions Can This Platform Answer?

| Domain | Practical Question | Platform Capability |
|---|---|---|
| 🛣️ **Highway Transit & Toll Costing** | *"What is the realistic driving duration and FASTag toll burden for a Multi-Axle Vehicle (MAV 20T) hauling between Pune and Chennai?"* | **Strategic Highway Router**: Computes shortest-path network distances, road class speeds, and distance-based toll expenses in sub-seconds. |
| 🚂 **Multimodal Freight Optimization** | *"For a 24-tonne container shipment from Indore to JNPT, is rail or DFC cheaper than road trucking? What is the breakeven distance?"* | **Intermodal Cost Optimizer**: Compares linehaul rates, first/last mile cartage, terminal handling, and inventory transit costs to find optimal modal shift and INR savings. |
| 🚢 **Port Hinterlands & Contestability** | *"If Jawaharlal Nehru Port (JNPT) improves turnaround times, how many central Indian districts shift cargo share from Mundra or Kandla?"* | **Huff Gravity Catchment Model**: Models national port hinterland contestability and population capture across all 12 Major Commercial Ports. |
| 🏭 **Site Selection & Facility Siting** | *"Where should a retail giant or 3PL position an express fulfillment center to reach 75% of regional consumer demand within a 4-hour drive?"* | **Isochrone & Accessibility Engine**: Generates 1h, 2h, 4h, and 8h vector drive-time catchments and nearest-infrastructure matrices. |
| 🌾 **Rural Reach & Agri-Supply Chain** | *"What percentage of agricultural villages in Haryana or Uttar Pradesh are within 10 km of a rail siding or 50 km of an ICD?"* | **Dual-Distance Village Access Engine**: Geodesic spatial overlay across 578,000+ village polygons and settlements in all 36 States/UTs. |
| 📊 **District Logistics Scorecards** | *"How do infrastructure connectivity, highway proximity, and demographic indicators rank across every district in India?"* | **Automated District Scorecards**: Instant 360° logistical profiles for all 781 districts with 2024-25 LGD coding and conserved Census populations. |

---

## 👥 Who Is This Relevant For?

- 🚚 **Logistics Providers, 3PLs & Fleet Operators**: Plan corridor routing, evaluate multimodal rail/DFC substitution, estimate toll budgets, and benchmark transit times.
- 💼 **Strategy & Infrastructure Consultants (McKinsey, BCG, Big 4)**: Instant, defensible baseline data for PM Gati Shakti master plans, multimodal logistics park (MMLP) feasibilities, and regional supply chain studies.
- 🏢 **Industrial Real Estate & Warehouse Developers**: Screen land parcels, benchmark highway access, and generate drive-time isochrones for prospective tenant pitches.
- 🔬 **Geospatial Data Scientists & Academic Researchers**: High-quality, clean geospatial datasets (EPSG:7755 LCC + EPSG:4326) with standardized LGD join keys, free from scraping, license ambiguities, or geometry errors.
- 🤖 **AI Agents & LLM Engineers**: Equip Claude, ChatGPT, Cursor, and Antigravity agents with spatial intelligence via the built-in **Model Context Protocol (MCP)** tool server.
- 🏛️ **Policymakers & Public Sector Agencies**: Identify infrastructure cold-spots, audit rural connectivity, and model the economic impact of new freight corridor investments.

---

## 🚀 Quickstart: Choose Your Interface

### 1. Python SDK (`pip install gisindia4logistics`)

```python
import gisindia4logistics as gis

# 1. Get complete district logistics scorecard & demographics
pune = gis.get_district("Pune")
print(f"Nearest Highway: {pune['nearest_highway_km']} km | Nearest Port: {pune['nearest_port']['name']}")

# 2. Strategic Highway Routing & FASTag Toll Estimation
route = gis.route_highway(origin=(18.5204, 73.8567), destination=(18.9500, 72.9500), vehicle_type="MAV_20T")
print(f"Distance: {route['distance_km']} km | Drive Time: {route['drive_time_formatted']} | Toll: INR {route['estimated_toll_cost_inr']}")

# 3. Intermodal Freight Cost Optimization (Road vs Rail vs DFC)
freight = gis.calculate_freight_cost(origin_district="Indore", payload_tons=24.0, road_linehaul_rate=3.80)
print(f"Optimal Mode: {freight['optimal_mode']} | Savings: {freight['modal_shift_savings_pct']:.1f}% (INR {freight['modal_shift_savings_total_inr']:,.0f})")

# 4. Find Nearest Logistics Facilities (Geodesic KDTree)
nearest = gis.find_nearest(latitude=28.6139, longitude=77.2090, facility_types=["port", "icd", "mmlp"], top_k=3)

# 5. Simulate Port Hinterland Catchment
ports = gis.simulate_port_catchment(alpha=0.85, beta=1.65)
print(f"Top Port: {ports[0]['port_name']} captures {ports[0]['captured_districts_count']} districts")
```

---

### 2. Command-Line Interface (CLI)

```bash
# Calculate highway route, travel hours, and toll estimate
gisindia4logistics route --origin 18.5204,73.8567 --dest 18.9500,72.9500 --vehicle MAV_20T

# Simulate intermodal freight cost for a district
gisindia4logistics cost --origin Indore --payload 24.0 --road-rate 3.80

# Launch the FastAPI REST Server
gisindia4logistics serve --port 8000

# Launch the stdio Model Context Protocol (MCP) server
gisindia4logistics mcp
```

---

### 3. High-Performance FastAPI REST Server & Docker

Deploy locally or in production in seconds:

```bash
# Run with Docker Compose
docker-compose up --build -d

# Or run directly with Uvicorn
uvicorn server.app:app --host 0.0.0.0 --port 8000
```
- **Interactive Swagger UI**: Explore and test all endpoints interactively at `http://localhost:8000/docs`.
- **OpenAPI 3.1 Spec**: Ready for client generation in TypeScript, Go, Java, or Python.

---

### 4. AI Agent Integration (Model Context Protocol / MCP)

Equip AI assistants (Claude Desktop, Cursor, Antigravity) with spatial intelligence. Add this to your `mcp_config.json`:

```json
{
  "mcpServers": {
    "gisindia4logistics": {
      "command": "python",
      "args": ["-m", "mcp_server.server"]
    }
  }
}
```
**Built-In AI Tools:**
- `gis_get_district_scorecard`: Instant logistics & demographic profile for any Indian district.
- `gis_highway_route_and_tolls`: Strategic highway transit times and distance-based FASTag toll estimation.
- `gis_calculate_intermodal_freight_cost`: Multimodal freight comparison (Road, Rail, DFC) with custom rate overrides.
- `gis_find_nearest_facilities`: Nearest ports, ICDs, MMLPs, toll plazas, and stations to any coordinates.
- `gis_simulate_port_catchment`: Gravity-based port contestability modeling.
- `gis_plot_villages_map`: Generates interactive Leaflet HTML or 300 DPI PNG accessibility choropleths.

---

### 5. Direct Desktop GIS Loading (QGIS / ArcGIS / Kepler.gl)

Load committed datasets directly from the `data/` folder without network dependencies:

```python
import geopandas as gpd

# Load 781 Current LGD Districts
districts = gpd.read_file("data/administrative/india_districts_lgd.geojson")

# Load National Highway Network (141k segments)
nh_network = gpd.read_file("data/roads/india_nh_network.geojson")

# Load Dedicated Freight Corridors (WDFC & EDFC)
dfc_lines = gpd.read_file("data/rail/dfc_corridors.geojson")

# Load Logistics Infrastructure Hubs
ports = gpd.read_file("data/logistics_hubs/ports.csv")
icds  = gpd.read_file("data/logistics_hubs/icds.csv")
mmlps = gpd.read_file("data/logistics_hubs/mmlps.csv")
```

---

## 📦 Curated Geospatial Data Catalog

Every committed dataset is standardized in **`EPSG:4326` (WGS 84)** / **`EPSG:7755` (India NSF LCC)** and indexed with official **Local Government Directory (LGD)** codes.

| Layer | What's Included | Feature Count | Official Source | License |
|---|---|---|---|---|
| 🏛️ **States (Current)** | LGD-coded administrative boundaries | **36 States/UTs** | Survey of India (ABDB) | GODL-India / Good-Faith |
| 🏛️ **Districts (Current)** | Complete 2024–25 administrative polygons | **781 Districts** | Survey of India (ABDB) | GODL-India / Good-Faith |
| 🏛️ **Sub-Districts (Talukas)** | Sub-district administrative polygons | **6,636 Sub-districts** | Survey of India (ABDB) | GODL-India / Good-Faith |
| 🏡 **Villages & Habitations** | 100% nationwide coverage (polygons + habitations) | **578,345 Settlements** | Survey of India / ORGI | GODL-India / Good-Faith |
| 🛣️ **National Highways** | Topological road network by class (Motorway/Trunk/Primary) | **141,990 Segments (~634 Routes)** | OpenStreetMap / MoRTH | ODbL |
| 🏷️ **Toll Plazas** | Clustered FASTag highway toll plazas | **1,536 Plazas** | NHAI TIS Model | Open Access |
| 🚂 **Rail Infrastructure** | All operational railway stations with NSG classification | **8,697 Stations (5,938 Categorized)** | Indian Railways / DataMeet | Open Reference |
| ⚡ **Dedicated Freight Corridors** | Operational WDFC & EDFC routes + 54 DFC junctions | **2,843 km Alignment** | DFCCIL | GODL-India |
| 🏗️ **Freight Terminals** | Gati Shakti Cargo Terminals (GCT) with geocodes | **84 Terminals** | Ministry of Railways / PIB | GODL-India |
| 🚢 **Ports & Maritime** | Major Commercial Ports and Non-Major Cargo Ports | **22 Ports** | IPA / Ministry of Ports | GODL-India |
| 📦 **Inland Container Depots** | Dry ports, ICDs, and Container Freight Stations (CFS) | **44 ICDs/CFSs** | CBIC / CONCOR | GODL-India |
| 🏬 **Multimodal Logistics Parks** | Awarded & planned MMLP hub locations | **20 MMLPs** | NHLML / MoRTH | GODL-India |
| ✈️ **Air Cargo Terminals** | International and domestic air cargo airports | **25 Airports** | AAI / Cargo Logistics | GODL-India |
| 🛂 **Land Border ICPs** | Land Customs Stations & Integrated Check Posts | **19 ICPs** | LPAI / CBIC | GODL-India |
| 🌾 **Grain Storage & Agri Mandis** | FCI food grain storage depots & APMC e-NAM Mandis | **77 FCI Silos + 16 e-NAM Mandis** | FCI / e-NAM Portal | GODL-India |
| ❄️ **Cold Chain Infrastructure** | Multi-commodity cold storage facilities | **15 Key Hubs** | NCCD / MoFPI | GODL-India |
| 🏭 **Industrial Clusters** | NICDC Industrial Nodes & PM MITRA Textile Mega Parks | **21 NICDC Nodes + 7 PM MITRA** | DPIIT / Ministry of Textiles | GODL-India |
| 👥 **Demographics & Population** | Conserved 2011 Census + 781-District Spatial Allocation | **1.21 Billion Pop. Conserved** | ORGI / Registrar General | GODL-India |

The machine-readable catalog specification is published at [`catalog.yaml`](catalog.yaml).

---

## 🗺️ Interactive Visualizations & Mapping

Generate interactive Leaflet HTML maps with mouseover tooltips or 300 DPI publication choropleths:

```bash
# Generate interactive HTML map and high-resolution PNG choropleth for a district
python scripts/analyze/plot_villages.py --state Haryana --district Ambala --metric dist_rail_station_km --format both
```
View directly in your browser: `http://localhost:8000/api/v1/admin/villages/map.html?state=Haryana&district=Ambala`

![Accessibility Choropleth](docs/img/accessibility_map.png)

---

## 🛡️ Rigorous Quality Assurance & Legal Compliance

### 279-Check Automated National Data Audit
Every dataset committed to this repository passes an extensive, automated 279-point audit gate (`scripts/audit/audit_all.py`):
- ✅ **Boundary Topology**: Exact 36 States, 781 Districts, 0 zero-area polygons, 100% valid geometries on disk re-read.
- ✅ **Population Conservation**: 1,210,846,210 population conserved across 781 current districts (<0.001% delta from Census 2011).
- ✅ **Highway Routing Physics**: Implied network speeds strictly bounded between $25\text{ and }110\text{ km/h}$ (median $69.4\text{ km/h}$). Zero unrouted mainland districts. Island UTs strictly null-routed for land freight.
- ✅ **Pickle-Free Security**: Cache loading enforced with `allow_pickle=False` and full 64-character SHA-256 geometry/attribute fingerprinting.

Check the live report at [`docs/audit_report.md`](docs/audit_report.md).

### 2021 DST Geospatial Guidelines Compliance
This repository operates strictly under the **Liberalized Geospatial Data Guidelines (2021)** issued by the Department of Science & Technology (DST), Government of India:
- Free access to civilian, unrestricted spatial datasets.
- No defence/sensitive attributes or high-precision restricted data (gravity/sub-threshold elevation).
- Standard disclaimer: All boundaries are indicative representations published for logistics analytics, not authoritative Survey of India boundary certifications. See [`docs/legal_compliance.md`](docs/legal_compliance.md).

---

## 📖 Documentation & Guides

- 📘 **[User Manual & Developer Guide](docs/USER_MANUAL.md)** — In-depth tutorials, Python cookbook, API specs, and QGIS workflows.
- 📐 **[Model Assumptions & Analytical Formulas](docs/model_assumptions.md)** — Speed calculations, toll formulas, and freight cost models.
- 📚 **[Data Sources & Provenance](docs/sources.md)** — Upstream source URLs, vintages, and update frequencies.
- 📜 **[Data Licensing Details](DATA_LICENSE.md)** — Layer-by-layer license breakdowns (MIT, GODL-India, ODbL).

---

## 🤝 Contributing & Community

Contributions of new logistics datasets, improved routing algorithms, and documentation improvements are warmly welcomed!
Please read our [Contributing Guide](CONTRIBUTING.md), [Security Policy](SECURITY.md), and [Code of Conduct](CODE_OF_CONDUCT.md) before submitting pull requests.

---

<div align="center">
  <sub>Built with ❤️ for Indian logistics, infrastructure planners, and supply chain innovators.</sub>
</div>
