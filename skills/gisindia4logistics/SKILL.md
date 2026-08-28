---
name: gisindia4logistics
description: >-
  Provides logistics intelligence, spatial infrastructure queries, highway routing,
  and multi-modal freight cost optimization across all 36 Indian States/UTs, 781 districts,
  and 578,000+ habitations.
---

# GISIndia4Logistics Skill Guide

Use this skill when you need to answer questions, generate reports, or run simulations on:
1. **District Logistics Scorecards & Demographics**: Proximity to National Highways, Railway Stations, Toll Plazas, ICDs, Ports, and MMLPs across India's 781 current LGD districts.
2. **Multi-Modal Freight Cost Optimization**: Comparing Road Trucking vs Conventional Indian Railways vs Dedicated Freight Corridor (DFC) with custom freight rates, toll outlays, and transit delays.
3. **Nearest Logistics Infrastructure**: Finding the closest Sea Ports, ICDs, MMLPs, Air Cargo terminals, Inland Waterways, Cold Chain storages, APMC e-NAM Mandis, or Toll Plazas to any coordinate.
4. **Highway Route & Toll Friction**: Computing road driving distance, driving hours, toll plaza counts, and FASTag toll expense.
5. **Port Hinterland Gravity & Contestability**: Modeling cargo catchment and market share across India's 12 Major Commercial Ports.
6. **Village Accessibility Mapping**: Generating interactive Leaflet web maps or 300 DPI PNG choropleths of district villages.

---

## Tool Reference & Usage Examples

### 1. `gis_get_district_scorecard`
Use to retrieve full logistics and demographic indicators for any Indian district:
```json
{
  "district_name_or_code": "Pune"
}
```

### 2. `gis_calculate_intermodal_freight_cost`
Use to simulate financial freight cost (INR/tonne) and transit time across Road vs Rail vs DFC:
```json
{
  "origin_district": "Indore",
  "target_port": "Jawaharlal Nehru Port (JNPT)",
  "payload_tons": 24.0,
  "road_linehaul_rate": 3.60,
  "toll_cost_per_plaza": 400.0,
  "rail_base_class_rate": 1.50,
  "dfc_linehaul_rate": 1.10
}
```

### 3. `gis_find_nearest_facilities`
Use to locate the closest logistics infrastructure to any coordinate:
```json
{
  "latitude": 28.6139,
  "longitude": 77.2090,
  "top_k": 3
}
```

### 4. `gis_highway_route_and_tolls`
Use to calculate commercial highway distance, driving hours, and FASTag toll outlay:
```json
{
  "origin": [18.5204, 73.8567],
  "destination": [18.9500, 72.9500],
  "vehicle_type": "MAV_20T"
}
```

### 5. `gis_simulate_port_catchment`
Use to model national port contestability under custom gravity parameters:
```json
{
  "alpha": 0.85,
  "beta": 1.65
}
```

### 6. `gis_plot_villages_map`
Use to render village-level accessibility choropleths:
```json
{
  "state": "Haryana",
  "district": "Ambala",
  "metric": "dist_rail_station_km",
  "format": "html"
}
```
