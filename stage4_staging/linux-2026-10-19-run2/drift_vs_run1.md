## Drift: staged run vs B0

### `nh_district_travel_time_summary.csv`

Rows: new 781, B0 781, matched 781, only new 0, only B0 0.

| column | changed rows | NaN new→B0 mismatches | median abs Δ | max abs Δ | median rel Δ |
|---|---|---|---|---|---|
| (no numeric changes) | | | | | |

- No categorical/text changes.

### `nh_district_port_matrix.csv`

Rows: new 781, B0 781, matched 781, only new 0, only B0 0.

| column | changed rows | NaN new→B0 mismatches | median abs Δ | max abs Δ | median rel Δ |
|---|---|---|---|---|---|
| (no numeric changes) | | | | | |

- No categorical/text changes.

### `district_freight_modal_split.csv`

Rows: new 781, B0 781, matched 781, only new 0, only B0 0.

| column | changed rows | NaN new→B0 mismatches | median abs Δ | max abs Δ | median rel Δ |
|---|---|---|---|---|---|
| (no numeric changes) | | | | | |

- No categorical/text changes.

### `district_port_hinterland_catchment.csv`

Rows: new 781, B0 781, matched 781, only new 0, only B0 0.

| column | changed rows | NaN new→B0 mismatches | median abs Δ | max abs Δ | median rel Δ |
|---|---|---|---|---|---|
| primary_port_probability | 775 | 0 | 3.4 | 17.7 | 10.50% |
| primary_port_drive_hours | 73 | 0 | 5.21 | 6.98 | 35.85% |
| secondary_port_probability | 768 | 0 | 2.8 | 8.1 | 16.11% |
| secondary_port_drive_hours | 160 | 0 | 6.94 | 10.01 | 32.63% |
| contestability_index | 773 | 0 | 0.032 | 0.201 | 3.99% |

- `primary_port`: 73 rows changed; top transitions: Paradip Port → Syama Prasad Mookerjee Port (Kolkata/Haldia) (40); Paradip Port → Jawaharlal Nehru Port (JNPT) (7); Deendayal Port (Kandla) → Jawaharlal Nehru Port (JNPT) (6); Jawaharlal Nehru Port (JNPT) → Mumbai Port (5); Paradip Port → Deendayal Port (Kandla) (5)
- `secondary_port`: 160 rows changed; top transitions: Syama Prasad Mookerjee Port (Kolkata/Haldia) → Paradip Port (40); Paradip Port → Jawaharlal Nehru Port (JNPT) (35); Deendayal Port (Kandla) → Syama Prasad Mookerjee Port (Kolkata/Haldia) (16); Paradip Port → Mumbai Port (8); Jawaharlal Nehru Port (JNPT) → Deendayal Port (Kandla) (6)
- `hinterland_category`: 31 rows changed; top transitions: Highly Contested / Split Hinterland → Dominant / Mildly Contested (14); Dominant / Mildly Contested → Highly Contested / Split Hinterland (9); Dominant / Mildly Contested → Captive Hinterland (8)

### `major_ports_isochrones.geojson`

Features: new 48, B0 48. Columns only new: ['snap_component_node_count']; only B0: [].

Matched by hub and threshold: 48; only new 0; only B0 0.
Catchment area (km², EPSG:7755): total new 31,825,617 vs B0 19,129,613 (+66.4%); per feature median rel Δ +0.0%, min +0.0%, max +1203500.7%.
- Features with |area Δ| > 10%: 16 — Mumbai Port @4h +10897%, Chennai Port @4h +18318%, Jawaharlal Nehru Port (JNPT/Navi Mumbai) @4h +29405%, Visakhapatnam Port @4h +29844%, Mumbai Port @8h +85478%, Chennai Port @8h +104424%, Visakhapatnam Port @8h +123818%, Jawaharlal Nehru Port (JNPT/Navi Mumbai) @8h +136342%
- Status changes: 0

### `mmlp_isochrones.geojson`

Features: new 60, B0 60. Columns only new: ['snap_component_node_count']; only B0: [].

Matched by hub and threshold: 60; only new 0; only B0 0.
Catchment area (km², EPSG:7755): total new 23,459,727 vs B0 16,926,064 (+38.6%); per feature median rel Δ +0.0%, min +0.0%, max +448601.6%.
- Features with |area Δ| > 10%: 15 — MMLP Surat @4h +4697%, MMLP Chennai (Mappedu) @4h +4767%, MMLP Chennai (Mappedu) @8h +25898%, MMLP Surat @8h +26023%, MMLP Nashik @4h +29337%, MMLP Visakhapatnam @4h +29844%, MMLP Chennai (Mappedu) @12h +57529%, MMLP Anantapur @4h +64165%
- Status changes: 3: unsupported_threshold → ok (3)

Parquet/CSV consistency (new summary): shapes (781, 52) / (781, 52); same columns True.
