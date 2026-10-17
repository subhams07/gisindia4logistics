"""Review round 1 fixes: post-exit gates, receipt/exit-marker rules, swap gate, identity-checked rmdir,
EROFS/fstype stub rules, PF_EXITING absorption, exact NSpid discovery."""
import os
import unittest

from n2h import cg as cgmod
from n2h import controller, pidfd, sysfs
from n2h import policy as P
from n2h import telemetry as tel
from n2h.controller import Attempt
from n2h.errors import CgroupStop, TelemetryStop
from n2h.fsio import RealFs
from tests import helpers as H
from tests import stubrun
from tests.mockcg import MockWorld, w

MIB = 1 << 20


def small_limit(**kw):
    d = {"attempt_memory_limit_bytes": 48 * MIB, "running_floor_attempt_headroom_bytes": 0, "conservative_peak_max_bytes": 1 << 40,
         "sample_interval_ms": 400, "per_slot_elapsed_cap_s": 40}
    d.update(kw)
    return d


class PostExit(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:30])

    def tearDown(self):
        self.scr.cleanup()
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M09_post_exit_failcnt_last_interval(self):
        wd = H.make_world(self.scr, work={"mode": "filecache", "mib": 160, "path": "", "seconds": 0}, policy_over=small_limit())
        wd["work"]["path"] = os.path.join(wd["slot_root"], "fill.bin")
        res, p = H.run_driver(self.scr, H.spec_path(wd))
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "breach_failcnt_nonzero")
        self.assertEqual(t["post"]["final_memory"]["oom_kill"], 0)

    def test_M09_post_exit_conservative_peak_last_interval(self):
        wd = H.make_world(self.scr, work={"mode": "alloc", "mib": 40, "seconds": 0},
                          policy_over=small_limit(attempt_memory_limit_bytes=128 * MIB, conservative_peak_max_bytes=40 * MIB))
        res, p = H.run_driver(self.scr, H.spec_path(wd))
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "breach_conservative_peak")

    def bare_attempt(self, mw, synthetic_mock=True, require=False, code=0):
        a = object.__new__(Attempt)
        a.fs, a.layout, a.stops, a.policy = mw.fs, mw.layout, [], mw.policy
        a.markers, a.cfg, a.nonce = None, {"synthetic_mock": synthetic_mock, "require_receipt": require, "slot_root": self.scr.root}, "n"
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

    def stat_with_flags(self, pid, flags, state=None):
        txt = open("/proc/%d/stat" % pid).read()
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
        fs.over["/proc/%d/task/%d/cgroup" % (p.pid, p.pid)] = self.mw.outside_cgroup_text()
        s = self.mw.telemetry(d).sample()
        self.assertEqual(s["absorbed_mismatches"][0]["how"], "pf_exiting_or_dead")
        self.assertEqual(s["absorbed_mismatches"][0]["pid"], p.pid)

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
