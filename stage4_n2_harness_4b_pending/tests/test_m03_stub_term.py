"""M03-L7..L12: stub self-check refusals, termination paths, site equivalence, unrelated-process safety,
launch failures. Real forks happen in driver processes only."""
import json
import os
import re
import subprocess
import unittest

from n2h import launch, stubsrc
from n2h import ledger as L
from tests import helpers as H
from tests import stubrun


class Base(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:30])
        self.sleepers = None

    def tearDown(self):
        if self.sleepers:
            self.sleepers.close()
        self.scr.cleanup()

    def drive(self, world=None, extra=None, tag="run", **world_kw):
        w = world or H.make_world(self.scr, **world_kw)
        res, p = H.run_driver(self.scr, H.spec_path(w, extra), tag=tag)
        return w, res, p

    def stderr_log(self, w):
        return open(os.path.join(w["slot_root"], "stderr.log")).read()


class L7Refusals(Base):
    def refusal(self, extra, name, code, **kw):
        w, res, p = self.drive(extra=extra, **kw)
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "child_refused_before_ready", t["stops"])
        self.assertIn(name, t["stops"][0]["detail"])
        err = self.stderr_log(w)
        self.assertIn("refuse code=%d name=%s" % (code, name), err)
        self.assertNotIn(" ready ", err, "refused before any payload/site step")
        self.assertNotIn("selfcheck_ok", err)
        self.assertIs(t["post"]["cgroups_removed"], True)
        self.assertEqual(H.no_n2_cgroups(), [])
        return w, t

    def test_M03_L7_stub_refusal_wrong_cgroup(self):
        self.refusal({"expected_patch": {"cgroups": {"memory": "/n2-not-the-attempt", "pids": "/n2-x"}}}, "CG", 83)

    def test_M03_L7_stub_refusal_nspid_field_count(self):
        self.refusal({"expected_patch": {"ctrl_nspid_fields": 2}}, "NSPID", 84)

    def test_M03_L7_stub_refusal_nspid_last_not_1(self):
        w, t = self.refusal({"expected_patch": {"nspid_last": 2}}, "NSPID", 84)
        self.assertIn("last_field", self.stderr_log(w))

    def test_M03_L7_stub_refusal_no_pdeathsig(self):
        w, t = self.refusal({"inject": {"no_kill_child": True}}, "PDEATHSIG", 85)
        self.assertIn("pdeathsig=0", self.stderr_log(w))

    def test_M03_L7_stub_refusal_env_differs(self):
        w, t = self.refusal({"env_extra_actual": {"N2_NEUTRAL_INJECTED_VAR": "1"}}, "ENV", 87)
        self.assertIn("N2_NEUTRAL_INJECTED_VAR", self.stderr_log(w))

    def test_M03_L7_stub_refusal_exe_identity_or_hash(self):
        interp = launch.verify_binary(H.PY)
        bad_hash = {"path": H.PY, "ident": interp["ident"], "sha": "0" * 64}
        self.refusal({"expected_patch": {"interp": bad_hash}}, "EXE", 82)
        scr = H.Scratch("l7swap")
        try:
            w = H.make_world(scr)
            res, p = H.run_driver(scr, H.spec_path(w, {"swap_symlink_after_verify": {"link": w["link"], "to": "/usr/bin/python3.12"}}))
            t = res["terminal"]
            self.assertEqual(t["stops"][0]["code"], "child_refused_before_ready", (t["stops"], p.stderr))
            self.assertIn("EXE", t["stops"][0]["detail"])
            err = open(os.path.join(w["slot_root"], "stderr.log")).read()
            self.assertIn("name=EXE", err)
            self.assertIn("identity_differs", err, "TOCTOU at the unshare->python hop: a substituted interpreter is caught by identity")
            self.assertEqual(H.no_n2_cgroups(), [])
        finally:
            scr.cleanup()

    def test_M03_L8_stub_only_fds_0_2(self):
        w, res, p = self.drive()
        t = res["terminal"]
        self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
        self.assertEqual(self.stderr_log(w).count("selfcheck_ok"), 1)
        rc, err, ev = stubrun.run_stub(self.scr, extra_fd=True)
        self.assertEqual(rc, 88, err)
        self.assertEqual(ev[0][0], "refuse")
        self.assertEqual(ev[0][1]["name"], "FD")
        rc, err, ev = stubrun.run_stub(self.scr)
        self.assertEqual(rc, 0, err)


class Termination(Base):
    def run_term(self, work, policy_over, faults=()):
        self.sleepers = H.Sleepers(3)
        w, res, p = self.drive(work=work, policy_over=policy_over, extra={"faults": list(faults)})
        t = res["terminal"]
        self.assertTrue(self.sleepers.all_alive(), "unrelated sleepers adjacent in PID space must survive")
        return w, t, p

    def test_M03_L9_graceful_path(self):
        w, t, p = self.run_term({"mode": "sleep", "seconds": 60}, {"per_slot_elapsed_cap_s": 1})
        tm = t["termination"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertTrue(tm["graceful"])
        self.assertEqual([s["step"] for s in tm["steps"]], ["sigterm_ns_init", "graceful_wait"])
        self.assertTrue(tm["emptied"])
        self.assertEqual(tm["pids_current"], 0)
        self.assertEqual(tm["zombies"], [])
        self.assertEqual(tm["reap"]["direct"]["status"], {"kind": "exit", "code": 0, "raw": 0})
        self.assertIn("sigterm", self.stderr_log(w))
        self.assertIs(t["post"]["cgroups_removed"], True)

    def test_M03_L9_hard_path_option_a(self):
        w, t, p = self.run_term({"mode": "ignore_sigterm", "seconds": 60},
                                {"per_slot_elapsed_cap_s": 1, "graceful_wait_s": 1})
        tm = t["termination"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertFalse(tm["graceful"])
        names = [s["step"] for s in tm["steps"]]
        self.assertEqual(names[:4], ["sigterm_ns_init", "graceful_wait", "sigkill_ns_init", "wait_unshare_exit"], names)
        self.assertTrue([s for s in tm["steps"] if s["step"] == "wait_unshare_exit"][0]["exited"],
                        "unshare exited by itself after reaping ns-init: no SIGKILL to unshare needed")
        self.assertNotIn("sigkill_direct", names)
        self.assertFalse(tm["used_cgroup_kill"])
        self.assertTrue(tm["emptied"])
        self.assertEqual(tm["pids_current"], 0)
        self.assertEqual(tm["zombies"], [])
        self.assertEqual(tm["unowned"], [])
        d = tm["reap"]["direct"]
        self.assertTrue(d["reaped_by_controller"])
        self.assertIn(d["status"]["kind"], ("exit", "signal"))
        self.assertIs(t["post"]["cgroups_removed"], True)

    def test_M03_L9_aplus_cgroup_kill_backstop(self):
        w, t, p = self.run_term({"mode": "ignore_sigterm", "seconds": 60},
                                {"per_slot_elapsed_cap_s": 1, "graceful_wait_s": 1},
                                faults=["INJECTED_E_SIGKILL_VIA_FD"])
        tm = t["termination"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        names = [s["step"] for s in tm["steps"]]
        self.assertIn("sigkill_ns_init_fault", names)
        self.assertIn("cgroup_kill_written", names)
        self.assertTrue(tm["used_cgroup_kill"])
        self.assertEqual(tm["injected"], ["INJECTED_E_SIGKILL_VIA_FD"])
        self.assertTrue(tm["emptied"], tm)
        self.assertEqual(tm["pids_current"], 0)
        self.assertEqual(tm["zombies"], [])
        self.assertEqual(tm["unowned"], [])
        self.assertTrue(tm["reap"]["direct"]["reaped_by_controller"])
        self.assertIs(t["post"]["cgroups_removed"], True)

    def test_M03_L10_site_equivalence(self):
        probe = ("import sys, json\nimport site\n%s\nprint(json.dumps({'path': sys.path, 'prefix': sys.prefix, "
                 "'flags': [sys.flags.isolated, sys.flags.dont_write_bytecode, sys.flags.ignore_environment, sys.flags.safe_path], "
                 "'user_site': site.ENABLE_USER_SITE, 'distutils_hack': '_distutils_hack' in sys.modules, "
                 "'uno_pth': any('uno' in str(p) for p in sys.path)}))")
        def run(args, extra):
            r = subprocess.run([H.PY] + args + ["-c", probe % extra], capture_output=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)
        normal = run(["-I", "-B"], "")
        s_only = run(["-I", "-S", "-B"], "")
        s_main = run(["-I", "-S", "-B"], "site.main()")
        self.assertEqual(s_main["path"], normal["path"], "-S plus site.main() equals a normal start")
        self.assertEqual(s_main["prefix"], normal["prefix"])
        self.assertEqual(s_main["flags"], normal["flags"])
        self.assertEqual(s_main["user_site"], normal["user_site"])
        self.assertFalse(s_only["distutils_hack"], "no system .pth runs in the controller/stub start")
        self.assertNotIn("dist-packages", " ".join(s_only["path"]))
        self.assertTrue(normal["distutils_hack"], "control: a normal start does run the system distutils-precedence.pth")
        self.assertIn("site_main", stubsrc.STUB)
        # 4a runs the stub with site_main False (no venv exists yet: D-9): the .pth hazard is avoided, not hidden
        exp, _ = stubrun.build_expected(self.scr)
        self.assertIs(exp["site_main"], False)

    def test_M03_L11_unrelated_sleeper_survives(self):
        self.sleepers = H.Sleepers(4)
        before = self.sleepers.pids()
        flows = [
            ({"mode": "sleep", "seconds": 0.3}, {}, ()),
            ({"mode": "sleep", "seconds": 60}, {"per_slot_elapsed_cap_s": 1}, ()),
            ({"mode": "ignore_sigterm", "seconds": 60}, {"per_slot_elapsed_cap_s": 1, "graceful_wait_s": 1}, ()),
            ({"mode": "ignore_sigterm", "seconds": 60}, {"per_slot_elapsed_cap_s": 1, "graceful_wait_s": 1},
             ("INJECTED_E_SIGKILL_VIA_FD",)),
            ({"mode": "spawn", "n": 3, "seconds": 60}, {"per_slot_elapsed_cap_s": 1, "graceful_wait_s": 1}, ()),
        ]
        for i, (work, pol, faults) in enumerate(flows):
            scr = H.Scratch("l11-%d" % i)
            try:
                w = H.make_world(scr, work=work, policy_over=pol)
                res, p = H.run_driver(scr, H.spec_path(w, {"faults": list(faults)}))
                self.assertIn("terminal", res, (res, p.stderr))
                self.assertTrue(self.sleepers.all_alive(), "flow %d killed an unrelated process" % i)
                self.assertEqual(H.no_n2_cgroups(), [], "flow %d" % i)
            finally:
                scr.cleanup()
        self.assertEqual(before, self.sleepers.pids())

    def test_M03_L11_static_no_raw_kill(self):
        pkg = os.path.join(H.HARNESS, "n2h")
        allowed_sig_files = {"pidfd.py", "controller.py"}
        for fn in sorted(os.listdir(pkg)):
            if not fn.endswith(".py"):
                continue
            src = open(os.path.join(pkg, fn)).read()
            code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
            code = re.sub(r'"""[\s\S]*?"""', "", code)
            for bad in ("os.kill(", "os.killpg(", "killpg", "pkill", "pgrep", "os.system(", "subprocess", "os.popen("):
                self.assertNotIn(bad, code if fn != "stubsrc.py" else "", "%s: %s" % (fn, bad))
            if "pidfd_send_signal" in code:
                self.assertIn(fn, allowed_sig_files, fn)
            if "cgroup.kill" in code:
                self.assertEqual(fn, "cg.py")
        # the only raw-pid operations are read-only /proc reads and exact-pid wait on owned zombies
        tsrc = open(os.path.join(pkg, "stubsrc.py")).read()
        self.assertIn("os.fork()", tsrc)  # workload-side forks (inside the namespace) are the only forks


class LaunchFailures(Base):
    def test_M03_L12_exec_fd_hash_mismatch_refused(self):
        ub = H.copy_binary(self.scr, H.UNSHARE, "unshare")
        w = H.make_world(self.scr)
        w["unshare"] = ub
        w, res, p = self.drive(world=w, extra={"rewrite_exec_at": {"hook": "at_cgroups_ready", "path": ub}})
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "exec_fd_hash_mismatch")
        self.assertFalse(os.path.exists(os.path.join(w["slot_root"], "stdout.log")), "refused before the fork")
        key = L.attempt_key(w["approval_sha"], "H0")
        self.assertEqual(L.Ledger(w["control_root"]).state(key), "terminal", "refusal still consumes the attempt")
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M03_L12_unshare_exec_failure(self):
        bad = H.copy_binary(self.scr, "/etc/hostname", "badunshare")  # a regular, executable, non-ELF file
        w = H.make_world(self.scr)
        w["unshare"] = bad
        w, res, p = self.drive(world=w)
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "child_refused_before_ready")
        self.assertIn("E_INTERNAL", t["stops"][0]["detail"], "execve failure inside the child -> os._exit(E_INTERNAL)")
        self.assertIs(t["post"]["cgroups_removed"], True)
        key = L.attempt_key(w["approval_sha"], "H0")
        self.assertEqual(L.Ledger(w["control_root"]).state(key), "terminal")

    def test_M03_L12_newpid_eperm_injected(self):
        w, res, p = self.drive(extra={"inject": {"drop_sysadmin": True}})
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "child_refused_before_ready")
        self.assertIn("'code': 1", t["stops"][0]["detail"], "unshare itself failed (EPERM on CLONE_NEWPID)")
        err = open(os.path.join(w["slot_root"], "stderr.log")).read()
        self.assertIn("unshare failed", err)
        self.assertIn("Operation not permitted", err)
        self.assertNotIn("N2M", err)
        self.assertIs(t["post"]["cgroups_removed"], True)
        key = L.attempt_key(w["approval_sha"], "H0")
        self.assertEqual(L.Ledger(w["control_root"]).state(key), "terminal")
        self.assertEqual(H.no_n2_cgroups(), [])


if __name__ == "__main__":
    unittest.main()
