"""M03-L1..L6 and reviewer notes 1-5: attempt-before-cgroup, limits, fork preconditions, the gated child,
joins and the launch record. Real forks happen only in driver processes (strict L-3 allowlist)."""
import json
import os
import signal
import sys
import threading
import time
import unittest

from n2h import cg as cgmod
from n2h import controller, launch, native, pidfd, stubsrc
from n2h import ledger as L
from n2h.allowlist import STDLIB_ALLOWLIST
from n2h.errors import CgroupStop, ContainmentStop, LaunchStop
from n2h.fsio import FaultFs, RealFs
from tests import helpers as H
from tests import stubrun


def read_fds(pid):
    out = {}
    for n in os.listdir("/proc/%d/fd" % pid):
        try:
            out[int(n)] = os.readlink("/proc/%d/fd/%s" % (pid, n))
        except OSError:
            pass
    return out


def sig_masks(pid):
    d = {}
    for line in open("/proc/%d/status" % pid):
        for k in ("SigCgt", "SigIgn"):
            if line.startswith(k + ":"):
                d[k] = int(line.split()[1], 16)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:30])

    def tearDown(self):
        self.scr.cleanup()

    def drive(self, world=None, extra=None, tag="run", env=None, **world_kw):
        w = world or H.make_world(self.scr, **world_kw)
        res, p = H.run_driver(self.scr, H.spec_path(w, extra), tag=tag, env=env)
        return w, res, p


class M03Early(Base):
    def test_M03_L1_attempt_record_precedes_mkdir(self):
        w, res, p = self.drive()
        t = res["terminal"]
        self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
        tl = t["timeline_ns"]
        self.assertLess(tl["attempt_created"], tl["cgroups_created"])
        key = L.attempt_key(w["approval_sha"], "H0")
        att = L.Ledger(w["control_root"]).read("attempt", key)
        self.assertEqual(att["planned_cgroup_name"], "n2-" + key[:20])
        self.assertEqual(att["boot_id"], open("/proc/sys/kernel/random/boot_id").read().strip())
        for k in ("interpreter", "unshare", "setpriv"):
            self.assertEqual(len(att[k]["sha"]), 64)
            self.assertEqual(len(att[k]["ident"]), 5)
        self.assertEqual(att["stub_sha"], controller.hashlib_sha(stubsrc.STUB))
        self.assertEqual(att["controller_cgroup_paths"]["memory"], t["launch_avail"]["ancestors"][0]["rel"])
        self.assertNotIn("pid", att)
        self.assertEqual(att["swap_total_bytes"], 0, "memsw=2x relies on no swap: bound in the attempt record")

    def test_M03_L1_eexist_stops(self):
        w, res, p = self.drive(extra={"precreate_after_attempt": True})
        t = res["terminal"]
        pre = res["extras"]["precreated"]
        try:
            self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
            self.assertEqual(t["stops"][0]["code"], "cgroup_exists")
            self.assertTrue(os.path.isdir(pre), "a cgroup the attempt did not create is never removed")
            key = L.attempt_key(w["approval_sha"], "H0")
            self.assertEqual(L.Ledger(w["control_root"]).state(key), "terminal", "attempt stays consumed")
            self.assertIsNot(t["post"].get("cgroups_removed"), True, "nothing of ours was created, nothing removed")
        finally:
            if os.path.isdir(pre):
                os.rmdir(pre)
        self.assertEqual(H.no_n2_cgroups(), [])

    def new_layout(self):
        name = "n2-ut-" + os.urandom(4).hex()
        paths = cgmod.controller_cgroup_paths(RealFs())
        return cgmod.Layout(name, paths)

    def test_M03_L2_limits_page_aligned_order_readback(self):
        lay = self.new_layout()
        fs = FaultFs(RealFs(), {})
        with self.assertRaises(CgroupStop) as cm:
            cgmod.set_limits(lay, (64 << 20) + 1, 16, fs)
        self.assertEqual(cm.exception.code, "limit_not_page_aligned")
        with self.assertRaises(CgroupStop):
            cgmod.set_limits(lay, 0, 16, fs)
        with self.assertRaises(CgroupStop) as cm:
            cgmod.set_limits(lay, 64 << 20, 0, fs)
        self.assertEqual(cm.exception.code, "pids_max_not_finite")
        self.assertEqual([w for w in fs.log if w[0] == "write"], [], "nothing written before validation passes")
        made = cgmod.create_all(lay, fs)
        try:
            self.assertEqual(len(made), 3)
            got = cgmod.set_limits(lay, 64 << 20, 16, fs)
            order = [os.path.basename(w[1]) for w in fs.log if w[0] == "write"]
            self.assertEqual(order, ["memory.limit_in_bytes", "memory.memsw.limit_in_bytes", "pids.max"])
            self.assertEqual(got, {"memory_limit": 64 << 20, "memsw_limit": 128 << 20, "pids_max": 16})
            self.assertEqual(cgmod.procs(lay, "memory", fs), [])
            self.assertTrue(cgmod.is_empty(lay, fs))
        finally:
            cgmod.remove(lay, RealFs())
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M03_L2_injected_write_failure_consumes_and_removes_own_cgroups(self):
        w, res, p = self.drive(extra={"fs_faults": {"write|memory.memsw.limit_in_bytes": "INJECTED_E_CGWRITE"}})
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "limit_write_failed")
        self.assertEqual(t["stops"][0]["injected"], "INJECTED_E_CGWRITE")
        key = L.attempt_key(w["approval_sha"], "H0")
        led = L.Ledger(w["control_root"])
        self.assertEqual(led.state(key), "terminal")
        self.assertFalse(led.can_dispatch_slot(w["approval_sha"], "H0")[0])
        self.assertIs(t["post"].get("cgroups_removed"), True)
        self.assertEqual(H.no_n2_cgroups(), [])
        self.assertEqual(os.listdir(os.path.join(w["slot_root"])).count("stdout.log"), 0, "no child was ever created")

    def test_M03_L3_fork_refused_multi_thread(self):
        ev = threading.Event()
        th = threading.Thread(target=ev.wait)
        th.start()
        try:
            with self.assertRaises(LaunchStop) as cm:
                launch.check_fork_preconditions(set(sys.modules))
            self.assertEqual(cm.exception.code, "fork_multiple_threads")
        finally:
            ev.set()
            th.join()
        box = []

        def other():
            try:
                launch.check_fork_preconditions(set(sys.modules))
            except LaunchStop as e:
                box.append(e.code)
        t2 = threading.Thread(target=other)
        t2.start()
        t2.join()
        self.assertEqual(box, ["fork_not_from_main_thread"])

    def test_M03_L3_fork_refused_module_outside_allowlist(self):
        allow = set(sys.modules) - {"unittest"}
        with self.assertRaises(LaunchStop) as cm:
            launch.check_fork_preconditions(allow)
        self.assertEqual(cm.exception.code, "fork_module_outside_allowlist")
        self.assertIn("unittest", cm.exception.detail)
        w, res, p = self.drive(extra={"import_before_launch": "sqlite3"})
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t, p.stderr))
        self.assertEqual(t["stops"][0]["code"], "fork_module_outside_allowlist")
        self.assertIn("sqlite3", t["stops"][0]["detail"])
        self.assertEqual(H.no_n2_cgroups(), [])
        key = L.attempt_key(w["approval_sha"], "H0")
        self.assertEqual(L.Ledger(w["control_root"]).state(key), "terminal", "refused fork still consumes")
        # pinned list sanity: every non-package entry is a stdlib module name
        std = sys.stdlib_module_names
        for m in STDLIB_ALLOWLIST:
            self.assertTrue(m.startswith("n2h") or m in ("__main__", "_frozen_importlib", "_frozen_importlib_external")
                            or m.split(".")[0] in std, m)

    def test_M03_L3_fork_refused_sigchld_ign(self):
        old = signal.getsignal(signal.SIGCHLD)
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        try:
            with self.assertRaises(LaunchStop) as cm:
                launch.check_fork_preconditions(set(sys.modules))
            self.assertEqual(cm.exception.code, "fork_sigchld_ignored")
        finally:
            signal.signal(signal.SIGCHLD, old)
        w, res, p = self.drive(extra={"sigchld_ign": True})
        self.assertEqual(res["terminal"]["stops"][0]["code"], "fork_sigchld_ignored")
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M03_L3_gc_disabled_and_stdio_flushed(self):
        w, res, p = self.drive(extra={"buffered_stdout_probe": True, "inject": {"report_gc": True}})
        t = res["terminal"]
        self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
        ex = res["extras"]
        self.assertIs(ex["stdout_pending_at_S0"], False, "stdout buffer flushed before fork")
        self.assertGreaterEqual(ex["stdout_flushes"], 1)
        self.assertIs(ex["parent_gc_enabled_after_fork"], True, "parent re-enables gc after fork")
        err = open(os.path.join(w["slot_root"], "stderr.log")).read()
        self.assertIn("N2CHILD gc_enabled=0", err, "gc was disabled across fork (child view)")
        self.assertEqual(err.count("BUFFERED_BEFORE_FORK"), 0)


class GatedChild(Base):
    """One paused controller per class: facts about the never-exec'd child, collected once."""

    facts = None

    @classmethod
    def setUpClass(cls):
        scr = H.Scratch("gated")
        cls.scr_cls = scr
        w = H.make_world(scr, work={"mode": "sleep", "seconds": 1})
        H.register_orphan(scr, w)
        spec = H.spec_path(w, {"pause_at": ["at_S0"], "pause_s": 60, "install_handlers": True,
                               "inject": {"extra_fd": False}})
        p, rf = H.spawn_driver(scr, spec)
        line = H.read_until(p, "AT at_S0")
        f = {"line": line}
        try:
            parts = dict(x.split("=") for x in line.split()[2:])
            ctrl, child = int(parts["ctrl"]), int(parts["child"])
            time.sleep(0.3)  # let the child reach its blocking read of the gate
            f.update({
                "ctrl": ctrl, "child": child,
                "exe": os.readlink("/proc/%d/exe" % child), "ctrl_exe": os.readlink("/proc/%d/exe" % ctrl),
                "cmdline": open("/proc/%d/cmdline" % child, "rb").read(),
                "ctrl_cmdline": open("/proc/%d/cmdline" % ctrl, "rb").read(),
                "state": pidfd.read_state_ppid(RealFs(), child)[0],
                "fds": read_fds(child), "ctrl_fds": read_fds(ctrl),
                "sig_child": sig_masks(child), "sig_ctrl": sig_masks(ctrl),
                "stderr_log": open(os.path.join(w["slot_root"], "stderr.log")).read(),
                "wchan": open("/proc/%d/wchan" % child).read(),
            })
            f["fdinfo_flags"] = {n: open("/proc/%d/fdinfo/%d" % (ctrl, n)).read().split("flags:")[1].split()[0]
                                 for n in f["ctrl_fds"]}
        finally:
            p.kill()
            p.wait()
            p.stdout.close()
            p.stderr.close()
        f["child_gone"] = H.pid_gone(f["child"], 5.0)
        cls.facts = f
        cls.world = w

    @classmethod
    def tearDownClass(cls):
        cls.scr_cls.cleanup()

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_M03_L4_gated_child_not_exec(self):
        f = self.facts
        self.assertEqual(f["exe"], f["ctrl_exe"], "the gated child is still the forked controller image")
        self.assertEqual(f["cmdline"], f["ctrl_cmdline"])
        self.assertNotIn("unshare", f["exe"])
        self.assertNotIn("N2M", f["stderr_log"], "no stub ran")
        self.assertIn(f["state"], ("S", "D"), "blocked in the gate read")
        self.assertTrue(f["child_gone"], "killing the controller removed the gated child (PDEATHSIG)")

    def test_M03_L4_fd_set_exact(self):
        fds = self.facts["fds"]
        self.assertEqual(len(fds), 5, fds)
        self.assertEqual(fds[0], "/dev/null")
        self.assertTrue(fds[1].endswith("/stdout.log"))
        self.assertTrue(fds[2].endswith("/stderr.log"))
        rest = sorted(v for k, v in fds.items() if k > 2)
        self.assertTrue(rest[0].startswith("/usr/bin/unshare") or rest[1].startswith("/usr/bin/unshare"), rest)
        self.assertTrue(any(v.startswith("pipe:[") for v in rest), rest)

    def test_M03_L4_gate_w_ebadf(self):
        f = self.facts
        child_pipe = [(k, v) for k, v in f["fds"].items() if v.startswith("pipe:[")]
        self.assertEqual(len(child_pipe), 1)
        inode = child_pipe[0][1]
        wr = [n for n, v in f["ctrl_fds"].items() if v == inode and (int(f["fdinfo_flags"][n], 8) & 3) == 1]
        self.assertEqual(len(wr), 1, "the controller holds exactly one write end")
        self.assertNotIn(wr[0], f["fds"], "the child's copy of gate_w number is closed (EBADF)")
        rd = [n for n, v in f["ctrl_fds"].items() if v == inode and (int(f["fdinfo_flags"][n], 8) & 3) == 0]
        self.assertEqual(rd, [], "the controller closed its read end after fork: EOF depends on gate_w alone")

    def test_M03_L4_signal_handlers_default(self):
        f = self.facts
        self.assertNotEqual(f["sig_ctrl"]["SigCgt"], 0, "controller has python handlers installed")
        self.assertEqual(f["sig_child"]["SigCgt"], 0, "no caught signals in the gated child")
        self.assertEqual(f["sig_child"]["SigIgn"], 0, "python's SIG_IGN dispositions (SIGPIPE) reset to default")


class M03Child(Base):
    def test_M03_L4_listdir_transient_duplicate_controls(self):
        alive, discarded, raw = launch.fd_snapshot()
        self.assertEqual(discarded, 1, "positive control: CPython os.listdir(fd) leaves exactly one transient number")
        self.assertEqual(len(raw), len(alive) + 1 + 1, "raw listing = alive set + dir fd + duplicate")
        extra = os.open("/dev/null", os.O_RDONLY)
        try:
            alive2, discarded2, _ = launch.fd_snapshot()
            self.assertIn(extra, alive2)
            self.assertEqual(discarded2, 1)
            keep = set(alive)
            self.assertTrue(launch.fd_assert_ok(alive, discarded, keep), "negative control: exact set passes")
            self.assertFalse(launch.fd_assert_ok(alive2, discarded2, keep), "an extra fd fails the exact-set assertion")
        finally:
            os.close(extra)
        self.assertFalse(launch.fd_assert_ok({0, 1, 2}, 0, {0, 1, 2}), "no transient duplicate seen: assertion fails")
        self.assertFalse(launch.fd_assert_ok({0, 1, 2}, 2, {0, 1, 2}))

    def exit_name(self, t):
        pg = t.get("pre_gate_exit")
        if pg:
            return pg["name"]
        d = (t["exit"].get("direct") or {}).get("status") or {}
        return {v: k for k, v in launch.CHILD_CODES.items()}.get(d.get("code"))

    def test_M03_L4_exec_fd_inplace_rewrite_detected(self):
        ub = H.copy_binary(self.scr, H.UNSHARE, "unshare")
        w = H.make_world(self.scr)
        w["unshare"] = ub
        w, res, p = self.drive(world=w, extra={"rewrite_exec_after_verify": ub})
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(self.exit_name(t), "E_EXEC")
        self.assertNotIn("ready", open(os.path.join(w["slot_root"], "stderr.log")).read())
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M03_L4_stub_source_change_refused(self):
        w, res, p = self.drive(extra={"stub_suffix": "\n# one byte of change\n"})
        t = res["terminal"]
        self.assertEqual(t["stops"][0]["code"], "child_refused_before_ready")
        self.assertIn("STUBHASH", t["stops"][0]["detail"])
        err = open(os.path.join(w["slot_root"], "stderr.log")).read()
        self.assertIn("name=STUBHASH", err)
        self.assertNotIn(" ready ", err)

    def test_M03_L4_extra_fd_E_FD(self):
        w, res, p = self.drive(extra={"inject": {"extra_fd": True}})
        self.assertEqual(self.exit_name(res["terminal"]), "E_FD", res["terminal"]["stops"])

    def test_M03_L4_exec_fd_swap_E_EXEC(self):
        w, res, p = self.drive(extra={"inject": {"swap_exec_fd_to": H.SETPRIV}})
        self.assertEqual(self.exit_name(res["terminal"]), "E_EXEC", res["terminal"]["stops"])

    def test_M03_L4_gate_eof_E_GATE_and_wrong_byte(self):
        for key in ("close_gate_instead_of_release", "wrong_gate_byte"):
            scr = H.Scratch("m03gate")
            try:
                w = H.make_world(scr)
                res, p = H.run_driver(scr, H.spec_path(w, {key: True}))
                t = res["terminal"]
                self.assertEqual(t["stops"][0]["code"], "child_refused_before_ready", (key, t["stops"], p.stderr))
                self.assertIn("E_GATE", t["stops"][0]["detail"], key)
                err = open(os.path.join(w["slot_root"], "stderr.log")).read()
                self.assertNotIn("N2M", err, "no exec happened")
                self.assertEqual(H.no_n2_cgroups(), [])
            finally:
                scr.cleanup()

    def test_M03_L4_child_exception_os_exit_E_INTERNAL(self):
        for step in (1, 3, 6):
            scr = H.Scratch("m03exc")
            try:
                w = H.make_world(scr)
                res, p = H.run_driver(scr, H.spec_path(w, {"inject": {"raise_at_step": step}}))
                t = res["terminal"]
                self.assertEqual(t["status"], "stopped", (step, t["stops"], p.stderr))
                name = self.exit_name(t)
                self.assertEqual(name, "E_INTERNAL", (step, t["stops"], t.get("pre_gate_exit")))
                self.assertEqual(p.stdout.decode().count("DONE"), 1, "the child never returned into controller code")
                self.assertEqual(H.no_n2_cgroups(), [])
            finally:
                scr.cleanup()


class M03Join(Base):
    def test_M03_L5_joins_ordered_and_verified(self):
        name = "n2-ut-" + os.urandom(4).hex()
        lay = cgmod.Layout(name, cgmod.controller_cgroup_paths(RealFs()))
        fs = FaultFs(RealFs(), {})
        cgmod.create_all(lay, fs)
        import subprocess
        child = subprocess.Popen([H.PY, "-c", "import time; time.sleep(60)"])
        try:
            cgmod.set_limits(lay, 64 << 20, 16, fs)
            joined = cgmod.join_and_verify(lay, child.pid, fs)
            self.assertEqual(joined, ["v2", "memory", "pids"])
            order = [w[1] for w in fs.log if w[0] == "write" and w[1].endswith("cgroup.procs")]
            self.assertEqual([("unified" in o, "/memory/" in o, "/pids/" in o) for o in order],
                             [(True, False, False), (False, True, False), (False, False, True)])
            self.assertEqual(cgmod.members(lay, fs), [child.pid])
            self.assertTrue(cgmod.proc_cgroup_matches(lay, child.pid, fs))
            for tid in pidfd.thread_ids(fs, child.pid):
                self.assertTrue(cgmod.proc_cgroup_matches(lay, child.pid, fs, tid=tid))
        finally:
            child.kill()
            child.wait()
            from n2h.controller import cleanup_test_orphan
            self.assertTrue(cleanup_test_orphan(lay))
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M03_L5_join_failure_R1_paths(self):
        # (a) write to the FIRST cgroup of the set fails: child is not a member -> S0 path
        w, res, p = self.drive(extra={"fs_faults": {"write|unified": "INJECTED_E_JOIN"}}, tag="a")
        t = res["terminal"]
        self.assertEqual(t["stops"][0]["code"], "join_failed")
        self.assertEqual(t["stops"][0]["injected"], "INJECTED_E_JOIN")
        self.assertEqual(t["r1"]["state"], "S0")
        self.assertEqual(t["r1"]["killed_status"]["signal"], 9)
        self.assertIs(t["post"]["cgroups_removed"], True)
        self.assertNotIn("N2M", open(os.path.join(w["slot_root"], "stderr.log")).read())
        # (b) first join succeeded but verification fails: S1 path, same SIGKILL through the S0 pidfd
        scr = H.Scratch("m03r1b")
        try:
            w2 = H.make_world(scr)
            res2, p2 = H.run_driver(scr, H.spec_path(w2, {"fs_faults": {"read|re:^/proc/[0-9]+/cgroup$": "INJECTED_E_VERIFY"}}))
            t2 = res2["terminal"]
            self.assertEqual(t2["stops"][0]["code"], "join_verify_failed", (t2["stops"], p2.stderr))
            self.assertEqual(t2["r1"]["state"], "unknown", "state read itself is faulted: both paths end in the same pidfd SIGKILL")
            self.assertEqual(t2["r1"]["killed_status"]["signal"], 9)
            self.assertIn("killed_status", t2["r1"])
            self.assertIs(t2["post"]["cgroups_removed"], True)
        finally:
            scr.cleanup()
        # (c) write to the second cgroup fails after the first succeeded
        scr = H.Scratch("m03r1c")
        try:
            w3 = H.make_world(scr)
            res3, p3 = H.run_driver(scr, H.spec_path(w3, {"fs_faults": {"write|re:/memory/.*cgroup\\.procs$": "INJECTED_E_JOIN"}}))
            t3 = res3["terminal"]
            self.assertEqual(t3["stops"][0]["code"], "join_failed")
            self.assertEqual(t3["r1"]["state"], "S1", "joined the first cgroup: S1 path")
            self.assertIs(t3["post"]["cgroups_removed"], True)
        finally:
            scr.cleanup()
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M03_L5_pidfd_open_failure_stop(self):
        # at S0 (right after fork): gate is closed so the child exits without exec, reaped by exact pid
        w, res, p = self.drive(extra={"faults": ["INJECTED_E_PIDFD_OPEN_S0"]}, tag="s0")
        t = res["terminal"]
        self.assertEqual(t["stops"][0]["code"], "pidfd_open_failed_s0")
        self.assertEqual(t["stops"][0]["injected"], "INJECTED_E_PIDFD_OPEN_S0")
        pid = int(t["stops"][0]["detail"].split("child pid ")[1].split()[0])
        self.assertTrue(H.pid_gone(pid), "child gone (EOF on gate, then reaped)")
        self.assertNotIn("N2M", open(os.path.join(w["slot_root"], "stderr.log")).read())
        self.assertEqual(H.no_n2_cgroups(), [])
        # member verification (ns-init discovery) fails: STOP and clean termination, never an unverified kill
        scr = H.Scratch("m03pf")
        try:
            w2 = H.make_world(scr, work={"mode": "sleep", "seconds": 30})
            res2, p2 = H.run_driver(scr, H.spec_path(w2, {"faults": ["INJECTED_E_PIDFD_OPEN"]}))
            t2 = res2["terminal"]
            self.assertEqual(t2["status"], "stopped", (t2["stops"], p2.stderr))
            self.assertEqual(t2["stops"][0]["code"], "pidfd_open_failed")
            self.assertEqual(t2["stops"][0]["injected"], "INJECTED_E_PIDFD_OPEN")
            self.assertEqual(H.no_n2_cgroups(), [], t2["post"])
        finally:
            scr.cleanup()


class M03Records(Base):
    def test_M03_L6_launch_record_and_k_samples_before_release(self):
        w, res, p = self.drive(work={"mode": "sleep", "seconds": 0.5})
        t = res["terminal"]
        self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
        key = L.attempt_key(w["approval_sha"], "H0")
        led = L.Ledger(w["control_root"])
        lr = led.read("launch", key)
        self.assertEqual(lr["gate_state"], "closed")
        self.assertTrue(lr["pidfd_open"])
        self.assertEqual(set(lr["cgroup_dirs"]), {"v2", "memory", "pids"})
        for h, d in lr["cgroup_dirs"].items():
            self.assertTrue(d["path"].endswith("n2-" + key[:20]))
            self.assertGreater(d["ino"], 0)
        self.assertEqual(lr["subreaper_at_fork"], 1, "PR_SET_CHILD_SUBREAPER was set before fork (note 4)")
        self.assertTrue(lr["pre_release_snapshot"]["cmdline_equals_controller"], "child had not exec'd when the launch record was made")
        rows = [json.loads(line) for line in open(os.path.join(w["slot_root"], "telemetry.jsonl"))]
        k = w["policy"]["fields"]["release_sample_count_k"]["value"]
        rel = t["timeline_ns"]["released_S2"]
        pre = [r for r in rows if r["mono_ns"] < rel]
        self.assertGreaterEqual(len(pre), k)
        for r in pre[:k]:
            self.assertEqual(r["listing"], [lr["pid"]])
            self.assertEqual(list(r["members"]), [str(lr["pid"])])
        self.assertLess(t["timeline_ns"]["assigned_S1"], pre[0]["mono_ns"])
        self.assertEqual(os.stat(os.path.join(w["control_root"], "launch", key + ".json")).st_mode & 0o777, 0o444)

    def test_M03_L6_telemetry_failure_at_S1_cleanup(self):
        w, res, p = self.drive(extra={"faults": ["INJECTED_E_TELEMETRY_S1"]})
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "s1_failure")
        self.assertEqual(t["stops"][0]["injected"], "INJECTED_E_TELEMETRY_S1")
        self.assertEqual(t["s1_cleanup"]["killed_status"]["signal"], 9)
        self.assertNotIn("N2M", open(os.path.join(w["slot_root"], "stderr.log")).read(), "never released, never exec'd")
        self.assertIs(t["post"]["cgroups_removed"], True)
        key = L.attempt_key(w["approval_sha"], "H0")
        self.assertEqual(L.Ledger(w["control_root"]).state(key), "terminal")
        self.assertEqual(H.no_n2_cgroups(), [])


class Notes(Base):
    def test_M03_note1_cmdline_index_and_argv_wording(self):
        self.assertEqual(stubsrc.CMDLINE_STUB_INDEX, 6)
        doc = stubsrc.__doc__
        self.assertIn("argv integrity only", doc)
        self.assertIn("sys.argv", doc)
        self.assertIn("FIXED", doc)
        # an extra interpreter argument shifts the index: the stub refuses instead of hashing something else
        rc, err, ev = stubrun.run_stub(self.scr, pre_args=("-X", "utf8"))
        self.assertEqual(rc, 81, err)
        self.assertEqual(ev[0][1]["name"], "STUBHASH")
        rc, err, ev = stubrun.run_stub(self.scr)
        self.assertEqual(rc, 0, err)

    def test_M03_note2_stub_ascii_size_no_secrets(self):
        self.assertTrue(stubsrc.STUB.isascii())
        self.assertTrue(stubsrc.WORKLOAD.isascii())
        self.assertLess(len(stubsrc.STUB.encode("ascii")), 128 * 1024)
        exp, env = stubrun.build_expected(self.scr)
        blob = json.dumps(exp)
        self.assertLess(len(blob), 64 * 1024)
        self.assertEqual(set(exp["env"]), set(launch.build_env("/h", "/t")))
        secret_like = [k for k in os.environ if any(s in k.upper() for s in ("TOKEN", "KEY", "SECRET", "PASS", "PROXY", "CRED"))]
        for k in secret_like:
            self.assertNotIn(k, blob)
            v = os.environ[k]
            if len(v) >= 8:
                self.assertNotIn(v, blob)
        self.assertIn("nonce is framing", open(os.path.join(H.HARNESS, "n2h", "stubsrc.py")).read())

    def test_M03_note3_fcntl_allowlisted_one_discarded(self):
        self.assertIn("fcntl", STDLIB_ALLOWLIST)
        alive, discarded, raw = launch.fd_snapshot()
        self.assertEqual(discarded, 1)
        self.assertTrue(launch.fd_assert_ok(alive, 1, alive))
        self.assertFalse(launch.fd_assert_ok(alive, 0, alive))
        self.assertFalse(launch.fd_assert_ok(alive, 2, alive))

    def test_M03_note4_subreaper_set_before_fork(self):
        src = open(os.path.join(H.HARNESS, "n2h", "controller.py")).read()
        self.assertLess(src.index("native.set_subreaper()"), src.index("self.gated = fork_gated("))
        import subprocess
        code = ("import sys; sys.path.insert(0, %r)\nfrom n2h import native\n"
                "a = native.get_subreaper(); native.set_subreaper(); print(a, native.get_subreaper())\n") % H.HARNESS
        r = subprocess.run([H.PY, "-I", "-S", "-B", "-c", code], capture_output=True, timeout=30)
        self.assertEqual(r.stdout.split(), [b"0", b"1"], r.stderr)
        w, res, p = self.drive()
        key = L.attempt_key(w["approval_sha"], "H0")
        self.assertEqual(L.Ledger(w["control_root"]).read("launch", key)["subreaper_at_fork"], 1)

    def test_M03_note4_static_no_wait_any(self):
        pkg = os.path.join(H.HARNESS, "n2h")
        for fn in os.listdir(pkg):
            if not fn.endswith(".py"):
                continue
            src = open(os.path.join(pkg, fn)).read()
            for bad in ("os.wait()", "os.waitpid(-1", "os.wait3(", "P_ALL", "os.waitid(os.P_ALL", "waitpid(0"):
                self.assertNotIn(bad, src, "%s uses %s" % (fn, bad))
        src = open(os.path.join(pkg, "pidfd.py")).read()
        self.assertIn("os.wait4(pid, 0)", src)
        self.assertIn("os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOWAIT)", src)

    def test_M03_note5_pidns_dev_ino_and_reconfirm(self):
        import subprocess
        procs = [subprocess.Popen([H.PY, "-c", "import time; time.sleep(60)"]) for _ in range(3)]
        try:
            fs = RealFs()
            lay = cgmod.Layout("n2-note5", cgmod.controller_cgroup_paths(fs))
            ms = [pidfd.open_verified(p.pid, lay, fs, "member", cg_check=False) for p in procs]
            nsi = ms[0]
            nsi.role = "ns_init"
            d, i = nsi.pidns
            ms[1].pidns = (d, i + 1)       # inode differs
            ms[2].pidns = (d + 1, i)       # device differs, inode equal
            for m in ms[1:]:
                with self.assertRaises(ContainmentStop) as cm:
                    pidfd.send(m, signal.SIGKILL, ns_init=nsi)
                self.assertEqual(cm.exception.code, "member_pidns_mismatch_not_signalled")
            self.assertIsNone(procs[1].poll())
            self.assertIsNone(procs[2].poll())
            ms[1].pidns = nsi.pidns
            calls = []
            real = signal.pidfd_send_signal
            signal.pidfd_send_signal = lambda fd, sig, *a: (calls.append(int(sig)), real(fd, sig, *a))[1]
            try:
                self.assertTrue(pidfd.send(ms[1], signal.SIGKILL, ns_init=nsi))
            finally:
                signal.pidfd_send_signal = real
            self.assertEqual(calls, [0, int(signal.SIGKILL)], "re-confirmed with signal 0 immediately before the real signal")
            procs[1].wait(timeout=5)
            for m in ms:
                m.close()
        finally:
            for p in procs:
                p.kill()
                p.wait()
        _ = native


if __name__ == "__main__":
    unittest.main()
