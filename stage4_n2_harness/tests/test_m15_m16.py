"""M15 confined memory limit (OOM and non-OOM variants); M16 escape detection and environment."""
import json
import os
import signal
import subprocess
import time
import unittest

from n2h import cg as cgmod
from n2h import launch, pidfd
from n2h import policy as policy_mod
from n2h import telemetry as tel
from n2h.errors import ContainmentStop
from n2h.fsio import RealFs
from tests import helpers as H
from tests import stubrun
from tests.mockcg import MockWorld

MIB = 1 << 20
FINDINGS = os.path.join(H.HARNESS, "results", "findings-m15-m16.json")


def record(key, value):
    os.makedirs(os.path.dirname(FINDINGS), exist_ok=True)
    try:
        with open(FINDINGS) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d[key] = value
    with open(FINDINGS, "w") as f:
        json.dump(d, f, indent=1, sort_keys=True)


def stat_alive(pid):
    try:
        txt = open("/proc/%d/stat" % pid).read()
        return txt[txt.rindex(")") + 2] != "Z"
    except OSError:
        return False


class M15(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:30])
        self.sleepers = H.Sleepers(3)

    def tearDown(self):
        alive = self.sleepers.all_alive()
        self.sleepers.close()
        self.scr.cleanup()
        self.assertTrue(alive, "the unrelated sleepers survived the confined OOM")
        self.assertEqual(H.no_n2_cgroups(), [])

    def policy(self, limit_mib):
        return {"attempt_memory_limit_bytes": limit_mib * MIB, "running_floor_attempt_headroom_bytes": 0,
                "conservative_peak_max_bytes": 1 << 40, "sample_interval_ms": 20, "per_slot_elapsed_cap_s": 40}

    def test_M15_oom_variant(self):
        me = os.getpid()
        w = H.make_world(self.scr, work={"mode": "alloc_until_killed"}, policy_over=self.policy(48))
        res, p = H.run_driver(self.scr, H.spec_path(w))
        self.assertIn("terminal", res, (res, p.stderr))
        t = res["terminal"]
        self.assertNotEqual(t["status"], "success")
        self.assertTrue(stat_alive(res["ctrl_pid"]) or p.returncode == 0, "the controller finished normally (it was never the victim)")
        self.assertEqual(p.returncode, 0)
        self.assertTrue(stat_alive(me))
        fm = t["post"]["final_memory"]
        self.assertGreaterEqual(fm["oom_kill"], 1, "the cgroup OOM killer acted inside the attempt cgroup")
        self.assertGreater(fm["failcnt"], 0)
        self.assertLessEqual(fm["cg_charged_peak_bytes"], 48 * MIB + MIB, "charged peak is bounded by the finite limit")
        self.assertGreaterEqual(fm["cg_charged_peak_bytes"], 40 * MIB)
        codes = [s["code"] for s in t["stops"]]
        self.assertTrue(set(codes) & {"oom_kill_detected", "breach_oom_signal", "breach_failcnt_nonzero", "ns_init_killed_or_unknown_exit"}, codes)
        self.assertIs(t["post"]["cgroups_removed"], True)
        err = open(os.path.join(w["slot_root"], "stderr.log")).read()
        last = [ln for ln in err.splitlines() if "alloc_progress" in ln][-1:]
        d = (t["exit"].get("direct") or {}).get("status")
        record("oom_variant", {"limit_mib": 48, "stops": t["stops"], "final_memory": fm, "last_progress_marker": last,
                               "direct_unshare_status": d, "termination": t.get("termination") and t["termination"]["steps"]})

    def test_M15_non_oom_variant(self):
        w = H.make_world(self.scr, work={"mode": "filecache", "mib": 160, "path": "", "seconds": 1.0},
                         policy_over=self.policy(48))
        w["work"]["path"] = os.path.join(w["slot_root"], "cache_fill.bin")
        res, p = H.run_driver(self.scr, H.spec_path(w))
        t = res["terminal"]
        fm = t["post"]["final_memory"]
        self.assertEqual(fm["oom_kill"], 0, "file cache beyond the limit is reclaimed, not OOM-killed")
        self.assertGreater(fm["failcnt"], 0, "the limit was hit and reclaimed")
        self.assertLessEqual(fm["cg_charged_peak_bytes"], 48 * MIB + MIB)
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "breach_failcnt_nonzero", "failcnt_action = STOP (D-11)")
        self.assertEqual(p.returncode, 0)
        self.assertIs(t["post"]["cgroups_removed"], True)
        record("non_oom_variant", {"limit_mib": 48, "written_mib": 160, "final_memory": fm, "stops": t["stops"]})


class M16(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:30])
        self.mw = None
        self.owned = []

    def tearDown(self):
        if self.mw:
            self.mw.close()
        for o in self.owned:
            o.close()
        self.scr.cleanup()
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M16_self_migration_mock_stop(self):
        self.mw = MockWorld(self.scr)
        p = self.mw.spawn()
        d = self.mw.member(p)
        self.mw.set_listing([p.pid])
        self.mw.telemetry(d).sample()
        # (a) the member's own cgroup file now shows a different cgroup although it is still listed
        self.mw.contain_overlay(p.pid, self.mw.outside_cgroup_text())
        with self.assertRaises(ContainmentStop) as cm:
            self.mw.telemetry(d).sample()
        self.assertEqual(cm.exception.code, "thread_outside_attempt_cgroup")
        # (b) it left the listing while alive
        self.mw.contain_overlay(p.pid)
        self.mw.set_listing([])
        with self.assertRaises(ContainmentStop) as cm:
            self.mw.telemetry(d).sample()
        self.assertEqual(cm.exception.code, "live_member_absent_from_listing")
        # (c) a newly seen process in the listing that is not in the attempt cgroups
        q = self.mw.spawn()
        self.mw.set_listing([p.pid, q.pid])
        self.mw.contain_overlay(q.pid, self.mw.outside_cgroup_text())
        with self.assertRaises(ContainmentStop) as cm:
            self.mw.telemetry(d).sample()
        self.assertIn(cm.exception.code, ("member_outside_attempt_cgroup", "live_member_absent_from_listing"))

    def test_M16_unexpected_process_in_cgroup_stops(self):
        self.mw = MockWorld(self.scr)
        p = self.mw.spawn()
        d = self.mw.member(p)
        # a stranger that is NOT a descendant of the member and not a child of the controller: double fork, reparented
        helper = self.mw.spawn("import os, time\nif os.fork() == 0:\n    os.setsid()\n    print(os.getpid(), flush=True)\n"
                               "    time.sleep(60)\n    os._exit(0)\nos._exit(0)\n")
        stranger = int(helper.stdout.readline())
        helper.wait()
        try:
            self.mw.set_listing([p.pid, stranger])
            self.mw.contain_overlay(stranger)  # it even looks contained
            with self.assertRaises(ContainmentStop) as cm:
                self.mw.telemetry(d).sample()
            self.assertEqual(cm.exception.code, "unexpected_process_in_cgroup")
            self.assertIn(str(stranger), cm.exception.detail)
        finally:
            os.kill(stranger, signal.SIGKILL)  # a process this test created itself
        H.pid_gone(stranger, 3.0)
        # once it has exited it is not an error, and the member alone is fine
        self.mw.set_listing([p.pid])
        self.mw.telemetry(d).sample()

    def test_M16_thread_moved_mock_stop(self):
        self.mw = MockWorld(self.scr)
        p = self.mw.spawn("import threading, time\nthreads = [threading.Thread(target=time.sleep, args=(60,)) for _ in range(2)]\n"
                          "[t.start() for t in threads]\ntime.sleep(60)\n")
        time.sleep(0.4)
        d = self.mw.member(p)
        self.mw.set_listing([p.pid])
        tids = pidfd.thread_ids(self.mw.fs, p.pid)
        self.assertEqual(len(tids), 3)
        t = self.mw.telemetry(d)
        s = t.sample()
        self.assertEqual(s["walk"]["thread_checks"], 3)
        moved = [x for x in tids if x != p.pid][0]
        self.mw.fs.over["/proc/%d/task/%d/cgroup" % (p.pid, moved)] = self.mw.outside_cgroup_text()
        self.assertTrue(cgmod.proc_cgroup_matches(self.mw.layout, p.pid, self.mw.fs), "the leader's file still looks fine")
        with self.assertRaises(ContainmentStop) as cm:
            self.mw.telemetry(d).sample()
        self.assertEqual(cm.exception.code, "thread_outside_attempt_cgroup")
        self.assertIn("tid %d" % moved, cm.exception.detail)

    def test_M16_real_thread_moved_via_tasks_detected(self):
        from tests.ocg import OwnedCg
        a, b = OwnedCg(), OwnedCg()
        self.owned += [a, b]
        a.spawn("import threading\nthreads = [threading.Thread(target=time.sleep, args=(60,)) for _ in range(2)]\n"
                "[t.start() for t in threads]\nsay('ready')\nwait_go()\ntime.sleep(60)\n")
        self.assertEqual(a.readline(), "ready")
        a.join()
        fs = RealFs()
        tids = pidfd.thread_ids(fs, a.child.pid)
        moved = [x for x in tids if x != a.child.pid][0]
        # v1 lets a single thread move through `tasks` (here between two cgroups this test created)
        fs.write(b.lay.abs["memory"] + "/tasks", str(moved))
        self.assertTrue(cgmod.proc_cgroup_matches(a.lay, a.child.pid, fs), "leader view is unchanged: /proc/<pid>/cgroup shows the leader only")
        self.assertFalse(cgmod.proc_cgroup_matches(a.lay, a.child.pid, fs, tid=moved))
        direct = pidfd.open_verified(a.child.pid, a.lay, fs, "direct", cg_check=True)
        ctx = tel.SampleCtx(fs, a.lay, direct, policy_mod.synthetic_test_policy(),
                            "/sys/fs/cgroup/memory", "/", self.scr.root)
        t = tel.Telemetry(ctx, os.devnull, persist=False)
        with self.assertRaises(ContainmentStop) as cm:
            t._walk_and_escape(cgmod.members(a.lay, fs))
        self.assertEqual(cm.exception.code, "thread_outside_attempt_cgroup")
        self.assertIn("tid %d" % moved, cm.exception.detail)
        fs.write(a.lay.abs["memory"] + "/tasks", str(moved))  # put it back before teardown
        direct.close()

    def test_M16_D4_readonly_cgroup_write_fails(self):
        w = H.make_world(self.scr, work={"mode": "d4_probe", "seconds": 0.3, "own_procs": "", "own_dir": ""})
        name = H.attempt_name(w)
        paths = cgmod.controller_cgroup_paths(RealFs())
        base = "/sys/fs/cgroup/memory" + paths["memory"] + "/" + name
        w["work"]["own_procs"] = base + "/cgroup.procs"
        w["work"]["own_dir"] = base
        res, p = H.run_driver(self.scr, H.spec_path(w))
        t = res["terminal"]
        self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
        err = open(os.path.join(w["slot_root"], "stderr.log")).read()
        line = [ln for ln in err.splitlines() if " d4_probe " in ln][0]
        kv = dict(x.split("=") for x in line.split()[4:])
        self.assertEqual(kv["open_wr"], "30", "EROFS: opening cgroup.procs for write is refused on the read-only mount")
        self.assertEqual(kv["mkdir"], "30", "EROFS: a nested cgroup cannot be created")
        self.assertEqual(kv["remount_rw"], "1", "EPERM: a read-write remount needs CAP_SYS_ADMIN, which is gone")
        self.assertEqual(int(kv["capeff"], 16), 0)
        self.assertEqual(int(kv["capbnd"], 16), 0)
        self.assertEqual(kv["nnp"], "1")
        self.assertIn("remounted_ro n=", err)
        # the host's own mounts are unchanged (the remount happened in a private mount namespace)
        mi = open("/proc/self/mountinfo").read()
        for ln in mi.splitlines():
            if " /sys/fs/cgroup" in ln and " - cgroup" in ln:
                self.assertIn(" rw,", ln)
        record("D4_probe", kv)

    def test_M16_D4_caps_dropped(self):
        # stage 1 without the setpriv hop (full caps) must be refused by the stub: caps are not assumed
        rc, err, ev = stubrun.run_stub(self.scr, patch={"stage": 1})
        self.assertEqual(rc, 92, err)
        self.assertEqual(ev[0][1]["name"], "CAPS")
        # full chain: stage 0 -> setpriv -> stage 1 passes only with empty cap sets and no_new_privs
        rc, err, ev = stubrun.run_stub(self.scr)
        self.assertEqual(rc, 0, err)
        names = [e[0] for e in ev]
        self.assertEqual(names[:4], ["remounted_ro", "exec_setpriv", "selfcheck_ok", "ready"])
        self.assertEqual(ev[2][1]["ro_errno"], "30")

    def run_report(self, env):
        w = H.make_world(self.scr, work={"mode": "report", "seconds": 0.2})
        res, p = H.run_driver(self.scr, H.spec_path(w), env=env)
        self.assertEqual(res["terminal"]["status"], "success", (res["terminal"]["stops"], p.stderr))
        err = open(os.path.join(w["slot_root"], "stderr.log")).read()
        line = [ln for ln in err.splitlines() if " report " in ln][0]
        return dict(x.split("=", 1) for x in line.split()[4:])

    def test_M16_env_scrub(self):
        env = {"PATH": "/usr/bin:/bin", "N2_TEST_CANARY_CREDENTIAL_LIKE": "canary-not-real",
               "HTTPS_PROXY": "http://canary.invalid:1", "GITHUB_TOKEN": "canary-not-real", "PYTHONPATH": "/nonexistent-canary",
               "PYTHONSTARTUP": "/nonexistent-canary", "LD_PRELOAD": "", "AWS_SECRET_ACCESS_KEY": "canary-not-real"}
        kv = self.run_report(env)
        keys = set(kv["env"].split(","))
        self.assertEqual(keys, set(launch.build_env("/h", "/t")))
        for bad in ("N2_TEST_CANARY_CREDENTIAL_LIKE", "HTTPS_PROXY", "GITHUB_TOKEN", "PYTHONPATH", "PYTHONSTARTUP",
                    "LD_PRELOAD", "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(bad, keys)
        self.assertEqual([k for k in keys if k.startswith("PYTHON")], ["PYTHONDONTWRITEBYTECODE"])

    def test_M16_thread_vars_D1(self):
        kv = self.run_report(None)
        self.assertEqual((kv["openblas"], kv["omp"], kv["mkl"], kv["gdal"]), ("1", "1", "1", "1"))
        self.assertEqual(set(launch.ENV_THREAD_VARS), {"OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                                                       "GDAL_NUM_THREADS"})
        self.assertTrue(all(v == "1" for v in launch.ENV_THREAD_VARS.values()))
        _ = subprocess


if __name__ == "__main__":
    unittest.main()
