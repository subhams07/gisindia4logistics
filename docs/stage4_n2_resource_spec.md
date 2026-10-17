# N2 resource-feasibility specification — paused handoff

Read [the remote handoff](claude_remote_handoff.md) first. This is a curated portable specification of reviewed design decisions, **not execution authority or implemented code**. No harness exists yet. Current operator pause overrides earlier synthetic-only design permission. Original model reports/control receipts remain private and are not bundled here.

## A. Purpose and scope

Establish whether national regeneration is operationally feasible, without confusing a bounded resource sample with national accuracy or orchestration readiness. First implement a temporary scratch-only harness and test it with tiny synthetic inputs. No production edits, packages, configuration changes, national data reads, audits or real profiles during that synthetic slice. Accepted first-party helpers may be imported read-only to build synthetic fixtures.

Modes:
- `selftest`: synthetic fixtures only, disjoint private scratch roots.
- `prepare`: only admitted code/configuration/path metadata; approved empty control roots. No primary/population/cache content reads, warming or transfer construction.
- `run`: refuse before creating a child unless approval, policy, finite read/write scope and ledger are present, correctly bound and hash-valid. Finding an existing artifact never grants authority.

Sonnet is the sole author of harness/support library/self-tests under its own private prefix. The coordinator owns the **actual runtime control root, attempt/launch/terminal writes, protected-state checks and real dispatch**. Sonnet may author controller-support functions, but only exercises them on synthetic mock control roots, never the canonical real ledger. Children/producers hold no ledger authority. Sol reviews independently; Opus aligns exact hashes.

## B. Exactly six serial profiles — later, separately admitted

Every slot is a fresh exclusive process. These are deterministic nested source-order prefixes, **not representative national samples**. Freeze actual source order and count on the first authorized read. Stop if a requested prefix cannot be satisfied. Never relax the independent graph oracle's 12-row cap or run an extra slot as a retry.

| Slot | Real computation | Scratch outputs |
|---|---|---|
| H0 | Real `graph_builder.load_highway_source`, then `load_or_build_cached_graph` with an absent fresh scratch cache; real `validate_graph_metadata`, fingerprint/version capture | Saved/closed graph archive and receipt; not repository cache |
| H1(12) | Real `nh_travel_matrix.build_travel_outputs(data_dir=source, cache_dir=H0, chunk_size=32)` with district-read boundary limited to first 12 rows; real `write_travel_outputs` separately | Summary CSV/Parquet and port matrix |
| H1(64) | Same, first 64 rows | Separate files |
| H1(192) | Same, first 192 rows | Only this slot's outputs qualify for H3 transfer |
| H2 | Real `generate_isochrones.build_isochrones`, all source major ports (`hub_type == major_port`) and all MMLPs; port thresholds 4/8/12/24 h, MMLP 4/8/12 h; Dijkstra chunk <=32 | Two scratch isochrone files or measured in-memory results; no national bundle |
| H3 | Real `compute_modal_costs(params, output_csv=own_path, data_dir=transfer_root)` and `run_port_gravity_model(alpha=1.2, beta=1.5, matrix_csv=transfer_matrix, population_csv=transfer_population, output_csv=own_path, data_dir=transfer_root)` | Sample freight/gravity tables only |

Trace current definitions and by-value import bindings before authoring; names above refer to accepted code, not a promise of a ready-made harness API. No fake graph, output, validator, collision rule, route kernel or numerical model.

## C. Preserve full W0 population context

A sampled district prefix must not change the full-source population collision policy.

H1:
1. Wrap `nh_travel_matrix.gpd.read_file`, intercepting **only** the canonical district source by resolved-path equality. Because geopandas is a shared module, all other calls delegate to the saved real callable.
2. Perform the full real read. Freeze a **deep identity-columns-only** context before the producer's float64 cast; return the frozen positional prefix. No full geometry retained in a closure/witness.
3. Save the real `producer_io.district_population` callable, then wrap the matrix module's **by-value** `district_population` binding. Call the real helper on the full identity frame and untrimmed population; select frozen positions, restore the sampled index, assert literal incoming identities/order/index. No new name/code join.
4. Real CSV/Parquet readback must preserve literal frozen identities without normalizing. A lossy large-code round trip STOPs; do not edit production to bypass it.

H3 gravity: adapt the `producer_io.district_population` module attribute used by `port_hinterland_model`, with the saved real callable and sample-to-full-context membership map. Both bindings need real-delegating witnesses. Any mismatch STOPs.

Full source read/projection and context setup are included in the child's private-commit high-water mark. **Never subtract setup costs.** Instrumentation is disclosed and restored; no retained geometry in witnesses.

## D. Guard the H0 cache

H0 succeeds only when the real cold build saved and closed an archive, metadata validated, fingerprint/version captured, the parent hashed the bytes and **no warning was latched**. Missing archive or warning fails H0.

For H1/H2:
- Require expected archive hash/version/fingerprint before and after each slot.
- Install a forbidden-build sentinel at `graph_builder.build_canonical_highway_graph`, the real builder binding reached by fallback. Any call raises before building/writing.
- Persist an out-of-band sentinel-hit latch plus a non-raising `graph_builder` WARNING-or-higher logging latch. Any latch fails the slot **even with exit 0**, including swallowed exceptions.
- Observe both real loader dispatch paths: matrix's by-value alias and the builder module attribute. Delegate real loads; never return cached fake captures.
- No warming, replacement, parent-retained graph, repository-cache fallback, ACL changes or claims that `chmod` establishes Windows containment.

A failed/invalid H0 blocks H1 and H2. No silent rebuild.

## E. H1(192) → H3 transfer has two domains

Use a separate disjoint scratch transfer root, never H1's own directory or live `data/analysis`.

1. **Sample domain:** ordered H1(192) positions and summary/matrix/freight/gravity row identities must match exactly.
2. **Full context domain:** complete primary identity context and complete population source, bound to original hashes, **never trimmed**.

Sample positions must map into full context. The two domains must **not** be required to equal each other. This supersedes the earlier draft's overly broad statement that every transferred identity equals the sample.

Copy only explicitly needed summary CSV (Parquet only if required), port matrix, population and serialized full identity context. Record every source/destination, containment, regular-file status, bytes, hashes, copy completion, readback, run/source/code/sample/row identities. All checks precede entering either model. Preserve intentional null population. No fallback to B0. Missing/invalid transfer blocks H3. No publication manifest/sidecar is produced by N2.

## F. Consumed attempts and Windows launch containment

The canonical coordinator-owned control root is private and separate from logs/slots/transfer. Two immutable records:

- **Attempt:** exclusive create before child creation, keyed by approval-receipt SHA plus slot, not arbitrary run/directory name. Bind approval/policy/harness/self-test hashes, interpreter, source/code identities, slot and roots. No PID. A failed launch remains consumed; any inability to persist consumption fails closed and blocks further dispatch.
- **Launch:** after creation, before release; exact owned PID, `GetProcessTimes` creation time and job identity.

A renamed directory/new run label does not reset consumption. Dependent slots that never dispatched stay unconsumed but require an explicit ruling. No later slot before logs are drained, process and job tree have ended and terminal receipt is durable. Do not claim filesystem transaction/fsync guarantees not implemented.

Reviewed Windows launch order, as amended:
1. Create a **non-inheritable** Job Object and configure `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` **before a child exists**. Failure consumes the attempt and stops dispatch.
2. `CreateProcessW(CREATE_SUSPENDED)`. Only slot stdout/stderr handles and `NUL` stdin may be inheritable. Correct native handle widths; no elevation or new ACLs. `Popen` does not expose the required initial thread handle for this design.
3. Assign suspended child to the owned job.
4. Persist launch record and establish working telemetry.
5. Only then `ResumeThread`.

Child's first statements use only the standard library (`sys`, `os`, `ctypes`, `json`, `time`), verify `IsProcessInJob`, and refuse with a fixed code before any first-party import/source read/work if not contained. Parent death must close the job and terminate its owned tree. Normally terminate through the **owned job handle** after the specified graceful wait, never by PID or `OpenProcess`.

Pre-assignment failure exception: a known newly created child that is still suspended and has never run may be terminated through the **exact `hProcess` returned by that `CreateProcessW`**, waited for and its owned handles closed. No process lookup, PID targeting or resuming merely to clean up.

**Clarification wording to resolve before implementation:** the follow-up ruling also permits exact-handle cleanup for launch-record/telemetry failure before resume, but states that once assignment succeeded every termination uses the job. Those overlap for post-assignment/pre-resume failure. Preserve both constraints and obtain an explicit interpretation (job termination after assignment versus the stated exception); do not invent a broader cleanup exemption. This document flags the ambiguity rather than claiming a third ruling occurred.

**Remote platform gate:** these native APIs and commit metrics are Windows-specific. Linux/container equivalents need a separately reviewed containment/telemetry/parent-death design and new synthetic proofs, not renamed Windows fields. Do not proceed if the remote host cannot provide the required isolation.

## G. Telemetry and unresolved policy

Record current/peak **process-private commit** (`PagefileUsage`, `PeakPagefileUsage`) separately from working set/peak working set. Job `PeakJobMemoryUsed` is optional corroboration, not a replacement. Record system `CommitTotal` and `CommitLimit` multiplied by `PageSize`; available commit is limit minus total. API failure is STOP, not zero or an omitted value. Available RAM/RSS/working set is not system commit.

Timestamp, process identity, interval, missed/out-of-order samples and phase-boundary observations must be persisted. Polling can miss spikes between samples; disclose that limitation. Observed boundaries include imports, source/context selection, fingerprint/cache build/load, snapping/Dijkstra passes, ISO geometry, transfer validation, freight/gravity, serialization, return/shutdown. Do not invent internal-phase attribution where no real boundary was instrumented. Retain scalar-only witnesses.

Real policy receipt must explicitly specify bytes/seconds, comparison strictness/inclusiveness, and a basis for each:
- `launch_min_system_commit_available_bytes`
- `running_floor_system_commit_available_bytes`
- `sample_interval_ms`, `max_sample_gap_ms`
- `per_slot_elapsed_cap_s`
- `graceful_wait_s`, `job_terminate_wait_s`

**No numerical values or admission formula have been approved.** Need idle-host current commit measurement, operator-agreed reserve and measured synthetic monitor/termination latency; Sol critiques, Opus rules. NO-GO is valid. Old working-set/system-commit arithmetic and linear extrapolation are superseded. Any later sample scaling fit is a labelled heuristic requiring review.

STOP on real `MemoryError`, disk exhaustion/WinError 112, commit/paging/1455, DLL/resource errors, telemetry loss/gap, policy floor/time breach, unexpected death, missing receipt, cache violations, unsafe roots, hash/identity drift or concurrency. Preserve logs/last telemetry/consumption and clean up only the owned contained tree. No unrelated kills, paging changes, retry, extra profile or receipt deletion. Synthetic faults must use neutral injection labels, not masquerade as actual host failures.

N2 does not measure N3 post-producer orchestration retention, full N4 audits or N6 DataStore startup. Each needs a separate resource admission. Resource feasibility is not output accuracy or certified headroom.

## H. Minimum synthetic controls — M01–M11

Persist a test-name-to-row map before long execution. Use tiny fake datasets and deliberate fault injection, not actual allocation/disk exhaustion.

| ID | Required controls |
|---|---|
| M01 | Mode isolation; selftest/prepare cannot reach national content paths; missing/changed approval, policy, scope or harness hash denies run before child creation |
| M02 | Exclusive attempt before creation; launch failure consumes; renamed run/root refused; exact six-slot order/dependencies; exit/log/terminal-receipt sequencing |
| M03 | Job-before-child setup, suspended assignment, launch record/telemetry then resume; child containment refusal; denied native APIs; failed suspended cleanup/no survivor/no resume; non-inheritable job/correct handle widths; parent death and graceful/job termination; unrelated process survives; no PID reuse targeting |
| M04 | Disjoint roots; resolved ancestors and leaf reparse rejection; missing/nonregular/swapped paths; no repository/default-cache/B0 fallback |
| M05 | Real tiny W0 helper parity: cross-prefix collision, same-state duplicate outside prefix, uncoded/null population, non-default index, literal labels, lossy large-code round trip STOP; both actual population bindings; no retained geometry |
| M06 | Tiny real cold graph and warm real loads through both bindings; missing/tampered cache, warning/fallback, swallowed handler exception, latch despite exit 0 |
| M07 | Both transfer domains, membership and exact real H3 arguments; tamper/missing/null/order drift refused before model entry |
| M08 | Native layout/page-to-byte conversion; private/current/peak/working fields distinct; API failure STOP; missed/out-of-order samples |
| M09 | Injected test-policy strict/inclusive crossings; gap/time expiry; resource-console detection; cache latch; exit without receipt; neutral injection labels |
| M10 | Only real phase witnesses; scalar retention; serialization included; partial output never grants authority |
| M11 | Repository preservation; scratch manifests cannot be confused with national publication manifests; exact logs/hashes |

Allowed native smoke: tiny **stdlib-only** owned child allocating a few MB and sleeping, real suspended/job/telemetry/graceful/termination/parent-death paths. No repository producer imports, national reads, models or actual profiling in that smoke.

Worker: synthetic self-tests, mapped matrix, scoped Ruff and preservation. Coordinator: scoped self-tests and direct protected checks. Sol: independent self-tests/static review. Opus: exact hash alignment. **No broad repository suite by default.** Keep review raw evidence private.

## I. N4 read-scope admission remains closed

Original metadata-only discovery found **11 non-producer registrations including `audit_catalog`**, 148 candidate existing leaf paths and 42 catalog entries. It checked lexical containment/leaf metadata, not full resolved ancestor containment. Its historical report had 279 rows: 268 PASS / 11 WARN / 0 FAIL. That is **not a fresh audit pass** or a current remote inventory.

Before N4: resolve and freeze every ancestor/leaf, persist missing candidates, distinguish content reads from stat/glob/count, adjudicate recursion/catalog targets and bind a complete finite ancillary `data/analysis` whitelist excluding the nine publication members. Build the admitted source/staged overlay without changing the actual audit code. Every fresh FAIL blocks publication. No baseline updates or filtering.

## J. Review provenance — references, not transferable authority

Original local design artifacts were authenticated before the pause; these public references are historical hashes only, not uploaded private reports or real execution receipts:
- Coordinator plan: `2520c14c9053a11da2627b0bb73f2a836e3a05c0ad389c46bf5e3575cc6aa95f`.
- Sol critique report exact SHA-256: `1cc34cd84556eb110978b589d4761f3ff37cf47d924339e4143c716eb678abfe`.
- Opus synthetic-only reconciliation native: `4f2ef416ae27400b966c3e8604d500f90da5bbe89a58a8a32649244d28159ffd`.

The real run, read/write admission, numerical policy, N3–N7, P5/P6 and merge remain blocked. Current code is the source for implementation signatures; this specification is the review contract and open-decision record, not a replacement producer manifest.
