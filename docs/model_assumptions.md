# Analytical model assumptions and limitations

This document describes what the service calculates. It is not a validation certificate, commercial quotation, or official transport model.

## 1. Nearest-facility proximity

`GET /api/v1/hubs/nearest` and the equivalent SDK/MCP operation calculate WGS84 geodesic straight-line distance between the query coordinate and facility coordinates.

They do not calculate road distance, border crossing feasibility, ferry access, operating hours, vehicle restrictions, or travel time.

## 2. National Highway routing

The route engine is a strategic accessibility model built from the committed National Highway network.

Current assumptions:

- network geometry is projected to EPSG:7755;
- modeled speeds are 90 km/h for motorway, 70 km/h for trunk, 55 km/h for primary, and 65 km/h for unclassified fallback edges;
- nearby disconnected nodes may be bridged at up to 350 m and modeled at 35 km/h;
- real network edges take precedence over synthetic bridges;
- origins and destinations are snapped to the principal connected component;
- off-network feeder access is modeled at 35 km/h;
- returned route distance sums the selected graph-path edge lengths plus feeder access;
- one-way restrictions, turn restrictions, live traffic, height/weight limits, closures, ferries, and local-road navigation are not modeled.

Toll count is currently estimated from average 65 km spacing. It is not calculated by intersecting the route with official toll-plaza records. The API returns a `toll_estimation_method` field to make this explicit.

Appropriate use: national and regional accessibility comparison, high-level logistics scenarios, and analytical screening.

Inappropriate use: dispatch, navigation, statutory route planning, invoicing, or safety-critical decisions.

## 3. Freight-cost scenarios

The freight endpoint compares modeled generalized cost per tonne for road, conventional rail, and—where eligible—Dedicated Freight Corridor movement.

Main assumptions include:

- configurable road line-haul, handling, average toll spacing, and payload;
- rail first-mile trucking, terminal handling, telescopic tariff, commercial speed, and yard detention;
- DFC eligibility by corridor-state membership, with configurable line-haul, handling, speed, and transfer delay;
- inventory holding cost expressed in INR per tonne-hour;
- requested Major Ports use the district-to-port highway matrix for distance and time;
- rail route length is approximated from road distance rather than a complete rail-network path.

Defaults are scenario parameters, not market quotations. Users should override them with dated, cited commercial assumptions and run sensitivity analysis.

## 4. Port hinterland model

The current Huff/Reilly-inspired endpoint computes utility from port throughput capacity and drive-time friction. It then assigns each district to the port with the highest utility.

Despite the `market_share` field name retained for API compatibility, this is a winner-take-all district assignment—not a fractional probability allocation. Results should be described as assigned hinterland share unless the implementation is upgraded to normalized probabilistic shares.

## 5. Population and accessibility

Current-district population fields combine official Census 2011 values with documented allocations for post-2011 administrative geography. Consult `data/demographic/district_population_estimates.csv` for the method and flags attached to each district.

Village accessibility tables generally use projected straight-line distance unless a field explicitly identifies NH graph road distance or drive time.

## 6. Boundary and source limitations

Administrative boundaries are indicative and not authoritative Survey of India boundary certifications. Source licenses, vintages, and redistribution conditions remain attached to each upstream dataset; see `catalog.yaml`, `docs/sources.md`, `DATA_LICENSE.md`, and `docs/legal_compliance.md`.
