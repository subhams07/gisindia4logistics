"""M10 phase witnesses / scalar retention / no authority from partial output; M11 repository preservation."""
import json
import os
import subprocess
import unittest

from n2h import controller, manifest
from n2h import ledger as L
from n2h import telemetry as tel
from n2h.errors import N2Stop, RefusedRun, RootsStop
from tests import helpers as H

MIB = 1 << 20


def git(*args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    return subprocess.run(["git", "--no-optional-locks", "-C", H.REPO] + list(args), capture_output=True, timeout=60, env=env)


class M10(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch("m10")

    def tearDown(self):
        self.scr.cleanup()

    def staged(self, declared, stages=None, secs=0.2, **kw):
        w = H.make_world(self.scr, work={"mode": "staged", "seconds": secs, "stages": []})
        for st in (stages or []):
            if st.get("write_mib"):
                st["path"] = os.path.join(w["slot_root"], st["name"] + ".out")
        w["work"]["stages"] = stages
        res, p = H.run_driver(self.scr, H.spec_path(w, dict({"declared_phases": declared}, **kw)))
        return w, res, p

    STAGES = [{"name": "setup", "alloc_mib": 8}, {"name": "compute", "alloc_mib": 8},
              {"name": "serialization", "write_mib": 4, "alloc_mib": 2}, {"name": "undeclared_extra", "alloc_mib": 1}]

    def test_M10_phase_witness_only_declared(self):
        w, res, p = self.staged(["setup", "compute", "serialization"], [dict(s) for s in self.STAGES])
        t = res["terminal"]
        self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
        names = [b["phase"] for b in t["phases"]["boundaries"]]
        self.assertEqual(names, ["setup", "compute", "serialization"])
        self.assertEqual(t["phases"]["undeclared"], ["undeclared_extra"])
        for b in t["phases"]["boundaries"]:
            self.assertEqual(set(b), {"phase", "child_mono_ns", "sample_seq", "cg_charged_peak_bytes", "cg_working_set_bytes"})
        # observed boundaries are marker events only: no value is attributed between boundaries
        self.assertNotIn("per_phase_cost", json.dumps(t))

    def test_M10_scalar_retention(self):
        rec = tel.PhaseRecorder(["a"])
        good = rec.scalar_witness({"seq": 3, "memory": {"cg_charged_peak_bytes": 1, "cg_working_set_bytes": 2}})
        self.assertTrue(tel.PhaseRecorder.assert_scalars(good))
        with self.assertRaises(N2Stop) as cm:
            tel.PhaseRecorder.assert_scalars({"geometry": [1, 2, 3]})
        self.assertEqual(cm.exception.code, "non_scalar_witness")
        with self.assertRaises(N2Stop):
            tel.PhaseRecorder.assert_scalars({"frame": {"a": 1}})
        with self.assertRaises(N2Stop):
            tel.PhaseRecorder.assert_scalars({"obj": object()})
        w, res, p = self.staged(["setup"], [{"name": "setup", "alloc_mib": 2}])
        for b in res["terminal"]["phases"]["boundaries"]:
            for v in b.values():
                self.assertIsInstance(v, (int, float, str, bool, type(None)))
        self.assertIsNone(rec.scalar_witness(None)["sample_seq"])

    def test_M10_peak_includes_setup_and_serialization(self):
        w, res, p = self.staged(["setup", "compute", "serialization"], [dict(s) for s in self.STAGES[:3]], secs=0.3)
        t = res["terminal"]
        self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
        peaks = [b["cg_charged_peak_bytes"] for b in t["phases"]["boundaries"]]
        self.assertEqual(peaks, sorted(peaks), "the charged peak is a high-water mark: it never decreases")
        final = t["peak"]["cg_charged_peak_bytes"]
        self.assertGreaterEqual(final, peaks[-1])
        self.assertGreaterEqual(final, 18 * MIB, "setup (8) + compute (8) + serialization (2) allocations are all inside the peak")
        self.assertIsNotNone(t["peak"]["conservative_bytes"])
        self.assertEqual(t["peak"]["conservative_bytes"], final + t["peak"]["resident_bytes_elsewhere"])
        # serialization ran inside the measured interval (its file exists and its phase marker precedes exit)
        self.assertEqual(os.path.getsize(os.path.join(w["slot_root"], "serialization.out")), 4 * MIB)
        for name in dir(tel.PhaseRecorder) + dir(controller.Attempt):
            self.assertNotIn("subtract", name.lower())
            self.assertNotIn("exclude_setup", name.lower())
        self.assertIn("NOT an upper bound", t["peak"]["label"])

    def test_M10_partial_output_grants_no_authority(self):
        # the run is cut short by policy; the partial output stays on disk but changes nothing
        scr2 = H.Scratch("m10b")
        try:
            w2 = H.make_world(scr2, work={"mode": "staged", "seconds": 60, "stages": [{"name": "serialization", "write_mib": 2, "path": ""}]},
                              policy_over={"per_slot_elapsed_cap_s": 1})
            w2["work"]["stages"][0]["path"] = os.path.join(w2["slot_root"], "serialization.out")
            res2, p2 = H.run_driver(scr2, H.spec_path(w2, {"require_receipt": True}))
            t2 = res2["terminal"]
            self.assertEqual(t2["status"], "stopped", (t2["stops"], p2.stderr))
            self.assertTrue(os.path.exists(w2["work"]["stages"][0]["path"]), "partial output exists")
            led = L.Ledger(w2["control_root"])
            self.assertFalse(led.can_dispatch_slot(w2["approval_sha"], "H1_12")[0])
            self.assertEqual(led.read("terminal", L.attempt_key(w2["approval_sha"], "H0"))["status"], "stopped")
        finally:
            scr2.cleanup()
        # leftovers and forgeries are not receipts: a pre-existing slot content refuses the start, and a
        # completion receipt without this attempt's nonce is rejected
        scr3 = H.Scratch("m10c")
        try:
            w3 = H.make_world(scr3)
            with open(os.path.join(w3["slot_root"], "completion_receipt.json"), "w") as f:
                f.write(json.dumps({"ok": True, "nonce": "forged"}))
            res3, _p3 = H.run_driver(scr3, H.spec_path(w3, {"require_receipt": True}))
            self.assertEqual(res3["refused"]["code"], "slot_root_not_empty")
            self.assertEqual(os.listdir(os.path.join(w3["control_root"], "attempts")), [])
            rp = os.path.join(scr3.root, "r.json")
            self.assertFalse(controller.verify_completion_receipt(rp, "n"))
            for body in ('{"ok": true, "nonce": "other"}', '{"ok": false, "nonce": "n"}', "not json", "[]"):
                with open(rp, "w") as f:
                    f.write(body)
                self.assertFalse(controller.verify_completion_receipt(rp, "n"), body)
                os.unlink(rp)
            with open(rp, "w") as f:
                f.write('{"ok": true, "nonce": "n"}')
            self.assertTrue(controller.verify_completion_receipt(rp, "n"))
            os.unlink(rp)
            os.symlink("/etc/hostname", rp)
            self.assertFalse(controller.verify_completion_receipt(rp, "n"))
        finally:
            scr3.cleanup()


class M11(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch("m11")

    def tearDown(self):
        self.scr.cleanup()

    def test_M11_repo_preserved(self):
        head0, st0 = git("rev-parse", "HEAD").stdout, git("status", "--porcelain", "--untracked-files=all").stdout
        self.assertTrue(head0.strip())
        w = H.make_world(self.scr)
        res, p = H.run_driver(self.scr, H.spec_path(w))
        self.assertEqual(res["terminal"]["status"], "success")
        self.assertEqual(git("rev-parse", "HEAD").stdout, head0)
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all").stdout, st0,
                         "a full harness run changed nothing in the repository")
        # static: no harness module writes anywhere near the repository (only reads via the guard lists)
        pkg = os.path.join(H.HARNESS, "n2h")
        for fn in os.listdir(pkg):
            if fn.endswith(".py"):
                self.assertNotIn("/home/user/gisindia4logistics", open(os.path.join(pkg, fn)).read(), fn)

    def test_M11_scratch_manifest_not_publication_manifest(self):
        f = self.scr.path("evidence.log")
        open(f, "w").write("x")
        ok = manifest.write_scratch_manifest(self.scr.path("n2_scratch_manifest.json"), self.scr.root, [f])
        self.assertIs(ok["publication"], False)
        self.assertIs(ok["grants_authority"], False)
        self.assertEqual(ok["kind"], "n2_scratch_manifest")
        for bad in ("producer_manifest.json", "producer_limitations.json", "producer_extra.json", "manifest.json"):
            with self.assertRaises(RootsStop) as cm:
                manifest.write_scratch_manifest(self.scr.path(bad), self.scr.root, [f])
            self.assertEqual(cm.exception.code, "manifest_name_not_scratch")
        with self.assertRaises(RootsStop) as cm:
            manifest.write_scratch_manifest("/tmp/n2_scratch_manifest.json", self.scr.root, [f])
        self.assertEqual(cm.exception.code, "manifest_outside_scratch")
        self.assertFalse(os.path.exists(self.scr.path("producer_manifest.json")))
        # a scratch manifest is not a receipt either
        with self.assertRaises(RefusedRun):
            L.read_receipt(self.scr.path("n2_scratch_manifest.json"), "approval")

    def test_M11_logs_exact_hashes(self):
        w = H.make_world(self.scr, work={"mode": "phases", "names": ["a", "b"], "seconds": 0.2})
        res, p = H.run_driver(self.scr, H.spec_path(w, {"declared_phases": ["a", "b"]}))
        t = res["terminal"]
        self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
        key = L.attempt_key(w["approval_sha"], "H0")
        term = L.Ledger(w["control_root"]).read("terminal", key)
        self.assertEqual(manifest.verify_logs(term, w["slot_root"]), [])
        self.assertEqual(set(term["logs"]), {"stdout.log", "stderr.log", "telemetry.jsonl"})
        with open(os.path.join(w["slot_root"], "stderr.log"), "ab") as f:
            f.write(b"x")
        probs = manifest.verify_logs(term, w["slot_root"])
        self.assertEqual({n for n, _ in probs}, {"stderr.log"})
        os.unlink(os.path.join(w["slot_root"], "stdout.log"))
        self.assertIn(("stdout.log", "missing"), manifest.verify_logs(term, w["slot_root"]))

    def test_M11_no_cgroup_state_in_repo(self):
        w = H.make_world(self.scr)
        res, p = H.run_driver(self.scr, H.spec_path(w))
        self.assertEqual(res["terminal"]["status"], "success")
        found = subprocess.run(["find", H.REPO, "(", "-name", "n2-*", "-o", "-name", "cgroup.*", "-o", "-name", "memory.*",
                                "-o", "-name", "pids.*", ")", "-not", "-path", "*/.git/*", "-not", "-path", "*/.venv/*"], capture_output=True, timeout=120)
        self.assertEqual(found.stdout.strip(), b"")
        self.assertEqual(H.no_n2_cgroups(), [])
        for p in H.no_n2_cgroups():
            self.assertFalse(p["path"].startswith(H.REPO))


if __name__ == "__main__":
    unittest.main()
