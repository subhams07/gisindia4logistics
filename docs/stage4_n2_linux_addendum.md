# N2 Linux addendum — containment, parent death, termination and telemetry

Addendum to [the N2 resource specification](stage4_n2_resource_spec.md) (sections F, G, H) for the Linux cloud host in [the handoff](claude_remote_handoff.md) section 0. **Design only: not approved, not execution authority.** No harness exists. Base-spec sections A–E and I–J apply unchanged except where noted. Every "smoke-verify" item is unproven on this host and must be proven by the synthetic native smoke before any real run is considered.

Terms used below:
- **Attempt cgroups:** the cgroup set created for one attempt and owned by the controller.
- **Controller:** the dispatch process, owned by the coordinator.
- **Direct child:** the process the controller forks; after exec it is `unshare`.
- **Producer / ns-init:** the Python process that is PID 1 of the new PID namespace.

## Revision history

- **r1** (SHA-256 `95771b73…44ca`): initial draft.
- **r2** (this revision): addresses the independent Opus review, verdict GO_WITH_REQUIRED_CHANGES. Each finding and how it was handled:
  1. **Controller killed while FROZEN** (blocking):
     - Frozen windows are now minimal and bounded (`max_frozen_window_ms`), and their start and end are persisted.
     - cgroup v2 core `cgroup.freeze`/`cgroup.kill`/`cgroup.events` in a non-root `unified` child is now the preferred kill/freeze path, subject to smoke proof. The v1 freezer is the fallback.
     - M13 now covers "controller killed while FROZEN", leading to an M12 STOP and an operator-ruled recovery (§3.4).
  2. **R-2 escalation:** THAW first (a thaw failure is a STOP), then the per-member fd kill. Every STOP path leaves the cgroup THAWED, never half-frozen (§3).
  3. **"Absent from cgroup.procs" is no longer treated as an exit:**
     - Each member gets a pidfd at first listing; it counts as exited only when that pidfd is readable.
     - A member that is absent but alive is a containment STOP.
     - Each sample checks that the direct child and ns-init are still members.
     - Optional hardening is listed: read-only `/sys/fs/cgroup` and capability drop (§1, D-4).
  4. **Gate-pipe EOF:** the pre-exec order is fixed and its fds are asserted (§1 L-4; M03-L4, M13).
  5. MiB figure corrected.
  6. `CAP_SYS_RESOURCE` absent; the wording is corrected.
  7. The controller runs with `-I -S`; a stdlib module allowlist is checked before fork; the venv has no system site packages; M03-L10 is widened.
  8. The memory/memsw cross-check is now made only after exit, with a tolerance.
  9. Gate metrics redefined (§4): the peak is made a true upper bound, running floors use working set, anonymous memory is reported but does not gate, the finite-limit sentinel is defined, limits are page-aligned and written in a fixed order.
  10. The shared `claude-code-bash` reserve basis and a concurrency rule were added (`bash_cgroup_growth_max_bytes`).
  11. NSpid rule tightened; a per-member pidfd verify primitive was added. It replaces signalling by listed PID and narrows R-2.
  12. Reaping via `WNOWAIT` then `wait4`; SIGCHLD prepare check; fd-verified `execve`; environment allowlist; per-attempt marker nonce; smoke items for `unshare` status propagation and for what a Bash timeout kills.
  13. New injected faults added; M15 is required pending approval, with a non-OOM variant.
  14. Third-party subprocesses and `pids.max` counting of threads are now disclosed.
  15. Probe record extended (dying memcg, `sbx-telemetry`).
- **Reviewer rulings adopted:**
  - R-1 confirmed, together with the ambiguity rule in §2.
  - Q-R4: NO-GO without CLONE_NEWPID.
  - Q-R6: `oom_score_adj=1000` on the child is acceptable.

## 0. Host facts the design depends on

Survey and probes from 2026-10-17 (r2 used read-only probes only; see §9).

**cgroup layout**
- cgroup **v1** controllers are mounted separately: `memory`, `pids`, `freezer`, `cpu`, `cpuacct`, `cpuset`, `blkio`, `devices`.
- A cgroup2 mount at `/sys/fs/cgroup/unified` has `hugetlb` as its only controller. Kernel features include `nsdelegate`, `memory_localevents` and `pids_localevents`. Its root shows no `cgroup.kill`/`cgroup.freeze` (normal for a root cgroup). Whether a non-root child supports them is not exercised.

**Platform memory cgroup**
- Bash-tool processes run in memory cgroup `/process_api/<id>/claude-code-bash`.
- It has a platform limit of **14,343,688,192 B = 13,679.2 MiB (13.36 GiB)**, with `use_hierarchy=1`.
- That cgroup is **shared by every Bash-tool command of every agent in the session**.
- The Claude Code process sits in the parent `/process_api/<id>`, which has no limit (covered only by host `MemAvailable`). The root has no limit either.
- A platform cgroup `sbx-telemetry` exists (memory limit 64 MiB, `pids.max` 16).
- PID 1 is the platform's `process_api`, not systemd.

**Host**
- 15.7 GiB `MemTotal`, no swap, `overcommit_memory=0`, `pid_max=32768` (PID reuse is realistic), THP `madvise`, `panic_on_oom=0`.
- Seccomp is off. `CapEff=000001fffeffffff` is broad but **lacks `CAP_SYS_RESOURCE`** (bit 24).
  - Raising one's own `oom_score_adj` is allowed.
  - Lowering `oom_score_adj` and raising hard rlimits are not.
- util-linux 2.39.3 provides `unshare --pid --fork --kill-child`.
- About 29 GiB of disk is available on `/`.

**Python**
- 3.11.17 has `os.pidfd_open`, `os.waitid`/`P_PIDFD`/`WNOWAIT`, `signal.pidfd_send_signal`, `os.wait4`, and `os.execve` with an fd. It has no `os.unshare`.
- System `.pth` files exist: `/usr/local/lib/python3.11/dist-packages/uno.pth` and `/usr/lib/python3/dist-packages/distutils-precedence.pth`.

**Probed in r1**
- Empty child memory and pids cgroups can be created; membership shows up in `/proc/<pid>/cgroup`; counters start at zero.
- `memory.max_usage_in_bytes` and `pids.peak` record kernel-maintained peaks.
- Memory allocated before joining stays charged to the old cgroup.
- About 0.7 MB of page cache stays charged after exit, and `rmdir` still succeeds. A dying memcg may persist afterwards; see §9.

**Consequence:** the limit that binds is the shared platform cgroup, not host RAM. An OOM there could kill the controller, other agents' commands or the shell, so every attempt gets its own hard limit below that ancestor.

## 1. Section F mapping

| Section F requirement | Linux mechanism | Status / limitation |
|---|---|---|
| Immutable attempt record, written with exclusive create before the child exists, keyed by approval SHA plus slot; consumed on failure; fail closed | `os.open(O_CREAT\|O_EXCL\|O_WRONLY\|O_NOFOLLOW)`, then `fsync` of the file and its directory, **before** the cgroups are created. It also binds: `boot_id`, kernel release, the controller's own cgroup paths (read at `prepare`), the planned cgroup names, and the SHA-256 of the `unshare` and interpreter files | **Met on this host.** It is **not durable across container loss**. Loss is treated as "consumption unknown → no dispatch without a new ruling" (D-6) |
| Launch record after creation, before release | Direct-child PID, plus `starttime` (field 22 of `/proc/<pid>/stat`), plus a pidfd opened right after `fork` while the child is still unreaped, plus the cgroup paths and the inode/`st_dev` of each cgroup directory, plus gate state `closed` | **Met.** The ns-init host PID goes in the first observation record after release |
| Job created before the child and non-inheritable | Exclusive `mkdir` (EEXIST → STOP), **before fork**, of: v1 `memory` (nested under the controller's memory cgroup), v1 `pids`, and a **kill/freeze cgroup**. The kill/freeze cgroup is preferably a v2 `unified` child using `cgroup.freeze`/`cgroup.kill`/`cgroup.events` (D-3); the fallback is a v1 `freezer` child. Limits must be page-aligned. Write `memory.limit_in_bytes` **before** `memory.memsw.limit_in_bytes`, then read both back exactly. Assert the cgroups are empty and their counters zero. The gate pipe is `O_CLOEXEC` | **Met, pending smoke proof** (neither v2 kill/freeze nor v1 freezer was exercised). Failure consumes the attempt; only the empty cgroups this attempt created are removed |
| `CREATE_SUSPENDED` | The controller is started as `python3.11 -I -S -B -u` (no system or venv `.pth` processing) and imports only stdlib modules. Before fork it asserts that `sys.modules` keys ⊆ a pinned stdlib allowlist, that it is single-threaded (`threading.active_count()==1` and `/proc/self/task` count 1), that SIGCHLD is not `SIG_IGN`, and that it has no other waiter or child. It then calls `os.fork()` from its main thread. The child is a copy of that clean interpreter and blocks on the gate **before `execve`** (L-4) | **Met (different primitive).** `Popen(preexec_fn=…)` is not used: it deadlocks against a gate |
| Only stdout/stderr + `NUL` stdin inheritable | L-4 step 3 handles this. Phase markers are tagged stderr lines carrying a per-attempt nonce and `CLOCK_MONOTONIC` ns | **Met** |
| Assign the suspended child to the job | The controller writes the PID to the kill/freeze cgroup first, verifies it, then does the same for `memory` and `pids`. Each is verified through both `/proc/<pid>/cgroup` and a listing equal to `{pid}`. The §2 ambiguity rule applies | **Met** |
| Persist launch record + telemetry, then resume | Write the launch record, start the sampler thread, require `k` consecutive complete samples, then write the gate byte `b"G"` and close `gate_w` | **Met** |
| Child's first statements stdlib-only, self-check before first-party import | Exec is by **absolute path, from an fd whose SHA-256 was verified after open** (`os.execve(fd, …)`; fd closed on exec), with an **explicit environment allowlist**: `PATH`, `LANG`, `TZ`, `HOME`, scratch `TMPDIR`, `PYTHONDONTWRITEBYTECODE=1`, the thread variables per D-1, and nothing else. No proxy, credential or `PYTHON*` variables. The target is `unshare --pid --fork --kill-child=SIGKILL -- <abs venv python3.11> -I -S -B -u <stub> <expected-json>`. The stub uses only `sys`/`os`/`ctypes`/`json`/`time`/`signal` and checks: its cgroup paths; `NSpid` has exactly two fields with the last `== 1`; `prctl(PR_GET_PDEATHSIG)==SIGKILL`; `sys.flags.isolated`; the environment equals the allowlist. It installs a SIGTERM handler, then runs `import site; site.main()`, then authenticates and imports the harness body | **Met.** Containment is enforced by the kernel before exec; the self-check only verifies it. The venv is created without `--system-site-packages` |
| Parent death kills the owned tree | Layers: (1) gate EOF before release; (2) `PR_SET_PDEATHSIG(SIGKILL)` on the direct child. It survives exec of non-setuid `unshare` but follows the **forking thread**, so the controller's main thread must outlive the attempt and never return while the sampler runs; (3) `--kill-child` gives ns-init PDEATHSIG SIGKILL; (4) the kernel kills the whole namespace when ns-init dies. **Exception:** a v1-frozen tree does not act on a queued SIGKILL until it is thawed (§3.4) | **Met only with CLONE_NEWPID (required; NO-GO without it)** and only outside frozen windows. Under v2 freeze, SIGKILL still kills frozen tasks (smoke-verify). Leftover cgroups mean STOP at the next start (M12) |
| Normal termination through the job, never by PID | §3. Signals are sent **only through a verified per-member pidfd** (§3.1), or through v2 `cgroup.kill`. Never a raw `kill(pid)`, `killpg`, or `ps`/`pgrep`/`/proc` scans | **Met, pending smoke proof** |
| Exact-handle cleanup before assignment | `pidfd_send_signal(SIGKILL)` on the gated, never-exec'd child, then `waitid(P_PIDFD)` | **Met** |
| No later slot until logs drained, tree ended, receipt durable | Tree ended means all of: the direct child's pidfd is readable and the child has been reaped (§3.5); every member pidfd is readable; `cgroup.procs` is empty everywhere; `pids.current==0`; v2 `cgroup.events populated 0` when used. Record the final counters, then `rmdir` (a failure is a STOP). Never `force_empty` | **Met** |
| Containment cannot be escaped | The child runs as root with `CAP_SYS_ADMIN`, so it **could write itself into another `cgroup.procs`**. Detection: each member's pidfd is opened at first listing. A member that is absent from the listing but whose pidfd is not readable, or a direct child or ns-init that is not a member, means **containment STOP**. Prevention is optional hardening (D-4): `unshare --mount` with a read-only remount of `/sys/fs/cgroup` for the child, plus a `setpriv` drop of the bounding and inheritable caps (at least `CAP_SYS_ADMIN`; the minimal set to be measured) | Detection: **met**. Prevention: **operator decision** |

**L-4 pre-exec order (child side, stdlib only, no allocation):**
1. `prctl(PR_SET_PDEATHSIG, SIGKILL)`.
2. `os.getppid() == controller_pid`, otherwise `_exit(E_PARENT)`.
3. `dup2` the slot stdout, stderr and `/dev/null` onto fds 0–2, then `os.closerange(3, maxfd)` excluding `gate_r`. This **closes the child's copy of `gate_w`**, so the controller holds the only write end.
4. Assert that `/proc/self/fd` contains exactly {0, 1, 2, `gate_r`}, otherwise `_exit(E_FD)`.
5. Blocking `os.read(gate_r, 1)`. EOF or any byte other than `b"G"` → `_exit(E_GATE)`, never exec.
6. Re-read `/proc/self/cgroup` and compare it with the expected paths, otherwise `_exit(E_CONTAIN)`.
7. Write `1000` to `/proc/self/oom_score_adj`.
8. `os.execve(fd, …)`.

## 2. Section F cleanup ambiguity — R-1 (confirmed by reviewer)

| State | Entered when | Only permitted termination |
|---|---|---|
| S0 forked-gated | `fork` returned | pidfd SIGKILL + `waitid` (the exact-handle exception) |
| S1 assigned | Kill/freeze-cgroup membership is **verified** | Cgroup termination (§3); the direct child's pidfd is used only to wait and reap |
| S2 released | Gate byte written | Cgroup termination (§3) |

**Ambiguity rule (adopted).** Suppose the write to the kill/freeze `cgroup.procs` returns success but verification fails or cannot complete. Then:
1. Freeze the cgroup and list it.
2. If the child is listed → S1 path.
3. Otherwise thaw and take the S0 pidfd path.
4. Record the ambiguity in the terminal receipt either way.

Failures in the launch record, telemetry, or the memory/pids joins at S1 are cleaned up through the cgroup. Closing the gate (EOF) may come first, but it never replaces the cgroup kill and the empty-cgroup check.

## 3. Termination procedure (S1/S2)

**3.1 Verified member primitive.** At first listing, for each member PID:
1. `pidfd_open(pid)`.
2. Verify that `/proc/<pid>/cgroup` shows the attempt paths and that `starttime` matches its first observation.
3. `pidfd_send_signal(fd, 0)` succeeds.

From then on, **signal only through that fd**. A member that fails verification means containment STOP. The race between listing and opening is harmless because verification happens after the open.

**3.2 Graceful.** `pidfd_send_signal(ns_init_fd, SIGTERM)`. No freeze is needed, because the target is a verified fd. Wait up to `graceful_wait_s` for every member fd to become readable and the listing to empty.

**3.3 Hard kill.**
- **v2 path (preferred, D-3):** write `1` to `cgroup.kill`, then wait for `cgroup.events populated 0`.
- **Fallback, v1 freezer:** after the graceful attempt, `pidfd_send_signal(fd, SIGKILL)` to every verified member, with no freeze.

  A freeze is used **only** to get a stable listing when verification races with forking: FROZEN → list and verify fds → **THAW** → signal through the fds. The window is bounded by `max_frozen_window_ms`, and the freeze and thaw timestamps are persisted.

**R-2 (narrowed by the primitive).** If a freeze does not reach FROZEN within `freeze_wait_s`:
1. Write THAWED. If the thaw fails or does not complete within `freeze_wait_s` → STOP.
2. Then `pidfd_send_signal(direct_child_fd, SIGKILL)`. The kernel then sends PDEATHSIG to ns-init, and the namespace is torn down.

Wait `cgroup_kill_wait_s`. Every STOP path leaves the cgroup **THAWED, never FREEZING or FROZEN**, with the cgroups preserved, and reports survivors. No unrelated kills.

**3.4 Controller death while frozen (v1).**
- The tree survives, because queued PDEATHSIG and `--kill-child` signals act only after a thaw.
- Mitigations: frozen windows exist only inside §3.3, are bounded, and are persisted; the v2 freeze path (where SIGKILL still kills frozen tasks) is preferred.
- At the next controller start, M12 sees the orphan cgroup → STOP.
- Recovery only on an explicit operator ruling naming that one owned orphan cgroup by path and inode: thaw it, let the queued kills act, verify it is empty, then `rmdir`. That attempt stays consumed.

**3.5 Reaping.**
1. `waitid(P_PIDFD, fd, WEXITED|WNOWAIT)` to observe the exit.
2. `os.wait4(pid, 0)` to reap and get `ru_maxrss`. This is safe because the zombie pins the PID.
3. Record the raw status.
4. Smoke-verify that `unshare` passes on a killed-by-signal status and how it handles SIGTERM.

**3.6 Disclosure.** Producers spawn no subprocesses (grep at r1). Any third-party library that does so fails closed as an unexpected-process STOP.

## 4. Section G mapping — telemetry and the "private commit" analogue

**Why there is no private-commit equivalent.**
- Windows `PagefileUsage` is private memory charged against a commit limit that is enforced.
- Here, with heuristic overcommit and no swap, commit is neither charged nor enforced. The binding resource is pages charged to the memory cgroup (anonymous + page cache + kernel).
- Running out shows up as an **OOM SIGKILL**, rarely as `MemoryError`.
- RSS is not commit: it includes shared file-backed pages and excludes the cgroup's page cache.
- No Linux field may be called "private commit".

**Gate metrics — "charged memory":**

| Field | Source | Role |
|---|---|---|
| `cg_charged_peak_bytes` | attempt `memory.max_usage_in_bytes` | **Peak gate metric.** Kernel-maintained and immune to sampling misses. To make it a **true upper bound** on what the run charges, the parent hashes inputs with `O_DIRECT` (no cache population), or before the attempt with the residual recorded. The bound and its basis are recorded |
| `cg_working_set_bytes` | `usage_in_bytes − total_inactive_file` (sampled) | **Running-floor metric** (`running_floor_attempt_headroom_bytes`) |
| `cg_anon_bytes` | `total_rss + total_shmem` | **Reported only, never gating.** It excludes kmem and page tables |
| `proc_*` per verified member | `/proc/<pid>/status` `VmRSS`, `VmHWM`, `RssAnon`, `RssFile`, `RssShmem`, `VmData`, `VmPeak`, `Threads`; `ru_maxrss` at reap | Working-set analogue; virtual size is reported, not enforced |

Also recorded:
- `memory.usage_in_bytes`, plus from `memory.stat`: `total_cache`, `total_inactive_file`, `total_mapped_file`, `total_dirty`, `total_writeback`.
- `memory.kmem.usage_in_bytes`/`memory.kmem.max_usage_in_bytes` (inclusion in usage to be smoke-verified, M14).
- `failcnt`, `oom_control` (`under_oom`, `oom_kill`).
- `pids.current`/`pids.peak`/`pids.events`.
- The freeze state.
- The member set with a pidfd state for each member.

**memory/memsw check:** `memsw.max_usage_in_bytes` vs `max_usage_in_bytes` is compared **only after exit**, with a tolerance of at least 64 pages × `nproc` × 4 KiB (per-CPU charge batching). It is recorded only, unless the policy says otherwise.

**Available-memory analogue:**
- `effective_available_bytes = min(host MemAvailable, min over ancestors with a **finite** limit of (limit − usage))`.
- A limit counts as finite only if it is not `9223372036854771712`.
- Each term is recorded separately.
- `CommitLimit`/`Committed_AS` are recorded, labelled "not enforced".
- Scratch filesystem free space comes from `statvfs` `f_bavail*f_frsize`.

**Shared-cgroup rule.**
- **Reserve basis:** the measured concurrent Bash usage of the shared `claude-code-bash` cgroup.
- **Concurrency rule:** no other Bash workloads run during a slot (operator/coordinator duty).
- **Enforcement:** if the non-attempt usage of `claude-code-bash` (its usage minus the attempt's usage) grows by more than `bash_cgroup_growth_max_bytes` since launch → STOP.
- The Claude Code process lies outside that cgroup and is covered only by `MemAvailable`.

**Page-cache pitfalls (disclosed; tested in M14):**
- (a) Pages are charged to whoever touches them first. The `O_DIRECT` hashing handles this.
- (b) Dirty pages stay charged until writeback.
- (c) `failcnt` counts limit hits that reclaim absorbed. It is not an OOM signal (`oom_kill`, `under_oom` and unexplained SIGKILL are).
- (d) Cache stays charged after exit, and the memcg may linger as "dying". Every attempt therefore gets a fresh cgroup, and residuals are reported.
- No `drop_caches` and no `force_empty`.

**Sampling.** Each sample carries a sequence number, monotonic and realtime timestamps, and every field above.
- It verifies that the direct child and ns-init are still members.
- Exit is recognised **only through a readable pidfd**.
- A read failure while the child is alive, a gap over `max_sample_gap_ms`, or a non-monotonic sequence or clock → STOP. Never record zero, never omit.
- Phase markers count only if they carry the nonce. An optional OOM eventfd via `cgroup.event_control` is to be smoke-verified.

**STOP mapping:**
- Real `MemoryError`, `ENOSPC`/`EDQUOT`.
- An `oom_kill` delta, `under_oom`, or a SIGKILL the controller did not send.
- `EAGAIN` on fork/thread creation, or a `pids.events max` delta.
- Library-load errors.
- Unexpected processes; cgroup escape.
- Telemetry loss or gap; a floor or time breach; unexpected exit; missing receipt.
- Section D latches; unsafe roots; identity drift.
- Concurrency (another `n2-*` cgroup, Bash-cgroup growth, another writer).

`pids.max` counts **threads**. OpenBLAS defaults to `nproc` threads here, and GDAL may create threads (D-1).

## 5. Policy fields (no values proposed)

Each field carries a value, unit, strictness (`<` or `<=`) and a basis.

| Field | Replaces / new | Measurement basis (synthetic or idle only) |
|---|---|---|
| `attempt_memory_limit_bytes` (page-aligned; written to `memory.limit_in_bytes` then `memory.memsw.limit_in_bytes`) | new | Ancestor limit − measured concurrent Bash usage − agreed reserve |
| `attempt_pids_max` | new | Smoke `pids.peak` + declared thread counts (D-1) |
| `launch_min_effective_available_bytes` | `launch_min_system_commit_available_bytes` | Idle series of `effective_available_bytes` + reserve |
| `running_floor_effective_available_bytes` | `running_floor_system_commit_available_bytes` | Idle series + measured monitor/kill latency × an allocation rate (labelled heuristic) |
| `running_floor_attempt_headroom_bytes` (on working set) | new | Must trip before the in-cgroup OOM |
| `bash_cgroup_growth_max_bytes` | new | Idle variance of non-attempt Bash usage |
| `launch_min_disk_free_bytes`, `running_floor_disk_free_bytes` | new | Idle `statvfs`; B0 output sizes |
| `sample_interval_ms`, `max_sample_gap_ms` | unchanged | Measured sampler jitter and cost |
| `per_slot_elapsed_cap_s` | unchanged | Must fit inside the controller lifetime (D-7) |
| `graceful_wait_s`, `freeze_wait_s`, `max_frozen_window_ms`, `cgroup_kill_wait_s` | `job_terminate_wait_s` split | Measured SIGTERM, freeze/thaw and kill-to-empty latencies |
| `release_handshake_timeout_s` | new | Gate write → stub self-check marker |
| `failcnt_action` (`record`/`stop`), `memsw_check_action` | new | Operator choice |

No approved numbers exist. The old Windows numbers and the linear extrapolation remain superseded.

## 6. Synthetic controls

**Unchanged in intent:** M01, M02, M05, M06, M07, M10 and M11. Changes in wording only:
- **M02:** the records gain the Linux bindings.
- **M04:** "reparse rejection" becomes rejection of symlinks and mountpoints (`lstat`, `O_NOFOLLOW`, an ancestor's `st_dev` changing), and of roots under `/sys`, `/proc` or `/dev`.
- **M11:** no cgroup state in the repository.

**M03-L (replaces M03).**
- **L1:** the attempt record exists before `mkdir`; EEXIST → STOP.
- **L2:** limits are page-aligned, written in order and read back. An injected limit-write failure consumes the attempt and removes only the attempt's own empty cgroups.
- **L3:** fork is refused when the controller has more than one thread, a module outside the allowlist, or SIGCHLD set to `SIG_IGN`.
- **L4:** the gated child has not exec'd. The asserted fd set is {0, 1, 2, `gate_r`}. The controller's `gate_w` is the only write end: closing it gives `E_GATE` with no exec. Injected wrong byte → `E_GATE`.
- **L5:** joins happen in order and are verified two ways. Injected join and verification failures at each controller follow R-1 and the ambiguity rule. Injected `pidfd_open` failure → STOP.
- **L6:** launch record and `k` samples before release. Injected telemetry failure at S1 → cgroup cleanup.
- **L7:** stub refusals each give a fixed code before `site.main()`: wrong cgroup, NSpid not exactly two fields ending in `1`, no PDEATHSIG, environment differs from the allowlist (injected proxy- or `PYTHONPATH`-style variable with a neutral name).
- **L8:** the stub has only fds 0–2.
- **L9:** graceful, v2 kill, v1 fallback and R-2 (thaw first) each end with an empty cgroup and a reaped child. The cgroup is never left frozen on STOP.
- **L10:** `-S` plus `site.main()` matches a normal venv start in `sys.path`, `sys.prefix`, `sys.flags` and `site.ENABLE_USER_SITE`. No system `.pth` runs in the controller or the stub.
- **L11:** an unrelated stdlib sleeper with an adjacent PID survives every path. Static check: no signal except through a verified fd or `cgroup.kill`.
- **L12:** `execve` refuses an interpreter or `unshare` hash mismatch on the opened fd. Injected `unshare` exec failure and an injected EPERM on CLONE_NEWPID → consumed, STOP.
- **L13:** a Bash-tool timeout kill of a test controller is observed: exactly what is signalled is recorded, to settle the D-7 premise.

**M08-L (replaces M08).**
- Unit conversion (kB → bytes; cgroup files already in bytes; the `max` token; the finite-limit sentinel).
- The four memory fields are kept distinct, and none is called commit.
- An injected read or parse failure while the child is alive → STOP.
- **Mock listing in which a live member is absent → containment STOP, not an exit.**
- Missed, duplicate or out-of-order samples and a non-monotonic clock are detected.
- The `effective_available_bytes` and Bash-growth formulas are checked on a mock sysfs tree.
- The memsw check runs only after exit, with the tolerance.

**M09-L (replaces M09).**
- Injected strict and inclusive crossings of every §5 floor.
- Gap and elapsed-time expiry.
- Mock-counter detection of OOM, `pids.events max`, `EAGAIN`, `ENOSPC` and library-load failures, all with neutral injection labels.
- Section D latch.
- Exit without a receipt.
- `failcnt>0` without `oom_kill` is handled according to `failcnt_action`.
- Markers without the nonce are ignored and flagged.

**New controls:**
- **M12 cgroup lifecycle.**
  - An existing `n2-*` cgroup (populated, empty or frozen) at controller start → STOP, with no cleanup.
  - Every attempt uses a unique name.
  - A failed `rmdir` → STOP.
  - The controller's cgroup path differs from the one recorded at prepare → STOP.
  - An operator-ruled recovery of exactly one frozen orphan follows §3.4.
  - A dying memcg or residual cache after `rmdir` is reported.
- **M13 parent death** (real stdlib child, under a test parent). Kill the controller with SIGKILL at:
  - **S0:** the child never execs.
  - **S1:** the child never execs.
  - **After release:** the namespace is gone and the cgroup is empty within a measured time.
  - **While v1-FROZEN:** the frozen survivors remain, M12 STOPs, and the ruled recovery succeeds.
  - **While v2-frozen:** the tree dies (if v2 is used).

  Additional cases:
  - The controller's main thread exits while the sampler thread lives: PDEATHSIG fires, which proves it follows the forking thread.
  - A grandchild that ignores SIGTERM and calls `setsid` is still killed by namespace teardown and by `cgroup.kill`.
  - Gate EOF via the controller's `gate_w` alone.
- **M14 accounting semantics** (real stdlib child):
  - Charges made before the join are excluded.
  - N MiB allocated after the join appear in both the peak and anonymous fields.
  - File I/O raises `total_cache`/`total_inactive_file`, not anonymous memory.
  - An `O_DIRECT` hash does not raise the cache.
  - Whether kmem is included in usage.
  - The residual after exit.
- **M15 confined memory limit — required, pending operator approval (D-8).**
  - **OOM variant:** a tiny child allocates past a limit of a few tens of MiB. The in-cgroup OOM is detected via `oom_kill`/SIGKILL (optionally an eventfd), and the controller, shell and sleeper survive.
  - **Non-OOM variant:** file cache beyond a small limit → reclaim, `failcnt>0`, `oom_kill==0`, normal exit.
- **M16 escape and environment.**
  - A mock listing shows a self-migrated member → STOP.
  - If D-4 is adopted, a real child's write to `cgroup.procs` fails on the read-only mount.
  - The environment scrub is checked against the allowlist.
  - Thread variables match D-1.

**Allowed native smoke:** stdlib-only children allocating a few MiB and sleeping, exercising the real cgroups, gate, namespace, telemetry and termination paths. No repository producer imports, national reads or models.

## 7. Operator decisions required

| ID | Decision |
|---|---|
| D-1 | BLAS/GDAL threads: pin `OPENBLAS_NUM_THREADS`/`OMP_NUM_THREADS`/`MKL_NUM_THREADS`=1 plus `GDAL_NUM_THREADS`=1 (as in handoff §5), or the defaults with `pids.max` sized to match |
| D-2 | Accept "charged memory" (§4) as the gating basis in place of private commit. Insisting on commit-enforced semantics means NO-GO on this host |
| D-3 | Use v2 `unified` `cgroup.freeze`/`cgroup.kill`/`cgroup.events` as the primary kill/freeze path if smoke proves it, with the v1 freezer as fallback; or v1 only, accepting the §3.4 frozen-window residual |
| D-4 | Whether the cap drop (`setpriv`) and the read-only `/sys/fs/cgroup` (`unshare --mount`) hardening are **required**, or only escape detection |
| D-5 | Reserve inside the shared platform cgroup; the no-concurrent-Bash rule; the value basis for `bash_cgroup_growth_max_bytes` |
| D-6 | Ledger-loss semantics: "consumption unknown → no dispatch without a new ruling". Any off-host copies are private and **never in the public repo** |
| D-7 | Controller lifetime is bounded by a 7,200 s background Bash command; `per_slot_elapsed_cap_s` must fit inside it. No `setsid`/detached controller unless separately reviewed. The premise depends on L13's observation |
| D-8 | Approve M15 (both variants) |
| D-9 | Approve the Python 3.11 venv (without system site packages), needed later only |
| D-10 | Whether platform intervention (`process_api`, `sbx-telemetry`) observed during smoke is an automatic NO-GO |

Remaining reviewer questions:
- **Q-R2:** is a pre-exec controller copy started with `-I -S` and checked against the allowlist an acceptable "suspended" analogue?
- **Q-R3:** does the `O_DIRECT` hashing satisfy the upper-bound claim?
- **Q-R5:** is D-3's fallback acceptable?

## 8. NO-GO conditions

NO-GO for any real profile if any of the following holds:
- Neither v2 kill/freeze nor v1 freezer works in a child cgroup (create, freeze, thaw, kill).
- CLONE_NEWPID is unavailable (ruled).
- The attempt limit cannot be set and read back below the binding ancestor with the reserve.
- Membership cannot be verified before release.
- A cgroup escape is undetectable.
- D-1 through D-8 are unresolved.
- Any of M03-L, M08-L, M09-L or M12–M16 fails (M15 is required once approved).
- Telemetry is incomplete at the measured interval.
- Windows commit semantics are required.
- Platform intervention is observed (D-10).

NO-GO is a valid outcome; the alternative is a host that can meet the base spec.

## 9. Probe record

**r1 (2026-10-17), read-only:**
- `uname -r`; `/proc/self/cgroup`; `/proc/cgroups`; cgroup mounts and directory listings.
- `meminfo`; overcommit settings; `panic_on_oom`; `pid_max`; `nproc`.
- Per-level memory limit, usage and hierarchy files.
- `memory.stat`; `unified/cgroup.controllers`; `df`.
- `unshare` and `prlimit` versions; capabilities and seccomp status; `ps`; the parent's cgroup; THP mode.
- Python 3.11 `os` capabilities; a producer grep for subprocess/multiprocessing/thread pools.

**r1, mutating (permitted then):**
- Created and removed `…/claude-code-bash/n2-design-probe-f5af6aea` (memory) and `/sys/fs/cgroup/pids/n2-design-probe-f5af6aea`.
- A FIFO-gated `python3.11 -I -c` child joined both cgroups (confirmed in `/proc/<pid>/cgroup`), allocated 32 MiB and exited 0.
- Readings: max usage = memsw max = 34,332,672; residual usage 753,664 (cache 503,808, rss 0); `failcnt` 0; `pids.peak` 1.
- Both `rmdir` calls succeeded; the FIFO was removed; no limits were written.
- Freezer, v2 and namespaces were not exercised.

**r2 (2026-10-17), read-only only, no host mutation:**
- `/proc/cgroups` shows memory `num_cgroups` 5 against 5 memory directories, and pids 2 against 2. The reviewer had observed 6 memory cgroups against 5 directories shortly after the r1 probe, consistent with a **dying memcg lingering after `rmdir`** that has since been freed. This is disclosed as a residual behaviour (M12).
- `/sys/kernel/cgroup/features` lists `nsdelegate`, `favordynmods`, `memory_localevents`, `memory_recursiveprot`, `memory_hugetlb_accounting`, `pids_localevents`.
- `unified` `cgroup.max.depth`/`cgroup.max.descendants` are `max`; its root has no `cgroup.kill`/`cgroup.freeze` (expected for a root cgroup).
- System `.pth` files are as listed in §0.
- The environment carries proxy- and credential-category variables (counted, values not read), which motivates the allowlist.
- `CapEff` decoded: `CAP_SYS_RESOURCE` absent.
