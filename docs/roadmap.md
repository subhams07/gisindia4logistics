# GIS4Logistics — Improvement Roadmap (Initiatives 1–6)

Status: PLANNED (not started). Written 2026-08-23 after three planning
rounds. Effort units: S = focused session (< half day), D = day-equivalent.

## Round history (how this plan was refined)

- **Round 1 — structure.** Defined goal, coarse tasks, effort, and ordering
  for each initiative. Identified the four hard constraints to respect:
  repo size, CI runner limits, RAM for routing, and the SoI public-flip gate.
- **Round 2 — technical depth.** Specified mechanisms: two-tier audit
  (fast/deep) for CI, two-tier population allocation (area-share vs
  village-exact), OSRM staged NH-first then full-India, freight-flow source
  list with validation anchors, Release-asset bundles instead of committed
  files. Added acceptance criteria per initiative.
- **Round 3 — stress test.** Found and fixed four plan defects: (a) full
  audit on every push would clone ~600 MB and run 10–15 min on a runner —
  split into push-time fast lane and weekly deep lane; (b) area-share
  population allocation misallocates dense urban carve-outs — added
  village-count-share blend and village-exact upgrade; (c) Zenodo DOI
  requires a public release — moved behind the SoI decision gate;
  (d) per-state bundles committed to git would double repo size — moved to
  GitHub Release assets. Added abort criteria and KPIs.

---

## Initiative 1 — CI gate + drift detection  (1 S + 1 S)

**Goal:** every push runs quality checks; data drift is detected weekly
without human vigilance.

**Design (round 2/3):**
- Split `scripts/audit/audit_all.py` into `--fast` (schema, counts vs
  baseline, catalog YAML parse, catalog-vs-disk; runs in <2 min) and
  `--deep` (adds geometry re-read validity, cross-layer joins,
  reproducibility recompute; the current full mode).
- `data/audit_baseline.json`: machine-readable snapshot of expected counts
  (states 36, districts 781, subdistricts 6,636, villages per state, hub
  row counts, analysis row counts). `audit_all.py --update-baseline`
  regenerates after intentional changes (documented in PR/commit message).
- `.github/workflows/ci.yml` — on push + PR: `--fast` + `yaml.safe_load`
  + `python -m py_compile scripts/**`. 
- `.github/workflows/audit-deep.yml` — weekly (cron) + `workflow_dispatch`:
  `--deep` with drift compare; opens an issue on failure.
- README badge; AGENTS.md updated ("CI is the enforcer; baseline refresh is
  a reviewed step").

**Acceptance:** green CI on HEAD; deliberate count change fails until
baseline refresh; weekly deep run <20 min on runner.
**Risk:** runner clone time (~600 MB) — measure; if >10 min, fast lane moves
to a slim manifest file instead of reading full data.
**Abort criteria:** none — this is pure upside.

## Initiative 2 — Population for post-2011 districts  (2 S + 1 S integration)

**Goal:** eliminate the ~140 NaN-population districts (all post-2011
districts incl. Telangana) from accessibility weighting; coverage 640 → 781.

**Tier 1 — geometric allocation (self-contained, do first):**
1. Build parentage: 2011 census district (640, from
   census2011_district_key_indicators + 2011 boundaries) → current child
   districts (781) via (a) `census2011_lgd_crosswalk.csv` name matches,
   (b) geometric intersection where names diverge, (c) hand-compiled LGD
   parentage table for the ~40 renamed/merged edge cases (Telangana split,
   AP reorganization 2022, Dadra & Daman merge, J&K 2019, etc.).
2. Allocate: `pop_2011_est(child) = pop_2011(parent) × area(parent ∩ child)
   / area(parent)`, computed in EPSG:7755.
3. **Urban carve-out correction (round 3):** where a child is mostly urban
   (village_area_share < 20% of child), blend area-share with
   village-count-share (50/50) to avoid dumping city population into rural
   remainder; flag method per row.
4. Validation: every parent's children sum back to parent ±0.5%; national
   total = 1,210,854,977 exactly; outliers (pop density > 20k/km² or
   < 50/km²) listed for manual review.

**Tier 2 — village-exact (upgrade, +1 S):** LGD village directory exports
(state-wise CSVs with both LGD and census-2011 village codes + census
population column) → exact sums per current district via `Vill_LGD` join.
Replaces estimates where census codes are available.

**Deliverables:** `data/demographic/district_population_estimates.csv`
(district_code, method ∈ {census2011, area_share, blended, village_exact},
pop_2011, flags); `nearest_facility.py` reads it as fallback; rerun all-India
analysis; audit checks added (sum preservation, coverage 781/781).
**Risk:** parentage errors → keep per-row provenance; spot-check 15 known
splits (Malegaon↔Dhule, Sangareddy↔Medak, etc.).
**Abort criteria:** if Tier 1 validation cannot reach sum-consistency for
>95% of parents, stop and reassess parentage table rather than shipping
estimates.

## Initiative 3 — Road-network travel time (OSRM)  (1a: 1–2 S; 3b: 2–3 D)

**Goal:** replace straight-line km with drive-time minutes for catchments.

**3a — NH-network pilot (small graph, immediate value):**
- Convert committed `india_nh_network.geojson` → OSM XML (ogr2osm) →
  `osrm-extract`/`osrm-contract` (graph <200 MB; runs on any laptop).
- Compute district-centroid → facility drive-time matrix (781 × ~250 hubs)
  with `osrm-table`. Commit the matrix as CSV.
- Sanity: drive-time ≥ straight-line/1.4 typical; Pune→Mumbai corridor
  lands 2.5–4 h; flag impossible pairs (islands).

**3b — full network (heavy):**
- Geofabrik `india-latest.osm.pbf` (~1.3 GB) → osrm car profile; prepare
  needs 8–16 GB RAM → run in WSL2/Docker locally, NOT on CI.
- Village representative points (543k) → nearest facility drive times via
  batched `osrm-table` (chunks of 5k) or `/match`+`/route` hybrid; expect
  hours of compute → run overnight, commit outputs only.
- Integrate: `nearest_facility.py --mode drive` selects distance column;
  district summaries gain `_min` variants.

**Acceptance (both):** travel-time column present for 100% of units; ratio
checks vs straight-line (median 1.3–2.5×); corridor spot-checks documented.
**Risks:** RAM unavailable → stay at 3a; Windows Docker friction → WSL2;
OSRM build pain → fallback Valhalla or GraphHopper.
**Abort criteria:** if 3b matrix errors >1% (unsnappable villages), cap at
3a + document coverage.

## Initiative 4 — Freight-flow / demand-side tables  (2–3 S, agent-assisted)

**Goal:** annual flow/throughput series enabling corridor analysis.

**Tables to compile (FY 2019-20 → 2024-25 where published):**
1. `data/freight/rail_freight_annual.csv` — revenue-earning freight by
   zone and commodity group (Indian Railways Year Book PDFs, GODL).
2. `data/freight/port_throughput_annual.csv` — major-port cargo by port
   and commodity (Ministry of Ports "Traffic Handled" / IPA annual, GODL).
3. `data/freight/road_indicators_annual.csv` — NHAI AADT/toll if public;
   else MoRTH Basic Road Statistics (network-km, registered vehicles) as
   context proxies — clearly labelled not-flows.

**Schema:** metric, fy, entity_type (zone/port/national), entity_code,
commodity_group, value, unit, source_url, license.
**Validation anchors (round 2):** rail FY23-24 ≈ 1,590 MT total; major
ports ≈ 820 MT; Paradip largest ~145 MT. Totals must reconcile ±2% to
headline PIB figures or the year is quarantined.
**Pattern:** reuse the GCT transcription playbook — agent drafts,
in-session validation gate, per-row source_url.
**Risk:** PDF tables rot (layout changes) — store page references.

## Initiative 5 — Adoption layer  (README 1 S; bundles 1 S; release 0.5 S)

- **README v2:** embed Pune demo map + a generated accessibility choropleth
  (state-level rail distance), no-network quickstart (`geopandas.read_file`
  on committed data only), dataset table with links, CI badge.
- **Per-state bundles:** `scripts/build/state_bundles.py` → one GPKG per
  state (districts, subdistricts, villages, hubs clipped, NH clipped,
  analysis summary) published as **GitHub Release assets** (round 3: NOT
  committed — avoids doubling repo size). Release tag `v1.0-data`.
- **Examples gallery:** three notebooks — planner (accessibility map),
  analyst (district comparison), developer (API of scripts).
- **Public flip + Zenodo DOI (round 3 gate):** send SoI the redistribution
  query letter (draft in docs), await ≥2 weeks or written reply → flip via
  `gh repo edit --visibility public` → Zenodo archive → DOI in README.
**Abort criteria:** SoI objects → keep private, drop DOI step, bundles stay
private-release assets.

## Initiative 6 — Remaining data debts  (≈3 S total)

- **6a Station zones (55% empty):** search data.gov.in zone-wise station
  resource + zone HQ list (17 zones); join via station code; if no clean
  source, derive zone from division prefix in internal codes where
  documented, else leave with caveat. (0.5 S)
- **6b 14 coordinate-less GCTs:** OSM Nominatim bulk geocode (1 req/s) +
  manual verification list; store `source_url=nominatim` + precision note.
  (0.5 S)
- **6c Station table refresh:** hunt a post-2020 open station list (IR
  official, data.gov.in); replace 2016 table only if join coverage ≥95%;
  else document. (1 S, may end "not available")
- **6d Subdistrict-level accessibility (quick win):** run
  nearest-facility on the 6,636 subdistrict centroids →
  `india_subdistrict_access_summary.csv`; reveals worst-served talukas.
  (1 S)

---

## Sequencing and gates

```
Phase A (lock + show):  1 CI  ─┬─ 6d taluka analysis ─┬─ 5 README v2
                            └─ 6b GCT geocoding      ┘
Phase B (complete analysis): 2 pop allocation → rerun all-India → 6a/6c
Phase C (decision-grade):   3a NH pilot → 3b full-network (gate: RAM)
Phase D (flows + public):   4 freight tables → 5 bundles → SoI gate → flip+DOI
```

Dependencies: 2 before analysis rerun; 3b after 3a validation; 5-flip after
4 (release should include flows); nothing blocks Phase A.

## KPIs

| KPI | Now | Target |
|---|---|---|
| Audit checks green in CI | manual only | every push, <10 min |
| Districts with population weight | 640/781 | 781/781 (Tier 1) |
| Analysis metric | straight-line km | + drive-time min (781 districts; 543k villages stretch) |
| Freight series | 0 | ≥2 tables × ≥5 FYs, totals reconciled |
| QGIS bundles | 0 | 27 state GPKGs as release assets |
| Citability | private repo | public + Zenodo DOI (gated) |

## Global risks

1. **Data rot** (SoI page moves, OSM schema drift) — weekly deep audit +
   baseline diff is the tripwire (Initiative 1).
2. **Licensing drift on public flip** — re-run legal review (docs/
   legal_compliance.md) before Phase D flip; station_categories community
   license is the weakest link; consider replacing with data.gov.in first.
3. **Scope creep in freight tables** — hard-cap at 3 tables, 6 FYs.
