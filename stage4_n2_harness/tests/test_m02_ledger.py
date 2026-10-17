"""M02: exclusive attempt, consumption, serial six-slot order, exit/log/terminal sequencing."""
import hashlib
import os
import shutil
import unittest

from n2h import ledger as L
from n2h.errors import FaultPlan, LedgerStop, RefusedRun
from tests import helpers as H


def led(w, faults=()):
    return L.Ledger(w["control_root"], FaultPlan(faults))


class M02(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch("m02")
        self.w = H.make_world(self.scr)
        self.a = self.w["approval_sha"]

    def tearDown(self):
        self.scr.cleanup()

    def term_ok(self, ledger, slot, status="success"):
        key, _ = ledger.create_attempt(self.a, slot, {"x": 1})
        ledger.create_launch(key, {"pid": 1})
        ledger.create_terminal(key, {"status": status})
        return key

    def test_M02_attempt_exclusive_create(self):
        lg = led(self.w)
        lg.create_attempt(self.a, "H0", {"x": 1})
        with self.assertRaises(LedgerStop) as cm:
            lg.create_attempt(self.a, "H0", {"x": 2})
        self.assertEqual(cm.exception.code, "attempts_exists")
        with self.assertRaises(LedgerStop):
            led(self.w).create_attempt(self.a, "H0", {"x": 1})  # a fresh Ledger object: same result

    def test_M02_attempt_record_immutable_mode_and_no_pid(self):
        lg = led(self.w)
        key, sha = lg.create_attempt(self.a, "H0", {"x": 1})
        p = os.path.join(self.w["control_root"], "attempts", key + ".json")
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o444)
        with self.assertRaises(LedgerStop) as cm:
            lg.create_attempt(self.a, "H1_12", {"pid": 5})
        self.assertEqual(cm.exception.code, "attempt_record_has_pid")
        body = lg.read("attempt", key)
        self.assertNotIn("pid", body)
        lg.create_launch(key, {"pid": 99})
        self.assertEqual(L.sha256_file(p), sha, "launch must not alter the attempt record")
        with self.assertRaises(LedgerStop):
            lg.create_launch(key, {"pid": 100})  # one launch only

    def test_M02_attempt_key_independent_of_run_label(self):
        k1 = L.attempt_key(self.a, "H0")
        k2 = L.attempt_key(self.a, "H0")
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, L.attempt_key(self.a, "H1_12"))
        self.assertNotEqual(k1, L.attempt_key("0" * 64, "H0"))
        self.assertEqual(k1, hashlib.sha256(("%s:%s" % (self.a, "H0")).encode()).hexdigest())
        with self.assertRaises(LedgerStop):
            L.attempt_key(self.a, "H9")

    def drive(self, extra, tag="r"):
        spec = H.spec_path(self.w, extra)
        return H.run_driver(self.scr, spec, tag=tag)

    def test_M02_launch_failure_consumes_attempt(self):
        res, p = self.drive({"faults": ["INJECTED_E_FORK_PRECHECK"]})
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (res, p.stderr))
        lg = led(self.w)
        key = L.attempt_key(self.a, "H0")
        self.assertEqual(lg.state(key), "terminal")
        self.assertEqual(lg.read("terminal", key)["status"], "stopped")
        ok, why = lg.can_dispatch_slot(self.a, "H0")
        self.assertFalse(ok)
        self.assertEqual(why, "slot_already_consumed")
        self.assertEqual(H.no_n2_cgroups(), [], "failed launch removed only its own empty cgroups")
        self.assertTrue(any(s["injected"] == "INJECTED_E_FORK_PRECHECK" for s in t["stops"]))

    def test_M02_renamed_run_label_does_not_reset(self):
        self.drive({"faults": ["INJECTED_E_FORK_PRECHECK"]}, tag="a")
        renamed = self.scr.mkdir("slots", "H0-renamed-run")
        spec = H.spec_path(self.w, {"slot_root": renamed})
        res, p = H.run_driver(self.scr, spec, tag="b")
        self.assertIn("refused", res, (res, p.stderr))
        self.assertEqual(res["refused"]["code"], "slot_not_dispatchable")
        self.assertEqual(os.listdir(renamed), [], "refusal happens before anything is created")

    def test_M02_moved_root_refused(self):
        moved = self.scr.path("control_moved")
        shutil.copytree(self.w["control_root"], moved)
        with self.assertRaises(RefusedRun) as cm:
            L.gate_run(moved, self.w["paths"]["approval"], self.w["paths"]["policy"], self.w["paths"]["scope"],
                       self.w["paths"]["selftest"], os.path.join(H.HARNESS, "n2h"))
        self.assertEqual(cm.exception.code, "ledger_root_moved")
        renamed = self.scr.path("control_renamed")
        os.rename(self.w["control_root"], renamed)
        with self.assertRaises(RefusedRun) as cm:
            L.gate_run(renamed, self.w["paths"]["approval"], self.w["paths"]["policy"], self.w["paths"]["scope"],
                       self.w["paths"]["selftest"], os.path.join(H.HARNESS, "n2h"))
        self.assertEqual(cm.exception.code, "ledger_root_moved")

    def test_M02_fresh_root_refused(self):
        fresh = self.scr.mkdir("fresh")
        L.init_mock_control_root(fresh, "some-other-root-id", True)
        with self.assertRaises(RefusedRun) as cm:
            L.gate_run(fresh, self.w["paths"]["approval"], self.w["paths"]["policy"], self.w["paths"]["scope"],
                       self.w["paths"]["selftest"], os.path.join(H.HARNESS, "n2h"))
        self.assertEqual(cm.exception.code, "ledger_root_id_mismatch")

    def test_M02_persist_failure_blocks_dispatch(self):
        lg = led(self.w, ["INJECTED_E_FSYNC"])
        with self.assertRaises(LedgerStop) as cm:
            lg.create_attempt(self.a, "H0", {"x": 1})
        self.assertEqual(cm.exception.code, "persist_failed")
        self.assertEqual(cm.exception.injected, "INJECTED_E_FSYNC")
        with self.assertRaises(LedgerStop) as cm2:
            lg.assert_dispatch_allowed()
        self.assertEqual(cm2.exception.code, "dispatch_blocked")
        other = led(self.w)  # a new process-equivalent object sees the block marker
        with self.assertRaises(LedgerStop):
            other.create_attempt(self.a, "H1_12", {"x": 1})
        ok, why = other.can_dispatch_slot(self.a, "H0")
        self.assertFalse(ok)
        lg2 = led(self.w, ["INJECTED_E_LEDGER_WRITE"])
        scr2 = H.Scratch("m02b")
        try:
            w2 = H.make_world(scr2)
            lg2 = L.Ledger(w2["control_root"], FaultPlan(["INJECTED_E_LEDGER_WRITE"]))
            with self.assertRaises(LedgerStop) as cm3:
                lg2.create_attempt(w2["approval_sha"], "H0", {"x": 1})
            self.assertEqual(cm3.exception.code, "persist_failed")
        finally:
            scr2.cleanup()

    def test_M02_six_slot_order_and_dependencies(self):
        self.assertEqual(L.SLOTS, ("H0", "H1_12", "H1_64", "H1_192", "H2", "H3"))
        lg = led(self.w)
        ok, why = lg.can_dispatch_slot(self.a, "H1_12")
        self.assertFalse(ok)
        self.assertTrue(why.startswith("earlier_slot_H0_not_terminal"), why)
        self.assertTrue(lg.can_dispatch_slot(self.a, "H0")[0])
        self.term_ok(lg, "H0")
        self.assertTrue(lg.can_dispatch_slot(self.a, "H1_12")[0])
        self.assertFalse(lg.can_dispatch_slot(self.a, "H2")[0], "H2 may not skip the H1 slots")
        self.assertFalse(lg.can_dispatch_slot(self.a, "H3")[0])
        self.term_ok(lg, "H1_12")
        self.term_ok(lg, "H1_64")
        self.assertFalse(lg.can_dispatch_slot(self.a, "H3")[0])
        self.term_ok(lg, "H1_192")
        self.assertTrue(lg.can_dispatch_slot(self.a, "H2")[0])
        self.assertFalse(lg.can_dispatch_slot(self.a, "H3")[0], "H3 still behind H2 (serial)")
        self.term_ok(lg, "H2")
        ok, why = lg.can_dispatch_slot(self.a, "H3")
        self.assertTrue(ok, why)
        self.assertEqual(L.SLOT_DEPS["H3"], ("H1_192",))
        self.assertEqual(L.SLOT_DEPS["H2"], ("H0",))

    def test_M02_failed_earlier_slot_blocks_without_consuming(self):
        lg = led(self.w)
        self.term_ok(lg, "H0", status="fail")
        ok, why = lg.can_dispatch_slot(self.a, "H1_12")
        self.assertFalse(ok)
        self.assertIn("needs_ruling", why)
        self.assertEqual(lg.state(L.attempt_key(self.a, "H1_12")), "none", "never dispatched stays unconsumed")

    def test_M02_no_later_slot_before_terminal_receipt(self):
        lg = led(self.w)
        key, _ = lg.create_attempt(self.a, "H0", {"x": 1})
        lg.create_launch(key, {"pid": 1})
        ok, why = lg.can_dispatch_slot(self.a, "H1_12")
        self.assertFalse(ok)
        self.assertIn("not_terminal(launched)", why)
        with self.assertRaises(LedgerStop):
            lg.create_terminal(L.attempt_key(self.a, "H2"), {"status": "success"})  # no attempt, no terminal

    def test_M02_exit_log_terminal_sequence(self):
        res, p = self.drive({}, tag="seq")
        t = res["terminal"]
        self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
        lg = led(self.w)
        key = L.attempt_key(self.a, "H0")
        self.assertEqual(lg.state(key), "terminal")
        base = self.w["control_root"]
        m = [os.stat(os.path.join(base, d, key + ".json")).st_mtime_ns for d in ("attempts", "launch", "terminal")]
        self.assertLessEqual(m[0], m[1])
        self.assertLessEqual(m[1], m[2])
        term = lg.read("terminal", key)
        self.assertTrue(term["logs_drained"])
        for name, meta in term["logs"].items():
            self.assertEqual(L.sha256_file(os.path.join(self.w["slot_root"], name)), meta["sha256"])
        self.assertEqual(term["exit"]["direct"]["status"]["code"], 0)
        self.assertIs(term["post"]["cgroups_removed"], True)
        tl = term["timeline_ns"]
        self.assertLess(tl["attempt_created"], tl["fork"])
        self.assertLess(tl["fork"], tl["forked_S0"])
        self.assertLess(tl["forked_S0"], tl["assigned_S1"])
        self.assertLess(tl["assigned_S1"], tl["released_S2"])
        self.assertLess(tl["released_S2"], tl["ready"])
        self.assertEqual(H.no_n2_cgroups(), [])


if __name__ == "__main__":
    unittest.main()
