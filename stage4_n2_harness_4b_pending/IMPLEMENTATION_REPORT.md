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

Harness hash (as bound by the run gate; `n2h/*.py` plus `n2_harness.py`, `driver_entry.py`, `run_selftests.py`): `eda94197db6cae464244d8bf844dc909ceef6d8d954a0d90240a00518138abec`.
Self-test report receipt (synthetic, `results/selftest_report.json`): `a0968f1b0e8b3cba53ca12c8babcbc05c36272b2557c1685032a79c6877bbf22`.
Full list (including this report, whose own hash cannot be listed here) in `results/FILES.sha256`; verify with `sha256sum -c results/FILES.sha256` from this directory. Line counts (before round 1): `n2h/` 4234, `tests/` 3840. Stub source 6961 bytes (limit 131072), workload source 5294 bytes.

| File (relative to `n2_harness/`) | SHA-256 |
|---|---|
| `MATRIX.md` | `832bab76334c74ea8a641b3ed04a37dff6977ad9fb7b5ee80c50df2fb6ed1204` |
| `driver_entry.py` | `e313638dbea00875238179a0fc4f2982c3b58e6dc22811258884874304b7c5a6` |
| `n2_harness.py` | `c79c00861d52b1aea3c8611de2fc76f21a22cd9c0fdeb7ac32c3a26c7db81769` |
| `n2h/__init__.py` | `d1ae8a435aaedd41aa5d154466d913fe2fcb407292a57148afb3013cc5a46ad8` |
| `n2h/allowlist.py` | `880a1a72dca2f854c9845b35330b5ed00d4623cff10646f77ffcf2a8d962870c` |
| `n2h/cg.py` | `ac9c13b972a1ee8f078a3b7df0268319b4bff1ab90cba6ceb31cfd3b80248000` |
| `n2h/cli.py` | `1e114a42d0a47d224317987ba4fa6d15708fa19f1a6cfb68216a4120042f688a` |
| `n2h/controller.py` | `5cf81e3ab54dc4734132a3d52db31c2e2d8e4819a8fa406d0b9795845165f1ca` |
| `n2h/driver.py` | `363b8de855f32d2c9bf606e81ba6d70ca5fb1d23eb5776c37161c25b57ff0696` |
| `n2h/errors.py` | `efb0fb87b278d72087380dc6d6c34b194a6ae2e1a0dbb41c0a66a9615ba1b3b2` |
| `n2h/fsio.py` | `206152edeb938b3995ee084a0364dab4752529badcb308737ec8ad0baea21be8` |
| `n2h/launch.py` | `7f480ca0ed48ea3fc1ba8d087fa435c91c70d5109bf8405837e70615a3acd21f` |
| `n2h/ledger.py` | `a6b0b322097d67c1841ac6a81230190e0b5fbb6c78b66b5d29e1680976fb0532` |
| `n2h/manifest.py` | `b7755623483ba745d5d9da566beb200c290c4749ba4f81f43ef45c21f9165407` |
| `n2h/native.py` | `820389997835cfce863bf9a8b6a6090ccda7388d21c4d7e7f5fd53dce6c8dead` |
| `n2h/pidfd.py` | `c088450266173b4f2dd628b03d741c0e447e222b528141f4bc66613af9bcb42e` |
| `n2h/policy.py` | `f29c38e644f5734930c41ad649ea1e3df938dfb79d7d5986a1d757c3a39d1a38` |
| `n2h/roots.py` | `3258864b174b17769242fd0a21542d39c1f369ded7a9ee5567f9428efedf421c` |
| `n2h/slots.py` | `d2ee1999d7c909fe8a79ed236508835f3f5824ae2cee552d92b33f86df7e95c6` |
| `n2h/stubsrc.py` | `18081711634d577df2576a4dd696ca56565d346b8ca171f6a7888fdf1a22874e` |
| `n2h/sysfs.py` | `85bab85aef54f0ab9034a3f3b4eb46783d266cb451ddf7b85d2a2ef486fce2f3` |
| `n2h/telemetry.py` | `8eeaa6cf20e9d6080da93ca54d47e45dcfae6df9ccdad046646644fd22ce743f` |
| `n2h/term.py` | `dccf1a25c93ede33763c9cfa0c6c207fb6666bf1b8181fff162ca46bde8c5452` |
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
| `tests/test_m15_m16.py` | `c9eca2031333d332632f81b6677fa0a2afe4e83d3f28ad585c0687d10c041862` |
| `tests/test_review1.py` | `100e61b26620b818b02d1208be705398f30413439a22362b7ec3046e2bf7220c` |
| `tests/test_meta.py` | `7190515894c326773e8dd697fbc80f10959ec874301f7e7dc9fbbb41b5da43e9` |

## 3. Commands run and exit codes

All from `n2_harness/`. Interpreter flags as shown.

| Command | Exit |
|---|---|
| `sha256sum docs/stage4_n2_linux_addendum.md` | 0, hash matches |
| `python3.11 -I -B -u run_selftests.py --report-out results/selftest_report.json` (final run) | 0: ran 179, fail 0, error 0, skip 0, 125.2 s |
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

Final suite: **179 tests, 179 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (no skips, so no non-coverage hidden behind skip counts). Per-control test counts:

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
- Also excluded from real dispatch (reachable only through `driver.run_spec`/test hooks): `hooks`, `sleep_before_monitor`, `fault_final_sample`, `close_gate_instead_of_release`, `wrong_gate_byte`, `swap_symlink_after_verify`, `rewrite_exec_*`, `precreate_after_attempt`. Real dispatch must build its attempt config without them, assert they are absent, and must not use `driver.run_spec`.
- The elapsed cap (`per_slot_elapsed_cap_s`) is checked only during live sampling, so up to one sample interval of run time before exit goes unflagged; 4b should decide whether to evaluate elapsed at exit.
- **Explicit driver-only exclusion list (round 6).** Real dispatch must build its attempt config without any of these keys or hooks, assert they are absent, and must not use `driver.run_spec`: `hooks` (all of: `at_cgroups_ready`, `at_S0`, `at_S1`, `before_release`, `after_release`, `after_attempt_record`, `after_verify`, `before_monitor`, `before_final_sample`, `before_finalize`); spec/config keys `pause_at`, `pause_s`, `sleep_before_monitor`, `fault_final_sample`, `telemetry_dev_full`, `finalize_boom`, `finalize_unserializable`, `marker_read_fault`, `post_exit_typeerror`, `record_signals`, `install_handlers`, `sigchld_ign`, `import_before_launch`, `buffered_stdout_probe`, `swap_symlink_after_verify`, `rewrite_exec_after_verify`, `rewrite_exec_at`, `precreate_after_attempt`, `close_gate_instead_of_release`, `wrong_gate_byte`, `stub_suffix`, `stub_actual`, `expected_patch`, `env_extra_actual`, `inject`, `faults`, `fs_faults`, `ledger_faults`, `no_subreaper`, `extra_allow_modules`, `plan`.

## 13. Review round 2 follow-ups

Suite after the follow-ups: **165 run, 165 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (101.3 s); `ruff check .` exit 0; cleanup clean. Harness hash `ceae8da64102e1517e728c26c81d90a2bd43285a3616cfd9c6b1e250aa9e546f`; self-test report receipt `8154b93836648e1ad603a60bfc5318c18eae021ac7f7b8ff9d333afd6235a493`. Files changed: `MATRIX.md`, `n2h/cg.py`, `n2h/controller.py`, `n2h/driver.py`, `n2h/fsio.py`, `n2h/pidfd.py`, `n2h/telemetry.py`, `tests/test_m15_m16.py`, `tests/test_review1.py` (`results/FILES.sha256` refreshed).

| # | Finding | Change | Test(s) |
|---|---|---|---|
| N1 | post-exit tests were also caught by the live breach check | `_monitor` now checks liveness first, so a child that already exited skips the live breach path and goes to the final sample plus post-exit gates. A `before_monitor` pause (driver option `sleep_before_monitor`) lets the child exit before the first monitor sample. The tests assert `exit.terminated_by_controller` is false, `termination` is null, and the breach code comes from the post-exit evaluation | `test_M09_post_exit_failcnt_last_interval`, `test_M09_post_exit_conservative_peak_last_interval` (rewritten) |
| N2 | final post-exit sample swallowed every error | errors are appended to `stops` as `final_sample_<code>` (injected label kept) and the run is classified `stopped`. The first attempt of the new test also showed a legitimate `sample_gap_exceeded` on the final sample, which is now reported instead of discarded | `test_M09_final_sample_failure_is_recorded_and_stops` (fault armed via `before_final_sample`) |
| N3 | per-thread check judged by the leader's process-level stat; `break` skipped remaining threads | `pid_is_exiting(fs, pid, tid)` reads `/proc/<pid>/task/<tid>/stat` for a given tid; absorption now `continue`s | `test_M16_exiting_leader_does_not_absorb_live_thread` (leader PF_EXITING, two live non-leaders outside: STOP; absorbing the first still STOPs on the second); `test_M16_pf_exiting_absorbed_and_recorded` updated |
| N4 | `synthetic_mock` defaulted to True | default is False at all three places in `controller.py` (the driver passes True explicitly); added to the 4b carry-forward | `test_M09_synthetic_mock_default_is_false` (missing key means a receipt is required; static check) |
| N5 | `os.stat` after mkdir outside the rollback | `fs.stat` immediately after each mkdir inside the rollback scope; failure becomes `cgroup_stat_failed` and everything made is removed | `test_M12_stat_failure_rolls_back_created_dirs` |
| N6 | loose OOM assertion | the OOM test now requires `oom_kill_detected` or `breach_oom_signal` in the stops. To make that true after a live failcnt STOP, final counters are evaluated after any STOP too (deduplicated by code) | `test_M15_oom_variant` |

4b carry-forward addition: real dispatch must pass `synthetic_mock` explicitly (the default is now False, so a forgotten key means a real slot with the receipt requirement).

## 14. Review round 3 follow-ups

Suite: **167 run, 167 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (104.4 s); `ruff check .` exit 0; cleanup clean. Harness hash `c4e90ebaf236ca27179310008abcdffec5465a2e58a45e1fb2c1514fc6962dfa`; self-test report receipt `54c75a0bbbe0ee8111ebd70eb6b7cad467e7510673cf9672a64aeb1f13c354a6`. Section numbering note: sections 11 and 13 keep their own round values (161 / 5354abcf... and 165 / ceae8da6...); sections 2 to 4 above carry the current values.

| # | Finding | Change | Test(s) |
|---|---|---|---|
| R1 | with liveness checked first, floors that persist after exit were no longer evaluated (reviewer demo: 300 MiB of output under `running_floor_disk_free_bytes` was reported success) | `_post_exit_gates` re-reads free disk and Bash-cgroup growth after exit and `evaluate_post_exit` checks `running_floor_disk_free_bytes` and `bash_cgroup_growth_max_bytes`; only genuinely live-only figures (available memory, headroom, elapsed) are skipped | `test_M09_post_exit_disk_floor_persists` (real cgroup, 160 MiB output, floor = free minus 100 MiB, child exits before the first monitor sample; asserts not terminated by the controller and `breach_floor_disk_free`; the file is removed by scratch cleanup) |
| - | gated breach followed by nonzero exit gave status `fail` | `_classify` returns `stopped` whenever any breach gate (or a failed final sample) fired | `test_M09_breach_then_nonzero_exit_is_stopped` |
| - | 4b exclusion list | extended in section 12 | (documentation) |
| - | report history | sections 11 and 13 restored to their own round values | (documentation) |
| - | stat-rollback test could leak real cgroups | wrapped in try/finally that removes any directory left | `test_M12_stat_failure_rolls_back_created_dirs` |
| - | `final_counters_failed` recorded twice | single `_record_final_counters_failed` helper, once per attempt | (covered by the existing final-counter tests) |

## 15. Review round 4 follow-ups

Suite: **169 run, 169 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (106.7 s); `ruff check .` exit 0; cleanup clean. Harness hash `bc1a0f3fe1087d9f5278e8868dfd7972a6e96722dab4d31ed33831676a68d2e0`; self-test report receipt `a0acc26bf34e8d54fd02d8bb92b68d5c694a7ac2227e839e9aa4e07a4786bd25`.

| # | Finding | Change | Test(s) |
|---|---|---|---|
| required | `_finalize` left status `success` when a final read failed (memsw, counters, markers); `memsw_check_action=stop` was silently skipped | every failed final read sets status `stopped`: final memory/memsw/counters (`final_counters_failed`), marker read (`final_marker_read_failed`, `_handle_markers_safely` now reports failure), log hashing (`log_hash_failed`). Scan of `_finalize`: the remaining excepts are informational (`memcg_counts`) or already STOP (`rmdir_failed`, terminal-write failure) | `test_M09_failed_final_reads_fail_closed` (injected fault on `memory.memsw.max_usage_in_bytes`; marker read broken by pointing it at a directory; both: status stopped, durable terminal receipt, cgroups removed) |
| non-blocking | `evaluate_post_exit` outside a try inside the `except N2Stop` handler could escape `run()` before `_finalize` | `_post_exit_gates` never raises: any parse/read error is recorded once as `final_counters_failed` and treated as a fired gate | `test_M09_malformed_counter_does_not_escape_gates` (malformed `pids.events`) |
| non-blocking | stale `evaluate_post_exit` docstring | updated: persistent floors are evaluated from fresh reads, live-only figures are not | (documentation) |
| record only | elapsed cap checked only during live sampling | noted in section 12 for 4b | (documentation) |

## 16. Review round 5 follow-ups (final 4a audit)

Suite: **175 run, 175 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (112.8 s); `ruff check .` exit 0; cleanup clean. Harness hash `ca21f4ed328326b3a2a2784ca19661a5efe2076c761ec492dcdf4a766c95b7ee`; self-test report receipt `39baa3dc2c9bc105cda15300adf3b56ac88ce05658fbc44fd379a144aa5a3ff3`.

| # | Finding | Change | Test(s) |
|---|---|---|---|
| R4-1 (required) | unguarded `tele.close()` could escape `_finalize` (ENOSPC on buffered telemetry) and leave no terminal receipt | `telemetry_close_failed` is recorded, status `stopped`, `_finalize` continues to the terminal receipt | `test_M09_telemetry_close_failure_fails_closed` (telemetry pointed at `/dev/full` from the `before_final_sample` hook; durable terminal receipt, status stopped, cgroups removed) |
| N1 | read error in `terminate()` wait loops skipped escalation | `_empty` treats a read/parse error as "not empty", records it once in `read_errors`, and the SIGKILL / `cgroup.kill` escalation continues | `test_M13_terminate_continues_escalating_on_read_errors` (read fault on `pids.current`) |
| N2 | `_create` opened O_EXCL before serialising (empty 0444 record, leaked fd on TypeError) | serialise first (`serialize_failed`, nothing created); the descriptor is closed in a `finally`; the controller retries once with a minimal body | `test_M02_serialize_failure_leaves_no_file_and_no_fd`; fallback covered by `test_M09_unexpected_exception_types_still_write_terminal` |
| N3 | failed rollback rmdir swallowed | carried in the CgroupStop detail (`rollback_failed: ...`) and `layout.rollback_errors` | `test_M12_rollback_failure_is_recorded` |
| N4 | structural guarantee | `_emergency_cleanup` and each `_finalize` step catch broad exceptions, record `finalize_*_exception`/`cleanup_exception`, and set status stopped; body building has a minimal fallback; invariant `stops and status == success -> stopped` before the terminal write; `memcg_counts` guarded for ValueError/IndexError; the misleading "never raises" comment corrected | `test_M09_unexpected_exception_types_still_write_terminal` (TypeError in the final reads, and an unserialisable body: terminal receipt present, status stopped, cgroups removed) |
| N5 | final marker poll discarded new error events | kept as `child_reported_*` stops (status stopped) | `test_M09_late_marker_error_events_are_kept` |

The `before_finalize` hook now runs before `_finalize` binds its locals so that it can replace `self.fs`. The hooks and driver options added here (`telemetry_dev_full`, `finalize_boom`, `finalize_unserializable`) are driver-only and belong on the 4b exclusion list in section 12.

## 17. Review round 6 follow-ups

Suite: **179 run, 179 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (125.2 s); `ruff check .` exit 0; cleanup clean. Harness hash `eda94197db6cae464244d8bf844dc909ceef6d8d954a0d90240a00518138abec`; self-test report receipt `a0968f1b0e8b3cba53ca12c8babcbc05c36272b2557c1685032a79c6877bbf22`.

| # | Finding | Change | Test(s) |
|---|---|---|---|
| RC-1 (required) | `_post_exit_gates` called inside the `except N2Stop` handler could raise TypeError/AttributeError/IndexError past `_finalize` (no terminal receipt, leaked cgroups) | `_post_exit_gates` is a thin wrapper that catches `Exception`, records `final_counters_failed` once and returns "gate fired"; the work moved to `_post_exit_gates_inner` | `test_M09_post_exit_unexpected_exception_on_stop_path` (TypeError injected into `evaluate_post_exit` while an elapsed-cap STOP is handled: terminal receipt, status stopped, cgroups removed) |
| RC-2 (required, docs) | explicit 4b exclusion list | section 12 now names every driver-only knob and hook | (documentation) |
| NB-1 | double close after a failing `os.close`; unguarded post-write hash | the descriptor number is cleared before closing; the post-write `sha256_file` becomes `LedgerStop persist_failed` | `test_M02_close_failure_does_not_escape_as_ebadf` |
| NB-2 | narrow excepts left in `_finalize` | `before_finalize` hook, `tele.close()`, log hashing and `memcg_counts` now catch `Exception`, each recording its stop (informational `memcg_counts` just yields `None`) | covered by the finalize tests of rounds 4-6 |
| NB-3 | fallback stop recorded only in the copied list | the serialize failure and fallback marker go into `self.stops` (and the body); a failing retry is also kept | `test_M09_fallback_stop_lands_in_self_stops` |
| NB-4 | `marker_read_fault` and `finalize_*` overwrote each other's `before_finalize` hook | hooks are collected and composed | `test_M09_finalize_hooks_compose` |


## 18. Step 4b

Author: Sonnet 5.5 (implementation writer, step 4b). Nothing here is approved or execution authority. Every number used by a test is a
`SYNTHETIC_TEST_POLICY_NOT_APPROVED` value. No national data, no repository `data/` path, no repository graph cache and no B0 file was read
(a suite-wide audit hook asserts it, below). The repository was not edited, committed or pushed (`git status --porcelain` empty before and after;
`.venv/` is excluded by `.git/info/exclude`). Sections 1 to 17 describe 4a; where they say "M05-M07 not implemented" or "real receipts refuse with
`real_dispatch_not_implemented_4a`", this section supersedes them.

Operator corrections received during the work and applied: (1) item (d) default is an attempt-private copy, no eviction of shared inputs;
(2) H0 is described narrowly (below); (3) no host/cgroup available memory is treated as a workload budget; (4) the review cap is a stopping point,
not an acceptance waiver; (5) resolved dependency versions are asserted and recorded.

### 18.1 What was built

| Component | File | Role |
|---|---|---|
| W0 population context (spec C) | `n2h4b/w0.py` | `FrozenContext` (deep identity-columns-only copy of the FULL primary frame taken at read time, before the producer's float64 cast; literal canonical cells; sha256). `W0Witness`: real-delegating witnesses at `nh_travel_matrix.gpd.read_file` (only the canonical district source, by resolved-path equality) and at the by-value binding `nh_travel_matrix.district_population` (refuses to install unless that binding IS the real `producer_io.district_population`). Calls the real helper on the full identity frame and the untrimmed population, selects the frozen positional prefix, restores the sampled index, asserts literal incoming identities/order/index. `verify_readback` (CSV and Parquet, literal compare, `lossy_code_roundtrip` STOP). `FullContextPopulation` for H3 gravity (the `producer_io.district_population` MODULE attribute, sample-to-full membership map). |
| H0 cache guard (spec D) | `n2h4b/h0guard.py` | `run_h0` (real cold build into an absent scratch cache, real `validate_graph_metadata`, closed archive, fingerprint/version capture, WARNING latch). `H1H2Guard`: forbidden-build sentinel at `graph_builder.build_canonical_highway_graph`, out-of-band `sentinel_latch`, non-raising WARNING-or-higher `LatchHandler` on the `graph_builder` logger (`logging_latch`), real-delegating wrappers on BOTH loader dispatch paths (builder module attribute and the matrix by-value alias), archive hash/version/fingerprint and directory listing verified before and after every load and at install, fresh-object check (no cached fake capture), cache-dir equality (no default/repository fallback). |
| Two-domain transfer (spec E) | `n2h/transfer.py` (stdlib) | `build_transfer` copies only the needed files into a disjoint scratch transfer root, records source/dest/bytes/sha256/regular/copy/readback, then `validate_transfer`: sample domain (summary and matrix identities equal the ordered first-n frozen positions, literally), full-context domain (complete identity context bound to the ORIGINAL district source: feature count and every literal identity re-read from that file; population copy hash equals the original; nothing trimmed). The domains are not required to equal each other. `check_outputs_identity` for freight/gravity rows, `h3_call_plan` for the exact real H3 arguments. |
| Slot mains | `n2h4b/slotmain.py` | H0/H1/H2/H3 as run inside the child (and in-process by the tests). `h3_enter_models` revalidates the transfer BEFORE either model and calls `compute_modal_costs(FreightCostParameters(), output_csv=, data_dir=)` and `run_port_gravity_model(alpha=1.2, beta=1.5, matrix_csv=, population_csv=, output_csv=, data_dir=)` under the gravity witness. A STOP writes `slot_stop.json`, emits a neutral `N2_<code>` error marker and exits 1. |
| Payload builder | `n2h/slots.py` | The payload is the concatenation of `n2h4b/w0.py`, `h0guard.py`, `n2h/transfer.py`, `n2h4b/slotmain.py` (plain ASCII text, no relative imports), hash-bound in the attempt record, exec'd by the stub with CONFIG/`mark`/`NONCE`. The harness hash now also binds `n2h4b/*.py` and `run_selftests_4b.py`. |
| Slot runner | `n2h/slotrun.py` | Builds the Attempt config without any driver-only key, asserts their absence, passes `synthetic_mock` explicitly (a non-bool is refused), passes the prepare-recorded controller cgroup paths, never imports the test driver. `parent_verify` (the PARENT hashes the cache archive bytes; latches fail the slot), `seal_cache` (root-owns and makes the H0 archive read-only for later unprivileged slots). |
| Gate / CLI | `n2h/ledger.py`, `n2h/cli.py` | Non-mock receipts refuse with `no_approved_policy_scope_receipt`; receipts that merely CLAIM approval refuse with `real_dispatch_disabled_pending_coordinator` (`REAL_DISPATCH_ENABLED = False`; the harness cannot authenticate approval). New `run` options: `--prepare-record`, `--payload-uid`, `--slot-config-json`. `run_after_gate` goes through `slotrun.dispatch`. |

H0 is a graph-build resource measurement on this host (cold build of the canonical highway graph from the highway source into a scratch cache). It is NOT
evidence that the national pipeline (matrices, isochrones, downstream models, audits, orchestration) fits, and it measures none of those.

### 18.2 Tests and commands

State the interpreters exactly (never `-O`; threads pinned to 1 in the child by the 4a environment allowlist; `PYTHONDONTWRITEBYTECODE=1` is set by the harness for the child, `-B` for the runners):

| Suite | Command (from `n2_harness/`) | Result |
|---|---|---|
| stdlib 4a + 4b stdlib additions | `/usr/bin/python3.11 -I -B -u run_selftests.py --report-out results/selftest_report.json` | **201 run, 201 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (138 s). 179 from 4a (two renamed/updated) + 22 in `tests/test_4b_carry.py` |
| venv 4b | `/home/user/gisindia4logistics/.venv/bin/python -I -B -u run_selftests_4b.py` | **49 run, 49 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (28 s): M05 10, M06 13, M07 12, contained 11, compat 1, preservation 2 |
| lint | `/root/.local/bin/ruff check .` | "All checks passed!" (`ruff.toml` extended: `tests4b/*` gets the same style-only ignores as `tests/*`; `n2h4b/slotmain.py` ignores F821/F811 because it is a payload part whose names come from the earlier parts) |

The two suites must not run concurrently (both create `n2-*` cgroups and fail closed on a foreign one). `run_selftests_4b.py` refuses to run unless `sys.prefix` is the project venv and `scripts.clean.graph_builder.__file__` resolves inside `/home/user/gisindia4logistics`; `test_M05_imports_resolve_inside_repository` and the in-child `module_origin` record assert the same for all six producer modules. [Corrected in section 19: the Python audit hook alone is NOT evidence (GDAL/pyogrio and pyarrow emit no audit events). The venv suite now runs inside a private mount namespace with an empty read-only tmpfs over `<repo>/data`.]

Synthetic inputs (`tests4b/fixtures.py`): an 8-feature grid highway network on shared vertices, 8 districts with the identity cases (cross-prefix code collision, MAHARASHTRA/"Gamma " literal labels, uncoded rows, a null-population row, optional same-state duplicate outside the prefix, optional 2**53+1 code), a 7-row population table, 12 major ports whose names map to the gravity model's 12 required keys plus one non-major row, 2 MMLPs.

Contained coverage (a contained child runs the REAL helpers; dispatched by `n2_harness.py run` with a synthetic-mock receipt through `cli.run_after_gate` -> `slotrun.dispatch`; six slots H0, H1(2), H1(3), H1(4), H2, H3 in ledger order, payload uid 65534, venv interpreter, `site.main()` enabled): M05 `test_M05_contained_h1_witness_inside_containment`, `test_M05_contained_lossy_code_roundtrip_stops_slot`; M06 `test_M06_contained_h0_cold_build_and_parent_hash`, `test_M06_contained_h1_h2_guard_clean_and_archive_unchanged`, `test_M06_contained_tampered_cache_stops_before_any_work`, `test_M06_contained_missing_cache_never_rebuilds`; M07 `test_M07_contained_h3_two_domain_transfer_then_models`; plus `test_4B_contained_runs_as_unprivileged_uid_with_site_main`, `test_M10_contained_only_declared_phase_boundaries`, `test_4B_module_origin_and_versions_recorded_in_child`, `test_M03_L10_site_equivalence_venv`. The remaining M05/M06/M07 tests are in-process unit tests on the same components (the same payload text is exec'd in-process for the slot code). `MATRIX.md` maps every test.

Dependency versions the M05-M07 tests ran against (asserted equal to `results/venv_freeze.txt` by `test_4B_installed_versions_equal_the_recorded_freeze`, written to `results/compat-4b.json`): Python 3.11.17, numpy 2.4.6, pandas 3.0.6, geopandas 1.2.0, shapely 2.2.0, pyarrow 26.0.0, pyproj 3.7.2, scipy 1.17.1, pyogrio 0.13.0. These DIFFER from the Windows-observed versions (geopandas 1.1.4, pandas 3.0.5, shapely 2.1.2, pyarrow 25.0.1); the test asserts all four differ and records both. Passing here says nothing about the Windows versions. Real H0 stays separately gated.

### 18.3 Carry-forward closure

| 4b carry-forward item | Closure | Test |
|---|---|---|
| Real dispatch config without driver-only knobs, asserted absent; not via `driver.run_spec`; `synthetic_mock` explicit | `slotrun.DRIVER_ONLY_KEYS` (the full 4a list plus `nonce`, `fs`, `cg_root`, `use_v2`); `assert_no_driver_only`; `build_attempt_config` refuses a non-bool `synthetic_mock`; `cli.run_after_gate` does not import the driver | `test_4B_runner_config_has_no_driver_only_knobs`, `test_4B_runner_refuses_non_mock_gate_by_itself` |
| `nonce`/`fs`/`cg_root`/`use_v2` | Decided: excluded for dispatch and asserted absent (random nonce, `RealFs`, real cgroup root and v2 are the only defaults) | same |
| `cli.run_after_gate` passes the prepare-recorded controller cgroup paths | `--prepare-record` (bound to harness hash and boot id; required for non-mock); mismatching paths stop in preconditions before any record or cgroup | `test_4B_cli_passes_prepare_recorded_controller_paths` |
| `after_attempt_record` hook inside the guarded try | moved into the try | `test_4B_after_attempt_record_hook_inside_guarded_try` |
| Guard member `close()` loop and `str(e)` in the BaseException handler | per-member guarded close (`member_close_failed`); `_exc_text` everywhere diagnostics stringify an exception | `test_4B_member_close_failure_is_guarded`, `test_4B_hostile_str_does_not_replace_outcome` |
| Ledger `finally` `os.close` OSError must not replace `LedgerStop` | guarded | `test_4B_ledger_close_oserror_does_not_replace_ledgerstop` |
| Short `os.write` | `fsio.write_all` loop (zero return = failure) used by ledger, receipts and payload write | `test_4B_short_write_is_completed_and_zero_write_fails` |
| Minimal-body serialize failure -> DISPATCH_BLOCKED | `Ledger.block()` called when even the minimal terminal body cannot be written (fault label `INJECTED_E_SERIALIZE_MINIMAL`) | `test_4B_minimal_body_serialize_failure_blocks_dispatch` |
| Compose all driver hooks | `driver.add_hook` composes every registration in order | `test_4B_hooks_compose_not_overwrite` |
| Final elapsed vs `per_slot_elapsed_cap_s` post-exit | `t_exit_observed` (monotonic time the controller observed the exit: an UPPER bound on the child's end) minus release time, evaluated in `evaluate_post_exit` | `test_4B_elapsed_cap_evaluated_post_exit` |
| `site.main()` in the stub stage 1 for the venv; re-run M03-L10 | `site_main` is a config key (True for every slot payload); attempt record binds it | `test_M03_L10_site_equivalence_venv` (venv: `-S` + `site.main()` equals a normal start; `-S` alone omits site-packages; no system dist-packages) |

### 18.4 Operator item (c): unprivileged payload

Implemented as recommended, pending ratification. `cfg payload_uid/payload_gid` (numeric, > 0, paired; the slot runner defaults to 65534; no system user is created). Stage 0 (root, private mount namespace, cgroupfs remounted read-only) execs the hash-verified `setpriv --reuid --regid --clear-groups --pdeathsig=keep --bounding-set=-all --inh-caps=-all --no-new-privs`; stage 1 verifies real/effective/saved ids, no supplementary groups and the cap sets. The payload can write only `home/`, `tmp/` and `out/` under the slot root (chowned to the uid, mode 0700); `payload.py`, logs, telemetry, the ledger and every other root-owned file stay unwritable. `check_traversable_by` refuses a slot root whose ancestor chain is not searchable by the uid (mode bits only; nothing is chmod-ed). Latches/receipt are read from `out/` (`payload_out_dir`).
Proof (`test_4B_payload_uid_not_root_and_cannot_write_root_owned`): uid=euid=gid=egid=65534, groups none, opening `payload.py`, `stdout.log` and the ledger root receipt for write gives EACCES, creating a file in the slot root EACCES, creating one in `out/` succeeds, `setuid(0)` EPERM, `/etc/shadow` EACCES; the attempt record carries the ids.
Consequences found and handled (deviations, section 18.7): (i) a credential change clears `PR_SET_PDEATHSIG`, so `--pdeathsig=keep` is used and stage 1 still verifies SIGKILL; (ii) an unprivileged opener meets the DAC check (EACCES) before the read-only-mount check (EROFS), so in uid mode the read-only cgroupfs is proven from `statvfs` mount flags plus EACCES instead of EROFS.

### 18.5 Operator item (d): cold-input handling for real inputs (REVISED by the operator)

This reverses the earlier acceptance (D-11) of host-global `fadvise` for real inputs. Default design, implemented:
1. Each admitted input (relative path under a source root, resolved-path containment in the scope's read roots, no symlink in the path, regular file) is copied into an attempt-private file the controller owns (`native.copy_input_private`: exclusive create, mode 0444, fsync). The source is opened read-only (`O_NOATIME` when permitted), its identity is compared before and after, and the copy's hash is compared with the bytes read from the source and then with a re-read of the copy. Inputs are never modified.
2. Only the private copy is evicted (`fadvise_dontneed_own`, still restricted to files we created in scratch) and measured (mincore); the producer is pointed at the private root (same relative layout).
3. A shared source is NEVER passed to `fadvise` (spied in `test_4B_private_copy_leaves_shared_sources_untouched_and_unevicted`, which uses a root-owned and a uid-65534-owned read-only file). Its residency (readable by the root controller regardless of ownership) is recorded and added to `resident_bytes_elsewhere`, hence to the conservative figure (`shared_input_resident_bytes`, re-measured at launch and pre-release; `test_4B_controller_records_shared_residency_in_conservative_figure`).
Wired in the controller as `cfg private_inputs {source_root, rel_paths, private_root, read_roots}` (recorded in the attempt record). The CLI does not expose it: deriving the roots from the real scope receipt is coordinator work (it needs the approved read scope).
Options NOT implemented, needing explicit operator approval: (a) evicting shared inputs with `posix_fadvise(DONTNEED)` on an `O_RDONLY` descriptor (permitted for any reader, root included, but host-wide: it evicts pages other cgroups use; not verified here, deliberately not exercised on a shared file); (b) reading inputs without any copy and relying on the disclosed shared residency alone (the copy-free variant of the same disclosure).

### 18.6 Measurement inputs for a numerical-limit recommendation (no numbers proposed)

The coordinator must measure, on the host and at the time of the decision, and bind the figures and their timestamps to the policy basis: (1) the `claude-code-bash` memory cgroup limit and its current usage; (2) the limits and usage of every ancestor cgroup up to the memory root (the 4a idle measurement found an ancestor limit binding below host RAM; this is a measurement input, not a budget); (3) idle usage of the Bash cgroup excluding the attempt, its drift between sessions and the growth caused by other agents sharing it; (4) page-cache and dying-memcg behaviour after attempts (`memcg_dying_residual`); (5) disk free on the scratch filesystem and the dirty/writeback allowance; (6) the basis of `reserve_bytes` and `conservative_peak_max_bytes` (what the reserve protects: the controller, the Bash tool, other agents); (7) the resident bytes of the interpreter, venv shared objects and shared inputs (recorded per attempt as `resident_bytes_elsewhere`); (8) the swap state (must stay 0 for the memsw rule). Host or cgroup available memory is not a permitted workload budget; the harness tests use clearly labelled synthetic values (`BIG_POLICY` in `tests4b/helpers4b.py` exists only so the real imports fit).

### 18.7 Deviations and findings (none silent)

1. The unprivileged-uid tests use a scratch base `/tmp/n2-4b-*` created with mode 0711 and removed by the tests, because the scratchpad chain is 0700 (an unprivileged payload cannot traverse it). It holds synthetic data only.
2. `setpriv --pdeathsig=keep` (credential change clears the signal). A residual race of a few microseconds exists between the credential change and the restore, the same kind as the one inherent in `unshare --kill-child`.
3. In uid mode the read-only proof is `statvfs` flags + EACCES (not EROFS).
4. `cg.members()` re-reads a disagreeing hierarchy listing up to 6 times (5 ms apart) before raising `listing_disagrees_between_hierarchies`; a process that exits between the two reads produced a spurious `membership_read_failed` STOP twice during development of the contained runs. Persistent disagreement still STOPs (existing mock test unchanged).
5. Elapsed is measured to the controller's observation of the exit (upper bound); a child that ends just under the cap but is observed just over it is flagged.
6. The run gate has two new refusal codes and the 4a test for the old code was updated (`test_M01_run_nonmock_valid_receipts_refused_no_approved_receipt`); `test_4B_interfaces_declared_not_implemented` was replaced by `test_4B_slot_table_and_payload_builder`; the M11 repository-scan test excludes `.venv/`; the copy test for the harness hash also copies `n2h4b/`.
7. The needle test (no national path literals in `n2h/`) exempts only `nh_district_travel` in `transfer.py`, which names the producer's relative file names inside a scratch transfer root.
8. W0: "untrimmed population" is checked by row count against the separately counted source CSV and by the source hash in the transfer; the full-frame release is asserted by weakref in the witness. The producer's own retention of the returned prefix is not measured.
9. `private_inputs` is not exposed through the CLI (needs the approved read scope).
10. Stdlib CSV parsing in `transfer.py` compares literal text/Decimal; the model-side pandas readers may normalise (for example the string `NA`); that path is not altered.

### 18.8 What remains

Real dispatch is refused at the gate and again in the runner (`REAL_DISPATCH_ENABLED = False`); there is no approved policy or scope receipt. Open for the operator: numerical policy and read/write admission (step 5), decisions (a), (b), (e), ratification of (c) and of the revised (d), and the decision on the `fadvise` options in 18.5. For the coordinator: authenticating real approval, deriving `private_inputs` and CONFIG from the approved scope, real control root and dispatch, the H1(12/64/192) row counts against the real source order, and everything in section 6 that is host-dependent. Real data stays locked; blockers that survive the capped Opus reviews are returned to the operator unresolved, not waived.

### 18.9 Cleanup verification

`find /sys/fs/cgroup -name 'n2-*'` -> no output; `/tmp/n2-4b-*` -> none left (the one leftover from an early failed setup was removed and `setUpClass` now cleans up on failure); `/tmp/n2smoke` (my scratch smoke script directory) removed; no `__pycache__` in the harness (`-B`); `scratch/` empty; repository `git status --porcelain` empty and HEAD unchanged; signals were sent only to processes the tests created (the harness itself never calls `kill`; static test unchanged); `mount` operations only inside `unshare --mount` namespaces; `fadvise(DONTNEED)` only on files created in the scratch directories.


## 19. Review round 1 (Opus) fixes

Suites after the fixes: stdlib `/usr/bin/python3.11 -I -B -u run_selftests.py --report-out results/selftest_report.json` **213 run, 213 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (140 s); venv `.venv/bin/python -I -B -u run_selftests_4b.py` **58 run, 58 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (35 s, run sequentially after the stdlib suite; repository check printed `REPO_UNCHANGED True`); `ruff check .` clean. Harness hash `9f9348a44e036dbf6df13d2dc0b2b0bd66c8855b7cd62e7f3bd4b2f52a1928f4`; self-test report receipt `3844e76e2062a8fa3e0b4a8af97c6d22bdb004817053093bef6907cf0451195a`.

| Finding | Change | Test(s) |
|---|---|---|
| B1 mock path could reach real inputs / repository | `slotrun.validate_slot_config` runs BEFORE any attempt record or cgroup: unknown CONFIG keys refused; every path-valued key (`data_dir`, `cache_dir`, `out_dir`, `transfer_record`, `district_source`, `population_source`) must be canonical (no symlink), inside the scratch base, outside the repository (`reject_forbidden`), and inside the receipt's read/write scope (`cache_dir`/`out_dir` need write scope); `repo_root` must equal the repository DERIVED from the payload interpreter's venv (`slots.verified_repo_root`, an import root only); `sample_rows` bound to the slot name (12/64/192) for a non-mock dispatch; `--payload-uid` required whenever `--slot-config-json` is given (CLI and `dispatch`); `slotmain` refuses (`config_path_under_repository`) any data/cache/out/source path under `repo_root` itself | `test_4B_config_repo_data_dir_refused_before_any_attempt`, `..._repo_cache_dir_...`, `test_4B_config_missing_payload_uid_refused`, `test_4B_config_other_refusals` (each asserts no attempt record, empty slot root, no cgroup, no `outputs/`), `test_B1_slot_code_refuses_repository_paths_itself` |
| B2 root helpers followed payload-planted symlinks | `parent_verify`, `seal_cache`, `sha256_file`, the completion-receipt reader, the `slot_result.json` reader and the transfer copy/hash now open with `O_NOFOLLOW|O_NONBLOCK`, `fstat` regular (single link for the archive), hash from the descriptor, `fchown`/`fchmod` on the descriptor; the cache directory must contain exactly `[archive]`; symlink, hard link, FIFO, directory entry, extra file and a symlinked cache directory are refused and nothing is chmod-ed. Audit of other root-side operations inside uid-writable dirs (`out/`, cache): receipt reader, `slot_result.json`, transfer inputs, sha helpers (all converted); latch checks use `lexists` (no follow-up action); the controller's own chown/chmod run on directories IT created before the child starts | `test_4B_symlinked_archive_refused_and_target_untouched` (a 0600 uid-owned target keeps mode and owner), `test_4B_cache_dir_content_rules`, `test_4B_other_root_side_reads_refuse_symlinks` |
| B3 no-`data/`-read evidence unsound | `run_selftests_4b.py` re-executes itself under `unshare --mount --propagation private` and mounts an empty read-only tmpfs over `<repo>/data` (and `<repo>/outputs` if it exists; it does not exist) so any reader (GDAL, pyarrow, open) fails; children inherit it; the audit hook is secondary; the outer process checks `git status`, HEAD and absence of `outputs/` before and after (`REPO_UNCHANGED`); section 18.2 wording corrected | `test_M11_repository_data_is_masked_for_every_reader` (empty read-only mask; CONTROL: the outer runner lstat-ed the sentinel files in the unmasked view, and `gpd.read_file`, `gpd.read_parquet` and `open` of exactly those paths now fail), `test_M11_no_repository_data_opened_by_python_in_the_suite`, `test_M11_repository_working_tree_unchanged_outside_masked_dirs` |
| B4 parent-side verification did not gate H1/H2 | `slotrun.dispatch(slot_run=True)` runs `parent_verify` after the terminal receipt and persists an immutable, hash-bound `parent_verification` receipt per slot (`Ledger.create_parent_receipt`); `seal_cache` is called by the runner after a passing H0; `Ledger.can_dispatch_slot(..., parent=...)` requires passing receipts for all earlier cache-reading slots, an H0 receipt whose `archive_sha256` equals CONFIG `expected_graph.sha256` for H1*/H2, and for H3 a `TRANSFER` receipt (`slotrun.register_transfer`, the parent-built record hash) equal to the hash of the CONFIG record; the CLI exits 4 when the parent verification fails | `test_B4_failed_parent_verification_blocks_h1_even_though_h0_child_succeeded` (child success, wrong archive name -> receipt not ok -> H1 refused, no attempt consumed), `test_B4_h1_requires_config_graph_hash_to_equal_the_h0_receipt` (also: runner sealed the archive root-owned 0444), `test_B4_h3_needs_registered_transfer_record_and_config_sha`, `test_4B_parent_receipts_gate_later_slots`, `test_4B_h3_needs_registered_transfer_record`; contained chain updated to register the transfer |
| N1 transient disagreements not persisted | terminal body carries `membership_transient_disagreements` | `test_4B_membership_transient_disagreement_counted` (first read disagrees, second agrees; persistent disagreement still STOPs), `test_4B_terminal_body_carries_transient_disagreement_count` |
| N2 | `run_h0` checks `LOGGER.isEnabledFor(WARNING)` | `test_N2_run_h0_checks_logger_enabled` |
| N3 | `FullContextPopulation.install` requires the real helper (module, qualname, `__code__` file, no closure/`__wrapped__`); `restore` verifies the attribute is still its own wrapper and the real helper afterwards | `test_N3_h3_witness_requires_and_restores_the_real_helper` |
| N4 | `validate_transfer` asserts `sample_positions == range(sample_rows)` (required at model entry); CONFIG carries `transfer_record_sha256`, checked by the runner (gate) and by the H3 payload | `test_N4_positions_and_registered_record_checked` |
| N5 | latch written at exit after a valid receipt, child exits 0 before the first monitor sample (hook), asserts `cache_latch_with_exit_zero` specifically | `test_4B_latch_in_uid_out_dir_with_exit_zero_fails` |
| N6 | `fadvise_dontneed_own` takes the set of (dev, ino) the attempt created (private copies; test-declared `cold_inputs`, now on the driver-only list) and refuses anything else | `test_4B_fadvise_refuses_inodes_the_attempt_did_not_create` |
| N7 | `--results-dir` for both runners | `test_sweep_detects_stale_scratch_dirs_and_runner_has_results_dir` |
| Sweep | both runners refuse to start when stale `/tmp/n2-4b-*` directories exist; the outer 4b runner also fails if any are left behind | same test |

Notes: `archive_name` is now a CONFIG key (the stdlib modules carry no cache path literal); `parent_verify` raises without it. Nothing else changed in scope. Cleanup at the end: no `n2-*` cgroups, no `/tmp/n2-4b-*`, no `__pycache__`, empty `scratch/`, repository `git status` empty and HEAD unchanged.
