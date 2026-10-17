"""Attempt controller: one slot attempt, serial, single-threaded (sections F/G of the addendum).

Order of effects (never reordered): policy+ledger gate -> binary/stub verification ->
cold-input checks -> ATTEMPT RECORD (consumes) -> cgroup mkdir + limits -> fork gated child (S0) ->
join + verify (S1) -> LAUNCH RECORD -> k complete samples -> residency re-check -> release (S2) ->
handshake -> monitor -> terminate/reap -> counters -> rmdir -> logs hashed -> TERMINAL RECEIPT.
"""
import hashlib
import json
import os
import signal
import stat
import sys
import time

from . import cg as cgmod
from . import launch as launch_mod
from . import ledger as ledger_mod
from . import native, stubsrc, sysfs
from . import pidfd as pidfd_mod
from . import policy as policy_mod
from . import telemetry as tel_mod
from . import term as term_mod

# Pinned stdlib allowlist for the L-3 module check is in allowlist.py (generated, reviewed).
from .allowlist import STDLIB_ALLOWLIST
from .errors import (
    CgroupStop,
    ContainmentStop,
    FaultPlan,
    InjectedFault,
    LaunchStop,
    N2Stop,
    PolicyStop,
    RootsStop,
    TelemetryStop,
)
from .fsio import FaultFs, RealFs
from .launch import build_env, fork_gated, verify_binary


def default_elsewhere_files():
    """Pinned file set whose page-cache residency is reported as additive uncertainty (D-11):
    the interpreter and every shared library / extension mapped by THIS controller process, plus the
    stdlib sources and their cached bytecode for the loaded modules. 4b adds the venv site-packages."""
    files = set()
    try:
        with open("/proc/self/maps") as f:
            for line in f:
                parts = line.split(None, 5)
                if len(parts) == 6 and parts[5].startswith("/") and "(deleted)" not in parts[5]:
                    p = parts[5].strip()
                    if os.path.isfile(p):
                        files.add(os.path.realpath(p))
    except OSError:
        pass
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if f and os.path.isfile(f) and f.startswith("/usr/lib/python3"):
            files.add(os.path.realpath(f))
            pyc = _pyc_of(f)
            if pyc and os.path.isfile(pyc):
                files.add(os.path.realpath(pyc))
    files.add(os.path.realpath("/usr/bin/python3.11"))
    return sorted(files)


def _pyc_of(src):
    d, n = os.path.split(src)
    if not n.endswith(".py"):
        return None
    return os.path.join(d, "__pycache__", n[:-3] + ".cpython-311.pyc")


def file_sha256(path):
    return ledger_mod.sha256_file(path)


class Attempt:
    def __init__(self, cfg):
        self.cfg = cfg
        self.plan = cfg.get("plan") or FaultPlan()
        self.hooks = cfg.get("hooks") or {}
        base_fs = cfg.get("fs") or RealFs()
        self.fs = FaultFs(base_fs, cfg["fs_faults"]) if cfg.get("fs_faults") else base_fs
        self.policy = cfg["policy"]
        self.ledger = cfg["ledger"]
        self.cg_root = cfg.get("cg_root", cgmod.CG_ROOT)
        self.mem_root = self.cg_root + "/memory"
        self.stops = []
        self.events = []
        self.timeline = {}
        self.key = None
        self.layout = None
        self.gated = None
        self.ctx = None
        self.tele = None
        self.markers = None
        self.phases = None
        self.termination = None
        self.exit_info = {}

    # ------------------------------------------------------------------ helpers
    def _mark(self, name):
        self.timeline[name] = time.monotonic_ns()

    def _hook(self, name):
        h = self.hooks.get(name)
        if h:
            h(self)

    def _stop(self, exc):
        rec = {"code": exc.code, "detail": exc.detail, "injected": exc.injected}
        self.stops.append(rec)
        return rec

    # ------------------------------------------------------------------ preconditions
    def preconditions(self):
        cfg, fs, pol = self.cfg, self.fs, self.policy
        self.ctrl_paths = cgmod.controller_cgroup_paths(fs)
        rec = cfg.get("recorded_controller_paths")
        if rec is not None and rec != self.ctrl_paths:
            raise CgroupStop("controller_cgroup_changed", "%r != %r" % (self.ctrl_paths, rec))
        cgmod.assert_no_existing(fs, self.cg_root)
        meminfo = fs.read("/proc/meminfo")
        self.mem_rel = self.ctrl_paths["memory"]
        avail = sysfs.effective_available(fs, self.mem_root, self.mem_rel, meminfo)
        self.launch_avail = avail
        self.swap_total = sysfs.swap_total_bytes(meminfo)
        if self.swap_total != 0:
            raise PolicyStop("swap_present", "SwapTotal=%d; memsw=2x limit relies on no swap" % self.swap_total)
        if policy_mod.check(pol, "launch_min_effective_available_bytes", avail["effective_available_bytes"]):
            raise PolicyStop("launch_floor_effective_available", str(avail["effective_available_bytes"]))
        need = policy_mod.value(pol, "attempt_memory_limit_bytes") + policy_mod.value(pol, "reserve_bytes")
        if need > avail["effective_available_bytes"]:
            raise PolicyStop("limit_plus_reserve_exceeds_available", "%d > %d" % (need, avail["effective_available_bytes"]))
        disk = sysfs.disk_free_bytes(cfg["scratch_base"])
        if policy_mod.check(pol, "launch_min_disk_free_bytes", disk):
            raise PolicyStop("launch_floor_disk_free", str(disk))
        self.launch_disk = disk
        self.bash_baseline = sysfs.bash_usage_excluding_attempt(fs, self.mem_root, self.mem_rel, "n2-none")
        self.binaries = {
            "unshare": verify_binary(cfg["unshare"]),
            "interpreter": verify_binary(cfg["interpreter"]),
            "setpriv": verify_binary(cfg["setpriv"]),
        }
        self.stub_sha = hashlib_sha(stubsrc.STUB)
        if len(stubsrc.STUB.encode("ascii")) >= 128 * 1024:
            raise LaunchStop("stub_too_large", "")
        # payload (workload) written read-only into the slot dir, sha bound in the attempt record
        pdir = cfg["slot_root"]
        if os.listdir(pdir):
            raise RootsStop("slot_root_not_empty", "a fresh empty slot root is required; found %r" % sorted(os.listdir(pdir))[:5])
        self.payload_path = os.path.join(pdir, "payload.py")
        fd = os.open(self.payload_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o444)
        os.write(fd, stubsrc.WORKLOAD.encode("ascii"))
        os.close(fd)
        self.payload_sha = hashlib_sha(stubsrc.WORKLOAD)
        # cold inputs and additive-uncertainty set (D-11); both are repeated just before release
        self.memcg_residual_before = (cgmod.memcg_counts() or 0) - cgmod.count_memory_dirs(self.cg_root)
        self.cold_report = self._cold_inputs("launch")
        self.elsewhere = self._elsewhere("launch")

    def _cold_inputs(self, phase):
        cfg = self.cfg
        files = cfg.get("cold_inputs") or []
        for p in files:
            native.fadvise_dontneed_own(p, cfg["scratch_base"])
        rep = tel_mod.residency_report(files) if files else {"files": [], "resident_bytes": 0, "size": 0, "fraction": 0.0}
        rep["phase"] = phase
        tel_mod.check_cold_inputs(rep, self.policy)
        return rep

    def _elsewhere(self, phase):
        files = self.cfg.get("elsewhere_files")
        if files is None:
            files = default_elsewhere_files()
        rep = tel_mod.residency_report(files)
        rep["phase"] = phase
        rep["resident_bytes_elsewhere"] = rep["resident_bytes"]
        return rep

    # ------------------------------------------------------------------ main
    def run(self):
        cfg = self.cfg
        self.ledger.assert_dispatch_allowed()
        ok, why = self.ledger.can_dispatch_slot(cfg["approval_sha"], cfg["slot"])
        if not ok:
            raise ledger_mod.LedgerStop("slot_not_dispatchable", why)
        self._install_term_handlers()
        try:
            return self._run_after_gate()
        finally:
            self._restore_term_handlers()

    def _install_term_handlers(self):
        """L13 (measured): the Bash tool's stop delivers SIGTERM to the whole process group. Treat
        SIGTERM/SIGHUP/SIGINT as a termination request handled in the main loop (flag only)."""
        self.term_signals = []
        self._old_handlers = {}

        def handler(sig, frame):
            self.term_signals.append(sig)
        for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            self._old_handlers[s] = signal.signal(s, handler)

    def _restore_term_handlers(self):
        for s, old in getattr(self, "_old_handlers", {}).items():
            signal.signal(s, old)

    def _check_external_term(self):
        if self.term_signals:
            raise PolicyStop("external_termination_request", "signals received by the controller: %r" % self.term_signals)

    def _run_after_gate(self):
        cfg = self.cfg
        self.preconditions()
        self.nonce = cfg.get("nonce") or os.urandom(8).hex()
        key = ledger_mod.attempt_key(cfg["approval_sha"], cfg["slot"])
        self.name = "n2-" + key[:20]
        body = {
            "policy_sha": cfg["policy_sha"], "harness_sha": cfg["harness_sha"], "scope_sha": cfg["scope_sha"],
            "selftest_sha": cfg["selftest_sha"], "control_root_id": cfg["control_root_id"],
            "control_root_realpath": os.path.realpath(self.ledger.root),
            "interpreter": {k: self.binaries["interpreter"][k] for k in ("path", "real", "ident", "sha")},
            "unshare": {k: self.binaries["unshare"][k] for k in ("path", "real", "ident", "sha")},
            "setpriv": {k: self.binaries["setpriv"][k] for k in ("path", "real", "ident", "sha")},
            "swap_total_bytes": self.swap_total, "stub_sha": self.stub_sha, "payload_sha": self.payload_sha,
            "boot_id": self.fs.read("/proc/sys/kernel/random/boot_id").strip(),
            "kernel_release": os.uname().release,
            "controller_cgroup_paths": self.ctrl_paths, "planned_cgroup_name": self.name,
            "slot": cfg["slot"], "slot_root": cfg["slot_root"], "scratch_base": cfg["scratch_base"],
            "nonce": self.nonce, "synthetic_mock": bool(cfg.get("synthetic_mock", False)),
            "policy_label": self.policy.get("label"),
            "d_options": {"D1_threads": 1, "D3": "a+", "D4_required": True},
        }
        self._mark("attempt_create")
        self.key, self.attempt_sha = self.ledger.create_attempt(cfg["approval_sha"], cfg["slot"], body)
        self._mark("attempt_created")
        self._hook("after_attempt_record")
        status = "fail"
        try:
            self._launch_and_monitor()
            status = self._classify()
        except N2Stop as e:
            self._stop(e)
            status = "stopped"
            self._emergency_cleanup()
            if self.layout is not None and self.ctx is not None:
                self._post_exit_gates()  # guarded internally (best effort); final counters are evidence after any STOP (e.g. the OOM behind a live failcnt STOP)
        except InjectedFault as e:
            self.stops.append({"code": "injected_fault", "detail": e.label, "injected": e.label})
            status = "stopped"
            self._emergency_cleanup()
        except BaseException as e:  # noqa: BLE001 - unexpected harness error: still clean up and record a terminal receipt
            self.stops.append({"code": "harness_exception", "detail": type(e).__name__ + ": " + str(e)[:120],
                               "injected": None})
            status = "stopped"
            self._emergency_cleanup()
        return self._finalize(status)

    # ------------------------------------------------------------------ launch
    def _launch_and_monitor(self):
        cfg, fs, pol = self.cfg, self.fs, self.policy
        pv = policy_mod.value
        self.layout = cgmod.Layout(self.name, self.ctrl_paths, self.cg_root, cfg.get("use_v2", True))
        cgmod.create_all(self.layout, fs)
        self._mark("cgroups_created")
        self.limits = cgmod.set_limits(self.layout, pv(pol, "attempt_memory_limit_bytes"),
                                       pv(pol, "attempt_pids_max"), fs)
        self._hook("at_cgroups_ready")
        st_mnt = os.stat("/proc/self/ns/mnt")
        out_p = os.path.join(cfg["slot_root"], "stdout.log")
        err_p = os.path.join(cfg["slot_root"], "stderr.log")
        self.out_path, self.err_path = out_p, err_p
        home = os.path.join(cfg["slot_root"], "home")
        tmpd = os.path.join(cfg["slot_root"], "tmp")
        os.makedirs(home, exist_ok=True)
        os.makedirs(tmpd, exist_ok=True)
        env = build_env(home, tmpd)
        work = dict(cfg["work"])
        expected = {
            "nonce": self.nonce, "codes": stubsrc.S_CODES,
            "interp": {"path": cfg["interpreter"], "ident": self.binaries["interpreter"]["ident"],
                       "sha": self.binaries["interpreter"]["sha"]},
            "cgroups": self.layout.rel,
            "ctrl_nspid_fields": len(pidfd_mod.nspid_fields(fs, os.getpid())), "nspid_last": 1,
            "env": env, "stage": 0, "ctrl_mntns": [st_mnt.st_dev, st_mnt.st_ino],
            "setpriv": {"path": cfg["setpriv"], "ident": self.binaries["setpriv"]["ident"],
                        "sha": self.binaries["setpriv"]["sha"]},
            "cgroup_probe_path": self.layout.abs["memory"] + "/cgroup.procs",
            "payload": {"path": self.payload_path, "sha": self.payload_sha}, "work": work, "site_main": False,
        }
        if cfg.get("expected_patch"):
            expected.update(cfg["expected_patch"])
            self.events.append({"injected_expected_patch": sorted(cfg["expected_patch"])})
        spec = {"unshare": self.binaries["unshare"], "interpreter": self.binaries["interpreter"] | {"path": cfg["interpreter"]},
                "stub": stubsrc.STUB, "stub_actual": cfg.get("stub_actual", stubsrc.STUB), "expected": expected, "env": dict(env, **cfg.get("env_extra_actual", {})),
                "out_path": out_p, "err_path": err_p, "cgroups": self.layout.rel}
        self._mark("fork")
        if not cfg.get("no_subreaper"):
            native.set_subreaper()  # BEFORE fork (reviewer note 4); reaping via held pidfds only
        self.subreaper_at_fork = native.get_subreaper()
        self.gated = fork_gated(spec, plan=self.plan, allow=STDLIB_ALLOWLIST,
                                extra_allow=cfg.get("extra_allow_modules", ()), inject=cfg.get("inject"),
                                hooks=self.hooks)
        self._mark("forked_S0")
        direct = pidfd_mod.Member(self.gated.pid, self.gated.pidfd, self.gated.starttime,
                                  pidfd_mod.pidns_identity(self.gated.pid), "direct", time.monotonic_ns())
        self.direct = direct
        self.markers = tel_mod.MarkerReader(err_p, self.nonce)
        self.phases = tel_mod.PhaseRecorder(cfg.get("declared_phases", ()))
        self._hook("at_S0")
        # --- S1: assign to the job
        try:
            self.joined = cgmod.join_and_verify(self.layout, self.gated.pid, fs)
        except CgroupStop as e:
            self.r1 = self._r1_ambiguity(e)
            raise
        self._mark("assigned_S1")
        self.ctx = tel_mod.SampleCtx(fs, self.layout, direct, pol, self.mem_root, self.mem_rel,
                                     cfg["scratch_base"], plan=self.plan,
                                     baseline={"bash_usage_excluding_attempt_bytes": self.bash_baseline})
        # R-1: failure from here until release means the only member is the gated child
        try:
            self.tele = tel_mod.Telemetry(self.ctx, os.path.join(cfg["slot_root"], "telemetry.jsonl"))
            self.launch_sha = self._launch_record()
            self._hook("at_S1")
            k = int(pv(pol, "release_sample_count_k"))
            interval = pv(pol, "sample_interval_ms") / 1000.0
            for i in range(k):
                if i == 1:
                    self.plan.check("INJECTED_E_TELEMETRY_S1")
                self._check_external_term()
                self._assert_child_alive_pre_release()
                self.tele.sample()
                time.sleep(interval)
            self._assert_child_alive_pre_release()
            self.release_cold = self._cold_inputs("pre_release")
            self.release_elsewhere = self._elsewhere("pre_release")
            self._hook("before_release")
        except (N2Stop, OSError) as e:
            self._s1_cleanup()
            if isinstance(e, N2Stop):
                raise
            raise TelemetryStop("s1_failure", str(e), injected=getattr(e, "label", None))
        # --- S2: release
        try:
            self.gated.release()
        except OSError as e:  # EPIPE: the gated child is gone
            self.gated.gate_w = -1
            self._assert_child_alive_pre_release(extra=str(e))
            raise LaunchStop("gate_release_failed", str(e))
        self._mark("released_S2")
        self.t_release = time.monotonic()
        self._hook("after_release")
        self._handshake()
        self._hook("before_monitor")
        self._monitor()

    def _assert_child_alive_pre_release(self, extra=""):
        if self.ctx.alive(self.direct):
            return
        r = pidfd_mod.reap_if_exited(self.direct) or {}
        self.gated.reaped = True
        st = r.get("status") or {}
        self.pre_gate_exit = {"status": st, "name": {v: k for k, v in launch_mod.CHILD_CODES.items()}.get(st.get("code"))}
        self.gated.close_gate_without_release()
        raise LaunchStop("child_exited_before_release", "%r %s" % (self.pre_gate_exit, extra))

    def _r1_ambiguity(self, err):
        """R-1: decide S0 vs S1 from /proc/<pid>/cgroup, then SIGKILL through the S0 pidfd."""
        info = {"join_error": err.code, "state": "unknown"}
        if pidfd_mod.readable(self.gated.pidfd):
            info["state"] = "exited_before_assignment"
        try:
            if pidfd_mod.read_starttime(self.fs, self.gated.pid) == self.gated.starttime:
                cur = sysfs.cgroup_paths_of(self.fs.read("/proc/%d/cgroup" % self.gated.pid))
                first = self.layout.join_order[0]
                if info["state"] == "unknown":
                    info["state"] = "S1" if cur.get(first) == self.layout.rel[first] else "S0"
        except (OSError, ValueError):
            pass
        self._kill_gated_via_pidfd(info)
        st = info.get("killed_status") or {}
        if st.get("kind") == "exit":
            info["pre_gate_exit_name"] = {v: k for k, v in launch_mod.CHILD_CODES.items()}.get(st["code"])
            self.pre_gate_exit = {"status": st, "name": info["pre_gate_exit_name"]}
        return info

    def _kill_gated_via_pidfd(self, info=None):
        g = self.gated
        try:
            signal.pidfd_send_signal(g.pidfd, signal.SIGKILL)
            os.waitid(os.P_PIDFD, g.pidfd, os.WEXITED | os.WNOWAIT)
            _p, status, _ru = os.wait4(g.pid, 0)
            g.reaped = True
            if info is not None:
                info["killed_status"] = pidfd_mod.decode_status(status)
        except (OSError, ChildProcessError) as e:
            if info is not None:
                info["kill_error"] = str(e)
        g.close_gate_without_release()

    def _s1_cleanup(self):
        self.s1_cleanup_info = {}
        self._kill_gated_via_pidfd(self.s1_cleanup_info)

    def _launch_record(self):
        g = self.gated
        dirs = {}
        for h, p in self.layout.abs.items():
            st = os.stat(p)
            dirs[h] = {"path": p, "dev": st.st_dev, "ino": st.st_ino}
        body = {"pid": g.pid, "starttime": g.starttime, "pidfd_open": True, "cgroup_dirs": dirs,
                "gate_state": "closed", "pre_release_snapshot": g.snapshot,
                "subreaper_at_fork": self.subreaper_at_fork, "controller_pid": os.getpid(),
                "limits": self.limits, "ns_init_pid": None}
        return self.ledger.create_launch(self.key, body)

    # ------------------------------------------------------------------ handshake + monitor
    def _handle_markers(self):
        sample = self.tele.samples[-1] if self.tele.samples else None
        for ev in self.markers.poll():
            self.phases.on_marker(ev, sample)
            if ev["event"] == "error":
                code = tel_mod.classify_error_event(ev["kv"].get("kind", "unknown"))
                raise TelemetryStop("child_reported_" + code, ev["kv"].get("kind", ""),
                                    injected=ev["kv"].get("kind") if ev["kv"].get("kind", "").startswith("INJECTED_") else None)

    def _breach_check(self):
        s = self.tele.samples[-1]
        elapsed = time.monotonic() - self.t_release
        br = tel_mod.evaluate_breaches(self.ctx, s, elapsed,
                                       self.release_elsewhere["resident_bytes_elsewhere"])
        # latches (section D): out-of-band files written by the child
        for name in ("sentinel_latch", "logging_latch"):
            p = os.path.join(self.cfg["slot_root"], name)
            if os.path.exists(p):
                br.append(("cache_latch", name))
        return br

    def _handshake(self):
        pol = self.policy
        pv = policy_mod.value
        interval = pv(pol, "sample_interval_ms") / 1000.0
        deadline = time.monotonic() + pv(pol, "release_handshake_timeout_s")
        ready = False
        while True:
            self._check_external_term()
            self.tele.sample()
            self._handle_markers()
            if any(e["event"] == "ready" for e in self.markers.events):
                ready = True
                break
            if self.ctx.ns_init is None and not self.ctx.alive(self.direct):
                break
            if not self.ctx.alive(self.direct):
                break
            br = self._breach_check()
            if br:
                raise PolicyStop("breach_" + br[0][0], repr(br[0][1]))
            if time.monotonic() > deadline:
                raise TelemetryStop("release_handshake_timeout", "")
            time.sleep(interval)
        if not ready:
            self._handle_early_exit()
        self._mark("ready")

    def _handle_early_exit(self):
        """The direct child exited before 'ready': interpret the fixed code."""
        for _ in range(100):
            if not self.ctx.alive(self.direct):
                break
            time.sleep(0.01)
        self._reap_all_after_exit()
        st = self.exit_info.get("direct", {}).get("status") or {}
        code = st.get("code")
        refuse = [e for e in self.markers.events if e["event"] == "refuse"]
        name = None
        if refuse:
            name = refuse[0]["kv"].get("name")
        elif code in launch_mod.CHILD_CODES.values():
            name = {v: k for k, v in launch_mod.CHILD_CODES.items()}[code]
        raise LaunchStop("child_refused_before_ready", "exit=%r refusal=%r" % (st, name))

    def _monitor(self):
        pol = self.policy
        pv = policy_mod.value
        interval = pv(pol, "sample_interval_ms") / 1000.0
        while True:
            if self.term_signals:
                self.termination = term_mod.terminate(self.ctx, self.layout, self.fs, pol, self.plan,
                                                      "external:%r" % self.term_signals, discover=self.tele_discover)
                self.exit_info["terminated_by_controller"] = True
                self._check_external_term()
            if not self.ctx.alive(self.direct):
                break  # already exited: the post-exit evaluation (final sample + final counters) decides
            self.tele.sample()
            self._handle_markers()
            br = self._breach_check()
            if br:
                self.breaches = br
                self.termination = term_mod.terminate(self.ctx, self.layout, self.fs, pol, self.plan,
                                                      "breach:" + br[0][0], discover=self.tele_discover)
                self.exit_info["terminated_by_controller"] = True
                raise PolicyStop("breach_" + br[0][0], repr(br[0][1]))
            if not self.ctx.alive(self.direct):
                break
            time.sleep(interval)
        # normal / child-initiated end: final sample, then reap
        self._hook("before_final_sample")
        try:
            self.tele.sample()
        except (N2Stop, OSError) as e:
            code = e.code if isinstance(e, N2Stop) else "final_sample_os_error"
            self.stops.append({"code": "final_sample_" + code, "detail": str(e)[:160], "injected": _inj(e)})
            self.final_sample_failed = True
        self._handle_markers()
        self._reap_all_after_exit()

    def tele_discover(self):
        self.tele._discover(cgmod.members(self.layout, self.fs))

    def _reap_all_after_exit(self):
        # wait for the whole namespace to drain (ns-init exit is reported only after teardown)
        pidfd_mod.wait_readable([m.fd for m in self.ctx.members.values() if not m.closed], 5.0)
        res = {}
        term_mod._finish(self.ctx, self.layout, self.fs, res, lambda *a, **k: None)
        self.exit_info.update({"direct": res.get("reap", {}).get("direct"),
                               "ns_init": res.get("reap", {}).get("ns_init"),
                               "unowned": res.get("unowned"), "zombies": res.get("zombies"),
                               "pids_current_after": res.get("pids_current")})
        if res.get("unowned"):
            raise ContainmentStop("unowned_members_after_exit", repr(res["unowned"]))

    def _post_exit_gates(self):
        """Apply the gates that stay meaningful after exit to the FINAL cgroup counters (a last-interval
        breach is not 'success'). Any exception, of any type, is recorded as a fired gate: this runs inside
        STOP handlers and must not let an exception escape past _finalize."""
        try:
            return self._post_exit_gates_inner()
        except Exception as e:  # noqa: BLE001 - structural guarantee: the terminal receipt must still be written
            self._record_final_counters_failed(e)
            return True

    def _post_exit_gates_inner(self):
        try:
            m = sysfs.read_memory_fields(self.fs, self.layout.abs["memory"])
            cn = cgmod.counters(self.layout, self.fs)
        except (N2Stop, OSError) as e:
            self._record_final_counters_failed(e)
            return True
        elsewhere = (getattr(self, "release_elsewhere", None) or {}).get("resident_bytes_elsewhere", 0)
        try:
            disk = sysfs.disk_free_bytes(self.cfg["scratch_base"])
            base = (self.ctx.baseline if self.ctx else {}).get("bash_usage_excluding_attempt_bytes")
            bash = None if base is None else sysfs.bash_usage_excluding_attempt(
                self.fs, self.mem_root, self.mem_rel, self.layout.name) - base
        except (N2Stop, OSError) as e:
            self._record_final_counters_failed(e)
            return True
        try:
            br = tel_mod.evaluate_post_exit(self.policy, m, cn, elsewhere, disk, bash)
        except (N2Stop, OSError, ValueError, KeyError) as e:  # e.g. a malformed pids.events: fail closed, still finalize
            self._record_final_counters_failed(e)
            return True
        have = {s["code"] for s in self.stops}
        for code, detail in br:
            if "breach_" + code not in have:
                self.stops.append({"code": "breach_" + code, "detail": repr(detail), "injected": None})
        return bool(br)

    def _record_final_counters_failed(self, e):
        if not any(s["code"] == "final_counters_failed" for s in self.stops):  # once per attempt
            self.stops.append({"code": "final_counters_failed", "detail": str(e)[:100], "injected": _inj(e)})

    def _exiting_marker(self):
        ev = [e for e in self.markers.events if e["event"] == "exiting"] if self.markers else []
        return ev[-1] if ev else None

    def _classify(self):
        d = self.exit_info.get("direct") or {}
        st = d.get("status") or {}
        gated = self._post_exit_gates()  # final counters are evaluated even after a live STOP (evidence, deduplicated)
        if self.exit_info.get("terminated_by_controller"):
            return "stopped"
        res = self._classify_exit(st)
        if gated or getattr(self, "final_sample_failed", False):
            return "stopped"  # any breach gate that fired is a STOP, whatever the exit status was
        return res

    def _classify_exit(self, st):
        if st.get("kind") == "exit" and st.get("code") == 0:
            if self.cfg.get("require_receipt") or not self.cfg.get("synthetic_mock", False):
                rp = os.path.join(self.cfg["slot_root"], "completion_receipt.json")
                if not verify_completion_receipt(rp, self.nonce):
                    self.stops.append({"code": "exit_without_receipt", "detail": "missing or invalid: " + rp, "injected": None})
                    return "fail"
            for name in ("sentinel_latch", "logging_latch"):
                if os.path.exists(os.path.join(self.cfg["slot_root"], name)):
                    self.stops.append({"code": "cache_latch_with_exit_zero", "detail": name, "injected": None})
                    return "fail"
            return "success"
        if st.get("kind") == "signal" or st.get("code") not in (None, 0):
            # unshare maps "ns-init killed by SIGKILL" to its own exit status 1 (measured), so a nonzero status
            # is only trusted as a real exit when ns-init framed it with a nonce-carrying "exiting code=N" marker.
            m = sysfs.read_memory_fields(self.fs, self.layout.abs["memory"])
            mk = self._exiting_marker()
            if m["oom_kill"] > 0:
                self.stops.append({"code": "oom_kill_detected", "detail": "oom_kill=%d status=%r" % (m["oom_kill"], st),
                                   "injected": None})
            elif mk is None or str(st.get("code")) != mk["kv"].get("code"):
                self.stops.append({"code": "ns_init_killed_or_unknown_exit",
                                   "detail": "status=%r marker=%r" % (st, mk and mk["kv"]), "injected": None})
            else:
                self.stops.append({"code": "child_nonzero_exit", "detail": repr(st), "injected": None})
        return "fail"

    # ------------------------------------------------------------------ cleanup / finalize
    def _emergency_cleanup(self):
        """Stop after an error: terminate whatever we own, only via verified fds."""
        try:
            if self.gated is not None and not self.gated.released and not self.gated.reaped:
                self._s1_cleanup()
            elif self.ctx is not None and self.termination is None:
                self.termination = term_mod.terminate(self.ctx, self.layout, self.fs, self.policy, self.plan,
                                                      "stop:" + (self.stops[-1]["code"] if self.stops else "?"),
                                                      discover=self.tele_discover)
        except Exception as e:  # noqa: BLE001 - cleanup must never prevent the terminal receipt
            self.stops.append({"code": "cleanup_exception", "detail": type(e).__name__ + str(e)[:100], "injected": None})

    def _finalize(self, status):
        try:
            self._hook("before_finalize")
        except Exception as e:  # noqa: BLE001 - nothing may prevent the terminal receipt
            self.stops.append({"code": "finalize_hook_exception", "detail": type(e).__name__ + ": " + str(e)[:100], "injected": None})
            status = "stopped"
        cfg, fs = self.cfg, self.fs
        info = {}
        # post-exit counters BEFORE rmdir; memsw cross-check only after exit
        try:
            if self.layout is not None:
                m = sysfs.read_memory_fields(fs, self.layout.abs["memory"])
                msw = sysfs.read_memsw(fs, self.layout.abs["memory"])
                info["final_memory"] = m
                info["final_memsw"] = msw
                exited = self.direct is not None and not self.ctx.alive(self.direct) if self.ctx else (
                    self.gated is None or self.gated.reaped)
                if exited:
                    delta, over = sysfs.memsw_check(m["cg_charged_peak_bytes"], msw["memsw_max_usage_bytes"],
                                                    policy_mod.value(self.policy, "memsw_tolerance_bytes"), True)
                    info["memsw_delta_bytes"] = delta
                    info["memsw_over_tolerance"] = over
                    if memsw_decision(over, self.policy["fields"]["memsw_check_action"]["value"]) == "stop":
                        self.stops.append({"code": "memsw_mismatch", "detail": str(delta), "injected": None})
                        status = "stopped"
                info["final_counters"] = cgmod.counters(self.layout, fs)
                if self.markers is not None and not self._handle_markers_safely():
                    status = "stopped"
        except (N2Stop, OSError, ValueError, KeyError) as e:
            self._record_final_counters_failed(e)
            status = "stopped"  # a failed final read fails closed, whatever _classify concluded
        except Exception as e:  # noqa: BLE001 - any unexpected type: record, fail closed, still write the terminal receipt
            self.stops.append({"code": "finalize_counters_exception", "detail": type(e).__name__ + ": " + str(e)[:100], "injected": None})
            status = "stopped"
        # remove the owned cgroups; a failure is a STOP and leaves the evidence in place
        removed = None
        if self.layout is not None:
            try:
                cgmod.remove(self.layout, fs)
                removed = True if getattr(self.layout, "created", None) else None
            except (CgroupStop, OSError) as e:
                removed = False
                self.stops.append({"code": "rmdir_failed", "detail": str(e)[:160], "injected": _inj(e)})
                status = "stopped"
            except Exception as e:  # noqa: BLE001 - see above
                removed = False
                self.stops.append({"code": "finalize_remove_exception", "detail": type(e).__name__ + ": " + str(e)[:100], "injected": None})
                status = "stopped"
            info["cgroups_removed"] = removed
            try:
                info["memcg_dying_residual"] = (cgmod.memcg_counts() or 0) - cgmod.count_memory_dirs(self.cg_root)
                info["memcg_dying_residual_before"] = getattr(self, "memcg_residual_before", None)
            except Exception:  # noqa: BLE001 - informational only
                info["memcg_dying_residual"] = None
        if self.tele is not None:
            try:
                self.tele.close()
            except OSError as e:  # buffered telemetry that cannot be flushed (e.g. ENOSPC) is a lost record: fail closed
                self.stops.append({"code": "telemetry_close_failed", "detail": str(e)[:100], "injected": _inj(e)})
                status = "stopped"
            except Exception as e:  # noqa: BLE001 - see above
                self.stops.append({"code": "telemetry_close_failed", "detail": type(e).__name__ + ": " + str(e)[:100], "injected": None})
                status = "stopped"
        for m in (self.ctx.members.values() if self.ctx else ([self.direct] if getattr(self, "direct", None) else [])):
            m.close()
        logs = {}
        for name in ("stdout.log", "stderr.log", "telemetry.jsonl"):
            p = os.path.join(cfg["slot_root"], name)
            if os.path.exists(p):
                try:
                    logs[name] = {"sha256": file_sha256(p), "bytes": os.path.getsize(p)}
                except Exception as e:  # noqa: BLE001 - any failure to hash a log fails closed
                    self.stops.append({"code": "log_hash_failed", "detail": "%s: %s" % (name, e), "injected": _inj(e)})
                    status = "stopped"
        if self.stops and status == "success":
            status = "stopped"  # invariant: a recorded stop never ends as success
        try:
            body = self._terminal_body(status, info, logs)
        except Exception as e:  # noqa: BLE001 - never lose the terminal receipt to a body-building bug
            self.stops.append({"code": "finalize_body_exception", "detail": type(e).__name__ + ": " + str(e)[:100], "injected": None})
            status = "stopped"
            body = self._minimal_body(status)
        try:
            self.terminal_sha = self.ledger.create_terminal(self.key, body)
        except N2Stop as e:
            rec = {"code": e.code, "detail": e.detail, "injected": e.injected}
            self.terminal_sha = None
            status = "stopped"
            if self.stops is not body["stops"]:  # a minimal body holds a copy: keep self.stops authoritative
                self.stops.append(rec)
            body["stops"].append(rec)
            if e.code == "serialize_failed" and not body.get("minimal"):  # retry once with a minimal body
                body = self._minimal_body("stopped")
                fb = {"code": "terminal_body_minimal_fallback", "detail": e.detail, "injected": None}
                self.stops.append(fb)
                body["stops"].append(fb)
                try:
                    self.terminal_sha = self.ledger.create_terminal(self.key, body)
                except N2Stop as e2:
                    rec2 = {"code": e2.code, "detail": e2.detail, "injected": e2.injected}
                    self.stops.append(rec2)
                    body["stops"].append(rec2)
        body["status"] = status
        return body

    def _minimal_body(self, status):
        return {"status": status, "stops": [dict(s, detail=str(s.get("detail"))[:200]) for s in self.stops],
                "synthetic_mock": bool(self.cfg.get("synthetic_mock", False)), "minimal": True}

    def _terminal_body(self, status, info, logs):
        cfg = self.cfg
        body = {
            "status": status, "stops": self.stops, "synthetic_mock": bool(cfg.get("synthetic_mock", False)),
            "policy_label": self.policy.get("label"),
            "exit": self.exit_info, "termination": self.termination, "post": info, "logs": logs,
            "logs_drained": True, "markers": {"trusted": len(self.markers.events) if self.markers else 0,
                                              "untrusted": list(self.markers.untrusted) if self.markers else []},
            "phases": {"boundaries": self.phases.boundaries if self.phases else [],
                       "undeclared": self.phases.undeclared if self.phases else []},
            "peak": self._peak_summary(info), "injected_hits": list(self.plan.hits),
            "r1": getattr(self, "r1", None), "s1_cleanup": getattr(self, "s1_cleanup_info", None),
            "pre_gate_exit": getattr(self, "pre_gate_exit", None),
            "external_signals": list(getattr(self, "term_signals", [])),
            "ns_init_pid": self.ctx.ns_init.pid if (self.ctx and self.ctx.ns_init) else None,
            "launch_avail": getattr(self, "launch_avail", None),
            "cold_report": {"launch": getattr(self, "cold_report", None), "pre_release": getattr(self, "release_cold", None)},
            "elsewhere": {"launch": _slim(getattr(self, "elsewhere", None)), "pre_release": _slim(getattr(self, "release_elsewhere", None))},
            "timeline_ns": self.timeline,
        }
        return body

    def _handle_markers_safely(self):
        try:
            new = self.markers.poll()
            ok = True
            for ev in new:  # error events that arrive after the monitor loop are not discarded
                if ev["event"] == "error":
                    kind = ev["kv"].get("kind", "unknown")
                    self.stops.append({"code": "child_reported_" + tel_mod.classify_error_event(kind), "detail": kind,
                                       "injected": kind if kind.startswith("INJECTED_") else None})
                    ok = False
            return ok
        except OSError as e:
            self.stops.append({"code": "final_marker_read_failed", "detail": str(e)[:100], "injected": _inj(e)})
            return False

    def _peak_summary(self, info):
        fm = info.get("final_memory")
        if not fm:
            return None
        elsewhere = (getattr(self, "release_elsewhere", None) or {}).get("resident_bytes_elsewhere")
        return {"cg_charged_peak_bytes": fm["cg_charged_peak_bytes"],
                "resident_bytes_elsewhere": elsewhere,
                "conservative_bytes": None if elsewhere is None else fm["cg_charged_peak_bytes"] + elsewhere,
                "label": "peak of pages charged to the attempt; NOT an upper bound; conservative adds additive uncertainty"}


def _inj(exc):
    return getattr(exc, "label", None) or getattr(exc, "injected", None)


def verify_completion_receipt(path, nonce):
    """A completion receipt counts only if it is a regular file whose JSON carries this attempt's nonce.
    Finding a file never grants authority (spec A): leftovers and forgeries without the nonce are rejected."""
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode):
            return False
        with open(path, "rb") as f:
            doc = json.loads(f.read().decode("ascii"))
        return doc.get("ok") is True and doc.get("nonce") == nonce
    except (OSError, ValueError, UnicodeError, AttributeError):
        return False


def memsw_decision(over_tolerance, action):
    """memsw_check_action: 'record' only records a mismatch; 'stop' turns it into a STOP."""
    if not over_tolerance:
        return "ok"
    return "stop" if action == "stop" else "record"


def _slim(rep):
    if not rep:
        return rep
    return {k: v for k, v in rep.items() if k != "files"} | {"n_files": len(rep.get("files", []))}


def hashlib_sha(text):
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def cleanup_test_orphan(layout, fs=None, wait_s=5.0):
    """TEST TEARDOWN ONLY: remove an orphaned attempt cgroup set that a killed test controller left.
    Refuses to touch non-empty cgroups; never signals anything."""
    fs = fs or RealFs()
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline and not cgmod.is_empty(layout, fs):
        time.sleep(0.02)
    if not cgmod.is_empty(layout, fs):
        return False
    # an orphan left by a killed test controller: adopt the directories that exist right now as "created"
    layout.created = {p: (os.stat(p).st_dev, os.stat(p).st_ino) for p in layout.abs.values() if os.path.isdir(p)}
    cgmod.remove(layout, fs)
    return True


