"""Sampling, membership/escape detection, markers, phase witnesses, residency (sections 3.1, 4).

Single-threaded by design: the controller's main thread forks the child and samples, because
PR_SET_PDEATHSIG follows the forking THREAD. Nothing is ever filled as zero or omitted:
any read/parse failure while a member is alive is a STOP.
"""
import json
import os
import re
import time

from . import cg as cgmod
from . import native, sysfs
from . import pidfd as pidfd_mod
from . import policy as policy_mod
from .errors import ContainmentStop, N2Stop, TelemetryStop

# An exiting task is moved to the root css set (cgroup_exit) before it becomes a zombie, so for a
# short window its /proc cgroup file shows paths outside the attempt. A mismatch only becomes an
# escape STOP if it persists, for a live task, beyond this grace. Harness robustness constant,
# reported in the report; it is NOT a policy value.
EXIT_RACE_GRACE_S = 0.25

STATUS_KB_FIELDS = ("VmRSS", "VmHWM", "RssAnon", "RssFile", "RssShmem", "VmData", "VmPeak")


def parse_status_fields(text):
    out = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in STATUS_KB_FIELDS:
            parts = rest.split()
            if len(parts) != 2 or parts[1] != "kB":
                raise TelemetryStop("parse_error", "status %s: %r" % (key, rest[:30]))
            out[key] = sysfs.parse_int(parts[0], key) * 1024
        elif key == "Threads":
            out["Threads"] = sysfs.parse_int(rest, "Threads")
    for k in STATUS_KB_FIELDS + ("Threads",):
        if k not in out:
            # kernel threads / zombies lack some fields; a LIVE member lacking them is a failure
            raise TelemetryStop("parse_error", "status missing " + k)
    return out


def confirm_outside(ctx, pid, tid=None):
    """True only if the task is alive and stays outside the attempt cgroups for the whole grace."""
    if pidfd_mod.pid_is_exiting(ctx.fs, pid):  # primary: PF_EXITING (set before cgroup_exit) or Z/X/gone
        ctx.absorbed.append({"pid": pid, "tid": tid, "how": "pf_exiting_or_dead"})
        return False
    deadline = time.monotonic() + EXIT_RACE_GRACE_S  # fallback only
    while True:
        if pidfd_mod.pid_is_exiting(ctx.fs, pid):
            ctx.absorbed.append({"pid": pid, "tid": tid, "how": "grace_then_exiting"})
            return False
        try:
            if cgmod.proc_cgroup_matches(ctx.layout, pid, ctx.fs, tid=tid):
                ctx.absorbed.append({"pid": pid, "tid": tid, "how": "grace_then_matches_again"})
                return False
        except OSError:
            ctx.absorbed.append({"pid": pid, "tid": tid, "how": "grace_then_unreadable"})
            return False
        if time.monotonic() >= deadline:
            return True
        time.sleep(0.005)


class SampleCtx:
    """Everything a sample needs. `fs` is the (possibly mock) read layer."""

    def __init__(self, fs, layout, direct, policy, mem_root, controller_mem_rel, scratch_path,
                 meminfo_text_fn=None, plan=None, baseline=None):
        self.fs = fs
        self.layout = layout
        self.direct = direct
        self.ns_init = None
        self.members = {direct.pid: direct}
        self.policy = policy
        self.mem_root = mem_root
        self.controller_mem_rel = controller_mem_rel
        self.scratch_path = scratch_path
        self.meminfo_text_fn = meminfo_text_fn or (lambda: fs.read("/proc/meminfo"))
        self.plan = plan
        self.baseline = baseline or {}
        self.transient_vanished = []
        self.absorbed = []  # mismatches absorbed as exit races: recorded, never silent
        self.ctrl_nspid_len = len(pidfd_mod.nspid_fields(fs, os.getpid()))
        self.events = []

    def alive(self, m):
        return not pidfd_mod.readable(m.fd)


class Telemetry:
    def __init__(self, ctx, out_path, clock_mono=time.monotonic_ns, clock_real=time.time_ns, persist=True):
        self.ctx = ctx
        self.out_path = out_path
        self.mono = clock_mono
        self.real = clock_real
        self.seq = 0
        self.last_mono = None
        self.last_real = None
        self.samples = []
        self.persist = persist
        self._fh = open(out_path, "ab") if persist else None  # noqa: SIM115 - kept open for the sampler lifetime
        self.prev_oom_kill = None
        self.prev_pids_max_events = None

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None

    # --- time and sequence integrity -------------------------------------------------
    def _tick(self):
        now, rnow = self.mono(), self.real()
        if self.last_mono is not None:
            if now < self.last_mono:
                raise TelemetryStop("clock_went_backwards", "monotonic")
            gap_ms = (now - self.last_mono) / 1e6
            if policy_mod.check(self.ctx.policy, "max_sample_gap_ms", gap_ms):
                raise TelemetryStop("sample_gap_exceeded", "%.1f ms" % gap_ms)
        else:
            gap_ms = None
        self.seq += 1
        return now, rnow, gap_ms

    # --- membership ------------------------------------------------------------------
    def _discover(self, listing):
        ctx = self.ctx
        for pid in listing:
            if pid in ctx.members:
                continue
            try:
                m = pidfd_mod.open_verified(pid, ctx.layout, ctx.fs, "member", plan=ctx.plan)
            except ContainmentStop as e:
                if e.code == "member_vanished_before_open" and pid not in self._relist():
                    ctx.transient_vanished.append(pid)
                    continue
                if e.code == "member_outside_attempt_cgroup" and not confirm_outside(ctx, pid):
                    ctx.transient_vanished.append(pid)
                    continue
                raise
            fields = pidfd_mod.nspid_fields(ctx.fs, pid)
            if fields[-1] == 1 and ctx.ns_init is None and len(fields) == ctx.ctrl_nspid_len + 1:
                m.role = "ns_init"
                ctx.ns_init = m
            ctx.members[pid] = m

    def _relist(self):
        return cgmod.procs(self.ctx.layout, "memory", self.ctx.fs)

    def _check_membership(self, listing):
        ctx = self.ctx
        for m in (ctx.direct, ctx.ns_init):
            if m is None or m.closed:
                continue
            # an exit between listing and the liveness check is benign, hence the second liveness test
            if ctx.alive(m) and m.pid not in listing and ctx.alive(m):
                raise ContainmentStop("live_member_absent_from_listing", "%s pid %d" % (m.role, m.pid))

    def _walk_and_escape(self, listing):
        """Descendant walk from ns-init (detection only) and per-thread cgroup checks."""
        ctx = self.ctx
        result = {"descendants": [], "unlisted": [], "thread_checks": 0}
        if ctx.ns_init is not None and ctx.alive(ctx.ns_init):
            desc = pidfd_mod.descendants(ctx.fs, ctx.ns_init.pid)
            result["descendants"] = sorted(desc)
            lst = set(listing)
            for pid in sorted(desc - lst):
                try:
                    attempt_paths = cgmod.proc_cgroup_matches(ctx.layout, pid, ctx.fs)
                except OSError:
                    continue  # exited meanwhile: not an escape
                if attempt_paths:
                    result["unlisted"].append(pid)  # forked after the listing; still contained
                elif not confirm_outside(ctx, pid):
                    continue
                else:
                    raise ContainmentStop("descendant_outside_attempt_cgroup", str(pid))
        for pid in set(listing) | {m.pid for m in ctx.members.values() if ctx.alive(m)}:
            try:
                tids = pidfd_mod.thread_ids(ctx.fs, pid)
            except OSError:
                continue
            for tid in tids:
                try:
                    ok = cgmod.proc_cgroup_matches(ctx.layout, pid, ctx.fs, tid=tid)
                except OSError:
                    continue
                result["thread_checks"] += 1
                if not ok:
                    if not confirm_outside(ctx, pid, tid):
                        break  # exited (or came back) between listing and this read
                    raise ContainmentStop("thread_outside_attempt_cgroup", "pid %d tid %d" % (pid, tid))
        return result

    def _check_unexpected(self, listing, walk):
        """Another writer: a listed process that is neither the direct child, ns-init, nor reachable from
        them through parentage is an unexpected process (concurrency STOP). Parentage is a fixpoint over
        /proc ppid so forks made after the last walk and reparented orphans are not false positives."""
        ctx = self.ctx
        known = {ctx.direct.pid}
        if ctx.ns_init is not None:
            known.add(ctx.ns_init.pid)
        known.update(walk["descendants"])
        known.update(walk["unlisted"])
        parents_ok = known | {os.getpid()}
        pending = [p for p in listing if p not in known]
        progress = True
        while pending and progress:
            progress = False
            for pid in list(pending):
                try:
                    _st, ppid = pidfd_mod.read_state_ppid(ctx.fs, pid)
                except (OSError, ValueError, IndexError):
                    pending.remove(pid)  # gone
                    progress = True
                    continue
                if ppid in parents_ok:
                    parents_ok.add(pid)
                    pending.remove(pid)
                    progress = True
        pending = [p for p in pending if not pidfd_mod.pid_is_exiting(ctx.fs, p)]
        if pending:
            raise ContainmentStop("unexpected_process_in_cgroup", "pids %r" % sorted(pending))

    # --- one sample ------------------------------------------------------------------
    def sample(self):
        ctx = self.ctx
        now, rnow, gap_ms = self._tick()
        s = {"seq": self.seq, "mono_ns": now, "real_ns": rnow, "interval_ms": gap_ms}
        try:
            listing = cgmod.members(ctx.layout, ctx.fs)
        except (OSError, N2Stop) as e:
            raise TelemetryStop("membership_read_failed", str(e), injected=getattr(e, "label", None))
        s["listing"] = listing
        self._discover(listing)
        self._check_membership(listing)
        s["walk"] = self._walk_and_escape(listing)
        self._check_unexpected(listing, s["walk"])
        try:
            mem = sysfs.read_memory_fields(ctx.fs, ctx.layout.abs["memory"])
            s["memory"] = mem
            s["counters"] = cgmod.counters(ctx.layout, ctx.fs)
            avail = sysfs.effective_available(ctx.fs, ctx.mem_root, ctx.controller_mem_rel, ctx.meminfo_text_fn())
            s["effective_available_bytes"] = avail["effective_available_bytes"]
            s["available_terms"] = avail["terms"]
            s["bash_usage_excluding_attempt_bytes"] = sysfs.bash_usage_excluding_attempt(
                ctx.fs, ctx.mem_root, ctx.controller_mem_rel, ctx.layout.name)
            s["disk_free_bytes"] = sysfs.disk_free_bytes(ctx.scratch_path)
            s["host_commit_unenforced"] = self._commit_note()
            swap = sysfs.parse_meminfo_bytes(ctx.fs.read("/proc/meminfo"), "SwapTotal")
            s["swap_total_bytes"] = swap
            if swap != 0:
                raise TelemetryStop("swap_appeared", "SwapTotal=%d; memsw=2x relies on no swap" % swap)
        except OSError as e:
            raise TelemetryStop("sample_read_failed", str(e), injected=getattr(e, "label", None))
        procs = {}
        for pid, m in sorted(ctx.members.items()):
            entry = {"role": m.role, "alive": ctx.alive(m)}
            if entry["alive"]:
                try:
                    entry["status"] = parse_status_fields(ctx.fs.read("/proc/%d/status" % pid))
                    st, ppid = pidfd_mod.read_state_ppid(ctx.fs, pid)
                    entry["state"], entry["ppid"] = st, ppid
                except (OSError, TelemetryStop) as e:
                    # an exiting task loses its mm (VmRSS etc. vanish) before it becomes a zombie:
                    # only a task that really exits within the grace may have its status unavailable
                    if not self._exits_within_grace(m, pid):
                        if isinstance(e, TelemetryStop):
                            raise
                        raise TelemetryStop("member_read_failed", "%d: %s" % (pid, e))
                    entry["alive"] = False
                    entry["status_unavailable_exiting"] = True
            procs[str(pid)] = entry
        s["members"] = procs
        s["transient_vanished"] = list(ctx.transient_vanished)
        s["absorbed_mismatches"] = list(ctx.absorbed)
        ctx.absorbed.clear()
        self.samples.append(s)
        if self._fh:
            self._fh.write(json.dumps(s, sort_keys=True).encode("ascii") + b"\n")
            self._fh.flush()
        self.last_mono, self.last_real = now, rnow
        return s

    def _exits_within_grace(self, m, pid):
        deadline = time.monotonic() + EXIT_RACE_GRACE_S
        while True:
            if pidfd_mod.readable(m.fd) or pidfd_mod.pid_is_exiting(self.ctx.fs, pid):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)

    def _commit_note(self):
        # CommitLimit/Committed_AS are recorded and labelled "not enforced" (overcommit_memory=0)
        txt = self.ctx.fs.read("/proc/meminfo")
        out = {"label": "not_enforced"}
        for key in ("CommitLimit", "Committed_AS"):
            out[key] = sysfs.parse_meminfo_bytes(txt, key)
        return out


# --- policy evaluation over a sample ---------------------------------------------------
def evaluate_breaches(ctx, sample, elapsed_s, resident_elsewhere_bytes=0, prev=None):
    """Return list of (code, detail) breaches for one sample under the policy semantics."""
    pol = ctx.policy
    b = []
    if policy_mod.check(pol, "running_floor_effective_available_bytes", sample["effective_available_bytes"]):
        b.append(("floor_effective_available", sample["effective_available_bytes"]))
    limit = sample["memory"]["limit_bytes"]
    if limit is not None:
        headroom = limit - sample["memory"]["cg_working_set_bytes"]
        if policy_mod.check(pol, "running_floor_attempt_headroom_bytes", headroom):
            b.append(("floor_attempt_headroom", headroom))
    base = ctx.baseline.get("bash_usage_excluding_attempt_bytes")
    if base is not None:
        growth = sample["bash_usage_excluding_attempt_bytes"] - base
        if policy_mod.check(pol, "bash_cgroup_growth_max_bytes", growth):
            b.append(("bash_cgroup_growth", growth))
    if policy_mod.check(pol, "running_floor_disk_free_bytes", sample["disk_free_bytes"]):
        b.append(("floor_disk_free", sample["disk_free_bytes"]))
    if policy_mod.check(pol, "per_slot_elapsed_cap_s", elapsed_s):
        b.append(("elapsed_cap", elapsed_s))
    conservative = sample["memory"]["cg_charged_peak_bytes"] + resident_elsewhere_bytes
    if policy_mod.check(pol, "conservative_peak_max_bytes", conservative):
        b.append(("conservative_peak", conservative))
    m = sample["memory"]
    if m["oom_kill"] > 0 or m["under_oom"]:
        b.append(("oom_signal", {"oom_kill": m["oom_kill"], "under_oom": m["under_oom"]}))
    if m["failcnt"] > 0 and pol["fields"]["failcnt_action"]["value"] == "stop":
        b.append(("failcnt_nonzero", m["failcnt"]))
    ev = sample["counters"].get("pids_events")
    if ev and pids_events_max(ev) > 0:
        b.append(("pids_max_hit", pids_events_max(ev)))
    return b


def pids_events_max(text):
    for line in text.splitlines():
        k, _, v = line.partition(" ")
        if k == "max":
            return sysfs.parse_int(v, "pids.events max")
    return 0


def evaluate_post_exit(policy, memory, counters, resident_elsewhere_bytes=0):
    """Gates that remain meaningful after the child has exited (final cgroup counters): charged peak +
    resident_elsewhere cap (D-11), failcnt (action stop), oom_kill / under_oom, pids.events max.
    Live-only floors (available memory, headroom, disk, Bash growth, elapsed) are not evaluated."""
    b = []
    conservative = memory["cg_charged_peak_bytes"] + resident_elsewhere_bytes
    if policy_mod.check(policy, "conservative_peak_max_bytes", conservative):
        b.append(("conservative_peak", conservative))
    if memory["oom_kill"] > 0 or memory["under_oom"]:
        b.append(("oom_signal", {"oom_kill": memory["oom_kill"], "under_oom": memory["under_oom"]}))
    if memory["failcnt"] > 0 and policy["fields"]["failcnt_action"]["value"] == "stop":
        b.append(("failcnt_nonzero", memory["failcnt"]))
    ev = counters.get("pids_events")
    if ev and pids_events_max(ev) > 0:
        b.append(("pids_max_hit", pids_events_max(ev)))
    return b


ERRNO_STOP = {"EAGAIN": "process_creation_failed", "ENOSPC": "disk_exhausted", "EDQUOT": "disk_exhausted",
              "ENOMEM": "memory_error", "EMFILE": "fd_exhausted"}


def classify_error_event(kind):
    """Child-reported error kinds (real errno names or neutral INJECTED_E_* labels) -> STOP code."""
    base = kind.removeprefix("INJECTED_E_")
    if base in ERRNO_STOP:
        return ERRNO_STOP[base]
    if base == "LIBRARY_LOAD" or base == "library_load":
        return "library_load_failure"
    if base == "MEMORYERROR":
        return "memory_error"
    return "child_error_" + re.sub(r"[^A-Za-z0-9_]", "_", base)[:24]


# --- markers ---------------------------------------------------------------------------
class MarkerReader:
    """Reads the child's stderr log incrementally. Only lines carrying the nonce are trusted."""

    def __init__(self, path, nonce):
        self.path = path
        self.nonce = nonce
        self.pos = 0
        self.partial = b""
        self.events = []
        self.untrusted = []
        self.other_bytes = 0

    def poll(self):
        new = []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read()
        except FileNotFoundError:
            return new
        self.pos += len(data)
        data = self.partial + data
        lines = data.split(b"\n")
        self.partial = lines.pop()
        for raw in lines:
            line = raw.decode("ascii", "replace")
            parts = line.split(" ")
            if parts and parts[0] == "N2M":
                if len(parts) >= 4 and parts[1] == self.nonce and parts[2].isdigit():
                    kv = {}
                    for p in parts[4:]:
                        k, _, v = p.partition("=")
                        kv[k] = v
                    ev = {"mono_ns": int(parts[2]), "event": parts[3], "kv": kv}
                    self.events.append(ev)
                    new.append(ev)
                else:
                    self.untrusted.append(line[:120])
            else:
                self.other_bytes += len(raw) + 1
        return new


class PhaseRecorder:
    """Only phases the child itself marked, only if declared; scalars only (M10).

    A boundary is the pair (marker event, the most recent sample). No per-phase attribution is
    computed between boundaries and no setup cost is ever subtracted.
    """
    SCALAR = (int, float, str, bool, type(None))

    def __init__(self, declared):
        self.declared = tuple(declared)
        self.boundaries = []
        self.undeclared = []

    def on_marker(self, ev, last_sample):
        if ev["event"] != "phase":
            return
        name = ev["kv"].get("name", "")
        if name not in self.declared:
            self.undeclared.append(name)
            return
        scal = self.scalar_witness(last_sample)
        self.boundaries.append({"phase": name, "child_mono_ns": ev["mono_ns"], **scal})

    def scalar_witness(self, sample):
        if sample is None:
            return {"sample_seq": None, "cg_charged_peak_bytes": None, "cg_working_set_bytes": None}
        w = {"sample_seq": sample["seq"],
             "cg_charged_peak_bytes": sample["memory"]["cg_charged_peak_bytes"],
             "cg_working_set_bytes": sample["memory"]["cg_working_set_bytes"]}
        self.assert_scalars(w)
        return w

    @classmethod
    def assert_scalars(cls, d):
        for k, v in d.items():
            if not isinstance(v, cls.SCALAR):
                raise N2Stop("non_scalar_witness", "%s is %s" % (k, type(v).__name__))
        return True


# --- residency -------------------------------------------------------------------------
def residency_report(paths):
    """mincore residency per file + total. `paths` must be files owned/readable by us."""
    rows = []
    tot_res = tot_size = 0
    for p in paths:
        r = native.residency(p)
        rows.append({"path": p, **r})
        tot_res += r["resident_bytes"]
        tot_size += r["size"]
    return {"files": rows, "resident_bytes": tot_res, "size": tot_size,
            "fraction": (tot_res / tot_size) if tot_size else 0.0}


def check_cold_inputs(report, policy):
    if policy_mod.check(policy, "cold_input_residency_max_fraction", report["fraction"]):
        raise N2Stop("cold_input_residency_above_threshold", "%.3f" % report["fraction"])
    return True


def validate_series(samples, max_gap_ms=None):
    """Detect missed, duplicate and out-of-order samples and clock regressions in a persisted series.
    Returns a list of (index, problem). Nothing is repaired or filled."""
    problems = []
    prev = None
    for i, s in enumerate(samples):
        if prev is not None:
            if s["seq"] == prev["seq"]:
                problems.append((i, "duplicate_seq"))
            elif s["seq"] < prev["seq"]:
                problems.append((i, "out_of_order_seq"))
            elif s["seq"] != prev["seq"] + 1:
                problems.append((i, "missed_samples"))
            if s["mono_ns"] < prev["mono_ns"]:
                problems.append((i, "clock_backwards"))
            elif max_gap_ms is not None and (s["mono_ns"] - prev["mono_ns"]) / 1e6 > max_gap_ms:
                problems.append((i, "gap_exceeded"))
        prev = s
    return problems
