# ADR 004: Calibrated Spatial Toll Plaza Matching and Tariff Transparency

## Status
Accepted

## Context
Previous toll calculations relied on a synthetic heuristic (`distance / 65`), which estimated toll counts without identifying actual plazas or their locations. A naive spatial buffer query (e.g. 1–2 km) falsely captures toll plazas on intersecting bypasses, parallel expressways, or state highways. Furthermore, full official toll tariffs for all Indian plazas are not published in a single static open dataset.

## Decision
1. **Calibrated Two-Stage Spatial Matching**:
   - **Candidate Radius**: Initial search restricted to a **500 m maximum bounding buffer** in `EPSG:7755`.
   - **Multi-Tier Confidence Scoring**:
     - $\le 100\text{ m}$ perpendicular distance: **High Base Confidence** (50 pts).
     - $100–250\text{ m}$: **Medium Base Confidence** (35 pts).
     - $250–500\text{ m}$: **Low Base Confidence** (15 pts).
     - $> 500\text{ m}$: **Reject**.
   - **Attribute Refinement**:
     - Bonus for matching `nh_number` (+30 pts).
     - Bonus for route direction alignment (+10 pts).
     - Penalty for probable parallel roads (-30 pts).
2. **Route Order Projection**:
   Plazas are projected along the route LineString using `line.project(point)` and sorted sequentially from origin to destination. Nearby duplicate representations are clustered and deduplicated.
3. **Transparent Tariff Status**:
   - Every matched toll plaza explicitly states `tariff_status`:
     - `"official"`: Exact published NHAI/MoRTH tariff.
     - `"modelled"`: Standard vehicle-class baseline estimate (e.g. INR 340 for MAV_20T, INR 120 for LMV).
     - `"unknown"`.
   - Modelled tariffs are never misrepresented as official quotations.

## Consequences
- Accurate, route-ordered list of real toll plazas encountered along any corridor.
- Clear separation between spatial plaza matching (high accuracy) and tariff rate estimation (transparently modeled).
- False positives on parallel local highways are eliminated.
