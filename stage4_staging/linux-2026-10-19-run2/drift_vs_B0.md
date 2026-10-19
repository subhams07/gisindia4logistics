## Drift: staged run vs B0

### `nh_district_travel_time_summary.csv`

Rows: new 781, B0 781, matched 781, only new 0, only B0 0.

| column | changed rows | NaN new→B0 mismatches | median abs Δ | max abs Δ | median rel Δ |
|---|---|---|---|---|---|
| pop_2011 | 1 | 0 | 146 | 146 | 0.01% |
| highway_access_dist_km | 62 | 0 | 8.25 | 587.8 | 66.87% |
| highway_access_time_min | 62 | 0 | 14.1 | 1008 | 66.78% |
| rail_station_straight_km | 39 | 0 | 6.5 | 139.5 | 39.45% |
| rail_station_road_distance_km | 190 | 0 | 0.1 | 260.2 | 0.22% |
| rail_station_drive_time_hours | 45 | 0 | 0.15 | 3.54 | 35.00% |
| rail_station_drive_time_min | 711 | 0 | 0.3 | 212.3 | 0.97% |
| freight_terminal_straight_km | 4 | 0 | 25.4 | 42 | 47.10% |
| freight_terminal_road_distance_km | 573 | 0 | 0.2 | 473.7 | 0.09% |
| freight_terminal_drive_time_hours | 8 | 0 | 0.87 | 7.46 | 57.04% |
| freight_terminal_drive_time_min | 690 | 0 | 0.3 | 447.9 | 0.14% |
| port_straight_km | 5 | 0 | 8.4 | 61.3 | 10.24% |
| port_road_distance_km | 756 | 0 | 0.5 | 334.7 | 0.08% |
| port_drive_time_hours | 9 | 0 | 0.62 | 4.86 | 26.05% |
| port_drive_time_min | 706 | 0 | 0.3 | 291.2 | 0.04% |
| icd_straight_km | 6 | 0 | 68.6 | 80.2 | 74.15% |
| icd_road_distance_km | 668 | 0 | 0.2 | 267.5 | 0.08% |
| icd_drive_time_hours | 12 | 0 | 1.285 | 3.88 | 51.80% |
| icd_drive_time_min | 723 | 0 | 0.3 | 232.8 | 0.11% |
| mmlp_road_distance_km | 690 | 0 | 0.25 | 277.5 | 0.07% |
| mmlp_drive_time_hours | 2 | 0 | 2.19 | 4.02 | 66.70% |
| mmlp_drive_time_min | 698 | 0 | 0.3 | 240.8 | 0.09% |
| air_cargo_straight_km | 6 | 0 | 76.9 | 217 | 82.80% |
| air_cargo_road_distance_km | 682 | 0 | 0.3 | 268.1 | 0.09% |
| air_cargo_drive_time_hours | 11 | 0 | 1.57 | 3.89 | 48.68% |
| air_cargo_drive_time_min | 698 | 0 | 0.3 | 233.8 | 0.10% |
| icp_road_distance_km | 708 | 0 | 0.5 | 103.3 | 0.06% |
| icp_drive_time_hours | 1 | 0 | 1.42 | 1.42 | 42.01% |
| icp_drive_time_min | 695 | 0 | 0.3 | 85.3 | 0.05% |
| toll_plaza_straight_km | 19 | 0 | 11.8 | 164.1 | 29.74% |
| toll_plaza_road_distance_km | 279 | 0 | 0.1 | 353.4 | 0.16% |
| toll_plaza_drive_time_hours | 26 | 0 | 0.24 | 2.63 | 27.20% |
| toll_plaza_drive_time_min | 715 | 0 | 0.3 | 157.7 | 0.53% |
| expressway_straight_km | 4 | 0 | 26.5 | 241.1 | 42.33% |
| expressway_road_distance_km | 674 | 0 | 0.3 | 384.4 | 0.09% |
| expressway_drive_time_hours | 4 | 0 | 0.595 | 5.66 | 44.40% |
| expressway_drive_time_min | 700 | 0 | 0.3 | 339.7 | 0.07% |

- `nearest_rail_station_name`: 40 rows changed; top transitions: UDALGURI → MAJBAT (2); PENDURTI → SIMHACHALAM (1); DHAMALGAON → RANGAPARA NORTH (1); DHAMALGAON → DEKARGAON (1); Jehanabad Court → Tehta (1)
- `nearest_freight_terminal_name`: 4 rows changed; top transitions: Lahli → Narela (2); Noli → Narela (1); Mappedu → Tondiarpet Marshalling Yard (1)
- `nearest_port_name`: 5 rows changed; top transitions: Cochin Port → Vizhinjam International Seaport (2); Gangavaram Port → Visakhapatnam Port (1); Hazira Port → Dahej Port (1); Deendayal Port (Kandla) → Mundra Port (1)
- `nearest_icd_name`: 6 rows changed; top transitions: ICD Kochi (Edappally) → ICD Thiruvananthapuram (4); ICD Melpakkam → ICD Chennai (Tondiarpet) (1); ICD Thiruvananthapuram → ICD Madurai (1)
- `nearest_air_cargo_name`: 6 rows changed; top transitions: Cochin International Airport (COK) → Trivandrum International Airport (TRV) (4); Jaipur International Airport (JAI) → Indira Gandhi International Airport (DEL) (2)
- `nearest_toll_plaza_name`: 13 rows changed; top transitions: THENI Toll Plaza → Old Toll Booth (2); CHANDRAPUR Toll Plaza → KONDAGAON Toll Plaza (1); GHAZIABAD Toll Plaza → SHAHDARA Toll Plaza (1); SHAHDARA Toll Plaza → GHAZIABAD Toll Plaza (1); JHAJJAR Toll Plaza → Mundka - Bakkarwala Toll Plaza - Towards IGI Airport (UER-2) (1)
- `nearest_expressway_name`: 4 rows changed; top transitions: Delhi-Dehradun Expressway → Delhi - Meerut Expressway (1); Trans Haryana Expressway → Delhi-Gurugram Expressway (1); Amritsar–Jamnagar Expressway → NE 752D (1); Kalanagar Flyover → Bandra Worli Sea Link Flyover (1)

### `nh_district_port_matrix.csv`

Rows: new 781, B0 781, matched 781, only new 0, only B0 0.

| column | changed rows | NaN new→B0 mismatches | median abs Δ | max abs Δ | median rel Δ |
|---|---|---|---|---|---|
| road_km_to_deendayal_(kandla) | 776 | 0 | 1.4 | 124.3 | 0.07% |
| drive_hours_to_mumbai | 1 | 0 | 0.07 | 0.07 | 11.11% |
| road_km_to_mumbai | 777 | 0 | 1.3 | 85.9 | 0.08% |
| road_km_to_jawaharlal_nehru_(jnpt/navi_mumbai) | 777 | 0 | 1.1 | 85.8 | 0.07% |
| road_km_to_mormugao | 776 | 0 | 1.7 | 92.2 | 0.09% |
| road_km_to_new_mangalore | 774 | 0 | 1.6 | 92.1 | 0.08% |
| drive_hours_to_cochin | 1 | 0 | 2.05 | 2.05 | 39.65% |
| road_km_to_cochin | 777 | 0 | 5.5 | 124.9 | 0.19% |
| road_km_to_v.o._chidambaranar_(tuticorin) | 776 | 0 | 5.9 | 106.6 | 0.19% |
| drive_hours_to_chennai | 1 | 0 | 4.86 | 4.86 | 94.00% |
| road_km_to_chennai | 775 | 0 | 4.1 | 334.7 | 0.13% |
| road_km_to_kamarajar_(ennore) | 775 | 0 | 4.1 | 60.6 | 0.13% |
| drive_hours_to_visakhapatnam | 1 | 0 | 0.36 | 0.36 | 46.75% |
| road_km_to_visakhapatnam | 776 | 0 | 1.2 | 23.8 | 0.08% |
| road_km_to_paradip | 774 | 0 | 1 | 27.4 | 0.06% |
| road_km_to_kolkata_(syama_prasad_mookerjee)_incl._haldia_dock_complex | 776 | 0 | 1.1 | 27.4 | 0.08% |

- No categorical/text changes.

### `district_freight_modal_split.csv`

Rows: new 781, B0 781, matched 781, only new 0, only B0 0.

| column | changed rows | NaN new→B0 mismatches | median abs Δ | max abs Δ | median rel Δ |
|---|---|---|---|---|---|
| pop_2011 | 1 | 0 | 146 | 146 | 0.01% |
| road_distance_km | 756 | 0 | 0.5 | 334.7 | 0.08% |
| road_drive_time_hours | 9 | 0 | 0.62 | 4.86 | 26.05% |
| road_cost_per_ton_inr | 756 | 0 | 1.8 | 1228 | 0.07% |
| rail_cost_per_ton_inr | 758 | 0 | 1 | 1123 | 0.04% |
| dfc_cost_per_ton_inr | 310 | 0 | 0.65 | 157.9 | 0.04% |
| modal_shift_savings_inr | 604 | 0 | 1.4 | 363.6 | 0.16% |
| modal_shift_savings_pct | 199 | 0 | 0.1 | 7.7 | 0.38% |

- `target_port`: 5 rows changed; top transitions: Cochin Port → Vizhinjam International Seaport (2); Gangavaram Port → Visakhapatnam Port (1); Hazira Port → Dahej Port (1); Deendayal Port (Kandla) → Mundra Port (1)

### `district_port_hinterland_catchment.csv`

Rows: new 781, B0 781, matched 781, only new 0, only B0 0.

| column | changed rows | NaN new→B0 mismatches | median abs Δ | max abs Δ | median rel Δ |
|---|---|---|---|---|---|
| pop_2011 | 4 | 0 | 4.47e+04 | 7.33e+05 | 3.17% |
| primary_port_probability | 4 | 0 | 11.9 | 68.8 | 31.35% |
| primary_port_drive_hours | 4 | 0 | 1.205 | 4.86 | 43.20% |
| secondary_port_probability | 4 | 0 | 3.3 | 24.2 | 46.92% |
| contestability_index | 4 | 0 | 0.152 | 0.93 | 46.74% |

- `is_island`: 4 rows changed; top transitions: False → True (4)
- `primary_port`: 4 rows changed; top transitions: <NA> → N/A (Island) (4)
- `hinterland_category`: 6 rows changed; top transitions: Isolated → Island / Isolated (4); Highly Contested / Split Hinterland → Dominant / Mildly Contested (1); Highly Contested / Split Hinterland → Captive Hinterland (1)

### `major_ports_isochrones.geojson`

Features: new 48, B0 48. Columns only new: ['capacity_notes', 'city', 'coverage_assumptions', 'coverage_method', 'feeder_time_hours', 'latitude', 'longitude', 'name', 'operator', 'reachable_node_count', 'snap_component_node_count', 'snap_feeder_km', 'source_url', 'state', 'status']; only B0: [].

Matched by hub and threshold: 24; only new 24; only B0 24.
Catchment area (km², EPSG:7755): total new 3,121,154 vs B0 1,929,441 (+61.8%); per feature median rel Δ +13.8%, min -51.7%, max +137144.4%.
- Features with |area Δ| > 10%: 18 — Paradip Port @4h -52%, Mormugao Port @4h -19%, V.O. Chidambaranar Port (Tuticorin) @8h -16%, Mormugao Port @8h -11%, Deendayal Port (Kandla) @8h +11%, Kolkata Port (Syama Prasad Mookerjee Port) incl. Haldia Dock Complex @8h +12%, V.O. Chidambaranar Port (Tuticorin) @4h +16%, New Mangalore Port @4h +30%
- Status values in new: {'ok': 48} (B0 has no status column)

### `mmlp_isochrones.geojson`

Features: new 60, B0 60. Columns only new: ['capacity_notes', 'city', 'coverage_assumptions', 'coverage_method', 'feeder_time_hours', 'latitude', 'longitude', 'name', 'operator', 'reachable_node_count', 'snap_component_node_count', 'snap_feeder_km', 'source_url', 'state', 'status']; only B0: [].

Matched by hub and threshold: 20; only new 40; only B0 40.
Catchment area (km², EPSG:7755): total new 1,890,313 vs B0 1,471,109 (+28.5%); per feature median rel Δ +12.8%, min -51.7%, max +64221.0%.
- Features with |area Δ| > 10%: 16 — MMLP Indore @4h -52%, MMLP Jammu @4h -40%, MMLP Vijayawada @4h -21%, MMLP Jalna @4h +10%, MMLP Hyderabad @4h +11%, MMLP Jogighopa @4h +12%, MMLP Sangrur @4h +13%, MMLP Coimbatore @4h +14%
- Status values in new: {'ok': 60} (B0 has no status column)

Parquet/CSV consistency (new summary): shapes (781, 52) / (781, 52); same columns True.
