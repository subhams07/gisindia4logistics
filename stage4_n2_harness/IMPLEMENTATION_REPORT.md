# N2 step 4a implementation report

Author: Sonnet 5.5 (implementation writer, step 4a). Date of work: 2026-10-17. Nothing here is approved or execution
authority. All numeric policy values used by the self-tests are `SYNTHETIC_TEST_POLICY_NOT_APPROVED`.
Everything lives under `/tmp/claude-0/-home-user-gisindia4logistics/2b5950a5-fafd-5793-853f-5f4a9a86d77d/scratchpad/n2_harness/`.
The repository was not edited, committed or pushed (`git status --porcelain` empty, HEAD `d32d0a6fb0b6e9cd3f9fcf2ddbb47080ab52e63b`
before and after, checked read-only).

Sections 3, 4, 6 and 7 record what was actually run and measured; section 9 lists the claims that rest on inference or on code paths exercised only indirectly.

## 0. Binding documents

- Addendum r4 SHA-256 verified before starting: `26d285927cf26d8317fd32206ae57b757045950d81259e885f395f724831969f` (match, `sha256sum`, exit 0).
- Base spec sections A, B, F, G, H and handoff section 0 (rulings D-1..D-11 and the six r4 reviewer notes) were read and applied.
- Not read: `AGENTS.md`, `.env`, credential files, `data/`. No producer module imported. No package installed, no network use.

## 1. What was implemented

Stdlib-only (Python 3.11.17, run with `-I -B -u`; the controller modes additionally require `-S`; `-O` is refused).

| Module | Role |
|---|---|
| `n2h/cli.py`, `n2_harness.py` | Modes `selftest`, `prepare` (metadata only, `content_reads: 0`), `run` (refuses before any child/cgroup/attempt unless approval, policy, scope, selftest report and ledger receipts exist, are hash-valid and bound to each other and to the current harness hash; in 4a even fully valid NON-mock receipts refuse with `real_dispatch_not_implemented_4a`; only receipts flagged `synthetic_mock` in a mock control root dispatch the synthetic workload). Hand-rolled argument parsing (argparse would break the pre-fork module allowlist). |
| `n2h/ledger.py` | Canonical hash-bound receipts, run gate, `Ledger` (attempt / launch / terminal records: `O_CREAT|O_EXCL|O_NOFOLLOW`, mode 0444, fsync of file and directory, no PID in the attempt record, key = sha256(approval_sha:slot) independent of run label or directory, root id + realpath binding, `DISPATCH_BLOCKED` on any persistence failure, six-slot order and dependency rule). |
| `n2h/policy.py` | Policy receipt model with value, unit, strictness (`strict`/`inclusive`) and basis for every addendum section-5 field, plus `reserve_bytes` (D-5) and `conservative_peak_max_bytes` (D-11 gate on peak + resident_elsewhere); documented comparison semantics; synthetic labelled values only. |
| `n2h/roots.py` | M04 root checks: no defaults, disjointness, symlink / mount-boundary (`st_dev` change) rejection, `/sys` `/proc` `/dev` and repository trees forbidden, file identity and swap detection. |
| `n2h/cg.py` | The "job": exclusive `mkdir` of `n2-<id>` in v1 memory (nested under the controller's own memory cgroup), v1 pids and a v2 child of the unified root; finite page-aligned limits written before any join and read back; empty/zero assertion; ordered, doubly-verified joins; membership listing; v2 `cgroup.kill`; `rmdir` ownership; foreign `n2-*` scan; optional v2 probe. |
| `n2h/launch.py` | Fork-gated launch (the CREATE_SUSPENDED analogue): L-3 pre-fork conditions (single thread, main thread, pinned stdlib module allowlist incl. `fcntl`, SIGCHLD not ignored, no unreaped children, gc disabled, stdio flushed), hash-verified `exec_fd`, gate pipe, child code L-4 steps 1-8 (no imports, `try/except BaseException -> os._exit`), fixed child exit codes 70-76. |
| `n2h/stubsrc.py` | The ASCII child stub (6961 bytes, passed with `-c`, SHA bound in the attempt record) and the synthetic workload (sleep, alloc, OOM-until-killed, file cache, stubborn descendants, staged phases, latch, D-4 probe, ...). |
| `n2h/pidfd.py`, `n2h/term.py` | Verified pidfd members (cgroup path, starttime, signal-0, `st_dev`+`st_ino` pid-ns identity re-confirmed with signal 0 immediately before the real signal); signalling only through those fds; D-3 option (a+) termination: SIGTERM, bounded graceful wait, SIGKILL ns-init, bounded `unshare_reap_wait_s` wait before any SIGKILL to `unshare`, v2 `cgroup.kill` backstop only while ns-init is alive and no unowned process is listed, subreaper-adopted zombies reaped by exact pid, unowned members reported and never killed. |
| `n2h/telemetry.py`, `n2h/sysfs.py`, `n2h/native.py` | Single-threaded sampler (PDEATHSIG follows the forking thread), the four distinct memory metrics, effective available memory with separate terms, Bash-cgroup growth, disk, per-member `/proc` fields, escape detection (listing, descendant walk, per-thread cgroup check), unexpected-process detection, nonce-framed marker reader, phase witnesses (scalar only), series validation, `mincore` residency / `fadvise` (own scratch files only), prctl / subreaper. |
| `n2h/controller.py`, `n2h/driver.py` | One attempt end to end (attempt record -> cgroups -> fork -> join -> launch record -> k samples -> residency re-check -> release -> handshake -> monitor -> terminate/reap -> counters -> rmdir -> logs hashed -> terminal receipt); SIGTERM/SIGHUP/SIGINT handled as a termination request; `driver.py` is the small controller process the tests start (the unittest process can never satisfy the strict L-3 allowlist). |
| `n2h/manifest.py`, `n2h/slots.py` | Scratch manifest that cannot be confused with a publication manifest; slot table and 4b plug-in points (`real_payload()` always raises `not_implemented_4b`). |

Exec chain (D-3/D-4): `unshare --pid --fork --kill-child=SIGKILL --mount -- python -I -S -B -u -c <stub> <expected-json>`; stub stage 0 checks
(stub source hash from the FIXED `/proc/self/cmdline` index 6, `/proc/self/exe` identity+hash, cgroups, NSpid field count+1 with last `1`,
`PR_GET_PDEATHSIG==SIGKILL`, flags, exact environment, exactly fds 0-2) then, only inside a private mount namespace, remounts every
`/sys/fs/cgroup*` mount read-only and `fexecve`s the hash-verified `setpriv --bounding-set=-all --inh-caps=-all --no-new-privs`, which
re-executes the same stub as stage 1 (all checks again, capability sets all zero, `NoNewPrivs`, cgroup file not openable for write), then
`SIGTERM` handler, then the hash-bound payload. ns-init stays PID 1 across the execs.

## 2. Files and SHA-256

Harness hash (as bound by the run gate; `n2h/*.py` plus `n2_harness.py`, `driver_entry.py`, `run_selftests.py`): `5354abcf89b6bb029d4cd808e07924e5330185a0cad6e64c7ef766029553620c`.
Self-test report receipt (synthetic, `results/selftest_report.json`): `8b81e0f28bdeb6b971a007b35d69227edd9b0b559175b2da1c451bf353a7e41b`.
Full list also in `results/FILES.sha256`. Line counts (before round 1): `n2h/` 4234, `tests/` 3840. Stub source 6961 bytes (limit 131072), workload source 5294 bytes.

| File (relative to `n2_harness/`) | SHA-256 |
|---|---|
| `MATRIX.md` | `303ee71ac86084b3db3250a82f75bfe4cf9faba37b6b89c1241f2925763f6cff` |
| `driver_entry.py` | `e313638dbea00875238179a0fc4f2982c3b58e6dc22811258884874304b7c5a6` |
| `n2_harness.py` | `c79c00861d52b1aea3c8611de2fc76f21a22cd9c0fdeb7ac32c3a26c7db81769` |
| `n2h/__init__.py` | `d1ae8a435aaedd41aa5d154466d913fe2fcb407292a57148afb3013cc5a46ad8` |
| `n2h/allowlist.py` | `880a1a72dca2f854c9845b35330b5ed00d4623cff10646f77ffcf2a8d962870c` |
| `n2h/cg.py` | `56212be7260eb43a5e0c3eae34d0e790816ca5ba9e8df56351ba282e9f614ed3` |
| `n2h/cli.py` | `1e114a42d0a47d224317987ba4fa6d15708fa19f1a6cfb68216a4120042f688a` |
| `n2h/controller.py` | `e43c48c09c20a950cb428a15eafebda05059b6e458747bdbca6c0ce214e82dd6` |
| `n2h/driver.py` | `eebd73c193898403c3b84575f31d3b0c67cdbcd113c1cf80f86643d584fbc598` |
| `n2h/errors.py` | `efb0fb87b278d72087380dc6d6c34b194a6ae2e1a0dbb41c0a66a9615ba1b3b2` |
| `n2h/fsio.py` | `e72c487c2adcee4356e18f739d3e39cc1c886dc0d8a318fa05203e9f3c3c7136` |
| `n2h/launch.py` | `7f480ca0ed48ea3fc1ba8d087fa435c91c70d5109bf8405837e70615a3acd21f` |
| `n2h/ledger.py` | `b15db45bb465aa1d8db8ced0ff2bab2fb71cb780ed567006522cd3ba252048a4` |
| `n2h/manifest.py` | `b7755623483ba745d5d9da566beb200c290c4749ba4f81f43ef45c21f9165407` |
| `n2h/native.py` | `820389997835cfce863bf9a8b6a6090ccda7388d21c4d7e7f5fd53dce6c8dead` |
| `n2h/pidfd.py` | `1fd9ac120167ee67673181c748f933054ba51c25a7b2a01c1304430276f9b021` |
| `n2h/policy.py` | `f29c38e644f5734930c41ad649ea1e3df938dfb79d7d5986a1d757c3a39d1a38` |
| `n2h/roots.py` | `3258864b174b17769242fd0a21542d39c1f369ded7a9ee5567f9428efedf421c` |
| `n2h/slots.py` | `d2ee1999d7c909fe8a79ed236508835f3f5824ae2cee552d92b33f86df7e95c6` |
| `n2h/stubsrc.py` | `18081711634d577df2576a4dd696ca56565d346b8ca171f6a7888fdf1a22874e` |
| `n2h/sysfs.py` | `85bab85aef54f0ab9034a3f3b4eb46783d266cb451ddf7b85d2a2ef486fce2f3` |
| `n2h/telemetry.py` | `443c6e6e9e4b42c148d6562bead54a7d6201ff19d49c89ac45cc86cd9a2003b8` |
| `n2h/term.py` | `b1100a25efe9c820b0474d0c2da4cdeaf7c4b40b6cfeaf735c6235dcdb223a84` |
| `ruff.toml` | `a64697483723175aaa5d8fbcb76e45a77ad68cdf3213ba2d844acec7230ed54d` |
| `run_selftests.py` | `ff258f0c16a8618cd80fc38775345709361f4b6037b3213d03cb35076092db01` |
| `scripts/l13_observe.py` | `fb581d3b40e220e47610a37fc7c117e4f381f50142bc81d2e449b1b58940cf21` |
| `scripts/measure_idle.py` | `d791f68993b09f7fad8754f880ed8a316e7c8fa302a780ebb4e5bcb0670f7e06` |
| `scripts/smoke_oom_eventfd.py` | `957944acb63d636da9f208f6ef8406ee61bf7be5e3ec51f3c1588e264d3f937c` |
| `tests/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `tests/helpers.py` | `3de8112bb36bf074477235209dde5dc27fff0e428c43dbf884b6f00a31c185f2` |
| `tests/mockcg.py` | `f4a62f8b2e06978de8f8cd4f284457c86fea72875c5084d0bda95f48379da44e` |
| `tests/ocg.py` | `a3f9c6484e12d0cc2d40ea3a8ebddcece381563f60bdd7dde171b09fd65eba54` |
| `tests/pdeath_probe.py` | `d4f741b2e6c15b002b9dd6de9706a025a63ee94cac6d6c1a4b3554ccdaf27420` |
| `tests/stubrun.py` | `0ff3f40c9f3e720710be5f07e94329ecfd927009574ca451cf705474c98aa310` |
| `tests/test_m01_modes.py` | `ed26d334eb40972e495cb886ad54ff0b49a77e64f0aa2b8d169044ed480d0040` |
| `tests/test_m02_ledger.py` | `071e0441adbf7e8821a69f6c9ba700cb07725b6fbbf2f5aa4d99e659992e4d33` |
| `tests/test_m03_launch.py` | `cbadd41ff2e34c9907c8f3d43a26bd83548bf2fda03d90e5c9db0746b7ac3d05` |
| `tests/test_m03_stub_term.py` | `f6a8e4ebeed1ecc444d3b58b5c8c2639780bbc94147b6e5da84fe839c59253e7` |
| `tests/test_m04_roots.py` | `9f471a103956076787cc2ee33ecfaeb6d6686696fbfd4ef99e5a4819b62d4b80` |
| `tests/test_m08_telemetry.py` | `e968a7c4820270db8ef0ad8ec33e5d535c89312d85e74a1e16f11a7968ade72c` |
| `tests/test_m09_policy.py` | `96bbfb1ac949ef6e18db7aa683331d33f89660299bfdab3a8a9adecb9bb331c0` |
| `tests/test_m10_m11.py` | `21cd7c57d5cd59cbe7c0949b9e1c72380ffe1985f70d300c8b1b40f6d4e7580d` |
| `tests/test_m12_lifecycle.py` | `560387c70c7bc2f7ec03eca174e0ef81abf02427810409846ca3e1e410e93353` |
| `tests/test_m13_death.py` | `195773c0d8cc2435b4bd60fac1780fd8f91c37047df6a5493bdea6d43d6ef49a` |
| `tests/test_m14_accounting.py` | `63e1cf2082e928c4a9b8673eadac818f9ff3d287379cea0c1769b3631d2342e5` |
| `tests/test_m15_m16.py` | `956021978414a321cafda31a16044c0b4eb47a5faa331c592d606b9760d8588f` |
| `IMPLEMENTATION_REPORT.md` | `0ac8a9c64e28ac5343ac8a0169e6cd99abe8a039a11f7899a4fbc074cebf2557` |
| `tests/test_review1.py` | `81a9a10e7d022a7615d639f9d8997083b2b98d21a4154e2c5087ba9afd580795` |
| `tests/test_meta.py` | `7190515894c326773e8dd697fbc80f10959ec874301f7e7dc9fbbb41b5da43e9` |

## 3. Commands run and exit codes

All from `n2_harness/`. Interpreter flags as shown.

| Command | Exit |
|---|---|
| `sha256sum docs/stage4_n2_linux_addendum.md` | 0, hash matches |
| `python3.11 -I -B -u run_selftests.py --report-out results/selftest_report.json` (final run) | 0: ran 161, fail 0, error 0, skip 0, 99.4 s |
| the same suite run three more times earlier in the work (after each large change) | 0 each, 147-151 tests |
| `/root/.local/bin/ruff check .` | 0 ("All checks passed!") |
| `python3.11 -I -B -u n2_harness.py selftest m04` | 0 |
| `python3.11 -I -S -B -u n2_harness.py prepare --config cfg.json` (synthetic fixture dirs) | 0, `content_reads: 0` |
| `python3.11 -I -S -B -u n2_harness.py run` (no inputs) | 3 `REFUSED run_inputs_missing` |
| `python3.11 -I -B -u scripts/measure_idle.py` | 0 |
| `python3.11 -I -B -u scripts/smoke_oom_eventfd.py` | 0 |
| `python3.11 -I -B -u scripts/l13_observe.py start|report` (see 6.9) | start: foreground call backgrounded by the tool; second run stopped by the tool's background limit (137); report 0 |
| `find /sys/fs/cgroup -name 'n2-*'` | 0 matches at the end |

Earlier ad-hoc read-only probes (cgroup mounts, caps, `/proc/cgroups`, `unshare`/`setpriv` behaviour) were run before the code existed; one
`n2-probe-aa11` unified cgroup was created, written (`cgroup.kill`) and removed during the first probe.

## 4. Test results

Final suite: **161 tests, 161 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (no skips, so no non-coverage hidden behind skip counts). Per-control test counts:

| Control group | Tests |
|---|---|
| 4B | 1 |
| M01 | 17 |
| M02 | 12 |
| M03 | 47 |
| M04 | 6 |
| M08 | 11 |
| M09 | 12 |
| M10 | 4 |
| M11 | 4 |
| M12 | 5 |
| M13 | 12 |
| M14 | 9 |
| M15 | 2 |
| M16 | 8 |
| MATRIX | 1 |

(`M03` counts M03-L1..L12 and reviewer notes 1-5; `META` rows are folded into `MATRIX`/`4B`.) `MATRIX.md` maps every control and sub-item to its test
names; `test_MATRIX_consistency` fails if a listed test does not exist or a test is not listed.

Coverage statement, per task scope:

- Implemented and tested: M01, M02, M03-L1..L12, reviewer notes 1-6 (note 6 is `test_M14_mincore_ownership_mode_control`), M04, M08-L, M09-L, M10, M11, M12, M13, M14, M15 (both variants), M16, plus D-1/D-4/D-5 checks.
- M03-L13 is a MANUAL smoke (section 6.9), not an automated test; the automated companion is `test_M13_sigterm_to_process_group_graceful_terminal_receipt`.
- **Not yet implemented (4b), recorded as such in the matrix:** M05 (real tiny W0 helper parity), M06 (real tiny cold/warm graph cache), M07 (H1 to H3 transfer with real producer helpers). Interfaces are in place: payloads are hash-bound source texts run by the stub inside the contained namespace; `slots.real_payload()` refuses.
- Not applicable in 4a: Windows Job Object items of M03, M12 option (c) frozen-orphan recovery, M13 v1/v2-frozen variants (no freeze is ever applied under option a+).
- Mock-based tests (M08, M13 unowned/pid-ns, M16) use a scratch cgroup tree and an overlay filesystem; they exercise decision logic, not kernel behaviour. Kernel behaviour is covered by the real-process tests listed in section 6.

Ruff disclosure: the ruff in this environment (0.16.8) enables a much broader default rule set than I expected (first run: 207 findings). I fixed
real issues (unused names, narrowed broad excepts or justified them with `# noqa: BLE001` plus reason, B023/B017, SIM102, import order). I added
`ruff.toml` in the harness directory: it ignores only `UP031` globally (printf-style formatting, stylistic) and ignores a short list of style-only
rules in `tests/*` and `scripts/*` (`SIM115`, `RUF059`, `PLW1510`, `RUF015`, `RUF012`, `PLC0206`, `PERF102`, `SIM117`, `S110`). `ruff format` was not applied.

## 5. Policy inputs I had to define (need a reviewer ruling)

- Comparison semantics (documented in `policy.py`, tested at equality for every floor and cap): floor `strict` breaches iff observed < floor, `inclusive` iff <=; cap `strict` breaches iff observed > cap, `inclusive` iff >=.
- `reserve_bytes` (D-5) is enforced at launch as `attempt_memory_limit_bytes + reserve_bytes <= effective_available_bytes`.
- `conservative_peak_max_bytes` gates `cg_charged_peak_bytes + resident_bytes_elsewhere` (D-11). The addendum names the figure but not a field.
- `failcnt_action` must be `stop` (D-11); `memsw_tolerance_bytes` must be at least 64 x nproc x 4 KiB.

## 6. Smoke findings (measured on this host, kernel 6.18.44, 4 vCPU, no swap)

6.1 v2 `cgroup.kill` (D-3 a+): AVAILABLE. A non-root child of the unified root accepts `cgroup.procs`, `cgroup.kill`, `cgroup.events`.
`probe_v2` result: `has_cgroup_kill True`, `has_cgroup_events True`, `populated 0` after kill on an empty cgroup. Real kill-to-empty with
a full tree (`cgroup.kill` written while ns-init was alive): 11.0-11.4 ms (5/5); `unshare` itself dies by SIGKILL (status `signal 9`).
It is used only as a backstop after the freeze-free escalation, only while ns-init is alive, and never when an unowned process is listed (tested).

6.2 Who reaps ns-init: (a) normal exit and hard-path: `unshare` reaps ns-init (the controller sees ns-init as "not our child"); (b) `cgroup.kill`
path: `unshare` and ns-init die together and the CONTROLLER reaps the orphaned ns-init through its held pidfd because it set
`PR_SET_CHILD_SUBREAPER` before fork (`reaped_by_controller: true`, 5/5); (c) controller killed (parent death): `unshare` and ns-init both reparent to
host PID 1 (`process_api`, ppid 1 observed) and stay zombies for **about 1.4-1.9 s** (measured 1.40-1.94 s in 12 runs) before PID 1 reaps them; `pids.current` returns
to 0 afterwards. A reaping wait shorter than ~3 s would misreport a zombie as persistent on this path.

6.3 `unshare` status propagation: ns-init `exit(n)` -> `unshare` exit n (0 and 7 checked); ns-init killed by SIGKILL -> `unshare` exits with status **1**,
not a signal status (so "a SIGKILL the controller did not send" is not visible as a signal; the controller uses the memory cgroup `oom_kill` counter
to discriminate OOM kills and reports anything else as `child_nonzero_exit`); `unshare` SIGKILLed by `cgroup.kill` -> `signal 9`.

6.4 What killing the controller does: at S0 (gated) and S1 (assigned) the gated child dies by PDEATHSIG within about 3 ms (state `Z`, ppid 1) and never execs (no marker in its log); with
PDEATHSIG disabled by injection the gate EOF alone makes it exit (proves the child's copy of `gate_w` is closed). After release the whole namespace goes (cgroup empty, no member left). Empty `n2-*`
cgroups are left behind and the next start STOPs on them (tested), as designed. PDEATHSIG follows the forking THREAD (both probes: `forker_thread_exits` and `main_exits` killed the child in ~20 ms
while the probe process lived on), which is why the sampler runs in the forking main thread; the controller has no threads.

6.5 `mincore` controls (corrected in review round 1): the earlier "foreign-owned file reads 1.0 without capabilities" check was vacuous, because when the kernel's `can_do_mincore()` is false (file not owned and not writable by the caller) it reports ALL pages resident. Real control now: files are written, fsynced, `fadvise(DONTNEED)`-ed, then chowned 65534 / chmod 0444 (and a root-owned 0644 file). The controller reads about 0 for both; a capability-less process reads exactly 1.0 for the foreign-owned file (the fallback) and about 0 for the root-owned file it still owns; after reading, the controller reads about 1.0. Hence residency must be measured only by the controller; the fallback overstates, so it fails closed. Other results: written+fsynced 8 MiB 1.0, after DONTNEED 0.0, re-read 1.0, sparse file 0.
Default `resident_bytes_elsewhere` set (interpreter + mapped libraries + stdlib sources/pyc, 191 files): see `results/findings-m14.json`.

6.6 Accounting semantics (M14, real children in unit-created `n2-ut-*` cgroups): charges made before joining are NOT in the attempt (24 MiB held before the join: attempt peak 0); 32 MiB allocated after
the join: peak 33,816,576 and anon 33,681,408; 32 MiB of file writes: `cache` +32 MiB, anon about 1 MiB, working set 2 MiB, dirty 32 MiB (so working set is blind to dirty cache);
kmem IS included in `usage_in_bytes` (30,000 empty files: kmem +40.0 MB, usage +45.7 MB, anon+cache +5.6 MB); residual after exit: 8 MiB of cache stays charged until rmdir; anon 0.

6.7 **memsw finding:** with `memory.memsw.limit_in_bytes == memory.limit_in_bytes` both `failcnt` counters stayed 0 although the peak equalled the limit (measured: peak 33,554,432, failcnt 0, memsw failcnt 0). With memsw = 2x the limit the plain counter
binds first and `failcnt` counts (71 hits for a 100 MiB write into a 32 MiB limit). I therefore write memsw = 2 x memory limit (still finite; the host has no swap so memsw usage cannot exceed memory usage).
`failcnt` is meaningless as a limit-hit signal otherwise.

6.8 M15 (D-8) with a 48 MiB attempt limit: OOM variant: allocating past the limit: final `oom_kill` 1, `failcnt` 143-160, charged peak 50,331,648 (= limit); the `failcnt` breach tripped first but the allocator outran the sample/terminate latency and was OOM-killed during the graceful wait; the
controller, the test shell and three unrelated sleepers survived; status `stopped`, cgroups removed. Non-OOM variant: 160 MiB of file writes: `failcnt` 21-24, `oom_kill` 0, peak 50,065,408, cache 40.9 MB, dirty up to 16.8 MB; STOP `breach_failcnt_nonzero` (D-11 failcnt_action=STOP); nothing killed. Repeated about six times, `oom_kill==0` held every time.
OOM eventfd via `cgroup.event_control` also works (`scripts/smoke_oom_eventfd.py`: fired, value 1, child -9); it is not wired into the controller (optional).

6.9 L13 (D-7 premise): (i) a foreground Bash call that exceeds its timeout is NOT killed: the tool moves it to the background; the controller and its tree kept running and no signal was delivered (instrumented handlers recorded nothing). (ii) A background
command stopped at its time limit receives **SIGTERM delivered to the whole process group at the same millisecond** (the wrapper and the controller both recorded `sig=15` at the same timestamp); the whole tree then disappeared (the later kill is not observable by handlers; I infer SIGKILL).
Consequence implemented: the controller treats SIGTERM/SIGHUP/SIGINT as a termination request (terminal receipt `external_termination_request`), and `test_M13_sigterm_to_process_group_graceful_terminal_receipt` sends SIGTERM to a test-created process group.
My first manual run left a wrapper sleeping in the background; I stopped it by SIGKILL through a pidfd after checking its command line, and the controller completed and cleaned up on its own.

6.10 Platform intervention (D-10): none observed. `sbx-telemetry` unchanged (64 MiB memory limit, `pids.max` 16), `claude-code-bash` and its ancestors show `failcnt 0` and `oom_kill 0` after the OOM tests (counters are local to the attempt cgroups), no unrelated process died. Side effect to report: `/proc/cgroups` memory `num_cgroups` is now 16 (5 at the start): about 11
dying memcgs from roughly 300 attempts are lingering after `rmdir` (residual page cache), reported per attempt as `memcg_dying_residual`; the kernel reclaims them lazily.

## 7. Measurements for D-5 / D-11 (MEASUREMENTS, not approved values)

Synthetic policy values were used; these are observations from `results/measurements.json` and the findings files, one 15 s idle series of 299 samples at a configured 50 ms interval plus 5 repetitions per termination path.

- Sampler interval: mean 51.23 ms, p50 51.39, p95 52.6, p99 54.62, max 57.7 ms (overhead about 1.2 ms per cycle; a gap of 2000 ms was never approached).
- Effective available memory at idle: about 14.22-14.23 GB (the `claude-code-bash` ancestor limit 14,343,688,192 minus usage binds, not host RAM 16.3 GB); range over 15 s: 6643712 bytes.
- Concurrent Bash-cgroup usage excluding the attempt: 111898624-114679808 bytes, range 2781184 over 15 s at idle (it also drifted by tens of MB between measurement sessions because other work shares the cgroup).
- Idle stub attempt: charged peak 8359936 bytes, member RSS max 15613952, `pids.current` max 2, `pids.peak` 2, memsw post-exit delta 0, `resident_bytes_elsewhere` 15175314.
- Release -> `ready` marker: [115.8, 104.8, 104.8, 103.9, 103.8] ms (remount, setpriv hop, two interpreter hashes).
- SIGTERM -> everything exited and cgroup empty (graceful path): [11.0, 10.9, 11.2, 11.4, 11.1] ms.
- SIGKILL ns-init -> `unshare` exited -> cgroup empty: 5.3-5.6 ms and 6.1-6.9 ms. `cgroup.kill` -> empty: 11.0-11.4 ms.
- Orphan reaping by host PID 1 after controller death: 1.4-1.94 s.
- Allocation rate of the test OOM workload outran a 20 ms sampler: 48 MiB in well under the graceful-wait window (the in-cgroup OOM can precede any controller reaction at this allocation rate).
- Attempt launch cost is dominated by hashing the 6.6 MB interpreter twice (stage 0 and 1) and the fixed `k` samples.

## 8. Deviations from the addendum (none silent)

1. memsw limit = 2 x memory limit (6.7); the addendum does not fix the memsw value.
2. D-4 needs a second hop: the addendum's single `unshare ... python` exec line cannot both remount before the cap drop and keep ns-init as the producer process, so the stub runs as stage 0 (remount in the private mount namespace, refusing if its mount namespace equals the controller's), then `fexecve`s `setpriv`, then stage 1. `setpriv` is a third hash-verified binary (identity+SHA in the attempt record).
3. The sampler is not a thread: the controller is single-threaded. M13's "main thread exits while the sampler thread lives" is covered by a kernel-semantics probe (6.4) plus a static "no `threading.Thread(`" check, not by a live controller.
4. `site.main()` is not called by the stub in 4a (`site_main: false`): no venv exists (D-9 deferred) and the system interpreter would run the system `.pth` files. L10 checks `-S` + `site.main()` equals a normal start in `sys.path`, `sys.prefix`, flags and `ENABLE_USER_SITE`, and that a plain `-I -S` start runs no system `.pth`.
5. A SIGKILL of ns-init that the controller did not send is not observable as a signal (6.3); detection relies on `oom_kill` and the nonzero exit status.
6. A harness robustness constant `EXIT_RACE_GRACE_S = 0.25` (not policy): an exiting task is moved to the root css set before it becomes a zombie and loses `VmRSS` before that, so a mismatch is an escape STOP only if it persists for a live task beyond 0.25 s. Found by the tests (deterministic false STOPs without it).
7. The controller must collect owned children (reap) before it can observe an empty cgroup (zombies stay counted in `pids.current`); not stated in the addendum.
8. Extra STOPs beyond the addendum text: `unexpected_process_in_cgroup` (another writer), non-empty slot root, completion receipt must carry the attempt nonce, `cgroup.kill` skipped when an unowned process is listed, SIGTERM/SIGHUP/SIGINT treated as termination requests, `-O` and missing `-S` refused.
9. `prepare` does not probe v2 (the probe creates a cgroup); `probe_v2` exists for the smoke only. Falling back from option (a+) to (a) is a configuration choice (`use_v2=False`), never automatic.
10. The harness hash binds `n2h/*.py` and the three entry scripts, not the tests (their result is bound through the self-test report receipt).

## 9. Open issues and things I did not do

- M05, M06, M07 (4b). `run` cannot dispatch any real producer; real receipts always refuse.
- No sample scaling fit, no admission formula, no numeric policy proposal (by instruction).
- The optional OOM eventfd is a smoke result only.
- `reserve_bytes` basis and the `bash_cgroup_growth` baseline are measured once at launch; the Bash cgroup is shared with other agents, so a parallel agent can trip the growth STOP (by design, D-5).
- Tests create about 300 real cgroups and fail closed if another `n2-*` cgroup exists; they must not run concurrently with another N2 user. Timing assertions were loosened to 6 s for the PID 1 reaping latency (measured 1.4-1.9 s).
- `L-4` relies on CPython 3.11.17 `os.listdir(fd)` leaving exactly one transient duplicate (tested with positive and negative controls); a different CPython would fail closed with `E_FD`.
- Bytecode: `-B` everywhere; one subprocess uses `-X pycache_prefix` into scratch. No `__pycache__` remains.
- Signals sent during testing: only to processes the tests created (Popen children, one test-created double-forked grandchild killed with `os.kill`, one test-created process group with `os.killpg`, and my own L13 wrapper through a pidfd). The harness itself never calls `kill`/`killpg` (static test).

### Claims that rest on inference or on code paths the tests exercise only indirectly

- That the tool follows its SIGTERM with SIGKILL when it stops a background command is inferred from the tree disappearing; the SIGTERM itself was recorded.
- The `sigkill_direct_no_ns_init` branch (ns-init never verified, direct child killed through its pidfd) runs in `test_M03_L5_pidfd_open_failure_stop` but its step names are not asserted there; the SIGKILL-to-`unshare`-after-bounded-wait branch is asserted only with mock processes (`test_M13_unshare_killed_only_after_bounded_wait`), because real `unshare` always exited by itself.
- Behaviour with swap enabled, other kernels and other CPython versions was not examined.

## 10. Cleanup verification (end of work)

- `find /sys/fs/cgroup -name 'n2-*'` -> no output (0).
- No owned process survives (`ps` shows no `unshare`, `setpriv`, test `python3.11` children or zombies; zombies seen mid-run were reaped by PID 1).
- `scratch/` empty; `results/` holds the final results JSON, findings, measurements, self-test report and `FILES.sha256`; `.ruff_cache` remains in the harness directory.
- Repository untouched. `mount` operations occurred only inside `unshare --mount` namespaces (M04 tmpfs test, the stub's read-only remount). No sysctl, paging or OOM setting of another process changed; `fadvise(DONTNEED)` only on files created in the scratch directory.
- Residual host side effect: dying memcgs, memory `num_cgroups` 16 vs 5 at the start (6.10).


## 11. Review round 1 fixes

Independent review: ACCEPT_WITH_REQUIRED_CHANGES. Suite after the fixes: **161 tests, 161 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (99.4 s); `ruff check .` exit 0; cleanup clean (below).
Files whose SHA-256 changed: `IMPLEMENTATION_REPORT.md`, `MATRIX.md`, `n2h/cg.py`, `n2h/cli.py`, `n2h/controller.py`, `n2h/ledger.py`, `n2h/pidfd.py`, `n2h/stubsrc.py`, `n2h/sysfs.py`, `n2h/telemetry.py`, `tests/mockcg.py`, `tests/test_m01_modes.py`, `tests/test_m03_launch.py`, `tests/test_m13_death.py`, `tests/test_m14_accounting.py`, `tests/test_m15_m16.py`, `tests/test_review1.py` (new: `tests/test_review1.py`; full list in `results/FILES.sha256`). Harness hash now `5354abcf89b6bb029d4cd808e07924e5330185a0cad6e64c7ef766029553620c`, self-test report receipt `8b81e0f28bdeb6b971a007b35d69227edd9b0b559175b2da1c451bf353a7e41b`.

| # | Finding | Change | Test(s) |
|---|---|---|---|
| 1 (blocking) | Post-exit gates not applied: a last-interval failcnt, conservative peak or `pids.events max` was classified success | `_classify` first runs `_post_exit_gates` on the FINAL cgroup counters (`telemetry.evaluate_post_exit`: conservative peak cap, failcnt per `failcnt_action`, `oom_kill`/`under_oom`, `pids.events max`; live-only floors skipped); any breach turns success into `stopped` with `breach_*` | `test_M09_post_exit_failcnt_last_interval` (160 MiB into 48 MiB, seconds 0), `test_M09_post_exit_conservative_peak_last_interval`, `test_M09_post_exit_gates_classify_unit` (deterministic final counters, incl. a clean control) |
| 2 (blocking) | Note-6 mincore control vacuous | Cold-file control as described in 6.5; report 6.5/9 corrected | `test_M14_mincore_ownership_mode_control` |
| 3 | memsw=2x relies on no swap | `SwapTotal==0` checked in `prepare` and in preconditions, bound as `swap_total_bytes` in the attempt record, re-checked every sample (`swap_appeared` STOP) | `test_M09_swap_gate`; assertions added to `test_M03_L1_attempt_record_precedes_mkdir` and the prepare test |
| 4 | `pid_is_exiting` only Z/X | PF_EXITING (`/proc/<pid>/stat` field 9 & 0x4) is primary; the 0.25 s grace is fallback only; every absorbed mismatch is recorded in the sample (`absorbed_mismatches`, like `transient_vanished`) | `test_M16_pf_exiting_absorbed_and_recorded` |
| 5 | `remove()` removed every layout path | `create_all` records `(st_dev, st_ino)` of each directory it made; `remove` rmdirs only those and refuses on identity mismatch (`rmdir_identity_mismatch`); `cleanup_test_orphan` (test teardown only) adopts the existing directories explicitly | `test_M12_foreign_cgroup_survives_remove` (foreign dir pre-exists; our dir replaced by a foreign one) |
| 6 | unshare exit-1 masking | the stub writes a nonce-framed `exiting code=N` marker on every exit path it controls; a nonzero exit without a matching marker is STOP `ns_init_killed_or_unknown_exit` (to be ratified by the operator); `require_receipt` is mandatory when `synthetic_mock` is false | `test_M13_nonzero_exit_requires_nonce_framed_marker`, `test_M09_receipt_mandatory_for_non_mock` |
| 7 | stage-1 read-only probe / remount targets | probe must see EROFS (errno 30) exactly, else refusal `RO`; remount targets chosen by mountinfo fstype `cgroup`/`cgroup2`, not path prefix | `test_M16_D4_erofs_specific_and_fstype_selection` |
| 10a | DISPATCH_BLOCKED write failure | documented in `Ledger._block`: block then exists in memory only, the existing attempt record still blocks the slot, D-6 applies | covered by `test_M02_persist_failure_blocks_dispatch` |
| 10b | ns-init discovery | requires NSpid length exactly controller+1 and last field 1 | `test_M16_nsinit_discovery_requires_exact_nspid_length` |
| 10c | kmem probe weak | now asserts kmem accounting active (delta > 1 MiB) and kmem included in usage on this host | `test_M14_kmem_included_in_usage_probe` |

Side effects of the fixes: the OOM/SIGKILL discrimination now reports `ns_init_killed_or_unknown_exit` when `oom_kill` is 0 and no marker exists; tests that accepted `child_nonzero_exit` were widened accordingly. `cgroups_removed` is `None` (not `True`) when the attempt created nothing.

## 12. 4b carry-forward (recorded only, not implemented)

- `cli.run_after_gate` does not pass the prepare-recorded controller cgroup paths (`recorded_controller_paths`) to the attempt.
- The injection knobs (`inject`, `faults`, `expected_patch`, `stub_actual`, `env_extra_actual`, `no_subreaper`, `fs_faults`) are reachable through `driver.run_spec`; real dispatch must build its config without them and assert they are absent.
- `native.fadvise_dontneed_own` refuses repository inputs (it only touches our own scratch files, so it fails closed for real inputs); the cold-input method for real inputs needs a 4b design and a ruling on the host-global effect.
