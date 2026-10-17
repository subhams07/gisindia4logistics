# N2 Linux addendum — containment, parent death, termination and telemetry

This is an addendum to [the N2 resource specification](stage4_n2_resource_spec.md), covering its sections F, G and H, for the Linux cloud host described in section 0 of [the handoff](claude_remote_handoff.md). **It is a design only: not approved, and not execution authority.** No harness exists. Sections A–E and I–J of the base spec apply unchanged except where noted here. Anything marked "smoke-verify" is unproven on this host and must be proven by the synthetic native smoke before any real run is considered. Wherever this document says "recommended" or "preferred", that is the designer's recommendation pending the operator decision named alongside it.

Terms used throughout:

- **Attempt cgroups:** the per-attempt cgroup set, owned by the controller.
- **Controller:** the dispatch process, owned by the coordinator.
- **Direct child:** the process the controller forks. After exec it becomes `unshare`.
- **ns-init:** the producer Python process, which is PID 1 of the new PID namespace.
- **Verified fd:** a per-member pidfd that has passed the check in §3.1.

## Revision history

- **r1** (`95771b73…44ca`): initial draft.
- **r2** (`f222e687…968f`): addressed the first independent Opus review (GO_WITH_REQUIRED_CHANGES):
  - controller death while frozen;
  - R-2 thaw ordering;
  - escape detection via pidfds;
  - gate-EOF fd order;
  - eleven non-blocking items: MiB figure, CAP_SYS_RESOURCE, `-I -S` controller, memsw tolerance, gate metrics, shared Bash cgroup, NSpid/pidfd primitive, reaping/exec/environment, new faults and M15, disclosures, probe record.
  - It also adopted R-1 plus the ambiguity rule, Q-R4 (NO-GO without CLONE_NEWPID) and Q-R6.
- **r3** (this revision) addresses the re-review of r2 (GO_WITH_REQUIRED_CHANGES; all r1 blockers closed):
  - **B-1 (blocking):** L-4 now keeps and asserts `{0, 1, 2, gate_r, exec_fd}`, both `O_CLOEXEC`. The parent verifies the SHA-256 of `exec_fd` before fork. The child re-checks `st_dev`/`st_ino` with `fstat`. The fd set is asserted by listing an explicitly opened `/proc/self/fd` dir fd (which is then excluded) and by probing the `gate_w` number for `EBADF`. The TOCTOU at the hop where `unshare` execs Python by path is closed by the stub re-verifying `/proc/self/exe` (identity and hash). M03-L4 is updated.
  - **N-1:** the ambiguity-rule freeze is dropped. The pre-release child has no descendants, so verification via `/proc/<pid>/cgroup` plus a pidfd kill is enough.
  - **N-2:** R-2 wording. R-2 now exists only under D-3 option (c): thaw is always attempted, SIGKILL is still sent through the verified fds, and any residual frozen state is reported.
  - **N-3:** forked-child safety: try/except BaseException → `_exit`, `gc.disable()`, signal handlers reset, stdio flushed, "no imports, no threads".
  - **N-4:** each sample walks descendants from the ns-init pidfd for detection.
  - **N-5:** §8 made consistent with §7.
  - **N-6:** the decisions list is completed (`failcnt`/memsw actions, `k`, memsw tolerance, cache method, D-3 options).
  - **N-7:** `sbx-telemetry` added to the §9 probe record; the NSpid rule restated.
  - **Q-R2:** resolved.
  - **Q-R3:** fadvise + mincore residency method adopted.
  - **Q-R5:** the freeze-free termination design is adopted as the recommendation, with the options in D-3. As a result, §3.4's frozen residual exists only under option (c).
  - The host note that the controller is at the unified root has been added.

## 0. Host facts the design depends on

Survey and probes, 2026-10-17. r2 and r3 used read-only probes only; see §9.

**cgroups**

- cgroup **v1** controllers are mounted separately: `memory`, `pids`, `freezer`, `cpu`, `cpuacct`, `cpuset`, `blkio`, `devices`.
- There is also a cgroup2 mount at `/sys/fs/cgroup/unified`:
  - Its only controller is `hugetlb`.
  - Kernel features include `nsdelegate`, `memory_localevents` and `pids_localevents`.
  - The controller process sits at its root (`0::/`), so a v2 attempt cgroup would be a **direct child of the unified root**. Whether non-root `cgroup.kill`/`cgroup.freeze` work there has not been exercised.
- Bash-tool processes are in memory cgroup `/process_api/<id>/claude-code-bash`:
  - Its platform limit is **14,343,688,192 B = 13,679.2 MiB (13.36 GiB)**, with `use_hierarchy=1`.
  - It is shared by every Bash-tool command of every agent.
  - The Claude Code process is in the unlimited parent `/process_api/<id>`.
- The platform's `sbx-telemetry` cgroup has a 64 MiB memory limit and `pids.max` 16.
- PID 1 is `process_api`, not systemd.

**Host**

- Memory: `MemTotal` 15.7 GiB, no swap, `overcommit_memory=0`.
- `pid_max=32768`, so PID reuse is realistic.
- THP is `madvise`; `panic_on_oom=0`.
- `/proc/<pid>/task/<tid>/children` is available.
- Security:
  - Seccomp is off.
  - `CapEff=000001fffeffffff` **lacks `CAP_SYS_RESOURCE`**. Raising one's own `oom_score_adj` is allowed; lowering it or raising hard rlimits is not.
- Disk: about 29 GiB available on `/`.

**Tools and Python**

- util-linux 2.39.3 (`unshare --pid --fork --kill-child`).
- Python 3.11.17 has:
  - `os.pidfd_open`, `os.waitid`/`P_PIDFD`/`WNOWAIT`;
  - `signal.pidfd_send_signal`, `os.wait4`;
  - fd-based `os.execve`, `os.posix_fadvise`.
- It has no `os.unshare`.
- System `.pth` files exist at `/usr/local/lib/python3.11/dist-packages/uno.pth` and `/usr/lib/python3/dist-packages/distutils-precedence.pth`.

**r1 probe findings**

- Empty memory and pids child cgroups work.
- Membership shows up in `/proc/<pid>/cgroup`.
- The kernel keeps peaks itself (`memory.max_usage_in_bytes`, `pids.peak`).
- Charges made before joining stay with the old cgroup.
- Some page cache stays charged after exit (about 0.7 MB).
- A dying memcg may linger after `rmdir` (see §9).

**Consequence:** the limit that actually binds is the shared platform cgroup, not host RAM. Each attempt therefore gets its own hard limit below that ancestor.

## 1. Section F mapping

| Section F requirement | Linux mechanism | Status / limitation |
|---|---|---|
| Immutable attempt record: exclusive create before the child exists, keyed by approval SHA + slot, consumed on failure, fail closed | `os.open(O_CREAT\|O_EXCL\|O_WRONLY\|O_NOFOLLOW)` plus `fsync` of the file and its directory, done **before** any cgroup is created. The record binds `boot_id`, the kernel release, the controller's cgroup paths (read at `prepare`), the planned cgroup names, and the SHA-256 and `st_dev`/`st_ino` of `unshare` and of the resolved interpreter | **Met on this host.** It does not survive loss of the container. Ledger loss is handled per D-6 |
| Launch record after creation, before release | Records the direct child's PID, `starttime` (field 22 of `/proc/<pid>/stat`), the pidfd opened right after `fork` (while the child is unreaped), the cgroup paths and the inode/`st_dev` of each cgroup directory, and gate state `closed` | **Met.** The ns-init host PID is recorded in the first observation after release |
| Job created before the child exists; not inheritable | Exclusive `mkdir` (EEXIST → STOP) **before fork** of v1 `memory` (nested under the controller's memory cgroup) and v1 `pids`, plus the kill/freeze cgroup required by the D-3 option (none for option (a); a v2 `unified` child for (a+)/(b); a v1 `freezer` child for (c)). Limits must be page-aligned. `memory.limit_in_bytes` is written before `memory.memsw.limit_in_bytes`, and both are read back. Each cgroup is asserted empty with zero counters | **Met** for memory and pids. The v2 and freezer parts must be smoke-verified. On failure the attempt is consumed and only the attempt's own empty cgroups are removed |
| `CREATE_SUSPENDED` | The controller is started as `python3.11 -I -S -B -u` and is stdlib-only. Before fork it asserts: `sys.modules` ⊆ a pinned stdlib allowlist; one thread (`threading.active_count()==1` and `/proc/self/task` has 1 entry); SIGCHLD is not `SIG_IGN`; no other children or waiters. It flushes `sys.stdout`/`sys.stderr`, calls `gc.disable()`, then `os.fork()` from the main thread. The parent calls `gc.enable()` afterwards. The child blocks before `execve` (L-4) | **Met with a different primitive** (Q-R2 resolved). `Popen(preexec_fn)` is not used: it deadlocks against the gate |
| Only stdout/stderr and `NUL` stdin are inheritable | L-4 handles the fds. The two internal fds (`gate_r`, `exec_fd`) are `O_CLOEXEC` and close at exec. Phase markers go to stderr as lines carrying a per-attempt nonce and a `CLOCK_MONOTONIC` timestamp in ns | **Met** |
| Assign the suspended child to the job | The controller writes the PID into the first cgroup of the set: the v2 child if D-3 uses one, otherwise `memory`. It verifies that, then joins the remaining cgroups. Each join is verified two ways: `/proc/<pid>/cgroup`, and the cgroup listing being exactly `{pid}`. The §2 ambiguity rule applies | **Met** |
| Persist launch record and telemetry, then resume | Write the launch record, start the sampler, and require `k` consecutive complete samples (`release_sample_count_k`, see §5). Then write `b"G"` to `gate_w` and close it | **Met** |
| Child's first statements are stdlib-only and self-check before any first-party import | Exec goes through `exec_fd`. Its SHA-256 is verified by the parent before fork, and the child re-checks `st_dev`/`st_ino` with `fstat`. The `unshare` binary is ELF, so fexecve with an `O_CLOEXEC` fd is valid. Exec uses an **explicit environment allowlist**: `PATH`, `LANG`, `TZ`, `HOME`, scratch `TMPDIR`, `PYTHONDONTWRITEBYTECODE=1`, plus the thread variables per D-1. No proxy, credential or other `PYTHON*` variables. The full exec line is `unshare --pid --fork --kill-child=SIGKILL -- <abs venv python3.11> -I -S -B -u <stub> <expected-json>`. **TOCTOU:** `unshare` execs Python by path. The stub's first check closes this: `/proc/self/exe` must match the expected `st_dev`/`st_ino`, and the SHA-256 of `open('/proc/self/exe','rb')` must match the expected value (stdlib `hashlib`). Further stub checks: cgroup paths; `NSpid` has **(controller's NSpid field count + 1)** fields, with the last equal to `1` (the controller currently has 1 field, so the child must have 2); `prctl(PR_GET_PDEATHSIG)==SIGKILL`; `sys.flags.isolated`; the environment equals the allowlist. Only after these checks does it install a SIGTERM handler, run `import site; site.main()`, and authenticate and import the harness body | **Met.** The kernel enforces containment before exec; the self-check only verifies it. The venv is created without `--system-site-packages` |
| Parent death kills the owned tree | Four layers: (1) the gate sees EOF before release; (2) `PR_SET_PDEATHSIG(SIGKILL)` on the direct child, which survives exec of the non-setuid `unshare` but follows the **forking thread**, so the main thread must outlive the attempt; (3) `--kill-child` makes ns-init receive SIGKILL when `unshare` dies; (4) the kernel kills the whole namespace when ns-init dies | **Met, provided CLONE_NEWPID works** (NO-GO without it). With D-3 option (a) no freeze is ever applied, so nothing blocks the kills. Under option (c) the §3.4 residual applies. Leftover cgroups cause a STOP at the next start (M12) |
| Normal termination through the job, never by PID | §3. Signals go **only** through verified fds, or v2 `cgroup.kill` where D-3 includes it. Never raw `kill(pid)`, `killpg`, `ps`/`pgrep`, or `/proc` scans as signal targets | **Met, pending smoke** |
| Exact-handle cleanup before assignment | `pidfd_send_signal(SIGKILL)` on the gated, never-exec'd child, then `waitid(P_PIDFD)` | **Met** |
| No later slot until logs are drained, the tree has ended and the receipt is durable | The direct child's pidfd is readable and the child is reaped (§3.5). Every member fd is readable. `cgroup.procs` is empty in every cgroup, `pids.current==0`, and v2 `cgroup.events` shows `populated 0` if a v2 cgroup is used. Then record the counters and `rmdir` (failure → STOP). Never `force_empty` | **Met** |
| Containment cannot be escaped | The child is root with `CAP_SYS_ADMIN`, so it could migrate itself out. Detection: (i) per-member pidfds, where "absent but fd not readable" → containment STOP; (ii) every sample checks the direct child and ns-init are still members; (iii) every sample walks all descendants of ns-init via `/proc/<pid>/task/<tid>/children` (detection only, never a signal target) and requires the result ⊆ the listing, otherwise STOP. Prevention, optional (D-4): `unshare --mount` with a read-only `/sys/fs/cgroup` for the child, plus a `setpriv` drop of bounding and inheritable caps | Detection is **met**. The descendant walk can still miss a process that forks and escapes between two samples; D-4 would close that gap. Prevention is an operator decision |

**L-4: child-side code between fork and exec.** It is stdlib-only, with **no imports and no threads**, and runs entirely inside `try: … except BaseException: os._exit(E_INTERNAL)`. It never returns into controller code.

1. Call `prctl(PR_SET_PDEATHSIG, SIGKILL)`. Then reset every Python-level signal handler to `SIG_DFL`, except SIGCHLD, which also goes to the default.
2. Check `os.getppid() == controller_pid`; otherwise `_exit(E_PARENT)`.
3. `dup2` the slot stdout, stderr and `/dev/null` onto fds 0–2. Close every fd ≥ 3 except `gate_r` and `exec_fd` (`closerange` over the gaps). This **also closes the child's copy of `gate_w`**.
4. Assert the fd set:
   - Open `d = os.open('/proc/self/fd', O_RDONLY|O_DIRECTORY|O_CLOEXEC)`.
   - The entries of `os.listdir(d)`, minus `d`, must equal `{0, 1, 2, gate_r, exec_fd}`.
   - Close `d`.
   - Also, `fcntl(gate_w_number, F_GETFD)` must raise `EBADF`.
   - Otherwise `_exit(E_FD)`.
5. Check `os.fstat(exec_fd)` `st_dev`/`st_ino` equal the values the parent verified; otherwise `_exit(E_EXEC)`.
6. Do a blocking `os.read(gate_r, 1)`. On EOF or any byte other than `b"G"`, `_exit(E_GATE)`. It never execs.
7. Re-read `/proc/self/cgroup`, compare it with the expected paths, and write `1000` to `/proc/self/oom_score_adj`.
8. `os.execve(exec_fd, argv, env_allowlist)`.

## 2. Section F cleanup ambiguity — R-1 (confirmed)

| State | Entered when | Only permitted termination |
|---|---|---|
| S0 forked-gated | `fork` returned | pidfd SIGKILL + `waitid` (the exact-handle exception) |
| S1 assigned | The first cgroup of the set has **verified** membership | Cgroup termination (§3). Before release the only member is the gated child, so this is a SIGKILL through its verified fd. The pidfd is otherwise used only to wait and reap |
| S2 released | The gate byte has been written | Cgroup termination (§3) |

**Ambiguity rule (adopted; freeze removed in r3).** Case: writing to `cgroup.procs` of the first cgroup succeeds, but verification fails or cannot complete. The pre-release child is gated and has no descendants, so no freeze is needed:

1. Read `/proc/<pid>/cgroup` through the S0 pidfd identity.
2. If the child is in the attempt cgroup, take the S1 path. Otherwise take the S0 path.
3. Both paths end in SIGKILL through the same pidfd, followed by `waitid`.
4. Record the ambiguity and the observed state in the terminal receipt.

## 3. Termination procedure (S1/S2)

**3.1 Verified member primitive.** At first sight of any member PID, from a listing or the descendant walk:

1. `pidfd_open(pid)`.
2. Check that `/proc/<pid>/cgroup` shows the attempt paths, and that `starttime` matches the first observation.
3. Check that `pidfd_send_signal(fd, 0)` succeeds.

From then on, the member is signalled **only through that fd**. Failing verification → containment STOP.

**3.2 Graceful.** `pidfd_send_signal(ns_init_fd, SIGTERM)`. The stub's handler is what makes this work, because PID 1 ignores signals it has no handler for. Then wait up to `graceful_wait_s` for every member fd to be readable and the listing to be empty.

**3.3 Hard. Designer recommendation, D-3 option (a): freeze-free.**

1. `pidfd_send_signal(ns_init_fd, SIGKILL)`. The kernel tears down the whole namespace, including any process forked after the last listing.
2. `pidfd_send_signal(direct_child_fd, SIGKILL)` to `unshare`.
3. SIGKILL any remaining verified member fd.
4. Wait up to `cgroup_kill_wait_s` for the cgroup to empty.

No freeze is used, so the §3.4 residual does not arise. **Option (a+)** additionally writes `cgroup.kill` in a v2 child and waits for `populated 0`, if smoke proves this works. **Option (b)** uses v2 only, and is NO-GO if v2 fails smoke. **Option (c)** uses the v1 freezer for a stable listing:

1. FROZEN.
2. List the members and verify their fds.
3. THAW.
4. Signal through the fds.

The frozen window is bounded by `max_frozen_window_ms`, and its start and end timestamps are persisted. **R-2 (option (c) only):** if FROZEN is not reached within `freeze_wait_s`, write THAWED. **Whether or not the thaw succeeds**, still SIGKILL via ns-init, the direct child and the member fds. Record the freezer state in the receipt, then STOP. Thaw is always attempted, and any residual frozen state is reported and handled per §3.4.

Survivors after `cgroup_kill_wait_s` → STOP. The cgroups are preserved and the survivors reported. No unrelated kills.

**3.4 Controller death during a v1 freeze (option (c) only).** Queued SIGKILLs only take effect after a thaw, so the frozen tree survives. At the next start, M12 STOPs. Recovery happens only by explicit operator ruling naming that one owned orphan cgroup, by path and inode: thaw it, let the queued kills act, verify it is empty, then `rmdir`. The attempt stays consumed. Option (c) requires this residual to be **explicitly accepted under D-3**.

**3.5 Reaping.**

1. `waitid(P_PIDFD, fd, WEXITED|WNOWAIT)` to observe the exit.
2. `os.wait4(pid, 0)` to reap and get `ru_maxrss`. The zombie pins the PID until this point.
3. Record the raw status.
4. Smoke-verify how `unshare` propagates a killed-by-signal status, and how it handles SIGTERM.

**3.6 Disclosure.** Producers spawn no subprocesses (checked by grep). A third-party library that did would fail closed as an unexpected-process STOP.

## 4. Section G mapping — telemetry and the "private commit" analogue

**Why there is no private-commit equivalent.**

- On Windows, `PagefileUsage` is private memory charged against an enforced commit limit.
- On this host, with heuristic overcommit and no swap, commit is neither charged nor enforced. The binding resource is pages charged to the memory cgroup: anonymous memory, page cache, and kernel memory.
- Exhaustion shows up as an **OOM SIGKILL**, rarely as `MemoryError`.
- RSS is not commit either: it includes shared file-backed pages and excludes the cgroup's page cache.
- No Linux field may be called "private commit".

**Gate metrics ("charged memory"):**

| Field | Source | Role |
|---|---|---|
| `cg_charged_peak_bytes` | Attempt cgroup `memory.max_usage_in_bytes` | **Peak gate metric.** The kernel maintains it, so sampling cannot miss a peak. It is a true upper bound only under the **cold-input condition**. Inputs may already be cached, and charged elsewhere, from the checkout, earlier attempts or other agents, so `O_DIRECT` hashing alone is not enough. Before launch, the parent therefore: (1) `posix_fadvise(POSIX_FADV_DONTNEED)` on every admitted input; (2) measures residency with `mincore` (ctypes, via a read-only mmap); (3) records the resident fraction per file and in total. The bound is claimed **only** if residency ≤ `cold_input_residency_max_fraction` (D-11); otherwise STOP. Hashing may use `O_DIRECT` with page-aligned (mmap-allocated) buffers, or ordinary reads before the fadvise. Note that DONTNEED does not evict dirty pages or pages mapped by others |
| `cg_working_set_bytes` | `usage_in_bytes − total_inactive_file`, sampled | **Running-floor metric** |
| `cg_anon_bytes` | `total_rss + total_shmem` | Reported only. Excludes kmem and page tables |
| `proc_*` per verified member | `/proc/<pid>/status`: `VmRSS`, `VmHWM`, `RssAnon`, `RssFile`, `RssShmem`, `VmData`, `VmPeak`, `Threads`; plus `ru_maxrss` at reap | Working-set analogue. Virtual sizes are reported but not enforced |

**Also recorded:**

- `memory.usage_in_bytes`.
- From `memory.stat`: `total_cache`, `total_inactive_file`, `total_mapped_file`, `total_dirty`, `total_writeback`.
- `memory.kmem.usage_in_bytes`/`max_usage_in_bytes`, whether kmem is included in usage (M14), `failcnt`, and `oom_control` (`under_oom`, `oom_kill`).
- `pids.current`/`pids.peak`/`pids.events`.
- The freeze state, if option (c) is chosen.
- The member set, with each member's fd state, and the result of the descendant walk.

**memsw cross-check.** Compare `memsw.max_usage_in_bytes` with `max_usage_in_bytes` **only after exit**. The tolerance is `memsw_tolerance_bytes`, and it must be at least 64 pages × `nproc` × 4 KiB (per-CPU charge batching). The action on mismatch is `memsw_check_action`, set under D-11.

**Available memory:**

- `effective_available_bytes = min(host MemAvailable, min over ancestors with a finite limit of (limit − usage))`.
- A limit counts as finite when it is ≠ `9223372036854771712`.
- Each term is recorded separately.
- `CommitLimit` and `Committed_AS` are recorded and labelled "not enforced".
- Disk: `statvfs` of the scratch filesystem, `f_bavail × f_frsize`.

**Shared Bash cgroup.**

- The reserve is based on measured concurrent Bash usage.
- No other Bash workloads may run during a slot.
- STOP if usage of `claude-code-bash` excluding the attempt grows by more than `bash_cgroup_growth_max_bytes` from launch.
- The Claude Code process sits outside `claude-code-bash`, so only `MemAvailable` covers it.

**Page-cache pitfalls (disclosed; tested in M14):**

- (a) Pages are charged to whichever cgroup touches them first; the cold-input method above handles this.
- (b) Dirty pages stay charged until writeback.
- (c) `failcnt` also counts limit hits that reclaim absorbed, so it is not an OOM signal.
- (d) Cache and a dying memcg can outlive `rmdir`, so every attempt uses a fresh cgroup and residuals are reported.
- No `drop_caches` and no `force_empty`.

**Sampling.** Each sample carries a sequence number, monotonic and realtime timestamps, all the fields above, the membership check for the direct child and ns-init, and the descendant walk.

- An exit counts only when the member's pidfd is readable.
- STOP on any of: a read failure while the child is alive, a gap over `max_sample_gap_ms`, or a sequence number or clock that goes backwards. Nothing is ever filled as zero or omitted.
- Only markers carrying the nonce are trusted.
- Optional: an OOM eventfd via `cgroup.event_control` (smoke-verify).

**STOP mapping:**

- Memory and disk: real `MemoryError`, `ENOSPC`/`EDQUOT`, an `oom_kill` delta, `under_oom`.
- Processes: a SIGKILL the controller did not send; `EAGAIN`; a `pids.events max` delta; library-load errors; unexpected processes; escape or a descendant outside the listing.
- Telemetry and policy: telemetry loss or gap; a floor or time breach; unexpected exit; missing receipt.
- Section D latches; unsafe roots; identity drift.
- Concurrency: another `n2-*` cgroup, Bash-cgroup growth, or another writer.
- Cold-input residency above the threshold.

`pids.max` counts **threads**. OpenBLAS defaults to `nproc` threads here, and GDAL may add more (D-1).

## 5. Policy fields (no values proposed)

Each field carries a value, a unit, a strictness (`<` or `<=`) and a basis.

| Field | Replaces / new | Measurement basis (synthetic/idle only) |
|---|---|---|
| `attempt_memory_limit_bytes` (page-aligned) | new | Ancestor limit − concurrent Bash usage − reserve |
| `attempt_pids_max` | new | Smoke `pids.peak` + declared threads (D-1) |
| `launch_min_effective_available_bytes` | `launch_min_system_commit_available_bytes` | Idle series + reserve |
| `running_floor_effective_available_bytes` | `running_floor_system_commit_available_bytes` | Idle series + monitor/kill latency × allocation rate (labelled heuristic) |
| `running_floor_attempt_headroom_bytes` (working set) | new | Must trip before the in-cgroup OOM |
| `bash_cgroup_growth_max_bytes` | new | Idle variance of non-attempt Bash usage |
| `launch_min_disk_free_bytes`, `running_floor_disk_free_bytes` | new | Idle `statvfs`; B0 output sizes |
| `sample_interval_ms`, `max_sample_gap_ms` | unchanged | Sampler jitter and cost |
| `release_sample_count_k` | new | Sampler start-up behaviour in smoke |
| `per_slot_elapsed_cap_s` | unchanged | Must fit within controller lifetime (D-7) |
| `graceful_wait_s`, `cgroup_kill_wait_s` (+ `freeze_wait_s`, `max_frozen_window_ms` for option (c) only) | `job_terminate_wait_s` split | Measured SIGTERM, kill-to-empty and freeze/thaw latencies |
| `release_handshake_timeout_s` | new | Gate write → stub self-check marker |
| `cold_input_residency_max_fraction` | new | `mincore` after fadvise in smoke |
| `memsw_tolerance_bytes` (≥ 64 × `nproc` × 4 KiB) | new | Post-exit deltas observed in smoke |
| `failcnt_action`, `memsw_check_action` (`record`/`stop`) | new | Operator choice (D-11) |

No approved numbers exist. The old Windows numbers and the linear extrapolation remain superseded.

## 6. Synthetic controls

M01, M02, M05, M06, M07, M10 and M11 keep their intent. M02's records gain the Linux bindings. In M04, "reparse rejection" becomes symlink/mountpoint rejection (`lstat`, `O_NOFOLLOW`, `st_dev` change of an ancestor), and roots under `/sys`, `/proc` or `/dev` are rejected. M11 adds: no cgroup state in the repository.

**M03-L (replaces M03).**

- **L1:** the attempt record exists before `mkdir`; EEXIST → STOP.
- **L2:** limits are page-aligned, written in order and read back. An injected write failure consumes the attempt and removes only the attempt's own empty cgroups.
- **L3:** fork is refused on more than one thread, a module outside the allowlist, or `SIG_IGN` for SIGCHLD. `gc` is disabled across fork. stdio is flushed before fork, so buffered output does not appear twice.
- **L4:**
  - The gated child has not exec'd.
  - The fd assertion passes with exactly `{0, 1, 2, gate_r, exec_fd}`, where the `/proc/self/fd` dir fd is opened explicitly and excluded.
  - Probing the `gate_w` number gives `EBADF`.
  - Injected extra fd → `E_FD`.
  - Injected exchange of `exec_fd` (different `st_ino`) → `E_EXEC`.
  - Closing the controller's `gate_w` alone → `E_GATE` with no exec; a wrong byte gives the same.
  - Injected exception in any child step → `os._exit(E_INTERNAL)`, with no return into controller code.
  - Signal handlers are `SIG_DFL` after step 1.
- **L5:** joins happen in order and are verified two ways. Injected join and verification failures follow R-1 and the freeze-free ambiguity rule. Injected `pidfd_open` failure → STOP.
- **L6:** the launch record and `k` samples exist before release. An injected telemetry failure at S1 leads to cleanup through the verified fd.
- **L7:** each stub refusal returns a fixed code before `site.main()`: wrong cgroup, wrong `NSpid` field count or last field ≠ `1`, no PDEATHSIG, environment differs from the allowlist (injected variable with a neutral name), and `/proc/self/exe` identity or hash mismatch (injected substitute interpreter at the venv path between parent verification and the `unshare` hop).
- **L8:** the stub has only fds 0–2.
- **L9:** the graceful path, and the hard path for each D-3 option implemented, end with an empty cgroup and a reaped child. Under option (c), injected thaw failure → SIGKILLs are still sent, the state is recorded, and the run STOPs.
- **L10:** `-S` plus `site.main()` matches a normal venv start in `sys.path`, `sys.prefix`, `sys.flags` and `site.ENABLE_USER_SITE`. No system `.pth` runs in the controller or stub.
- **L11:** an unrelated sleeper with an adjacent PID survives every path. Static check: no signal except through a verified fd or `cgroup.kill`.
- **L12:** injected hash mismatch on `exec_fd` before fork → refused. Injected `unshare` exec failure, and injected EPERM on CLONE_NEWPID → consumed, STOP.
- **L13:** a Bash-tool timeout kill of a test controller is observed, and exactly what was signalled is recorded (D-7 premise).

**M08-L (replaces M08).**

- Unit conversions: kB vs bytes, the `max` token, and the finite-limit sentinel.
- The four memory fields are kept distinct, and none is called commit.
- Injected read or parse failure while the child is alive → STOP.
- Mock listing where a live member is absent → containment STOP, not exit.
- A mock descendant-walk result outside the listing → STOP.
- Missed, duplicate or out-of-order samples, and a clock going backwards, are detected.
- `effective_available_bytes` and Bash growth are tested on a mock sysfs tree.
- The memsw check runs only after exit, with `memsw_tolerance_bytes`.
- Cold-input residency is computed from mock `mincore` vectors, and the threshold crossing → STOP.

**M09-L (replaces M09).**

- Injected strict and inclusive crossings of every §5 floor.
- Gap and elapsed-time expiry.
- Mock-counter detection of OOM, `pids.events max`, `EAGAIN`, `ENOSPC` and library-load failures, all with neutral labels.
- The section D latch.
- Exit without a receipt.
- `failcnt`/memsw handled according to their actions.
- Markers without the nonce are ignored and flagged.

**New controls:**

- **M12, cgroup lifecycle.**
  - An existing `n2-*` cgroup at start, whether populated, empty or frozen → STOP, with no cleanup.
  - Names are unique per attempt.
  - A failed `rmdir` → STOP.
  - A controller cgroup path that differs from the one recorded at prepare → STOP.
  - Option (c) only: the ruled recovery of exactly one frozen orphan follows §3.4.
  - A dying memcg or residual cache left after `rmdir` is reported.
- **M13, parent death.** A real stdlib child under a test parent; SIGKILL the controller at each point below:
  - At S0 and at S1: the child never execs. Gate EOF works through the controller's `gate_w` alone, which proves the child's copy was closed.
  - After release: the namespace is gone and the cgroup is empty within a measured time.
  - Option (c) only, while v1-FROZEN: the survivors remain, M12 STOPs, and the ruled recovery succeeds.
  - Option (a+)/(b): while v2-frozen (if used), the tree dies.
  - The main thread exits while the sampler thread lives: PDEATHSIG fires.
  - A grandchild that ignores SIGTERM and calls `setsid` is killed by the namespace teardown.
- **M14, accounting semantics.** A real stdlib child, checking each of the following:
  - Charges made before the join are excluded.
  - N MiB allocated after the join show up in the peak field and in the anonymous field.
  - File I/O raises the cache fields, not the anonymous field.
  - fadvise followed by mincore shows that the residency drop is measurable.
  - Whether kmem is included in usage.
  - The residual after exit.
- **M15, confined memory limit.** Required once the operator approves it (D-8). Two variants:
  - OOM: allocation past a limit of a few tens of MiB is detected via `oom_kill` and SIGKILL, and the controller, the shell and the sleeper survive.
  - Non-OOM: file cache beyond the limit → reclaim, `failcnt > 0`, `oom_kill == 0`.
- **M16, escape and environment.**
  - Self-migration of a mock member → STOP.
  - If D-4 is adopted: a real child's write to `cgroup.procs` fails on the read-only mount.
  - The environment scrub is applied.
  - Thread variables match D-1.

Allowed native smoke: stdlib-only children that allocate a few MiB and sleep. No repository producer imports, national reads or models.

## 7. Operator decisions required

| ID | Decision |
|---|---|
| D-1 | BLAS/GDAL threads: either `OPENBLAS_NUM_THREADS`/`OMP_NUM_THREADS`/`MKL_NUM_THREADS`/`GDAL_NUM_THREADS`=1 (handoff §5), or the defaults with `pids.max` sized for them |
| D-2 | Accept "charged memory" (§4) as the gating basis in place of private commit. Insisting on commit semantics means NO-GO on this host |
| D-3 | Termination design: **(a) freeze-free verified-fd escalation plus namespace teardown, v1 memory/pids only — designer recommendation**; (a+) option (a) plus v2 `cgroup.kill` if smoke proves it; (b) v2 only (`cgroup.kill`/`freeze`/`events` in a child of the unified root), NO-GO if v2 fails smoke; (c) v1 freezer listing, with the §3.4 residual explicitly accepted |
| D-4 | Is the cap drop (`setpriv`) plus a read-only `/sys/fs/cgroup` (`unshare --mount`) **required**, or is detection only enough, accepting the between-sample escape gap? |
| D-5 | The reserve inside the shared platform cgroup; the no-concurrent-Bash rule; the basis for `bash_cgroup_growth_max_bytes` |
| D-6 | Ledger loss: "consumption unknown → no dispatch without a new ruling". Off-host copies are private and never placed in the public repo |
| D-7 | Controller lifetime is bounded by a 7,200 s background Bash command, and `per_slot_elapsed_cap_s` must fit within it. No `setsid` or detachment unless reviewed. The premise is checked by L13 |
| D-8 | Approve M15 (both variants) |
| D-9 | Approve the Python 3.11 venv without system site packages. It is needed only for the real profiles, so its approval may be **deferred** until after the stdlib-only synthetic smoke. It must be resolved before any profile |
| D-10 | Is platform intervention (`process_api`, `sbx-telemetry`) observed during smoke an automatic NO-GO? |
| D-11 | `failcnt_action`; `memsw_check_action` and `memsw_tolerance_bytes`; `release_sample_count_k`; the cold-input method (fadvise + mincore, with `O_DIRECT` or prior-read hashing) and `cold_input_residency_max_fraction`. Note that fadvise evicts clean cache only for the admitted inputs; this is a process-scoped action, not a host setting, but confirm it is acceptable |

Reviewer questions:

- **Q-R2:** resolved (B-1 and N-3 fixed).
- **Q-R3:** answered by the cold-input method; awaiting confirmation.
- **Q-R5:** answered by D-3.

## 8. NO-GO conditions

NO-GO for any real profile if any of these holds:

- The chosen D-3 termination path cannot be **proven** by smoke: kill-to-empty, namespace teardown of forks made after the listing, and reaping. For (b) this includes v2 kill/freeze working in a child of the unified root.
- CLONE_NEWPID is unavailable.
- The attempt limit cannot be set and read back below the binding ancestor with the reserve.
- Membership cannot be verified before release.
- Escape detection fails.
- Any of D-1–D-8, D-10 or D-11 is unresolved, or D-9 is unresolved before the first profile.
- Any M03-L, M08-L, M09-L, M12–M14, M16 control fails, or M15 fails once approved.
- Telemetry is incomplete at the measured interval.
- Cold-input residency is above the threshold.
- Windows commit semantics are required.
- Platform intervention is observed during smoke **and D-10 rules it a NO-GO**.

NO-GO is a valid outcome. The alternative is a host that meets the base spec.

## 9. Probe record

**r1 (2026-10-17), read-only.**

- System and kernel: `uname -r`; `/proc/self/cgroup`; `/proc/cgroups`; cgroup mounts and directories; meminfo; overcommit; `panic_on_oom`; `pid_max`; `nproc`.
- cgroup files: memory files at each level; `memory.stat`; `unified/cgroup.controllers`.
- Tools and permissions: `df`; `unshare`/`prlimit` versions; capabilities and seccomp; `ps`; the parent's cgroup; THP; Python 3.11 `os` capabilities.
- Code: a grep of the producers for subprocess, multiprocessing or thread pools.

**r1, mutating (permitted then).**

- Created and removed `…/claude-code-bash/n2-design-probe-f5af6aea` (memory) and `/sys/fs/cgroup/pids/n2-design-probe-f5af6aea`.
- A FIFO-gated `python3.11 -I -c` child joined both. It allocated 32 MiB and exited 0.
- Readings:
  - max usage = memsw max = 34,332,672;
  - residual usage 753,664 (cache 503,808, rss 0);
  - `failcnt` 0, `pids.peak` 1.
- Both `rmdir`s succeeded; no limits were written.
- Freezer, v2 and namespaces were not exercised.

**r2 (2026-10-17), read-only.**

- `/proc/cgroups` showed memory 5 = 5 directories and pids 2 = 2. The reviewer had seen 6 memory cgroups for 5 directories shortly after r1. This is consistent with a dying memcg that has since been freed; it is disclosed as residual behaviour (M12).
- `/sys/kernel/cgroup/features` lists `nsdelegate`, `favordynmods`, `memory_localevents`, `memory_recursiveprot`, `memory_hugetlb_accounting`, `pids_localevents`.
- `unified` has max depth/descendants `max`, and no `cgroup.kill`/`cgroup.freeze` at its root.
- System `.pth` files are as in §0.
- The environment carries proxy- and credential-category variables (counted, values not read).
- `CapEff` bit 24 (`CAP_SYS_RESOURCE`) is clear.
- **Platform cgroup `sbx-telemetry`** (memory and pids hierarchies): `memory.limit_in_bytes` 67,108,864 (64 MiB), `pids.max` 16. Seen in r1's survey reads and recorded here per N-7.

**r3 (2026-10-17), read-only.**

- `/proc/<shell>/task/<shell>/children` exists and lists child PIDs (`CONFIG_PROC_CHILDREN` present).
- The controller-side `NSpid` has 1 field.
- `/proc/self/cgroup` shows `0::/`, i.e. the unified root.
