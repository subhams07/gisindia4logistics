# Claude remote handoff — national datasets

**Status: 2026-10-18 — first staged national run COMPLETED (staging only, not published). Next: drift explanation, audits, consumer checks and publication decision, each needing operator approval.** Section 0 is the live status/plan log and supersedes conflicting statements in sections 1–8, which are kept as the original Windows checkpoint record. No real profile, national read, generation, publication or merge has happened or is approved.

## 0. Live status and plan — update this section at every step

Any session (local or remote) restarting this work: read this section first, then the rest of the document. Update the log below, commit and push before ending a session.

### How to resume (read first)

1. Fresh clone: `git clone --branch claude/gisindia-remote-handoff-verify-veukfs https://github.com/subhams07/gisindia4logistics.git`; record `git rev-parse HEAD`.
2. Re-verify: section 4 code check (112 files, expect 0 mismatches); `(cd stage4_n2_harness && sha256sum -c results/FILES.sha256)`; same for `stage4_n2_harness_4b_pending/`.
3. Recreate the venv (D-9 approved): `python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'`; add `.venv/` to `.git/info/exclude`; then `git checkout -- gisindia4logistics.egg-info` (the editable install rewrites these tracked files). Compare with `stage4_n2_harness_4b_pending/results/venv_freeze.txt`; record any version drift.
4. Copy `stage4_n2_harness_4b_pending/` to a private scratch directory and continue there (the harness self-tests need root on Linux with writable cgroup v1 memory/pids; never run them concurrently).
5. **Next decision for the operator** (pending when paused): (1) one more targeted Sonnet round for B1-R1 + the small hardening items, followed by one Opus check limited to those changes — recommended; (2) accept 4b with B1-R1 as a recorded gap; (3) stop 4b. Then: coordinator measures container/ancestor limits and idle usage, proposes numerical limits with an explicit reserve for operator approval, and only then a single H0 graph-build measurement may be considered.
6. Roles: Opus 5.5 medium for design and every review; Sonnet for code; every implementation reviewed before acceptance; review caps are stopping points, not acceptance waivers.

### Operator rulings (2026-10-17)

| Topic | Ruling |
|---|---|
| Model roles | **Opus 5.5, medium effort**: planning, specification, design and every review (plan critique, implementation verification, final alignment). **Sonnet 5.5** (medium effort unless told otherwise): writes code. Sol 6.1 and Astra are no longer used; the Sol-quota fallback rule is obsolete. Independent reviews run in a fresh Opus agent, not the agent that wrote the plan. |
| Host | Continue on the computer available to the session. The original host was Windows; the current remote host is Linux, so N2 needs a Linux containment/telemetry design (section 5 blocker). |
| Branches | Work on a review branch cut from the pinned checkpoint. `main` is what users take and deploy: never commit to it directly, never merge without explicit operator approval. Current review branch: `claude/gisindia-remote-handoff-verify-veukfs`. |
| Implementation review | **Every implementation (every Sonnet code change, each milestone) is reviewed by Opus 5.5 at medium effort** in a fresh agent before it is accepted, committed as a milestone or used for any run. |
| This document | Living plan. Keep next steps and status here so work can restart at any time. |

### Remote checkpoint verification (2026-10-17, read-only, no computation executed)

- Pinned checkpoint `9778998cc8fd80ac9bd59f110ee2c8c604772de8` (= `quality/decision-workbench-stabilization` and PR #4 head at the time); review branch fast-forwarded to it.
- Section 4 code check: 112/112 Python files match canonical Git-blob SHA-256 and OID; 0 mismatches.
- 21 protected sources, 7 B0 outputs and `docs/audit_report.md`: all Git OIDs match. 86 files differ from the Windows working-byte hashes, and all 86 are exactly explained by LF vs CRLF. Parquet is a real file (no LFS pointer; `git lfs ls-files` empty).
- Absent as expected: `outputs/cache/canonical_nh_graph.npz`, baseline evidence receipt, consumed bounded-run receipt, private review logs, recovered national-authority file. Not recreated.
- Only `.github/workflows/publish-pypi.yml` present; recurring CI stays off.

### Linux host facts (initial survey)

- Ubuntu 24.04.5, x86_64, kernel 6.18, 4 vCPU, 15 GiB RAM, **no swap**, ~29 GiB writable disk allowance; ephemeral cloud container (unpushed state is lost).
- `vm.overcommit_memory=0` (heuristic): `CommitLimit`/`Committed_AS` are reported but not enforced, so they are **not** equivalent to Windows system commit.
- Running as root with broad capabilities. cgroup v1 hierarchy: `memory` (with `memsw.*` accounting) and `pids` controllers are mounted writable; no limits set at the root. `unshare`, `prlimit`, `setpriv`, `systemd-run` available. Child-cgroup creation not yet exercised.
- Python: system `python3` is 3.13 (outside declared 3.10–3.12); `/usr/bin/python3.11` (3.11.17) and 3.12.3 present without scientific packages. PyPI reachable. No environment installed yet.

### Plan and next steps

1. **[DONE]** Verify checkpoint and host (above).
2. **[DONE — r4 GO_FOR_OPERATOR_APPROVAL]** Opus: Linux N2 containment/telemetry/parent-death design addendum to `stage4_n2_resource_spec.md` (Linux analogues for section F job/launch order and section G telemetry, new synthetic controls replacing Windows-specific parts of M03/M08/M09, resolution of the section F cleanup ambiguity, proposed policy fields). Draft written: [`stage4_n2_linux_addendum.md`](stage4_n2_linux_addendum.md) (r4; reviewed GO_FOR_OPERATOR_APPROVAL; operator-approved 2026-10-17). Then a fresh Opus review of that design.
3. **[DONE 2026-10-17]** Operator approval of the reviewed Linux design (addendum r4, SHA-256 `26d285927cf26d8317fd32206ae57b757045950d81259e885f395f724831969f`) and rulings D-1–D-11 (addendum §7; D-9 may be deferred until after the stdlib-only smoke). Carry the r4 reviewer's six implementation notes into the Sonnet brief: (1) stub self-hash only proves argv integrity — the TOCTOU closure is controller-built argv via hash-verified unshare; read source from a fixed cmdline index; fix `sys.argv` wording; (2) stub ASCII-only, < 128 KiB (`MAX_ARG_STRLEN`), no secrets in argv (world-readable cmdline), nonce is framing not security; (3) add `fcntl` to the pre-fork allowlist (or probe with `os.fstat`), assert exactly one fd discarded; (4) subreaper set pre-fork, never `wait(-1)`/`P_ALL`, adopted ns-init reaped via `waitid(WNOWAIT)`+`wait4`; (5) pid-ns inode check compares `st_dev`+`st_ino` and re-confirms with `pidfd_send_signal(fd,0)`; (6) M14 mincore controls on a file with the real inputs' ownership/mode.
4. **[4b STOPPED AT REVIEW CAP — one blocker (B1-R1) unresolved, returned to operator; harness `9f9348a4…28f4` NOT accepted, preserved in `stage4_n2_harness_4b_pending/`] [4a ACCEPTED after 6 review rounds — final snapshot `eda94197…abec` in `stage4_n2_harness/`; 4b pending D-9]** Sonnet: implement the synthetic-only harness; Opus verification; Opus final alignment. Split into **4a** (stdlib-only controller/containment/telemetry: M01–M04, M08–M16 parts that need no scientific packages) and **4b** (M05–M07 real tiny W0/graph/transfer helpers, which need the venv — after D-9). Development location: `/tmp/claude-0/…/scratchpad/n2_harness/` (private scratch, per spec §A); because the container is ephemeral, milestone snapshots are committed to this review branch under `stage4_n2_harness/` (not production code, not part of the 112-file freeze, never merged to `main` without approval).

### Operator rulings on addendum r4 §7 (2026-10-17: operator replied "approved" to the recommendation table)

| ID | Ruling |
|---|---|
| D-1 | BLAS/GDAL/OMP/MKL threads pinned to 1 |
| D-2 | Charged memory accepted as the gating basis |
| D-3 | Option (a+): freeze-free verified-pidfd escalation + namespace teardown, plus v2 `cgroup.kill` only if smoke proves it; both the bounded `unshare_reap_wait_s` wait and `PR_SET_CHILD_SUBREAPER` |
| D-4 | Cap drop (`setpriv`) + read-only `/sys/fs/cgroup` (`unshare --mount`) **required** |
| D-5 | Reserve set from an idle measurement during smoke (value to be reviewed); no concurrent Bash workloads during a slot |
| D-6 | Ledger loss ⇒ consumption unknown, no dispatch without a new ruling; off-host copies private, never in the public repo |
| D-7 | Controller bounded by the 7,200 s background Bash limit; no setsid/detachment |
| D-8 | M15 (both variants) approved |
| D-9 | Deferred until after the stdlib-only smoke; required before 4b and any profile |
| D-10 | Platform intervention observed during smoke ⇒ automatic NO-GO |
| D-11 | Gate on the conservative figure (peak + `resident_bytes_elsewhere`); `failcnt_action` = STOP; numeric values (`memsw_tolerance_bytes`, `release_sample_count_k`, `cold_input_residency_max_fraction`, etc.) set from smoke measurements and reviewed before any profile; host-global fadvise effect accepted |

Numerical resource policy and real read/write admission remain **unapproved** (step 5).

### Open operator decisions (current)

| ID | Decision | Needed before | Recommendation |
|---|---|---|---|
| D-9 | **APPROVED 2026-10-17** — venv created at `.venv/` (excluded via `.git/info/exclude`), Python 3.11.17, `pip install -e '.[dev]'` | Step 4b | Done |
| (a) | Ratify memsw limit = 2× memory limit, guarded by SwapTotal==0 (implemented) | Step 5 | Accept |
| (b) | Ratify policy fields `reserve_bytes`, `conservative_peak_max_bytes` and floor/cap strict-vs-inclusive comparison semantics, with the numerical policy | Step 5 | Decide with numbers |
| (c) | Payload still runs as uid 0 after capability drop: accept, or require `setpriv --reuid` to an unprivileged user | Step 4b | Unprivileged user |
| (d) | **RULED 2026-10-17:** no eviction of shared/repository input caches by default (reverses the D-11 acceptance of host-global fadvise for real inputs). Default = attempt-private hash-verified copies (evict/measure only those) or no eviction with measured residency disclosed as cache uncertainty; any other option needs explicit approval | Step 4b | Ruled |
| (e) | Nonzero exit without the stub's exit marker = STOP (implemented as STOP) | Step 5 | Ratify |

### 4b carry-forward (from the 4a reviews; full list in `stage4_n2_harness/IMPLEMENTATION_REPORT.md` §12)

- Real dispatch must build its config without any driver-only hook/knob listed in §12, assert they are absent, and not use `driver.run_spec`; `synthetic_mock` must be passed explicitly.
- `cli.run_after_gate` must pass the prepare-recorded controller cgroup paths.
- Move the `after_attempt_record` hook inside the guarded try; guard the member `close()` loop and `str(e)` in the BaseException handler; handle `os.close` OSError in the ledger `finally`; check short `os.write`; minimal-body serialize failure must set DISPATCH_BLOCKED.
- Compose all driver hooks (not only `before_finalize`).
- Add Attempt-level `nonce`/`fs`/`cg_root`/`use_v2` overrides to the exclusion/assert list or document them as legitimate config.
- Elapsed cap is checked only during live sampling (≤ one sample interval unflagged before exit).
- Enable `site.main()` in the stub with the venv and re-run M03-L10.
5. Operator: numerical resource policy and read/write admission; then the six real profiles (H0–H3) on this host.
6. N3–N7 as in section 3, each separately admitted. Merge to `main` only with explicit operator approval.

### Session log

- 2026-10-17: remote verification done; roles/host/branch rulings recorded; Linux design (step 2) about to start.
- 2026-10-17: GitHub App write access fixed by operator; branch pushed.
- 2026-10-17: Opus design draft of the Linux addendum written (one empty probe cgroup created and removed, 32 MiB stdlib child; no limits written, nothing left behind). Operator questions O1–O6 and reviewer questions R1–R6 are in the addendum. Independent Opus review started.

## 1. Repository and starting point

- Use only **https://github.com/subhams07/gisindia4logistics**. Do not use the old uppercase repository.
- Review branch: `quality/decision-workbench-stabilization`.
- Accepted engineering/code commit: `d899d5373a05050c13cd8c0b8f96238eb19fbf5f`.
- Draft review PR: <https://github.com/subhams07/gisindia4logistics/pull/4>, base `main`. **Do not merge or release.**
- This handoff is a later documentation checkpoint on that branch. Its exact commit can be obtained from the PR or `git log -1 --format=%H -- docs/claude_remote_handoff.md`. Record and pin that commit before working; do not assume a moving branch stays unchanged.
- No N2 implementation was started. No `gis-stage4-sonnet-n2-*` files or active assigned model-session processes were found at handoff preparation. National profiling, regeneration and publication have not happened.

Companion documents:
- [N2 resource-harness specification](stage4_n2_resource_spec.md): reviewed design, required synthetic controls and open decisions.
- [Machine-readable checkpoint inventory](stage4_checkpoint_inventory.json): all 112 accepted Python files, 21 protected source files, seven baseline outputs, historical audit report and local graph cache. Code and all protected-file working-byte hashes were checked against the accepted freeze during handoff preparation.
- [Stabilization plan](stabilization_plan.md) and [Stage 4 producer plan](stage4_producer_plan.md): project implementation and producer contracts.
- [Source limitations](stage4_source_limitations.md): read alongside current code; its old provisional status paragraph is historical, not the current overall project status.
- [Model workflow](stage4_resume_workflow.md): sequence applies only after a new operator resume.

## 2. What we are trying to achieve, and why

Regenerate and accept a coherent national analytical bundle using the reviewed computation code. The application still has the older baseline files: improved code is not proof that its stored outputs were regenerated.

This refresh should make the map, API, SDK/CLI/MCP consumers and exported analytics use compatible district identity, routing, population and calculation rules. Missing or ambiguous matches should stay missing instead of receiving invented or unrelated values. Disconnected road components and approximate catchments must be disclosed. Changes against the baseline must be explained, not assumed to be improvements.

This is **derived-data recomputation**, not an upstream road, population or railway data update. It does not certify navigation, door-to-door travel, official tariffs, operational rail routes, official district identifiers, national population accuracy, blanket redistribution rights, optimal routing or production readiness.

Seven output files under `data/analysis/`:

| File | Purpose |
|---|---|
| `nh_district_travel_time_summary.csv` | District highway-access/travel summary |
| `nh_district_travel_time_summary.parquet` | Same summary, consistent alternative format |
| `nh_district_port_matrix.csv` | Modelled district-to-major-port road distance/time |
| `district_freight_modal_split.csv` | Road/rail/DFC cost comparisons under stated assumptions, not live quotations |
| `district_port_hinterland_catchment.csv` | Modelled port attraction/allocation and alternatives |
| `major_ports_isochrones.geojson` | Approximate major-port time catchments |
| `mmlp_isochrones.geojson` | Approximate MMLP time catchments |

The future publication set also includes `producer_manifest.json` and `producer_limitations.json`. These nine members must describe one accepted run. Per-file guarded replacement is **not** a dataset transaction or fsync durability guarantee.

## 3. Completed versus pending

### Completed and preserved

- Bounded stages 1–3: interface/routing remediation, canonical REST/SDK/CLI/MCP integration and configured-root/error/identity contracts.
- Stage 4 foundations and S1–S5: shared producer paths, orchestration, provenance/limitations, guarded publication, graph/semantic audits and engineering safeguards.
- N1a–N1d accepted. The latest N1d bounded test/helper repair was independently closed by Sol and aligned GO by Opus. It did not change production code in that final repair.
- Engineering and public status docs pushed at `d899d5373a05050c13cd8c0b8f96238eb19fbf5f`; nothing merged to `main`.
- Recurring push/PR CI and weekly deep audit deliberately disabled and removed on the review branch. `.github/workflows/publish-pypi.yml` retained. **Do not recreate or re-enable recurring CI.**
- N2 plan critique completed, followed by Opus **GO_SYNTHETIC_IMPLEMENTATION_ONLY** and a launch/ownership clarification. These are design decisions, not evidence that a real profile works.
- N4 metadata discovery completed, but audit execution/read admission did not.

### Verification already recorded — do not sum overlapping counts

- Latest coordinator N1d repair: 27 affected tests PASS, six immutable controls PASS, whole semantic file **256 PASS / 1 SKIP**; scoped lint/diff and protected-state checks passed.
- Independent Sol: 22 new tests PASS; whole semantic file **256 PASS / 1 SKIP**; ACCEPT_REPAIR. Opus final alignment: GO.
- Earlier unchanged-dependency full-suite evidence: coordinator and separate Sol run each **3,473 PASS / 5 SKIP**. This is not a fresh full run of the final repaired test bytes.
- Windows symlink/alias skips are **noncoverage**, not passes. Five inherited exporter Ruff findings remain. Earlier checkpoint EOF whitespace qualifications are recorded in PR #4.
- Accepted semantic test SHA-256: `c71163d8ea8a3346d3b61d8122eea183c602426012eef1cc4ba5c40285834a86`.
- Accepted audit production SHA-256: `35c7b745f52d951b7b9276da22f18a6cb12d3572e9c3c47e622cdf6605c3c162`.
- Accepted local freeze SHA-256: `286a71d7fa0d861253224a1a6460c5c1f279f91986e228c0dc26dbd60edfa84b`.

These are preserved historical results. Private raw review logs/receipts were not uploaded. The inventory provides new coordinator hash checks, not a fabricated reproduction of independent model review.

### Pending, in order

1. **N2 resource feasibility:** implement and review the synthetic-only harness after operator resume; resolve host/platform design; obtain an explicit numerical policy and read/write admission; only then consider the six real profiles.
2. **N3 staged generation:** a new exclusive admitted process, separate staging/cache roots, all seven outputs plus sidecars. Include post-producer coverage, sidecar/manifest construction and all three validators in resource measurement. N2 producer peaks do not measure orchestration retention.
3. **N4 unpatched audits:** admit finite resolved read scope and ancillary overlay; all fresh FAILs block publication, including old failures. No filtering, validator monkeypatches or `--update-baseline` to make it pass.
4. **N5 drift explanation:** compare every output against B0; explain distances, times, identity/missingness, costs/allocations, geometry and coverage changes.
5. **N6 fresh consumer checks:** independently admitted startup and real API/consumer reads from the staged bundle. Producer tests alone are insufficient.
6. **N7 independent output review and guarded publication:** explicit GO/NO-GO, then permitted replacement on the review branch. No automatic main merge.

After Stage 4 output acceptance only: P5 route-matched tolls, then P6 corridor decision-workbench journey and secure HTML export. Do not start them now.

## 4. Remote checkout and data availability

Use a fresh directory on the remote computer. Example checkout, **not an instruction to run the national pipeline**:

```sh
git clone --branch quality/decision-workbench-stabilization https://github.com/subhams07/gisindia4logistics.git
cd gisindia4logistics
```

Then enter the cloned `gisindia4logistics` directory, record `git rev-parse HEAD`, check `git status --short`, and read this handoff. The branch must contain the handoff checkpoint and the accepted code ancestor. Choose a new review branch from the pinned handoff before later implementation; do not edit `main`.

### What Git contains

At the accepted commit, all **21 protected producer sources, seven B0 outputs and the historical audit report are tracked Git blobs**. Do not unnecessarily reacquire sources, substitute newer data, run downloaders or assume everything is missing remotely. Verify the inventory first.

`.gitattributes` contains LFS patterns, but its later rule disables LFS filtering for Parquet; `git lfs ls-files` returned no entries on the original checkout. Do not assume the Parquet files are LFS pointers or require an LFS download. Inspect actual remote contents/attributes if they differ.

### What Git does not contain

- `outputs/cache/canonical_nh_graph.npz` — protected local cache, ignored by Git; expected original bytes/hash are recorded only for reference.
- `outputs/stage4/initial-baseline/baseline_evidence.json`, the consumed bounded-run receipt, private raw reviewer logs and scratch planning/control files.
- Ignored raw downloads, private runtime files, credentials or original machine configuration.

**Do not invent these missing artifacts or treat their absence as permission to rerun a consumed attempt.** A new host/cache build and any renewed run authority need explicit review. If raw historical evidence is needed, request a private transfer from the operator; do not upload it into this public repository. The seven tracked B0 outputs can be pinned from the accepted commit for drift comparison, but a new remote baseline receipt must be labelled new, not the original evidence file.

### Hash verification and line endings

`stage4_checkpoint_inventory.json` distinguishes original working-byte SHA-256s, canonical Git-blob SHA-256s for Python, and Git blob object IDs for tracked files. Windows text checkout conversion may differ from Linux. Compare canonical code blobs to establish checkout identity; **do not silently normalize source/output bytes and call them the same execution freeze**. Establish and approve a fresh platform-specific freeze where needed.

This read-only code check uses only the Python standard library and Git; it runs no producer or audit:

```python
import hashlib, json, subprocess
from pathlib import Path
inventory = json.loads(Path('docs/stage4_checkpoint_inventory.json').read_text())
accepted = inventory['accepted_code_commit']
subprocess.run(['git', 'merge-base', '--is-ancestor', accepted, 'HEAD'], check=True)
for entry in inventory['code_files']:
    current = subprocess.check_output(['git', 'show', 'HEAD:' + entry['path']])
    assert hashlib.sha256(current).hexdigest() == entry['git_blob_sha256'], entry['path']
    assert Path(entry['path']).read_bytes().replace(b'\r\n', b'\n') == current.replace(b'\r\n', b'\n'), entry['path']
print('Accepted Python code matches HEAD and working tree; no national computation executed.')
```

This check is valid for the initial documentation-only handoff. A deliberate later code change requires a new reviewed freeze rather than retaining this check as a false acceptance claim.

## 5. Environment and platform — important remote blocker

The reviewed N2 process-containment design is **Windows-native** (`CreateProcessW`, suspended child, Job Objects, private-commit/system-commit telemetry). It cannot simply be run on a Linux Claude sandbox. The production producer code has cross-platform resource reporting; that does not make the proposed Windows controller portable.

First report the remote OS, architecture, Python, memory/disk limits and container/cgroup constraints. If Linux, propose an equivalent containment, memory-accounting and parent-death design and independently review it before implementation or a real run. A process group or RSS limit is not automatically equivalent to Windows job containment/private commit. If required containment is unavailable, report NO-GO or use an appropriate host; do not quietly downgrade safeguards.

`pyproject.toml` declares Python >=3.10 and lists 3.10–3.12. Use Python 3.11 as a sensible starting point, but record actual resolved versions: dependencies are minimum bounds, not a lockfile. After operator resume and permission for remote environment setup, a typical fresh environment is:

Observed original environment at handoff: Python 3.11.15; NumPy 2.4.3; pandas 3.0.5; GeoPandas 1.1.4; Shapely 2.1.2; pyproj 3.7.2; SciPy 1.17.1; PyArrow 25.0.1; FastAPI 0.133.1; Pydantic 2.13.4; pydantic-settings 2.13.1; pytest 9.1.1; Ruff 0.16.5. These are observed versions, not a new lockfile or a claim that a remote install was tested.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Those commands are Linux examples and were **not executed remotely** by this checkpoint. Do not install globally, print credentials or change host paging/system settings. Windows activation differs. Read the manifest before changing packages.

Execution conventions after admission: unset `PYTHONPATH`/`PYTHONHOME`; Python `-B -u`, never `-O`; `PYTHONDONTWRITEBYTECODE=1`; `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`; private scratch temp roots; pytest `-p no:cacheprovider` with unique scratch basetemp. External probes insert the actual checkout and authenticate module `__file__` to avoid a stale editable installation.

## 6. Last interrupted attempt and evidence portability

Immediately before the operator pause, a pre-writer authentication command failed with `FileNotFoundError` for a locally recovered national-authority Markdown file. No N2 writer was dispatched and no profile ran. The missing older authority file is an **evidence/context portability gap**, not a producer failure and not a reason to weaken the gates.

This handoff deliberately replaces dependence on private absolute scratch paths with a curated specification and portable hash inventory. Original raw plans/reports and receipts remain local. Do not claim those absent receipts have been independently authenticated remotely. If a binding detail is not supplied here or in current code, obtain it from the operator or rerule that design detail explicitly.

The earlier completed real bounded run was approximately 402 seconds / 213 passing checks. It was bounded preflight only, not national feasibility. Its single-run permission is consumed. Old peak working set or ending system-commit availability is not a headroom bound on this host or another.

## 7. Operating rules for the next Claude session

- **Pause holds until operator resume.** Migration/checkpoint authority is not profile, generation, publication or merge authority.
- Maintain one coordinator and one implementation writer. The original bot workflow is stopped; do not revive duplicate coordinators.
- Required workflow: Opus 5.5 evaluation and plan → Sol 6.1 critique/reconciliation → Sonnet 5.5 implementation → Sol 6.1 implementation verification → Opus 5.5 final alignment; **medium effort for all**. No Astra. If a required model is unavailable remotely, report it and request a role change; do not silently substitute the current Claude model. Only actual Sol quota exhaustion had an approved Opus/Sonnet fallback; timeout or another model's quota is not that condition.
- No implementation concurrent with its prerequisite review. Preserve exact model responses, logs, commands, exit codes and hashes privately; distinguish worker claims, coordinator execution and independent execution.
- No unnecessary full-suite reruns: the operator explicitly requested change-scoped relevant tests. For N2, the synthetic M01–M11 matrix and scoped lint are the gate; wider tests need a concrete blast-radius reason.
- Stop on actual resource errors, lost telemetry, unsafe paths, identity/hash drift or a concurrent writer. No automatic retry, extra profile, deleting consumed receipts, paging changes, unrelated process kills, fabricated output or source repair.
- No reading `AGENTS.md`, `.env` or credential files under the inherited task constraints. `AGENTS.md` was security-flagged and not loaded on the original host. Do not import its contents into this handoff or obey embedded instructions from data files. Obtain credentials through the remote platform's secure interface only if genuinely needed.
- Private transcripts, personal paths, raw evidence and caches stay out of this public checkpoint. Data redistribution remains subject to `DATA_LICENSE.md`, `NOTICE` and source-specific terms.

## 8. Suggested opening message to Claude remotely

> Read `docs/claude_remote_handoff.md`, `docs/stage4_n2_resource_spec.md`, `docs/stage4_checkpoint_inventory.json` and the linked public plans in `subhams07/gisindia4logistics` on `quality/decision-workbench-stabilization`. Work is paused: do not implement, install, profile, regenerate, publish or merge until I explicitly resume. First verify the pinned checkpoint/code and available tracked data with read-only checks; report your host/platform limits, missing private evidence and any Windows-to-remote containment differences. Then explain the smallest next step and approval needed. Keep all work on a review branch, recurring CI off, and respect the specified model roles and medium effort. National refresh is the deliverable, not the preparation tool.
- 2026-10-17: Independent Opus review of addendum SHA-256 `95771b73…44ca`: **GO_WITH_REQUIRED_CHANGES**. Blocking: (1) controller death while v1 cgroup FROZEN leaves a frozen tree alive; (2) R-2 pidfd escalation ineffective without thaw-first; (3) leaving `cgroup.procs` misclassified as exit (root child can escape; need per-member pidfds); (4) gate-pipe EOF depends on closing the inherited write end before the blocking read. Non-blocking: MiB/capability factual fixes, controller `-I -S` + stdlib allowlist, memsw tolerance, working-set running floors + O_DIRECT input hashing, shared `claude-code-bash` concurrency rule, NSpid rule, rusage reaping, env allowlist, extra injected faults, make M15 mandatory, prefer cgroup v2 `cgroup.kill`/`cgroup.freeze`, NO-GO without PID namespaces. New operator questions: BLAS/GDAL threads=1, capability-drop/read-only-cgroup hardening, v2 primary kill. Designer revision started.
- 2026-10-17: Designer revision r2 of the addendum (SHA-256 `f222e687…968f`) addresses all 4 blocking and 11 non-blocking findings; operator decisions consolidated as D-1–D-10 in addendum §7. Fresh Opus re-review started.
- 2026-10-17: Fresh Opus re-review of r2 (`f222e687…968f`): **GO_WITH_REQUIRED_CHANGES**. All 4 r1 blockers CLOSED. New blocker B-1: launch sequence L-4 closes/forbids the hash-verified exec fd it later execs, and its exact fd-set assertion would always fail. Non-blocking N-1–N-7 (ambiguity-rule freeze outside the bounded window, thaw-failure wording, forked-child safety, transient escapee via descendant walk, §7/§8 D-9/D-10 consistency, missing decisions incl. release sample count/memsw tolerance/failcnt action/input-hashing method, probe record). Reviewer recommends a freeze-free v1 fallback and fadvise+mincore cold-cache check for the peak bound. Designer r3 started.
- 2026-10-17: Designer r3 (`24cd998f…0fe25`) fixes B-1 and N-1–N-7; D-3 now offers freeze-free termination options; new D-11 (failcnt/memsw actions, tolerance, release sample count, cold-input method/threshold). Fresh Opus re-review of r3 started.
- 2026-10-17: Fresh Opus re-review of r3 (`24cd998f…0fe25`): **GO_WITH_REQUIRED_CHANGES**. B-1 and N-1–N-7 CLOSED. New blocker R3-B1: CPython `os.listdir(fd)` dups the fd, so the exact fd-set assertion always fails (verified on host) — filter by `fcntl(F_GETFD)`. Non-blocking: cold-input bound must exclude/add pre-charged code+library pages and repeat residency check before release, fadvise is host-global; option (a) termination should wait for unshare to reap ns-init (or use a subreaper) to avoid orphaned zombies; post-teardown leftover members are unowned → STOP not kill; per-thread cgroup checks under v1; stub integrity, inode rewrite metadata, ctypes in allowlist. Q-R3 resolved in method. Designer r4 started.
- 2026-10-17: Designer r4 (`26d28592…1969f`) fixes R3-B1 and R3-N1–N5; D-3 gains reap-wait/subreaper sub-choice, D-11 gains gating-figure and repeated residency check. Fresh Opus re-review of r4 started.
- 2026-10-17: Fresh Opus re-review of r4 (`26d28592…1969f`): **GO_FOR_OPERATOR_APPROVAL**, no blockers; R3-B1 and R3-N1–N5 CLOSED (listdir dup filter and `-c` stub self-hash verified by a stdlib-only scratch experiment); six non-blocking implementation notes recorded in step 3. Design converged after four rounds. Waiting on operator approval and D-1–D-11.
- 2026-10-17: Operator approved the r4 design and the D-1–D-11 recommendations (rulings table above). Sonnet harness step 4a started.
- 2026-10-17: Operator ruling: all implementations reviewed by Opus 5.5 medium.
- 2026-10-17: Sonnet 4a harness written in private scratch (~8.4k lines stdlib Python): writer reports 151/151 self-tests PASS (0 skip), ruff clean, 8 deviations (memsw=2x limit, extra stub+setpriv stage, main-thread sampler, no site.main in 4a, 0.25 s exit-race grace, reap-before-empty, extra STOPs, writer-defined policy semantics). Smoke: v2 `cgroup.kill` works; unshare reports ns-init SIGKILL as exit 1 (oom_kill counter used instead); Bash background stop sends SIGTERM to the process group; M15 OOM contained; no platform intervention. Coordinator check: repo clean, no `n2-*` cgroups, no surviving processes. Not yet accepted — independent Opus implementation review running; snapshot to `stage4_n2_harness/` only after it passes.
- 2026-10-17: Opus implementation review of 4a: **ACCEPT_WITH_REQUIRED_CHANGES**. Independent rerun 151/151 PASS, ruff clean, cleanup clean; contract largely verified (L-4, D-4 effective in child, D-3 (a+), escape detection, ledger, run-mode refusal, env allowlist). BLOCKING: (1) gates not applied to the final post-exit sample — last-interval failcnt/peak/pids breaches classified success; (2) mincore control vacuous — kernel `can_do_mincore` fallback reports all-resident, which explains the capability-less 1.0 reading (fails closed for D-11). Non-blocking: swap=0 guard for memsw=2x, PF_EXITING instead of grace-only, rmdir only own cgroups, stub exit marker so unshare exit-1 masking becomes STOP, EROFS-specific probe. Deviations 1–8 recommended ACCEPT (two with changes). New operator items: ratify memsw=2x + swap guard; ratify `reserve_bytes`/`conservative_peak_max_bytes` and comparison semantics (step 5); payload still uid 0 after cap drop (accept or `setpriv --reuid` in 4b); cold-input method for repo-resident inputs (4b); exit-without-marker = STOP. Sonnet fix round 1 started.
- 2026-10-17: Sonnet fix round 1 interrupted by an API session limit (partial edits in scratch, no cgroups/processes left behind, repo clean); resumed after reset at 15:10 UTC.
- 2026-10-17: Sonnet fix round 1: 161/161 PASS (10 new tests), ruff clean, harness hash `5354abcf…3620c`. Fresh Opus re-review: **ACCEPT** — all required items CLOSED (post-exit gates proven by a revert experiment; mincore control now discriminating), independent rerun 161/0/0/0, hashes verified, cleanup clean. Non-blocking follow-ups for the next Sonnet round: N1 rename/strengthen the two "last_interval" real-cgroup tests (they are caught by live checks, not the post-exit gate); N2 final post-exit sample must record N2Stop/OSError (swap_appeared, escapes, unexpected process) as stopped instead of `pass`; N3 per-thread exiting check must read `/proc/<pid>/task/<tid>/stat`; N4 `synthetic_mock` default must be False (fail closed); N5 stat after each mkdir inside rollback; N6 OOM test must assert `oom_kill_detected`/`breach_oom_signal`. Accepted 4a snapshot committed to `stage4_n2_harness/`.
- 2026-10-17: Snapshot files actually landed in commit `8648afb` (the previous commit's log line preceded the copy because `rsync` is absent; fixed with `cp`). Snapshot hashes verified against `results/FILES.sha256`. Sonnet round-2 follow-ups N1–N6 started in scratch; will get Opus review before the snapshot is updated. Step 4b is blocked on operator D-9 (venv).
- 2026-10-17: Sonnet round-2 follow-ups (harness `ceae8da6…e546f`, 165/165). Opus review: **ACCEPT_WITH_REQUIRED_CHANGES** — N1–N6 CLOSED (each verified by revert), independent run 165/0/0/0. Required R1: new liveness-first ordering skips persistent floors (disk free, Bash cgroup growth) when the child exits before the first sample (demonstrated: 208 MiB under disk floor → success). Also: status 'stopped' whenever a gate fired, 4b exclusion of test hooks, restore round-1 report history, regenerate FILES.sha256 last, N5 test cleanup, dedupe. Committed snapshot remains the accepted round-1 version until this passes review. Sonnet round 3 started.
- 2026-10-17: Sonnet round 3 (harness `c4e90eba…2dfa`, 167/167). Opus review: **ACCEPT_WITH_REQUIRED_CHANGES** — R1 and round-2 items CLOSED (verified by revert), independent run 167/0/0/0. New required fix, **also present in the committed round-1 snapshot**: in `_finalize`, a failed final memsw/counter/marker read records `final_counters_failed` but leaves status `success` (fail-open; demonstrated). The committed `stage4_n2_harness/` snapshot therefore must not be used for any run until replaced by a reviewed fixed version. Non-blocking: exception in post-exit gates inside the STOP handler can skip `_finalize`; stale docstring; elapsed cap only live (4b note). Sonnet round 4 started.
- 2026-10-17: Sonnet round 4 (harness `bc1a0f3f…d2e0`, 169/169). Opus final-4a review with exhaustive fail-closed audit: **ACCEPT_WITH_REQUIRED_CHANGES** — round-3 items CLOSED (verified by revert), independent run 169/0/0/0, no path ends `success` after a failed gate/read. Required R4-1: unguarded telemetry `close()` on ENOSPC skips the terminal receipt (dispatch stays blocked; reproduced). Hardening N1–N5: terminate() read errors keep escalating, serialise-before-O_EXCL in ledger, record failed rollback rmdir, structural guarantee that `create_terminal` always runs + final success invariant, keep final marker error events. Sonnet round 5 started; per operator ruling it gets an Opus review before replacing the snapshot.
- 2026-10-17: Sonnet round 5 (harness `ca21f4ed…b7ee`, 175/175). Opus review: **ACCEPT_WITH_REQUIRED_CHANGES** — R4-1, N1–N3, N5 CLOSED (reverts demonstrated); broad excepts preserve evidence paths and KeyboardInterrupt/SystemExit; minimal-body retry keeps status `stopped` and all stops. Required RC-1: an unexpected exception type in post-exit gates inside the STOP handler still skips `_finalize` (reproduced; not fail-open — slot stays `launched`); RC-2: list all driver-only knobs in the 4b exclusion list. Non-blocking NB-1–NB-4 (ledger double-close, remaining narrow excepts, fallback stop placement, hook composition). Sonnet round 6 started; light Opus re-check to follow.
- 2026-10-17: Sonnet round 6 interrupted by an API session limit (~16:10 UTC; partial edits in scratch; no cgroups/processes left; repo clean); resumed after reset at 20:10 UTC.
- 2026-10-17: Sonnet round 6 (harness `eda94197…abec`, 179/179). Fresh Opus re-check: **ACCEPT** — RC-1 verified by revert (narrowed except → no terminal + 3 leaked cgroups), RC-2 and NB-1–NB-4 done, independent run 179/0/0/0, hashes/receipt verified, structural guarantee holds for Exception paths. **Snapshot in `stage4_n2_harness/` replaced with this version** (flagged round-1 copy removed). 4b carry-forward (non-blocking): move `after_attempt_record` hook inside the try; `os.close` OSError in ledger `finally` can replace LedgerStop (DISPATCH_BLOCKED already set); guard the member `close()` loop and `str(e)` in the BaseException handler; minimal-body serialize failure leaves no terminal (slot still consumed); compose all driver hooks, not only `before_finalize`; check short `os.write`; add Attempt-level `nonce`/`fs`/`cg_root`/`use_v2` overrides to the 4b exclusion/assert list or document them as legitimate config.
- 2026-10-17: Operator paused for independent external verification. Session record for reviewers: [`stage4_n2_session_record_2026-10-17.md`](stage4_n2_session_record_2026-10-17.md). No further work until the operator resumes.
- 2026-10-17: External (OpenAI) verification of `8d59b91` by operator: committed evidence consistent (112 frozen files, 49 harness hashes, 179 test records); flagged two doc inconsistencies — fixed (stale "not yet reviewed" phrase in plan step 2; open decisions (a)–(e) and 4b carry-forward now have their own subsections above).
- 2026-10-17: Operator approved D-9 and the capped 4b plan (one implementation pass, at most two review rounds, then report; then numbers + single H0 run). Venv: Python 3.11.17; resolved numpy 2.4.6, pandas 3.0.6, geopandas 1.2.0, shapely 2.2.0, pyproj 3.7.2, scipy 1.17.1, pyarrow 26.0.0, fastapi 0.143.3, pydantic 2.14.1, pydantic-settings 2.16.0, pytest 9.1.1, ruff 0.17.1 (newer than the Windows observation; full freeze in harness `results/venv_freeze.txt`). Editable install rewrote tracked `gisindia4logistics.egg-info/` files — restored with `git checkout`; 112-file freeze re-verified 0 mismatches. (c) and (d) implemented per recommendation in 4b, subject to operator ratification at step 5.
- 2026-10-17: Operator corrections to 4b (after external review), sent to the 4b writer: (1) no eviction of shared/repo input caches — private copies or disclosed cache uncertainty; (2) H0 is only a graph-build resource measurement on this host, not evidence the national pipeline fits; (3) numerical limits must come from measured container/ancestor limits and an explicit reserve, presented for approval — ~13.4 GiB is an observed cgroup limit, not a workload budget; (4) the two-review cap is a stopping point, not an acceptance waiver — unresolved blockers go back to the operator; (5) record resolved venv versions in 4b compatibility results. No national profiling, generation, publication or merge approved.
- 2026-10-17: Sonnet 4b implementation pass (harness `801748d6…f7b7`): writer reports stdlib suite 201/201 (179 4a + 22 carry-forward), venv suite 49/49 (M05 10, M06 13, M07 12, contained 11, compat 1, preservation 2), ruff clean; real dispatch refused at gate and runner; (c) payload as uid 65534; (d) attempt-private hash-verified input copies, shared inputs never fadvised; H0 wording narrow; no budget defaults; venv versions recorded. Deviations incl. uid tests using `/tmp/n2-4b-*` outside the scratchpad (removed). Coordinator check: repo clean, 112-file freeze 0 mismatches, 67 harness hashes OK, no `n2-*` cgroups, `/tmp/n2*` dirs or stray processes. Opus review 1 of 2 started.
- 2026-10-17: Opus 4b review 1/2: **ACCEPT_WITH_REQUIRED_CHANGES** — independent runs 201/201 and 49/49, ruff clean; real helpers confirmed; sentinel and no-shared-fadvise proven non-vacuous by revert; operator corrections compliant; carry-forward closed. BLOCKING: B1 mock dispatch path accepts unchecked CONFIG paths, so a self-minted mock receipt could point the real producers at `<repo>/data` or write `<repo>/outputs` (code-read, not executed); B2 root-side seal/verify follow symlinks planted in the uid-writable cache dir (demonstrated); B3 the 'no data/ reads' audit hook cannot see GDAL/pyarrow reads (measured) — claim overstated; B4 parent-side H0 verification does not gate H1/H2. Non-blocking N1–N7. Coordinator removed 7 untracked `__pycache__` dirs in the repo created by its own D-9 import check (N8). Sonnet's single fix round started; review 2 is final — unresolved blockers go to the operator.
- 2026-10-17: Sonnet 4b fix round (harness `9f9348a4…28f4`): writer reports stdlib 213/213, venv 58/58 (with `data/` masked by a private read-only tmpfs), ruff clean. Coordinator check: repo clean, no `outputs/`, 112-file freeze 0 mismatches, 68 harness hashes OK, no leftovers.
- 2026-10-17: Opus 4b review 2/2 (FINAL): **ACCEPT_WITH_REQUIRED_CHANGES**. Independent runs 213/213 and 58/58, ruff clean. B1, B2, B3, B4 and N1–N6 CLOSED (B1, B2, B4 verified by revert). **Open BLOCKER B1-R1 (returned to operator unresolved per cap ruling):** path validation only resolves the configured directory, so a symlink or hard link *inside* a validated read directory can still reach `<repo>/data` (demonstrated in-process with `roads -> <repo>/data/roads`; scratch and repo share a filesystem; uid 65534 can read the repo). Only the venv suite's mount mask prevents it; a CLI mock dispatch outside the suite would not. No harness code plants such links; real dispatch remains disabled (`REAL_DISPATCH_ENABLED=False`). Proposed fix: walk read trees with lstat, refuse symlinks/non-regular entries/`nlink>1`/foreign `st_dev`, require root-owned trees not writable by the payload uid, re-check in the slot before imports, plus tests. Non-blocking: run stdlib suite under the same mask (it dispatches with `data_dir=<repo>/data` in refusal tests); read-only bind of the whole repo in both runners; `--masked` private-namespace self-check; `archive_name` validation; seal cache before writing the ok receipt; harden `verified_repo_root` against a fabricated venv; validate transfer record at registration; route 4 hard-coded result files through `--results-dir`. **Snapshot not replaced.** Waiting for operator decision.
- 2026-10-17: Operator paused work to resume later. Unaccepted 4b work preserved in `stage4_n2_harness_4b_pending/` (hashes verified; README marks it not accepted); accepted harness remains `stage4_n2_harness/` (4a). Venv and scratch are not in Git and must be recreated (see "How to resume"). Pending operator decision: options (1)–(3) for B1-R1.
- 2026-10-18: Operator resumed and chose option (1): one targeted Sonnet round for B1-R1 plus the small hardening items (stdlib suite under the mount mask / read-only repo bind in both runners, private-namespace self-check, archive_name validation, seal before ok receipt, realpath-based forbidden repo root, validate transfer at registration, full --results-dir coverage), followed by one Opus check limited to those changes; anything still open returns to the operator. Container state checked first: scratch harness hashes OK, venv present, no leftover cgroups or /tmp dirs.
- 2026-10-18: Operator halted harness work ("proceed to work on work"). Sonnet stopped before making any change (4b harness still `9f9348a4…28f4`, B1-R1 and hardening unimplemented); cleanup verified. Proposed instead: run the existing `scripts/analyze/stage4_generate.py` unchanged, reading tracked `data/` inputs, writing only `/home/user/n2_stage/run1` and `/home/user/n2_stage/cache` (outside the repo; no `data/analysis` write, no publication), in its own memory cgroup capped at 11 GiB (shell cgroup limit 13.36 GiB, 26 MB in use), background ≤2 h, peak memory logged. First attempt to create the run script was blocked by the permission system as bypassing the approval gates; nothing ran. Awaiting explicit operator approval.
- 2026-10-18: Operator approved "run stage4_generate in /home/user/n2_stage with a 12 GiB cap". Script `/home/user/n2_stage/run.sh`: own memory cgroup `n2-run1` (limit 12 GiB, memsw 24 GiB), threads=1, `.venv` Python 3.11.17, unchanged `scripts/analyze/stage4_generate.py`. Attempt 1 failed in preflight after 19 s (`FileNotFoundError`: source root given as repo root; inputs are relative to `data/`); nothing written; peak 216 MB; its cgroup was left behind because the monitor's `sleep` was still a member — removed manually; script fixed to wait for the monitor. **Attempt 2 (source root `data/`) completed rc=0** in 1,045 s (orchestrator elapsed 1,031.6 s). Peak memory: cgroup `max_usage_in_bytes` 709,541,888 B (≈0.66 GiB), orchestrator `ru_maxrss` 748,089,344 B; failcnt 0, oom_kill 0. Graph: 316,930 nodes / 932,722 edges from `roads/india_nh_network.parquet`; cache 9.9 MB. Outputs in `/home/user/n2_stage/run1/analysis/` (outside the repo; ephemeral container): 7 artifacts + `producer_limitations.json` + `producer_manifest.json`. Shape check vs B0: all row/feature counts equal (781 districts; 48 port and 60 MMLP isochrone features); columns identical except `district_freight_modal_split.csv` column ORDER differs (same set). SHA-256: freight `73cf17aa…`, hinterland `3e22c019…`, ports_iso `02f09945…`, mmlp_iso `a5c150af…`, port_matrix `a3ef880b…`, summary_csv `5baad8c5…`, summary_parquet `cd0fb6e2…`, limitations `6eadc43f…`, manifest `3e73719e…`. Repo unchanged; no `data/analysis` write; nothing published. Note: this is a staged generation with a single memory cap, replacing the planned harness/six-profile route at the operator's direction.
- 2026-10-18: Operator approved saving outputs and drift explanation. Outputs saved to `stage4_staging/linux-2026-10-18-run1/` (commit `ec42e6e`). Drift report: [`stage4_drift_linux-2026-10-18-run1.md`](stage4_drift_linux-2026-10-18-run1.md) (coordinator analysis, not yet independently reviewed). Headlines: identity 781/781 matched everywhere; highway access decreases in 62 districts (component snapping fix; Andaman largest); routing distances shift slightly (median ≈0.1%); hinterland primary port changes for 77 districts (40 Kolkata/Haldia→Paradip) — needs review; uncoded NAZUL/MIRPUR/MUZAFFARABAD now carry source-file area_share population (0 in B0) — operator decision against the 'missing stays missing' rule; isochrone thresholds changed by design (ports 4/8/12/24 h vs B0 1/2/4/8 h; MMLP 4/8/12 h vs 1/2/4 h), MMLP Jammu unsupported_threshold; freight table column order differs.
- 2026-10-18: Independent Opus review of the staged run and drift report. No producer defect found; integrity and manifest/profile/limitations validation pass; no input data changed vs B0 (`3ce9a5e`); fast audit 1,727 checks / 1,714 PASS / 13 WARN / 0 FAIL; API/SDK/MCP work against the staged copy; full pytest vs B0 3,485 passed / 14 environmental failures (missing `tabulate`, PATH). Drift report had 8 factual errors — **corrected** (high confidence; coordinator re-verified that only 4 drive-hour cells changed and that today's code reproduces B0 at α/β 0.85/1.65 and the new output at 1.2/1.5). Decisions now needed: (1) gravity α/β 1.2/1.5 vs 0.85/1.65 — the whole hinterland shift (73 real primary-port changes, 40 Kolkata→Paradip) comes from this; (2) component-bounded catchments — MMLP Jammu lost its catchment (new regression), JNPT/Mumbai/Chennai/Visakhapatnam ports and 5 MMLPs are tiny fragments (pre-existing); (3) PoK area_share populations. Not published.
- 2026-10-19: Operator rulings on the staged run: (1) gravity α/β — operator has no basis to choose; coordinator to recommend (see next entry); rerun cost is not a concern. (2) **Catchments must be corrected**: isochrone hub snapping to use the district matrix's component-aware rule; Sonnet implementing, Opus to review, then a full regeneration. (3) **Uncoded-district populations (NAZUL, MIRPUR, MUZAFFARABAD) kept as-is and disclosed.**
- 2026-10-19: Gravity basis finding: the repo has no district-to-port flow data to calibrate α/β; port 'capacities' are FY2023-24 cargo throughput (`data/freight/port_throughput_annual.csv`, e.g. Paradip 145.38 MT), so fitting α/β to throughput would be circular (and throughput is dominated by bulk coal/ore, not population-driven hinterland demand). Coordinator recommendation: use the long-standing 0.85/1.65 (all prior versions, B0 and the live API/SDK/MCP default), labelled uncalibrated, and publish a sensitivity table across an α/β grid; real calibration needs district-origin port data (e.g. CBIC/ICEGATE port-of-loading by district) — future work.
