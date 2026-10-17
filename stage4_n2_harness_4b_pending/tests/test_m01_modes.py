"""M01: mode isolation; run refuses before any child/cgroup/attempt exists."""
import json
import os
import shutil
import subprocess
import sys
import unittest

from n2h import cli
from n2h import ledger as ledger_mod
from n2h import roots as roots_mod
from n2h.errors import RefusedRun, RootsStop
from tests import helpers as H

EVENTS = []
ACTIVE = [False]
OPENS = []


def _hook(event, args):
    if not ACTIVE[0]:
        return
    if event in ("os.fork", "os.forkpty", "subprocess.Popen", "os.posix_spawn", "os.system", "os.exec"):
        EVENTS.append(event)
    if event == "open":
        OPENS.append(str(args[0]))


sys.addaudithook(_hook)


class Watch:
    def __enter__(self):
        del EVENTS[:]
        del OPENS[:]
        ACTIVE[0] = True
        return self

    def __exit__(self, *a):
        ACTIVE[0] = False


def gate(world, **over):
    opts = {"control-root": world["control_root"], "approval": world["paths"]["approval"],
            "policy": world["paths"]["policy"], "scope": world["paths"]["scope"],
            "selftest-report": world["paths"]["selftest"]}
    opts.update(over)
    return opts


class M01(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch("m01")
        self.w = H.make_world(self.scr)

    def tearDown(self):
        self.scr.cleanup()

    def refused(self, opts, code):
        with Watch():
            with self.assertRaises(RefusedRun) as cm:
                cli.run_gate(opts)
        self.assertEqual(cm.exception.code, code)
        self.assertEqual(EVENTS, [], "no process may be created on refusal")
        self.assertEqual(os.listdir(os.path.join(self.w["control_root"], "attempts")), [], "no attempt record")
        self.assertEqual(H.no_n2_cgroups(), [], "no cgroup may exist")

    def test_M01_mode_selftest_roots_confined(self):
        base = self.scr.root
        with self.assertRaises(RootsStop):
            roots_mod.check_root("/tmp", base)  # outside the private scratch base
        with self.assertRaises(RootsStop):
            roots_mod.check_root(H.REPO, base, extra_forbidden=[H.REPO])
        ok = roots_mod.check_root(self.scr.mkdir("sub"), base)
        self.assertEqual(ok["path"], self.scr.path("sub"))

    def test_M01_mode_prepare_metadata_only_no_content_open(self):
        data = self.scr.mkdir("fixture_primary")
        for n in ("primary.csv", "population.csv", "cache.npz"):
            with open(os.path.join(data, n), "w") as f:
                f.write("SYNTHETIC_FIXTURE_CONTENT_NOT_TO_BE_READ\n")
        ctrl, logs = self.scr.mkdir("empty_control"), self.scr.mkdir("logs")
        cfg = {"scratch_base": self.scr.root, "control_root": ctrl, "log_root": logs, "repo_root": H.REPO,
               "binaries": {"unshare": H.UNSHARE, "interpreter": H.PY, "setpriv": H.SETPRIV},
               "metadata_paths": [os.path.join(data, n) for n in os.listdir(data)],
               "policy": self.w["policy"]}
        with Watch():
            rec = cli.prepare(cfg)
        under = [p for p in OPENS if p.startswith(data)]
        self.assertEqual(under, [], "prepare must not open fixture content paths")
        self.assertEqual(rec["content_reads"], 0)
        self.assertEqual(rec["swap_total_bytes"], 0)
        self.assertEqual(set(rec["metadata_stat_only"]), set(cfg["metadata_paths"]))
        self.assertIn("memory", rec["controller_cgroup_paths"])
        self.assertEqual(os.listdir(ctrl), [], "prepare creates nothing in the control root")
        self.assertEqual(EVENTS, [])

    def test_M01_prepare_refuses_national_paths(self):
        ctrl, logs = self.scr.mkdir("c2"), self.scr.mkdir("l2")
        base = {"scratch_base": self.scr.root, "control_root": ctrl, "log_root": logs, "repo_root": H.REPO,
                "binaries": {"unshare": H.UNSHARE, "interpreter": H.PY, "setpriv": H.SETPRIV}}
        with self.assertRaises(RootsStop):
            cli.prepare(dict(base, metadata_paths=[H.REPO + "/data/analysis/nh_district_travel_time_summary.csv"]))
        with self.assertRaises(RootsStop):
            cli.prepare(dict(base, control_root=H.REPO + "/data"))
        for forbidden in ("/proc/self", "/sys/fs", "/dev/shm"):
            with self.assertRaises(RootsStop):
                roots_mod.reject_forbidden(forbidden)
        # static: harness code carries no national content path literals
        pkg = os.path.join(H.HARNESS, "n2h")
        for fn in os.listdir(pkg):
            if fn.endswith(".py"):
                src = open(os.path.join(pkg, fn)).read()
                for needle in ("data/analysis", "nh_district_travel", "outputs/cache", "canonical_nh_graph"):
                    if fn == "transfer.py" and needle == "nh_district_travel":
                        continue  # 4b: the transfer validator names the producer's relative file names INSIDE a scratch transfer root
                    self.assertNotIn(needle, src, fn)

    def test_M01_run_refuses_without_receipts(self):
        with Watch():
            with self.assertRaises(RefusedRun) as cm:
                cli.run_gate({})
        self.assertEqual(cm.exception.code, "run_inputs_missing")
        self.assertEqual(EVENTS, [])

    def test_M01_run_refuses_missing_approval(self):
        os.unlink(self.w["paths"]["approval"])
        self.refused(gate(self.w), "receipt_missing")

    def test_M01_run_refuses_changed_approval_hash(self):
        p = self.w["paths"]["approval"]
        raw = open(p, "rb").read()
        os.chmod(p, 0o644)
        open(p, "wb").write(raw.replace(b"SYNTHETIC_MOCK_APPROVAL_NOT_REAL", b"SYNTHETIC_MOCK_APPROVAL_NOT_REAX"))
        self.refused(gate(self.w), "receipt_hash_invalid")

    def test_M01_run_refuses_changed_policy(self):
        other = ledger_mod.make_receipt({"kind": "policy", "policy": self.w["policy"], "synthetic_mock": True, "x": 1})
        p = self.w["paths"]["policy"]
        os.chmod(p, 0o644)
        open(p, "wb").write(ledger_mod.canonical(other))
        self.refused(gate(self.w), "binding_policy_mismatch")

    def test_M01_run_refuses_changed_scope(self):
        other = ledger_mod.make_receipt({"kind": "scope", "read_scope": ["x"], "write_scope": [], "synthetic_mock": True})
        p = self.w["paths"]["scope"]
        os.chmod(p, 0o644)
        open(p, "wb").write(ledger_mod.canonical(other))
        self.refused(gate(self.w), "binding_scope_mismatch")

    def test_M01_run_refuses_changed_harness_hash(self):
        w = self.w
        for what in ("package_module", "entry_script"):
            root = self.scr.path("copy-" + what)
            shutil.copytree(os.path.join(H.HARNESS, "n2h"), os.path.join(root, "n2h"), ignore=shutil.ignore_patterns("__pycache__"))
            for name in ledger_mod.ENTRY_SCRIPTS:
                shutil.copy(os.path.join(H.HARNESS, name), os.path.join(root, name))
            shutil.copytree(os.path.join(H.HARNESS, "n2h4b"), os.path.join(root, "n2h4b"), ignore=shutil.ignore_patterns("__pycache__"))
            copy = os.path.join(root, "n2h")
            # control: an unmodified copy has the same harness hash as the approved one
            self.assertEqual(ledger_mod.harness_hash(copy), w["harness_sha"])
            target = os.path.join(copy, "errors.py") if what == "package_module" else os.path.join(root, "driver_entry.py")
            with open(target, "a") as f:
                f.write("# changed after approval\n")
            with Watch():
                with self.assertRaises(RefusedRun) as cm:
                    ledger_mod.gate_run(w["control_root"], w["paths"]["approval"], w["paths"]["policy"],
                                        w["paths"]["scope"], w["paths"]["selftest"], copy)
            self.assertEqual(cm.exception.code, "binding_harness_mismatch", what)
            self.assertEqual(EVENTS, [])

    def test_M01_interpreter_flags_enforced(self):
        script = os.path.join(H.HARNESS, "n2_harness.py")
        for flags, ok in ((["-I", "-S", "-B", "-u"], True), (["-I", "-S", "-B", "-u", "-O"], False), (["-S", "-B", "-u"], False),
                          (["-I", "-S", "-u"], False), (["-I", "-B", "-u"], False)):  # last: no -S for a controller mode
            p = subprocess.run([H.PY, "-X", "pycache_prefix=" + self.scr.path("pyc")] + flags + [script, "run"],
                               capture_output=True, timeout=30)
            if ok:
                self.assertEqual(p.returncode, 3, (flags, p.stderr))
                self.assertIn(b"run_inputs_missing", p.stderr)  # passed the flag check, reached the run gate
            else:
                self.assertEqual(p.returncode, 3, (flags, p.stderr))
                self.assertIn(b"bad_interpreter_flags", p.stderr)

    def test_M01_run_refuses_missing_ledger(self):
        os.unlink(os.path.join(self.w["control_root"], "ROOT.json"))
        self.refused(gate(self.w), "ledger_missing")
        self.refused(gate(self.w, **{"control-root": self.scr.path("nonexistent")}), "ledger_missing")

    def test_M01_run_refuses_unbound_receipts(self):
        # a second, individually valid world: its policy receipt does not bind to this approval
        scr2 = H.Scratch("m01b")
        try:
            w2 = H.make_world(scr2, policy_over={"attempt_pids_max": 49})
            self.refused(gate(self.w, policy=w2["paths"]["policy"]), "binding_policy_mismatch")
            self.refused(gate(self.w, **{"selftest-report": w2["paths"]["selftest"]}), "binding_selftest_mismatch")
        finally:
            scr2.cleanup()
        # wrong ledger root id / moved root
        scr3 = H.Scratch("m01c")
        try:
            w3 = H.make_world(scr3)
            self.refused(gate(self.w, **{"control-root": w3["control_root"]}), "ledger_root_id_mismatch")
        finally:
            scr3.cleanup()

    def test_M01_run_refuses_nonfinite_scope(self):
        for bad in (["/tmp/*"], ["relative/path"], ["/"], ["/a/../b"], ["/x/{a,b}"], ["/tmp/ok", 5]):
            scr = H.Scratch("m01s")
            try:
                w = H.make_world(scr, scope_write=bad)
                with Watch():
                    with self.assertRaises(RefusedRun) as cm:
                        cli.run_gate(gate(w))
                self.assertEqual(cm.exception.code, "scope_not_finite", bad)
                self.assertEqual(EVENTS, [])
            finally:
                scr.cleanup()
        scr = H.Scratch("m01t")
        try:
            w = H.make_world(scr, scope_read=[scr.root + "/inputs"], scope_write=[scr.root + "/out"])
            self.assertEqual(cli.run_gate(gate(w))["approval_sha"], w["approval_sha"])
        finally:
            scr.cleanup()

    def test_M01_run_refusal_creates_no_child_or_cgroup(self):
        # whole CLI path in a subprocess: refusal exit code, nothing created
        os.unlink(self.w["paths"]["scope"])
        args = [H.PY, "-I", "-S", "-B", "-u", os.path.join(H.HARNESS, "n2_harness.py"), "run",
                "--control-root", self.w["control_root"], "--approval", self.w["paths"]["approval"],
                "--policy", self.w["paths"]["policy"], "--scope", self.w["paths"]["scope"],
                "--selftest-report", self.w["paths"]["selftest"], "--slot", "H0",
                "--slot-root", self.w["slot_root"], "--scratch-base", self.scr.root, "--interpreter", self.w["link"]]
        p = subprocess.run(args, capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn(b"receipt_missing", p.stderr)
        self.assertEqual(os.listdir(os.path.join(self.w["control_root"], "attempts")), [])
        self.assertEqual(os.listdir(self.w["slot_root"]), [])
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M01_run_nonmock_valid_receipts_refused_no_approved_receipt(self):
        scr2 = H.Scratch("m01d")
        try:
            w2 = H.make_world(scr2, synthetic_mock=False)
            self.refused_other(w2, "no_approved_policy_scope_receipt")
        finally:
            scr2.cleanup()

    def refused_other(self, w2, code):
        with Watch():
            with self.assertRaises(RefusedRun) as cm:
                cli.run_gate(gate(w2))
        self.assertEqual(cm.exception.code, code)
        self.assertEqual(EVENTS, [])

    def test_M01_run_mock_receipts_accepted_by_gate(self):
        with Watch():
            g = cli.run_gate(gate(self.w))
        self.assertEqual(EVENTS, [])
        self.assertEqual(g["approval_sha"], self.w["approval_sha"])
        self.assertEqual(g["policy"]["label"], "SYNTHETIC_TEST_POLICY_NOT_APPROVED")
        self.assertIs(g["policy"]["approved"], False)
        json.dumps(g["policy"])


if __name__ == "__main__":
    unittest.main()
