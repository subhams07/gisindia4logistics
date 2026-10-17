"""Cross-cutting: v2 kill probe, 4b interface stubs, CLI end-to-end on mock receipts, matrix consistency."""
import json
import os
import re
import subprocess
import unittest

from n2h import cg as cgmod
from n2h import ledger as L
from n2h import slots
from n2h.fsio import RealFs
from tests import helpers as H

MATRIX = os.path.join(H.HARNESS, "MATRIX.md")


class Meta(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:30])

    def tearDown(self):
        self.scr.cleanup()

    def test_M03_L9_v2_kill_probe_available(self):
        out = cgmod.probe_v2(RealFs())
        self.assertTrue(out["available"], out)
        self.assertTrue(out["has_cgroup_kill"])
        self.assertEqual(out["events_after_kill_on_empty"], ["populated", "0", "frozen", "0"])
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_4B_slot_table_and_payload_builder(self):
        self.assertEqual(set(slots.SLOT_REAL_COMPUTATION), set(L.SLOTS))
        for s in L.SLOTS:
            text = slots.real_payload(s)
            text.encode("ascii")
            self.assertIn("run_slot(CONFIG, mark, NONCE)", text)
        with self.assertRaises(slots.SlotStop):
            slots.real_payload("H9")
        self.assertFalse(any(ln.startswith("| M0") and "| 4b |" in ln for ln in open(MATRIX)))

    def test_M01_run_mock_receipts_dispatch_end_to_end(self):
        w = H.make_world(self.scr, work={"mode": "sleep", "seconds": 0.2})
        args = [H.PY, "-I", "-S", "-B", "-u", os.path.join(H.HARNESS, "n2_harness.py"), "run",
                "--control-root", w["control_root"], "--approval", w["paths"]["approval"], "--policy", w["paths"]["policy"],
                "--scope", w["paths"]["scope"], "--selftest-report", w["paths"]["selftest"], "--slot", "H0",
                "--slot-root", w["slot_root"], "--scratch-base", self.scr.root, "--interpreter", w["link"],
                "--work-json", json.dumps({"mode": "sleep", "seconds": 0.2})]
        p = subprocess.run(args, capture_output=True, timeout=120)
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertEqual(json.loads(p.stdout)["status"], "success")
        key = L.attempt_key(w["approval_sha"], "H0")
        self.assertEqual(L.Ledger(w["control_root"]).state(key), "terminal")
        # the same command again is refused: the attempt is consumed
        p2 = subprocess.run(args, capture_output=True, timeout=120)
        self.assertNotEqual(p2.returncode, 0)
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_MATRIX_consistency(self):
        text = open(MATRIX).read()
        listed = set(re.findall(r"\| (test_[A-Za-z0-9_]+) \|", text))
        found = set()
        for d in ("tests", "tests4b"):
            tdir = os.path.join(H.HARNESS, d)
            for fn in os.listdir(tdir):
                if fn.startswith("test_") and fn.endswith(".py"):
                    for m in re.finditer(r"^\s+def (test_[A-Za-z0-9_]+)\(self", open(os.path.join(tdir, fn)).read(), re.MULTILINE):
                        found.add(m.group(1))
        self.assertEqual(sorted(listed - found), [], "matrix names without a test")
        self.assertEqual(sorted(found - listed), [], "tests missing from the matrix")
        # every controlled item has a row
        for ctl in ["M01", "M02", "M03-L1", "M03-L13", "M04", "M05", "M06", "M07", "M08-L", "M09-L", "M10", "M11", "M12",
                    "M13", "M14", "M15", "M16"]:
            self.assertIn("| %s |" % ctl, text, ctl)
        self.assertEqual(len(re.findall(r"\| 4b \|", text)), 0)


if __name__ == "__main__":
    unittest.main()
