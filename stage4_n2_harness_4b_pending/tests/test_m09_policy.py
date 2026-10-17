"""M09-L: injected policy crossings (strict/inclusive), gaps, counters, latches, receipts. Labels are neutral."""
import json
import os
import re
import types
import unittest

from n2h import controller as ctl
from n2h import policy as P
from n2h import telemetry as tel
from n2h.errors import FaultPlan, PolicyStop, TelemetryStop
from tests import helpers as H
from tests.mockcg import MockWorld


def run(scr, work=None, policy_over=None, extra=None, slot="H0", **kw):
    wd = H.make_world(scr, work=work, policy_over=policy_over, slot=slot)
    res, p = H.run_driver(scr, H.spec_path(wd, extra), **kw)
    return wd, res, p


class M09(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch("m09")

    def tearDown(self):
        self.scr.cleanup()

    def test_M09_floor_crossings_strict_inclusive(self):
        base = P.synthetic_test_policy()
        floors = [n for n, (_u, k) in P.FIELDS.items() if k == "floor"]
        caps = [n for n, (_u, k) in P.FIELDS.items() if k == "cap"]
        self.assertGreaterEqual(len(floors), 5)
        self.assertGreaterEqual(len(caps), 6)
        for name in floors + caps:
            kind = P.FIELDS[name][1]
            thr = base["fields"][name]["value"]
            for strictness in ("strict", "inclusive"):
                pol = json.loads(json.dumps(base))
                pol["fields"][name]["strictness"] = strictness
                at = P.check(pol, name, thr)
                below = P.check(pol, name, thr - 1 if thr >= 1 else thr - 0.25)
                above = P.check(pol, name, thr + 1)
                with self.subTest(field=name, strictness=strictness):
                    if kind == "floor":
                        self.assertEqual(at, strictness == "inclusive")
                        self.assertTrue(below)
                        self.assertFalse(above)
                    else:
                        self.assertEqual(at, strictness == "inclusive")
                        self.assertFalse(below)
                        self.assertTrue(above)
        with self.assertRaises(PolicyStop):
            P.breach("floor", 1, 1, "n/a")

    def sample_ctx(self, **over):
        pol = P.synthetic_test_policy(**over)
        for n in ("running_floor_effective_available_bytes", "running_floor_attempt_headroom_bytes",
                  "bash_cgroup_growth_max_bytes", "running_floor_disk_free_bytes", "per_slot_elapsed_cap_s",
                  "conservative_peak_max_bytes"):
            pol["fields"][n]["strictness"] = "inclusive"
        return types.SimpleNamespace(policy=pol, baseline={"bash_usage_excluding_attempt_bytes": 0})

    def fake_sample(self, avail, headroom_ws, bash, disk, peak, limit=192 << 20):
        return {"effective_available_bytes": avail, "bash_usage_excluding_attempt_bytes": bash,
                "disk_free_bytes": disk, "counters": {"pids_events": "max 0"},
                "memory": {"limit_bytes": limit, "cg_working_set_bytes": limit - headroom_ws,
                           "cg_charged_peak_bytes": peak, "oom_kill": 0, "under_oom": 0, "failcnt": 0}}

    def test_M09_gap_and_elapsed_expiry(self):
        mw = MockWorld(self.scr)
        try:
            p = mw.spawn()
            d = mw.member(p)
            mw.set_listing([p.pid])
            cap = mw.policy["fields"]["max_sample_gap_ms"]["value"]  # 2000 ms synthetic, strict
            for strictness, equal_stops in (("strict", False), ("inclusive", True)):
                pol = P.synthetic_test_policy()
                pol["fields"]["max_sample_gap_ms"]["strictness"] = strictness
                mw.policy = pol
                ticks = iter([0, cap * 1_000_000])
                t = mw.telemetry(d, mono=lambda it=ticks: next(it))
                t.sample()
                if equal_stops:
                    with self.assertRaises(TelemetryStop):
                        t.sample()
                else:
                    t.sample()
        finally:
            mw.close()
        ctx = self.sample_ctx(per_slot_elapsed_cap_s=10)
        s = self.fake_sample(10 << 30, 100 << 20, 0, 10 << 30, 0)
        self.assertNotIn("elapsed_cap", [b[0] for b in tel.evaluate_breaches(ctx, s, 9.99)])
        self.assertIn("elapsed_cap", [b[0] for b in tel.evaluate_breaches(ctx, s, 10)])  # inclusive at equality

    def test_M09_sample_floors_every_field_strict_inclusive(self):
        ctx = self.sample_ctx(running_floor_effective_available_bytes=1000, running_floor_attempt_headroom_bytes=2000,
                              bash_cgroup_growth_max_bytes=3000, running_floor_disk_free_bytes=4000,
                              conservative_peak_max_bytes=5000)
        ok = self.fake_sample(1001, 2001, 2999, 4001, 4999)
        self.assertEqual(tel.evaluate_breaches(ctx, ok, 1.0, 0), [])
        eq = self.fake_sample(1000, 2000, 3000, 4000, 5000)
        names = sorted(b[0] for b in tel.evaluate_breaches(ctx, eq, 1.0, 0))
        self.assertEqual(names, ["bash_cgroup_growth", "conservative_peak", "floor_attempt_headroom",
                                 "floor_disk_free", "floor_effective_available"])  # inclusive: equality breaches
        strict = self.sample_ctx(running_floor_effective_available_bytes=1000, running_floor_attempt_headroom_bytes=2000,
                                 bash_cgroup_growth_max_bytes=3000, running_floor_disk_free_bytes=4000,
                                 conservative_peak_max_bytes=5000)
        for n in strict.policy["fields"]:
            if strict.policy["fields"][n]["strictness"] == "inclusive":
                strict.policy["fields"][n]["strictness"] = "strict"
        self.assertEqual(tel.evaluate_breaches(strict, eq, 1.0, 0), [])  # strict: equality is fine
        over = self.fake_sample(999, 1999, 3001, 3999, 5001)
        self.assertEqual(len(tel.evaluate_breaches(strict, over, 1.0, 0)), 5)
        # conservative figure = charged peak + resident_elsewhere (D-11)
        near = self.fake_sample(2000, 3000, 0, 5000, 4000)
        self.assertEqual(tel.evaluate_breaches(strict, near, 1.0, 999), [])
        self.assertEqual([b[0] for b in tel.evaluate_breaches(strict, near, 1.0, 1001)], ["conservative_peak"])

    def test_M09_mock_counters_oom_pidsmax_eagain_enospc_libload(self):
        ctx = self.sample_ctx()
        s = self.fake_sample(10 << 30, 100 << 20, 0, 10 << 30, 0)
        s["memory"]["oom_kill"] = 1
        self.assertIn("oom_signal", [b[0] for b in tel.evaluate_breaches(ctx, s, 0)])
        s["memory"].update(oom_kill=0, under_oom=1)
        self.assertIn("oom_signal", [b[0] for b in tel.evaluate_breaches(ctx, s, 0)])
        s["memory"].update(under_oom=0, failcnt=3)
        self.assertEqual([b[0] for b in tel.evaluate_breaches(ctx, s, 0)], ["failcnt_nonzero"])
        s["memory"]["failcnt"] = 0
        s["counters"]["pids_events"] = "max 2"
        self.assertEqual([b[0] for b in tel.evaluate_breaches(ctx, s, 0)], ["pids_max_hit"])
        self.assertEqual(tel.classify_error_event("INJECTED_E_EAGAIN"), "process_creation_failed")
        self.assertEqual(tel.classify_error_event("INJECTED_E_ENOSPC"), "disk_exhausted")
        self.assertEqual(tel.classify_error_event("INJECTED_E_LIBRARY_LOAD"), "library_load_failure")
        self.assertEqual(tel.classify_error_event("ENOMEM"), "memory_error")
        # end to end: the child reports a neutral injected error, the controller STOPs on it
        for kind, code in (("INJECTED_E_ENOSPC", "child_reported_disk_exhausted"),
                           ("INJECTED_E_LIBRARY_LOAD", "child_reported_library_load_failure")):
            scr = H.Scratch("m09e")
            try:
                _w, res, p = run(scr, work={"mode": "emit_error", "kind": kind, "seconds": 20})
                t = res["terminal"]
                self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
                self.assertEqual(t["stops"][0]["code"], code)
                self.assertEqual(t["stops"][0]["injected"], kind)
                self.assertEqual(t["post"]["cgroups_removed"], True)
            finally:
                scr.cleanup()

    def test_M09_cache_latch(self):
        for variant, secs in (("sentinel_latch", 5), ("logging_latch", 0.0)):
            scr = H.Scratch("m09l")
            try:
                wd = H.make_world(scr, work={"mode": "latch", "latch_path": "", "seconds": secs})
                wd["work"]["latch_path"] = os.path.join(wd["slot_root"], variant)
                res, p = H.run_driver(scr, H.spec_path(wd))
                t = res["terminal"]
                self.assertNotEqual(t["status"], "success", "a latch fails the slot even if exit status is 0")
                self.assertIn(t["stops"][0]["code"], ("breach_cache_latch", "cache_latch_with_exit_zero"))
                self.assertEqual(t["post"]["cgroups_removed"], True)
            finally:
                scr.cleanup()

    def test_M09_exit_without_receipt(self):
        wd, res, p = run(self.scr, work={"mode": "sleep", "seconds": 0.2}, extra={"require_receipt": True})
        t = res["terminal"]
        self.assertEqual(t["status"], "fail", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "exit_without_receipt")
        scr2 = H.Scratch("m09r")
        try:
            wd2 = H.make_world(scr2, work={"mode": "sleep", "seconds": 0.2, "receipt_path": ""})
            wd2["work"]["receipt_path"] = os.path.join(wd2["slot_root"], "completion_receipt.json")
            res2, p2 = H.run_driver(scr2, H.spec_path(wd2, {"require_receipt": True}))
            self.assertEqual(res2["terminal"]["status"], "success", (res2["terminal"]["stops"], p2.stderr))
        finally:
            scr2.cleanup()

    def test_M09_failcnt_and_memsw_actions(self):
        with self.assertRaises(PolicyStop) as cm:
            P.validate_policy(P.synthetic_test_policy(failcnt_action="record"))
        self.assertEqual(cm.exception.code, "policy_failcnt_action_must_be_stop")
        self.assertEqual(ctl.memsw_decision(False, "stop"), "ok")
        self.assertEqual(ctl.memsw_decision(True, "record"), "record")
        self.assertEqual(ctl.memsw_decision(True, "stop"), "stop")
        with self.assertRaises(PolicyStop):
            P.validate_policy(P.synthetic_test_policy(memsw_tolerance_bytes=4096))  # below 64 pages x nproc
        P.validate_policy(P.synthetic_test_policy(memsw_check_action="stop"))

    def test_M09_markers_without_nonce_ignored_flagged(self):
        f = self.scr.path("err.log")
        with open(f, "w") as fh:
            fh.write("N2M abc123 100 phase name=setup\n")
            fh.write("N2M WRONG 101 error kind=EAGAIN\n")
            fh.write("N2M abc123 notanumber phase name=x\n")
            fh.write("plain text\n")
            fh.write("N2M abc123 102 phase na")  # partial line stays buffered
        r = tel.MarkerReader(f, "abc123")
        new = r.poll()
        self.assertEqual([e["event"] for e in new], ["phase"])
        self.assertEqual(len(r.untrusted), 2)
        with open(f, "a") as fh:
            fh.write("me=done\n")
        self.assertEqual([e["kv"]["name"] for e in r.poll()], ["done"])
        # real run: a forged marker (wrong nonce) must not trigger the error STOP and must be reported
        scr2 = H.Scratch("m09f")
        try:
            _w, res, p = run(scr2, work={"mode": "forge_marker", "seconds": 0.3})
            t = res["terminal"]
            self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
            self.assertEqual(len(t["markers"]["untrusted"]), 1)
            self.assertIn("deadbeef", t["markers"]["untrusted"][0])
        finally:
            scr2.cleanup()

    def test_M09_neutral_injection_labels_only(self):
        with self.assertRaises(ValueError):
            FaultPlan(["ENOSPC"])
        with self.assertRaises(ValueError):
            FaultPlan(["E_NOSPC_REAL"])
        pat = re.compile(r'plan\.check\("([A-Z_0-9]+)"\)')
        pkg = os.path.join(H.HARNESS, "n2h")
        seen = set()
        for fn in os.listdir(pkg):
            if fn.endswith(".py"):
                for m in pat.finditer(open(os.path.join(pkg, fn)).read()):
                    seen.add(m.group(1))
        self.assertTrue(seen)
        for lab in seen:
            self.assertTrue(lab.startswith("INJECTED_E_"), lab)
        # test-side labels in every test module (FaultPlan lists, faults specs)
        tdir = os.path.join(H.HARNESS, "tests")
        for fn in os.listdir(tdir):
            if fn.endswith(".py"):
                for lab in re.findall(r'"(INJECTED[A-Z_0-9]*)"', open(os.path.join(tdir, fn)).read()):
                    self.assertTrue(lab.startswith("INJECTED_E_"), (fn, lab))

    def test_M09_reserve_gates_launch(self):
        # D-5: attempt limit + reserve must fit in the effective available memory, else refuse before consumption
        wd = H.make_world(self.scr, policy_over={"reserve_bytes": 1 << 50})
        res, _p = H.run_driver(self.scr, H.spec_path(wd))
        self.assertEqual(res["refused"]["code"], "limit_plus_reserve_exceeds_available")
        self.assertEqual(os.listdir(os.path.join(wd["control_root"], "attempts")), [])

    def test_M09_real_elapsed_cap_terminates(self):
        wd, res, p = run(self.scr, work={"mode": "sleep", "seconds": 60}, policy_over={"per_slot_elapsed_cap_s": 1})
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "breach_elapsed_cap")
        steps = [s["step"] for s in t["termination"]["steps"]]
        self.assertIn("sigterm_ns_init", steps)
        self.assertTrue(t["termination"]["graceful"])
        self.assertEqual(t["post"]["cgroups_removed"], True)
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M09_real_floor_breach_terminates(self):
        wd, res, p = run(self.scr, work={"mode": "sleep", "seconds": 60},
                         policy_over={"running_floor_effective_available_bytes": 1 << 50})
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "breach_floor_effective_available")
        self.assertEqual(t["post"]["cgroups_removed"], True)
        # launch floors refuse BEFORE the attempt is consumed
        scr2 = H.Scratch("m09g")
        try:
            wd2 = H.make_world(scr2, policy_over={"launch_min_effective_available_bytes": 1 << 50})
            res2, _p2 = H.run_driver(scr2, H.spec_path(wd2))
            self.assertEqual(res2["refused"]["code"], "launch_floor_effective_available")
            self.assertEqual(os.listdir(os.path.join(wd2["control_root"], "attempts")), [])
            scr3 = H.Scratch("m09h")
            try:
                wd3 = H.make_world(scr3, policy_over={"launch_min_disk_free_bytes": 1 << 50})
                res3, _p3 = H.run_driver(scr3, H.spec_path(wd3), tag="r3")
                self.assertEqual(res3["refused"]["code"], "launch_floor_disk_free")
            finally:
                scr3.cleanup()
        finally:
            scr2.cleanup()


if __name__ == "__main__":
    unittest.main()
