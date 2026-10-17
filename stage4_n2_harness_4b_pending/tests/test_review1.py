"""Review round 1 fixes: post-exit gates, receipt/exit-marker rules, swap gate, identity-checked rmdir,
EROFS/fstype stub rules, PF_EXITING absorption, exact NSpid discovery."""
import os
import time
import unittest

from n2h import cg as cgmod
from n2h import controller, pidfd, sysfs
from n2h import ledger as L
from n2h import policy as P
from n2h import telemetry as tel
from n2h.controller import Attempt
from n2h.errors import CgroupStop, ContainmentStop, FaultPlan, LedgerStop, TelemetryStop
from n2h.fsio import FaultFs, RealFs
from tests import helpers as H
from tests import stubrun
from tests.mockcg import MockWorld, w

MIB = 1 << 20


def small_limit(**kw):
    d = {"attempt_memory_limit_bytes": 48 * MIB, "running_floor_attempt_headroom_bytes": 0, "conservative_peak_max_bytes": 1 << 40,
         "sample_interval_ms": 400, "per_slot_elapsed_cap_s": 40, "max_sample_gap_ms": 10000}
    d.update(kw)
    return d


class PostExit(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:30])

    def tearDown(self):
        self.scr.cleanup()
        self.assertEqual(H.no_n2_cgroups(), [])

    def post_exit_run(self, work, policy_over):
        """The child exits (seconds 0) during a 2 s pause BEFORE the first monitor sample, so only the post-exit path can see it."""
        wd = H.make_world(self.scr, work=work, policy_over=policy_over)
        if work.get("mode") == "filecache":
            wd["work"]["path"] = os.path.join(wd["slot_root"], "fill.bin")
        res, p = H.run_driver(self.scr, H.spec_path(wd, {"sleep_before_monitor": 1.5}))
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertFalse(t["exit"].get("terminated_by_controller"), "no live breach/termination happened")
        self.assertIsNone(t["termination"], "the controller never had to terminate anything")
        return t

    def test_M09_post_exit_failcnt_last_interval(self):
        t = self.post_exit_run({"mode": "filecache", "mib": 160, "path": "", "seconds": 0}, small_limit())
        self.assertEqual(t["stops"][0]["code"], "breach_failcnt_nonzero")
        self.assertEqual(t["post"]["final_memory"]["oom_kill"], 0)

    def test_M09_post_exit_conservative_peak_last_interval(self):
        t = self.post_exit_run({"mode": "alloc", "mib": 40, "seconds": 0},
                               small_limit(attempt_memory_limit_bytes=128 * MIB, conservative_peak_max_bytes=40 * MIB))
        self.assertEqual(t["stops"][0]["code"], "breach_conservative_peak")

    def test_M09_post_exit_disk_floor_persists(self):
        free = os.statvfs(self.scr.root)
        floor = free.f_bavail * free.f_frsize - 100 * MIB  # live samples are above it; 160 MiB of output takes it under
        wd = H.make_world(self.scr, work={"mode": "filecache", "mib": 160, "path": "", "seconds": 0},
                          policy_over=small_limit(attempt_memory_limit_bytes=512 * MIB, running_floor_disk_free_bytes=floor,
                                                  launch_min_disk_free_bytes=MIB))
        wd["work"]["path"] = os.path.join(wd["slot_root"], "fill.bin")
        res, p = H.run_driver(self.scr, H.spec_path(wd, {"sleep_before_monitor": 1.5}))
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertFalse(t["exit"].get("terminated_by_controller"))
        self.assertIsNone(t["termination"])
        self.assertIn("breach_floor_disk_free", [s["code"] for s in t["stops"]])
        self.assertEqual(os.path.getsize(os.path.join(wd["slot_root"], "fill.bin")), 160 * MIB)  # removed by scratch cleanup

    def test_M09_breach_then_nonzero_exit_is_stopped(self):
        mw = MockWorld(self.scr)
        try:
            mw.set_mem(usage=10 << 20, peak=60 << 20, limit=192 << 20, failcnt=3)
            a = self.bare_attempt(mw, code=1)
            self.assertEqual(a._classify(), "stopped", "a fired gate wins over the exit-status classification")
            codes = [s["code"] for s in a.stops]
            self.assertIn("breach_failcnt_nonzero", codes)
        finally:
            mw.close()

    def test_M09_final_sample_failure_is_recorded_and_stops(self):
        wd = H.make_world(self.scr, work={"mode": "sleep", "seconds": 0})
        res, p = H.run_driver(self.scr, H.spec_path(wd, {"sleep_before_monitor": 1.0, "fault_final_sample": "INJECTED_E_READ"}))
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "final_sample_sample_read_failed")
        self.assertEqual(t["stops"][0]["injected"], "INJECTED_E_READ")
        self.assertIs(t["post"]["cgroups_removed"], True)

    def test_M09_failed_final_reads_fail_closed(self):
        for name, extra in (("memsw", {"fs_faults": {"read|memory.memsw.max_usage_in_bytes": "INJECTED_E_READ"}}),
                            ("markers", {"marker_read_fault": True})):
            scr = H.Scratch("m09ff")
            try:
                wd = H.make_world(scr, work={"mode": "sleep", "seconds": 0.2})
                res, p = H.run_driver(scr, H.spec_path(wd, extra))
                t = res["terminal"]
                codes = [s["code"] for s in t["stops"]]
                self.assertEqual(t["status"], "stopped", (name, t["stops"], p.stderr))
                self.assertTrue({"final_counters_failed", "final_marker_read_failed"} & set(codes), codes)
                key = L.attempt_key(wd["approval_sha"], "H0")
                self.assertEqual(L.Ledger(wd["control_root"]).state(key), "terminal", "durable terminal receipt")
                self.assertEqual(L.Ledger(wd["control_root"]).read("terminal", key)["status"], "stopped")
                self.assertEqual(H.no_n2_cgroups(), [])
            finally:
                scr.cleanup()

    def test_M09_malformed_counter_does_not_escape_gates(self):
        mw = MockWorld(self.scr)
        try:
            w(mw.layout.abs["pids"] + "/pids.events", "max not-a-number\n")
            a = self.bare_attempt(mw)
            self.assertTrue(a._post_exit_gates(), "fails closed instead of raising")
            self.assertEqual(a.stops[0]["code"], "final_counters_failed")
            b = self.bare_attempt(mw)
            self.assertEqual(b._classify(), "stopped")
            self.assertEqual([s["code"] for s in b.stops].count("final_counters_failed"), 1)
        finally:
            mw.close()

    def run_with(self, extra, work=None):
        wd = H.make_world(self.scr, work=work or {"mode": "sleep", "seconds": 0.2})
        res, p = H.run_driver(self.scr, H.spec_path(wd, extra))
        self.assertIn("terminal", res, (res, p.stderr))
        key = L.attempt_key(wd["approval_sha"], "H0")
        led = L.Ledger(wd["control_root"])
        self.assertEqual(led.state(key), "terminal", "durable terminal receipt")
        return res["terminal"], led.read("terminal", key), wd

    def test_M09_telemetry_close_failure_fails_closed(self):
        t, rec, wd = self.run_with({"telemetry_dev_full": True, "sleep_before_monitor": 1.0})
        codes = [s["code"] for s in t["stops"]]
        self.assertEqual(t["status"], "stopped", t["stops"])
        self.assertEqual(rec["status"], "stopped")
        self.assertIn("telemetry_close_failed", codes)
        self.assertIs(t["post"]["cgroups_removed"], True)
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M09_unexpected_exception_types_still_write_terminal(self):
        t, rec, wd = self.run_with({"finalize_boom": True})
        self.assertEqual(rec["status"], "stopped")
        self.assertIn("finalize_counters_exception", [s["code"] for s in rec["stops"]])
        self.assertIs(t["post"]["cgroups_removed"], True)
        scr2 = H.Scratch("m09ue")
        try:
            wd2 = H.make_world(scr2, work={"mode": "sleep", "seconds": 0.2})
            res, p = H.run_driver(scr2, H.spec_path(wd2, {"finalize_unserializable": True}))
            t2 = res["terminal"]
            led = L.Ledger(wd2["control_root"])
            key = L.attempt_key(wd2["approval_sha"], "H0")
            self.assertEqual(led.state(key), "terminal")
            rec2 = led.read("terminal", key)
            self.assertEqual(rec2["status"], "stopped")
            self.assertTrue(rec2.get("minimal"), "serialisation failed: minimal fallback body written")
            codes = [s["code"] for s in rec2["stops"]]
            self.assertIn("serialize_failed", codes)
            self.assertIn("terminal_body_minimal_fallback", codes)
            self.assertEqual(t2["status"], "stopped")
        finally:
            scr2.cleanup()

    def test_M02_serialize_failure_leaves_no_file_and_no_fd(self):
        wd = H.make_world(self.scr)
        led = L.Ledger(wd["control_root"])
        key = L.attempt_key(wd["approval_sha"], "H0")
        led.create_attempt(wd["approval_sha"], "H0", {"x": 1})
        before = len(os.listdir("/proc/self/fd"))
        with self.assertRaises(LedgerStop) as cm:
            led.create_terminal(key, {"bad": {1, 2}})
        self.assertEqual(cm.exception.code, "serialize_failed")
        self.assertFalse(os.path.exists(os.path.join(wd["control_root"], "terminal", key + ".json")))
        self.assertEqual(len(os.listdir("/proc/self/fd")), before, "no leaked descriptor")
        self.assertEqual(led.state(key), "consumed")
        led.create_terminal(key, {"status": "stopped"})  # the slot can still be closed properly

    def test_M13_terminate_continues_escalating_on_read_errors(self):
        from n2h import term
        from n2h.errors import FaultPlan
        mw = MockWorld(self.scr)
        try:
            pol = P.synthetic_test_policy(graceful_wait_s=1, unshare_reap_wait_s=1, cgroup_kill_wait_s=1)
            stubborn = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('up', flush=True)\ntime.sleep(60)\n"
            a, b = mw.spawn(stubborn), mw.spawn(stubborn)
            a.stdout.readline()
            b.stdout.readline()
            nsi = pidfd.open_verified(a.pid, mw.layout, mw.fs, "ns_init", cg_check=False)
            direct = pidfd.open_verified(b.pid, mw.layout, mw.fs, "direct", cg_check=False)
            mw.set_listing([])
            mw.fs.raise_on["pids.current"] = OSError(5, "INJECTED_E_READ")
            ctx = mw.telemetry(direct, ns_init=nsi).ctx
            res = term.terminate(ctx, mw.layout, mw.fs, pol, FaultPlan(), "mock")
            names = [x["step"] for x in res["steps"]]
            self.assertIn("sigkill_ns_init", names, "escalation was not skipped by the read error")
            self.assertTrue(res["read_errors"])
            self.assertTrue(pidfd.readable(nsi.fd) and pidfd.readable(direct.fd))
        finally:
            mw.close()

    def test_M12_rollback_failure_is_recorded(self):
        fs = FaultFs(RealFs(), {})
        lay = cgmod.Layout("n2-rbfault-" + os.urandom(3).hex(), cgmod.controller_cgroup_paths(RealFs()))
        fs.armed[("stat", lay.abs["memory"])] = "INJECTED_E_STAT"
        fs.armed[("rmdir", lay.abs["v2"])] = "INJECTED_E_RMDIR"
        try:
            with self.assertRaises(CgroupStop) as cm:
                cgmod.create_all(lay, fs)
            self.assertEqual(cm.exception.code, "cgroup_stat_failed")
            self.assertIn("rollback_failed", cm.exception.detail)
            self.assertIn(lay.abs["v2"], cm.exception.detail)
            self.assertTrue(lay.rollback_errors)
        finally:
            for h in reversed(lay.join_order):
                if os.path.isdir(lay.abs[h]):
                    os.rmdir(lay.abs[h])

    def test_M09_late_marker_error_events_are_kept(self):
        mw = MockWorld(self.scr)
        try:
            f = self.scr.path("late.log")
            with open(f, "w") as fh:
                fh.write("N2M n1 100 error kind=INJECTED_E_ENOSPC\n")
            a = self.bare_attempt(mw)
            a.markers = tel.MarkerReader(f, "n1")
            self.assertFalse(a._handle_markers_safely())
            self.assertEqual(a.stops[0]["code"], "child_reported_disk_exhausted")
            self.assertEqual(a.stops[0]["injected"], "INJECTED_E_ENOSPC")
        finally:
            mw.close()

    def test_M09_post_exit_unexpected_exception_on_stop_path(self):
        # evaluate_post_exit raises TypeError while the STOP handler runs it: the terminal receipt must still be written
        wd = H.make_world(self.scr, work={"mode": "sleep", "seconds": 60}, policy_over={"per_slot_elapsed_cap_s": 1})
        res, p = H.run_driver(self.scr, H.spec_path(wd, {"post_exit_typeerror": True}))
        self.assertIn("terminal", res, (res, p.stderr))
        t = res["terminal"]
        codes = [s["code"] for s in t["stops"]]
        self.assertEqual(t["status"], "stopped", t["stops"])
        self.assertIn("breach_elapsed_cap", codes)
        self.assertIn("final_counters_failed", codes)
        key = L.attempt_key(wd["approval_sha"], "H0")
        self.assertEqual(L.Ledger(wd["control_root"]).state(key), "terminal")
        self.assertIs(t["post"]["cgroups_removed"], True)
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M02_close_failure_does_not_escape_as_ebadf(self):
        wd = H.make_world(self.scr)
        led = L.Ledger(wd["control_root"])
        key = L.attempt_key(wd["approval_sha"], "H0")
        led.create_attempt(wd["approval_sha"], "H0", {"x": 1})
        from n2h import ledger as ledmod
        real_close, state = os.close, {"n": 0, "fds": []}

        def flaky(fd):
            state["n"] += 1
            state["fds"].append(fd)
            if state["n"] == 1:
                real_close(fd)  # really closed, but report failure once
                raise OSError(5, "INJECTED_E_CLOSE")
            return real_close(fd)
        ledmod.os.close = flaky
        try:
            with self.assertRaises(LedgerStop) as cm:
                led.create_terminal(key, {"status": "stopped"})
        finally:
            ledmod.os.close = real_close
        self.assertEqual(cm.exception.code, "persist_failed", "a failing close is a LedgerStop, never EBADF")
        self.assertEqual(state["n"], 2, "file close (failed once) + the DISPATCH_BLOCKED marker; no retry of the failed close")

    def test_M09_fallback_stop_lands_in_self_stops(self):
        class Led:
            def __init__(self):
                self.calls = 0

            def create_terminal(self, key, body):
                self.calls += 1
                raise LedgerStop("serialize_failed", "injected for the test")
        a = object.__new__(Attempt)
        a.cfg, a.policy, a.stops, a.ledger, a.key = {"slot_root": self.scr.root, "synthetic_mock": True}, P.synthetic_test_policy(), [], Led(), "k"
        a.layout = a.tele = a.ctx = a.markers = a.phases = None
        a.exit_info, a.termination, a.timeline, a.plan = {}, None, {}, FaultPlan()
        a.cg_root, a.fs = "/sys/fs/cgroup", RealFs()
        body = a._finalize("success")
        codes = [s["code"] for s in a.stops]
        self.assertEqual(codes.count("serialize_failed"), 2, "first failure kept in self.stops, then the retry's")
        self.assertIn("terminal_body_minimal_fallback", codes)
        self.assertEqual(body["status"], "stopped")
        self.assertEqual(a.ledger.calls, 2)

    def test_M09_finalize_hooks_compose(self):
        t, rec, wd = self.run_with({"marker_read_fault": True, "finalize_unserializable": True})
        codes = [s["code"] for s in rec["stops"]]
        self.assertIn("final_marker_read_failed", codes, "first hook took effect")
        self.assertIn("serialize_failed", codes, "second hook took effect")
        self.assertEqual(rec["status"], "stopped")

    def test_M09_synthetic_mock_default_is_false(self):
        mw = MockWorld(self.scr)
        try:
            a = self.bare_attempt(mw)
            del a.cfg["synthetic_mock"]  # key missing: must behave as a real slot (receipt required)
            self.assertEqual(a._classify(), "fail")
            self.assertEqual(a.stops[0]["code"], "exit_without_receipt")
        finally:
            mw.close()
        src = open(os.path.join(H.HARNESS, "n2h", "controller.py")).read()
        self.assertNotIn('"synthetic_mock", True', src)

    def bare_attempt(self, mw, synthetic_mock=True, require=False, code=0):
        a = object.__new__(Attempt)
        a.fs, a.layout, a.stops, a.policy = mw.fs, mw.layout, [], mw.policy
        a.markers, a.ctx, a.nonce = None, None, "n"
        a.cfg = {"synthetic_mock": synthetic_mock, "require_receipt": require, "slot_root": self.scr.root,
                 "scratch_base": self.scr.root}
        a.exit_info = {"direct": {"status": {"kind": "exit", "code": code, "raw": code}}}
        return a

    def test_M09_post_exit_gates_classify_unit(self):
        mw = MockWorld(self.scr)
        try:
            a = self.bare_attempt(mw)
            self.assertEqual(a._classify(), "success", "control: clean final counters")
            mw.set_mem(usage=10 << 20, peak=60 << 20, limit=192 << 20, failcnt=3)
            a = self.bare_attempt(mw)
            self.assertEqual(a._classify(), "stopped")
            self.assertEqual([s["code"] for s in a.stops], ["breach_failcnt_nonzero"])
            mw.set_mem(usage=10 << 20, peak=200 << 20, limit=256 << 20)
            mw.policy = P.synthetic_test_policy(conservative_peak_max_bytes=100 << 20, attempt_memory_limit_bytes=256 << 20)
            a = self.bare_attempt(mw)
            a.release_elsewhere = {"resident_bytes_elsewhere": 1}
            self.assertEqual(a._classify(), "stopped")
            self.assertEqual(a.stops[0]["code"], "breach_conservative_peak")
            mw.set_mem(usage=1, peak=1, limit=256 << 20)
            w(mw.layout.abs["pids"] + "/pids.events", "max 2\n")
            a = self.bare_attempt(mw)
            self.assertEqual(a._classify(), "stopped")
            self.assertEqual(a.stops[0]["code"], "breach_pids_max_hit")
            mw.set_mem(usage=1, peak=1, limit=256 << 20, oom_kill=1)
            w(mw.layout.abs["pids"] + "/pids.events", "max 0\n")
            a = self.bare_attempt(mw)
            self.assertEqual(a._classify(), "stopped")
            self.assertIn("breach_oom_signal", [s["code"] for s in a.stops])
        finally:
            mw.close()
        floors = tel.evaluate_post_exit(P.synthetic_test_policy(), {"cg_charged_peak_bytes": 0, "oom_kill": 0, "under_oom": 0, "failcnt": 0},
                                        {"pids_events": "max 0"})
        self.assertEqual(floors, [], "live-only floors are not evaluated after exit")

    def test_M09_receipt_mandatory_for_non_mock(self):
        mw = MockWorld(self.scr)
        try:
            a = self.bare_attempt(mw, synthetic_mock=False)
            self.assertEqual(a._classify(), "fail")
            self.assertEqual(a.stops[0]["code"], "exit_without_receipt")
            b = self.bare_attempt(mw, synthetic_mock=True)
            self.assertEqual(b._classify(), "success", "mock slots may omit it")
        finally:
            mw.close()

    def test_M09_swap_gate(self):
        self.assertEqual(sysfs.swap_total_bytes("SwapTotal:  0 kB\n"), 0)
        self.assertEqual(sysfs.swap_total_bytes("SwapTotal:  8 kB\n"), 8192)
        mw = MockWorld(self.scr)
        try:
            pr = mw.spawn()
            d = mw.member(pr)
            mw.set_listing([pr.pid])
            t = mw.telemetry(d)
            self.assertEqual(t.sample()["swap_total_bytes"], 0)
            mw.fs.over["/proc/meminfo"] = mw.fs.over["/proc/meminfo"].replace("SwapTotal: 0 kB", "SwapTotal: 1024 kB")
            with self.assertRaises(TelemetryStop) as cm:
                mw.telemetry(d).sample()
            self.assertEqual(cm.exception.code, "swap_appeared")
        finally:
            mw.close()

    def test_M13_nonzero_exit_requires_nonce_framed_marker(self):
        for silent, code in ((True, "ns_init_killed_or_unknown_exit"), (False, "child_nonzero_exit")):
            scr = H.Scratch("m13x")
            try:
                wd = H.make_world(scr, work={"mode": "exit", "code": 7, "silent": silent})
                res, p = H.run_driver(scr, H.spec_path(wd))
                t = res["terminal"]
                self.assertEqual(t["status"], "fail", (t["stops"], p.stderr))
                self.assertEqual(t["stops"][0]["code"], code, t["stops"])
            finally:
                scr.cleanup()


class Identity(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:30])
        self.dirs = []

    def tearDown(self):
        for d in self.dirs:
            try:
                os.rmdir(d)
            except OSError:
                pass
        self.scr.cleanup()
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M12_stat_failure_rolls_back_created_dirs(self):
        fs = FaultFs(RealFs(), {})
        lay = cgmod.Layout("n2-statfault-" + os.urandom(3).hex(), cgmod.controller_cgroup_paths(RealFs()))
        fs.armed[("stat", lay.abs["memory"])] = "INJECTED_E_STAT"  # the 2nd directory made: v2 first, then memory
        try:
            with self.assertRaises(CgroupStop) as cm:
                cgmod.create_all(lay, fs)
            self.assertEqual(cm.exception.code, "cgroup_stat_failed")
            self.assertEqual(cm.exception.injected, "INJECTED_E_STAT")
            for h in lay.join_order:
                self.assertFalse(os.path.exists(lay.abs[h]), "everything made so far was rolled back (%s)" % h)
            self.assertEqual(lay.created, {})
        finally:  # never leak real cgroups if create_all unexpectedly succeeded
            for h in reversed(lay.join_order):
                if os.path.isdir(lay.abs[h]):
                    os.rmdir(lay.abs[h])

    def test_M12_foreign_cgroup_survives_remove(self):
        fs = RealFs()
        paths = cgmod.controller_cgroup_paths(fs)
        lay = cgmod.Layout("n2-foreign-" + os.urandom(3).hex(), paths)
        # (a) a foreign directory already sits at the v2 path: create fails, the foreign one is untouched
        os.mkdir(lay.abs["v2"])
        self.dirs.append(lay.abs["v2"])
        with self.assertRaises(CgroupStop):
            cgmod.create_all(lay, fs)
        self.assertTrue(os.path.isdir(lay.abs["v2"]))
        self.assertEqual(lay.created, {})
        cgmod.remove(lay, fs)  # nothing was created by this attempt: nothing is removed
        self.assertTrue(os.path.isdir(lay.abs["v2"]))
        os.rmdir(lay.abs["v2"])
        # (b) our directory is replaced by a foreign one with the same name: identity check refuses
        cgmod.create_all(lay, fs)
        for h in lay.join_order:
            self.dirs.append(lay.abs[h])
        os.rmdir(lay.abs["memory"])
        os.mkdir(lay.abs["memory"])
        with self.assertRaises(CgroupStop) as cm:
            cgmod.remove(lay, fs)
        self.assertEqual(cm.exception.code, "rmdir_identity_mismatch")
        self.assertTrue(os.path.isdir(lay.abs["memory"]), "the foreign directory survives")
        self.assertFalse(os.path.isdir(lay.abs["pids"]), "our own pids directory was removed first")


class StubRules(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:30])

    def tearDown(self):
        self.scr.cleanup()

    def test_M16_D4_erofs_specific_and_fstype_selection(self):
        n = 0
        for line in open("/proc/self/mountinfo"):
            left, _s, right = line.partition(" - ")
            if right.split()[0] in ("cgroup", "cgroup2"):
                n += 1
        rc, err, ev = stubrun.run_stub(self.scr)
        self.assertEqual(rc, 0, err)
        self.assertEqual(ev[0], ("remounted_ro", {"n": str(n)}), "remount targets are chosen by fstype cgroup/cgroup2")
        exp, _env = stubrun.build_expected(self.scr)
        gone = exp["cgroup_probe_path"] + ".does-not-exist"  # open fails with ENOENT, not EROFS
        rc, err, ev = stubrun.run_stub(self.scr, patch={"cgroup_probe_path": gone})
        self.assertEqual(rc, 93, err)
        self.assertIn("expected_EROFS_got_errno_2", err)


class Exiting(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:30])
        self.mw = MockWorld(self.scr)

    def tearDown(self):
        self.mw.close()
        self.scr.cleanup()

    def stat_with_flags(self, pid, flags, state=None, tid=None):
        txt = open("/proc/%d/stat" % pid if tid is None else "/proc/%d/task/%d/stat" % (pid, tid)).read()
        k = txt.rindex(")") + 2
        rest = txt[k:].split()
        rest[6] = str(flags)
        if state:
            rest[0] = state
        return txt[:k] + " ".join(rest) + "\n"

    def test_M16_pf_exiting_absorbed_and_recorded(self):
        p = self.mw.spawn()
        d = self.mw.member(p)
        self.mw.set_listing([p.pid])
        fs = self.mw.fs
        fs.over["/proc/%d/stat" % p.pid] = self.stat_with_flags(p.pid, 0)
        self.assertFalse(pidfd.pid_is_exiting(fs, p.pid))
        fs.over["/proc/%d/stat" % p.pid] = self.stat_with_flags(p.pid, pidfd.PF_EXITING)
        self.assertTrue(pidfd.pid_is_exiting(fs, p.pid), "PF_EXITING is the primary signal")
        fs.over["/proc/%d/stat" % p.pid] = self.stat_with_flags(p.pid, 0, "Z")
        self.assertTrue(pidfd.pid_is_exiting(fs, p.pid))
        self.assertTrue(pidfd.pid_is_exiting(fs, 2 ** 22 + 5), "gone counts as exiting")
        # a thread whose cgroup file already shows the root css set while PF_EXITING is set: absorbed AND recorded
        fs.over["/proc/%d/stat" % p.pid] = self.stat_with_flags(p.pid, pidfd.PF_EXITING)
        fs.over["/proc/%d/task/%d/stat" % (p.pid, p.pid)] = self.stat_with_flags(p.pid, pidfd.PF_EXITING, tid=p.pid)
        fs.over["/proc/%d/task/%d/cgroup" % (p.pid, p.pid)] = self.mw.outside_cgroup_text()
        s = self.mw.telemetry(d).sample()
        self.assertEqual(s["absorbed_mismatches"][0]["how"], "pf_exiting_or_dead")
        self.assertEqual(s["absorbed_mismatches"][0]["pid"], p.pid)

    def test_M16_exiting_leader_does_not_absorb_live_thread(self):
        p = self.mw.spawn("import threading, time\nts = [threading.Thread(target=time.sleep, args=(60,)) for _ in range(2)]\n"
                          "[t.start() for t in ts]\ntime.sleep(60)\n")
        time.sleep(0.4)
        d = self.mw.member(p)
        self.mw.set_listing([p.pid])
        fs = self.mw.fs
        tids = [x for x in pidfd.thread_ids(fs, p.pid) if x != p.pid]
        self.assertEqual(len(tids), 2)
        # the leader looks like it is exiting (process-level stat), and BOTH non-leaders left the cgroup
        fs.over["/proc/%d/stat" % p.pid] = self.stat_with_flags(p.pid, pidfd.PF_EXITING)
        fs.over["/proc/%d/task/%d/stat" % (p.pid, p.pid)] = self.stat_with_flags(p.pid, pidfd.PF_EXITING, tid=p.pid)
        fs.over["/proc/%d/task/%d/cgroup" % (p.pid, p.pid)] = self.mw.outside_cgroup_text()  # leader: absorbed
        for tid in tids:
            fs.over["/proc/%d/task/%d/cgroup" % (p.pid, tid)] = self.mw.outside_cgroup_text()
        self.assertTrue(pidfd.pid_is_exiting(fs, p.pid))
        self.assertFalse(pidfd.pid_is_exiting(fs, p.pid, tids[0]), "the thread is judged by its own stat")
        with self.assertRaises(ContainmentStop) as cm:
            self.mw.telemetry(d).sample()
        self.assertEqual(cm.exception.code, "thread_outside_attempt_cgroup")
        self.assertIn("tid", cm.exception.detail)
        # absorbing the first non-leader must not skip the second (no break out of the thread loop)
        fs.over["/proc/%d/task/%d/stat" % (p.pid, tids[0])] = self.stat_with_flags(p.pid, pidfd.PF_EXITING, tid=tids[0])
        with self.assertRaises(ContainmentStop) as cm:
            self.mw.telemetry(d).sample()
        self.assertIn("tid %d" % tids[1], cm.exception.detail)

    def test_M16_nsinit_discovery_requires_exact_nspid_length(self):
        p = self.mw.spawn()
        d = self.mw.member(p)
        q = self.mw.spawn()
        self.mw.contain_overlay(q.pid)
        self.mw.set_listing([p.pid, q.pid])
        status = open("/proc/%d/status" % q.pid).read()
        ctrl_len = len(pidfd.nspid_fields(self.mw.fs, os.getpid()))
        def with_nspid(fields):
            out = []
            for line in status.splitlines():
                out.append("NSpid:\t" + "\t".join(map(str, fields)) if line.startswith("NSpid:") else line)
            return "\n".join(out) + "\n"
        bad = [q.pid] + [7] * ctrl_len + [1]  # one field too many, still ends in 1
        self.mw.fs.over["/proc/%d/status" % q.pid] = with_nspid(bad)
        t = self.mw.telemetry(d)
        t.sample()
        self.assertIsNone(t.ctx.ns_init, "length controller+2 is not ns-init")
        self.mw.fs.over["/proc/%d/status" % q.pid] = with_nspid([q.pid, 1])
        t2 = self.mw.telemetry(d)
        t2.sample()
        self.assertEqual(t2.ctx.ns_init.pid, q.pid)
        _ = (controller, MIB)


if __name__ == "__main__":
    unittest.main()
