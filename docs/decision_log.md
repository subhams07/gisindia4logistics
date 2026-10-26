# Decision log

This log records operator decisions taken during the 2026-10 national refresh and release work. For each decision it gives the reasoning and the checks that would show the decision was right. Larger decisions have their own ADR in [`docs/adr/`](adr/). Day-to-day progress is in [`claude_remote_handoff.md`](claude_remote_handoff.md).

Check status values:
- **Done**: the check was run and its evidence recorded.
- **Pending**: the check has not been run yet.
- **Open**: it cannot be checked with what the repository has.
- **Operator**: it needs the maintainer, for example a download from a site blocked from the build sandbox.

## Data and model decisions

| Date | Decision | Why | Checks that confirm it | Status |
|---|---|---|---|---|
| 2026-10-19 | **Gravity model α = 0.85, β = 1.65**, labelled uncalibrated | The repository has no observed district-to-port cargo flows, and fitting to port throughput would be circular (throughput is dominated by bulk coal and ore). These values are the long-standing defaults used by B0 and the API. | 1. The sensitivity table across an α/β grid exists in `docs/stage4_drift_linux-2026-10-18-run1.md` (**Done**). 2. Fit α/β to observed port-of-loading shares by district (e.g. CBIC/ICEGATE export data) and compare predicted primary ports for a sample of districts (**Open**: needs data). 3. Re-check whether the hinterland category counts are stable across the plausible α/β range (**Pending**). | Uncalibrated, disclosed |
| 2026-10-19 | **Isochrone catchments corrected**: hubs snap to the nearest highway component of at least 1,000 nodes within 150 km | Hubs had snapped to tiny road fragments, giving JNPT, Mumbai, Chennai, Visakhapatnam and six MMLPs (including Jammu) near-empty catchments. | 1. Outputs are identical for any floor between 225 and 289,458 nodes, so the result does not hinge on the exact number (**Done**, Opus review). 2. Exactly the 10 affected hubs moved; all others are unchanged (**Done**). 3. No island component reaches 1,000 nodes (**Done**: Andaman maximum 540). 4. **Operator** visual check: the JNPT and Jammu 4/8 h catchments look plausible against known road travel times (**Pending**). | Accepted as a modelling policy |
| 2026-10-19 | **Districts with population problems kept as is and disclosed** (NAZUL, MIRPUR, MUZAFFARABAD use `area_share` estimates) | The operator chose disclosure over a methodological change. | 1. Disclosed in the CHANGELOG and `producer_limitations.json` (**Done**). 2. Census 2011 did not enumerate MIRPUR and MUZAFFARABAD (PoK). Check whether their area share reduces the populations of the real J&K districts under the state-total scaling, and by how much (**Pending**). | Disclosed |
| 2026-10-20 | **Dhule/598 correction** (PR #8): the derived "Malegaon" was a duplicate Dhule; districts 781 → 780 | The source sub-district layer coded Dhule's four talukas as 598, which is Kerala's Alappuzha. Malegaon taluka belongs to Nashik. | 1. Maharashtra has 36 districts in the layer (**Done**, PR #8). 2. Dhule's population is no longer double-counted (**Done**, PR #8). 3. **Operator**: confirm against the official Maharashtra district list that no separate Malegaon district exists (**Pending**). | Merged |
| 2026-10-26 | **District identity and population allocation**: population fix with Stage 4 regeneration; ABDB code kept as the key with corrected "LGD" wording; `plot_villages` fix; location wins over source names; placeholder row excluded | See [ADR 007](adr/007-district-identity-and-population.md) | The full check list (A1–A9, B1–B5, C1–C4, D1–D9) is in ADR 007. | In progress |

## Engineering and release decisions

| Date | Decision | Why | Checks that confirm it | Status |
|---|---|---|---|---|
| 2026-10-17 | **Roles**: Opus 5.5 (medium) plans, designs and reviews every implementation; Sonnet writes code | Operator instruction. | Each implementation in the handoff log has a matching review entry and verdict (**Done** for all changes to date). | Standing rule |
| 2026-10-17 | **`main` is what users deploy**: work on a review branch and merge only on explicit operator instruction | Operator instruction. | Every merge to `main` (PRs #5, #6, #7, #8) followed an explicit instruction (**Done**). | Standing rule |
| 2026-10-18 | **National regeneration runs under a 12 GiB memory cap** (cgroup) | It fits the host (about 15 GiB, no swap) without disturbing it. | Run logs show `failcnt 0` and no OOM. Peaks are 0.67–0.77 GB (**Done** for runs 2–4). | Standing rule |
| 2026-10-19 | **Recurring CI stays off** (GitHub Actions billing problem) | Workflows failed for billing reasons, not because of the code. | 1. Before each merge, the full test suite and the national audit are run locally and the results recorded in the PR (**Done** for PR #5; PR #8 by its session). 2. Re-enable CI once billing is fixed (**Open**). | Active |
| 2026-10-19 | **Manifest must match the release code**: any edit to a file in `producer_manifest.json` `code_hashes` requires regenerating Stage 4 | The 1.1.0 version bump silently invalidated the run-2 manifest. | `validate_manifest(..., code_root='.')` is clean on `main` (**Done** 2026-10-26: run 4 validates). | Standing rule |
| 2026-10-19 | **Release 1.1.0** merged to `main` (PR #5); classifier `4 - Beta` | Uncalibrated gravity model and no CI, so "Production/Stable" overstated it. | Tests, the national audit and packaging checks passed at the release code (**Done**). | Merged |
| 2026-10-19 | **No PyPI release for now** | Operator decision; the Survey of India redistribution question is unresolved. | 1. No `v*` tag or GitHub release exists, since `publish-pypi.yml` fires on either (**Pending**: check before any tagging). 2. Consider making the workflow manual-only (**Open**). | Active |
| 2026-10-19 | **Positioned as a personal hobby project**; pull requests and outside contributions are not accepted | Operator decision. | 1. README, `CONTRIBUTING.md`, `SUPPORT.md`, `SECURITY.md` and `CODE_OF_CONDUCT.md` are consistent; the PR and feature-request templates are removed (**Done**, PR #7). 2. **Operator**: review GitHub repository interaction settings if unsolicited PRs arrive (**Open**). | Active |
| 2026-10-19 | **Package author**: Subham Sahu `<subhams07@gmail.com>` | The previous `info@gisindia4logistics.org` address is not available to the maintainer. | `pyproject.toml` and the tracked egg-info metadata updated; package tests pass (**Done**, PR #7). | Merged |
| 2026-10-26 | **Clear "data not found" error** when the data directory is missing (SDK, CLI, MCP), without touching Stage 4 closure files | Non-editable installs gave "An internal routing error occurred." or an empty result. | 1. 20 new tests and the full suite pass (**Done**: 3,531 passed). 2. The closure is unchanged (**Done**). 3. Independent Opus review (**Pending**). 4. Running `uvicorn server.app:app` directly without data still returns a generic error; this needs closure edits (**Open**). | In review |

## Open decisions (not yet taken)

| Topic | What is needed | Blocks |
|---|---|---|
| Survey of India redistribution posture | One of: written permission, a documented legal review, replacement data, or removal with fetch instructions (see `DATA_LICENSE.md`) | PyPI release, plugin or marketplace listing, hosted server |
| Official LGD district codes | The maintainer downloads the LGD district list (lgdirectory.gov.in) | `lgd_district_code` column; ADR 007 checks B1–B2 |
| Claude plugin / ChatGPT app | Go-ahead after the redistribution decision; a support email and privacy policy for listings | Marketplace work |
