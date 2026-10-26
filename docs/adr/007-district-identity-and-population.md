# ADR 007: District Identity, Village Assignment and Population Allocation

## Status
Accepted by the operator on 2026-10-26. Implementation is in progress on branch `claude/gisindia-remote-handoff-verify-veukfs`. The verification checks below are **pending** until the implementation, the Stage 4 regeneration (run 5) and the national audit are complete.

## Context
An Opus design review on 2026-10-26 found three linked problems:

1. **District codes are not all LGD.** The current district polygons (`data/administrative/india_districts_lgd.*`, Survey of India ABDB, 780 districts) carry codes that equal the LGD and Census 2011 codes for districts that existed in 2011. For about 143 districts created after 2011, and for Tripura's four districts, they use a different numbering.
   - Example: Devbhumi Dwarka is 656 in the polygons. Government sites keyed by LGD codes use 674 (secc.gov.in and wdcpmksy.dolr.gov.in, seen as search-result snippets only).
   - The village files (`data/analysis/*_village_access.csv`, from the SoI village product) carry real LGD codes, but some are stale or truncated for the newest districts.
   - Village district names contain legacy-font mojibake and typos, e.g. `DEVBHUMI DW>RKA`, `N>SHIK`, `DEVBHUMI DWARKAl`.
   - The README and other documents claimed every layer is "joined on official LGD codes". That is wrong for about 143 districts.
2. **District summaries were built from names.** `nearest_facility.py` grouped village rows by district name only, so one district could appear as several rows. Population was found with a fuzzy name match, and each variant row received the full district population.
3. **Population estimates had a cross-state code collision.** `allocate_population.village_counts()` counted villages by their source code across the whole country, then mapped the counts onto polygon codes. Because the two code schemes overlap, villages were credited to districts in other states:
   - polygon 762 (Mulugu, Telangana) received the villages of Sakti, Chhattisgarh;
   - polygon 766 (Tamulpur, Assam) received those of Mauganj, Madhya Pradesh.

   The 50/50 area/village blend then produced implausible figures: Mulugu 1,417,147; Tamulpur 1,388,011; Shahdara 2,820,107. `district_population_estimates.csv` is an input to the Stage 4 producers (gravity model, scorecards), so the error reaches the published analysis.

## Decisions

| ID | Decision | Alternatives considered |
|---|---|---|
| D-A | **Fix the population allocation and regenerate.** Villages are counted per current district by spatial position *within the same state*. Population estimates and all Stage 4 outputs are regenerated (run 5, 12 GiB cap). | Disclose only and fix later. |
| D-B | **Keep the ABDB polygon code as the project's district key and correct the "LGD" wording now.** A separate `lgd_district_code` column comes later, once the official LGD district list is available. | Recode to LGD now with about 59 districts unresolved; keep the current codes permanently and only fix the wording. |
| D-C | **Fix `scripts/analyze/plot_villages.py`** so it selects villages by their corrected district. It is a Stage 4 code-closure file, so Stage 4 is regenerated. | Rewrite the committed village boundary layers instead (no regeneration, but edits a source-derived layer). |
| D-D | **Location wins over the source district name.** Each village is assigned to the polygon containing its representative point, within its own state. A point that lands just outside every polygon goes to the nearest polygon in the same state, which must be within 2 km. The source values are kept in `source_district` / `source_district_code`. The `Disputed (Jharkhand)` placeholder row stays in the file, flagged with `exclude_from_district_stats`, but is left out of district statistics. | Keep the source names and fix only spelling; assign the placeholder to Sahibganj. |

API field descriptions that call `district_code` an "Official LGD district code" (`server/models/`) are corrected in the same change.

## Expected effects (from the design probe, to be confirmed)
- All 621,105 village and settlement rows are kept, and all 780 polygons receive at least one row, including Kushavati (Goa), which had none.
- About 182 villages change district relative to their source name: 100 move into Kushavati, 42 are in Chhattisgarh, and the rest are boundary-edge differences between the two SoI products.
- 134 points are assigned to the nearest polygon in their state. All of them are within 1 km (maximum 0.96 km).
- The national district summary goes from 817 to 816 rows (780 districts + 36 state rows), with no duplicate names.
- Village-level distances do not change: `dist_*` and `nearest_*` columns are untouched. The README's village medians are therefore unchanged.
- Population estimates change for districts whose village counts were wrong (about 232 had mismatched counts, of which 49 are "blended" and 13 "area_share"). Hinterland results, freight results and population-weighted figures can shift for those districts. The national total must not change.

## Verification checks

A check marked **operator** needs something only the operator can do. All others can be run in the repository.

### D-A Population allocation
| # | Check | Expected result | Status |
|---|---|---|---|
| A1 | National total of `pop_2011` in `district_population_estimates.csv` | Unchanged: 1,210,846,210 | Pending |
| A2 | Per-state totals before vs after | Unchanged for every state (the method preserves state totals) | Pending |
| A3 | Districts with method `census2011` (boundaries unchanged since 2011) | Values identical before and after | Pending |
| A4 | Spatial village count per polygon vs the count used by the allocator | Equal for all 780 districts; no polygon credited with another state's villages | Pending |
| A5 | List the 20 largest changes, and Mulugu, Tamulpur, Shahdara before and after | Each change traceable to a corrected village count; new values plausible relative to the parent 2011 district | Pending |
| A6 | **Operator:** compare a sample of post-2011 districts (Mulugu, Tamulpur, Shahdara, Charki Dadri, Kushavati) with state government district profiles that re-tabulate Census 2011 | Same order of magnitude; large gaps explained by the area/village blend | Pending |
| A7 | Stage 4 run 5: `validate_manifest`, `validate_national_profile`, `validate_limitations(require_coverage)` | No issues | Pending |
| A8 | Drift run 5 vs run 4 | Drive times, distances and isochrones unchanged (they do not use population); hinterland, freight and population-weighted changes only in districts whose population changed | Pending |
| A9 | National audit (`audit_all.py --national`, non-fast) and full test suite | 0 FAIL; 0 failed tests | Pending |

### D-B District code authority and wording
| # | Check | Expected result | Status |
|---|---|---|---|
| B1 | **Operator:** download the official LGD district list from lgdirectory.gov.in (unreachable from the sandbox) and compare it with the polygon codes | Pre-2011 districts match; about 143 newer districts differ; confirms the finding | Pending |
| B2 | Spot-check against that list: Devbhumi Dwarka, Mancherial, Yadadri Bhuvanagiri, Charki Dadri, Dhalai, Palghar | LGD 674, 684, 697, 701, 269 as found; Palghar's source code 601 (Thiruvananthapuram's code) explained | Pending |
| B3 | Find out what numbering ABDB uses for newer districts (ABDB metadata, or ask Survey of India) | Documented in `docs/sources.md` | Pending |
| B4 | `grep -rn "LGD" README.md docs catalog.yaml server/models AGENTS.md` | No remaining claim that the district *code* is LGD for all districts | Pending |
| B5 | No code path relies on district codes having LGD meaning (for example, joining to an external LGD-keyed dataset) | None found, or each listed for the later `lgd_district_code` work | Pending |

### D-C Village map tool
| # | Check | Expected result | Status |
|---|---|---|---|
| C1 | Map for a renamed or moved district: Goa/KUSHAVATI, Odisha/ANUGUL, Maharashtra/NASHIK | Returns villages; count equals the district's rows in the access table | Pending |
| C2 | Map for an unchanged district (Haryana/AMBALA, the README example) | Same output as before | Pending |
| C3 | Requests using an old source spelling (e.g. ANGUL) | Behaviour documented: either resolved by an alias or a clear not-found error | Pending |
| C4 | Manifest `code_hashes` after run 5 | Includes the new `plot_villages.py` hash and validates | Pending |

### D-D Spatial assignment and the placeholder row
| # | Check | Expected result | Status |
|---|---|---|---|
| D1 | Distance columns (`dist_*`, `nearest_*`) before vs after reassignment | Byte-identical | Pending |
| D2 | Every village row's (state, district_code, district) exists in the polygon layer | 100% | Pending |
| D3 | `nearest_in_state` assignments | All ≤ 2 km; maximum reported (about 0.96 km expected) | Pending |
| D4 | **Operator:** Goa notification creating Kushavati district (list of talukas) vs the 100 villages moved into it | The villages belong to Kushavati's talukas | Pending |
| D5 | The 42 Chhattisgarh moves | Explained by district formations newer than the village source | Pending |
| D6 | Visual check of 10 random boundary-edge moves on a map | Village point sits on or near the boundary between the two districts | Pending |
| D7 | Summary keys | Unique (state, district_code, district); 780 + 36 rows; no split names | Pending |
| D8 | Placeholder row | Flagged and absent from district and state statistics; state-row audit adjusted accordingly | Pending |
| D9 | Reversibility | `source_district` / `source_district_code` present for every row | Pending |

## Consequences
- District statistics become one row per real district, with code-based joins and no fuzzy matching.
- Published Stage 4 outputs change, so the drift is documented in the run-5 drift note.
- Until `lgd_district_code` exists, users joining external LGD-keyed data must use `source_district_code` on village rows, or a crosswalk, for post-2011 districts.
- Follow-up: obtain the official LGD district list and add `lgd_district_code` to the district layers and summaries (59 districts cannot be resolved from repository data alone).
