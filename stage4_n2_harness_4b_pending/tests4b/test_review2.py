"""Opus review 1 (4b) fixes that need the venv: B4 contained parent-verification gating, B1 in-slot defence,
N2 (run_h0 logger), N3 (real-helper check for the H3 witness), N4 (transfer positions and registered record)."""
import json
import logging
import os
import unittest
from pathlib import Path

import pandas as pd

from n2h import ledger as L
from n2h import slotrun, transfer
from tests4b import common as C
from tests4b.helpers4b import UScratch
from tests4b.test_contained import ARCH, World
from tests4b.test_m07_transfer import NS, Chain

builder, h0guard, w0 = C.builder, C.h0guard, C.w0


class ParentGating(unittest.TestCase):
    def setUp(self):
        self.scr = UScratch(self.id().split(".")[-1][5:24])

    def tearDown(self):
        self.scr.cleanup()

    def test_B4_failed_parent_verification_blocks_h1_even_though_h0_child_succeeded(self):
        W = World(self.scr)
        base = {"data_dir": W.data, "cache_dir": W.cache}
        r0 = W.run("H0", dict(base, archive_name="other_name.npz"))  # the parent expects another archive name
        self.assertEqual(r0["terminal"]["status"], "success", "the child itself succeeded")
        self.assertEqual(r0["rc"], 4, (r0["stdout"], r0["stderr"][-300:]))
        pv = json.loads(r0["stdout"])["parent_verification"]
        self.assertFalse(pv["receipt_ok"], pv)
        receipt = L.Ledger(W.w["control_root"]).read_parent_receipt(W.w["approval_sha"], "H0")
        self.assertIs(receipt["ok"], False)
        r1 = W.run("H1_12", dict(base, sample_rows=2, expected_graph={"sha256": "0" * 64}))
        self.assertEqual(r1["rc"], 3, (r1["stdout"], r1["stderr"]))
        self.assertIn("parent_verification_H0_missing_or_failed", r1["stderr"])
        self.assertIsNone(r1["terminal"], "no attempt was consumed for H1")
        self.assertEqual(os.listdir(os.path.join(W.w["control_root"], "attempts")),
                         [L.attempt_key(W.w["approval_sha"], "H0") + ".json"])

    def test_B4_h1_requires_config_graph_hash_to_equal_the_h0_receipt(self):
        W = World(self.scr)
        base = {"data_dir": W.data, "cache_dir": W.cache}
        r0 = W.run("H0", base)
        self.assertEqual(r0["rc"], 0, (r0["stdout"], r0["stderr"][-300:]))
        self.assertTrue(json.loads(r0["stdout"])["parent_verification"]["sealed"])
        st = os.stat(os.path.join(W.cache, ARCH))
        self.assertEqual((st.st_uid, st.st_mode & 0o777), (0, 0o444), "the runner sealed the archive")
        exp = W.expect_graph()
        r1 = W.run("H1_12", dict(base, sample_rows=2, expected_graph=dict(exp, sha256="f" * 64)))
        self.assertEqual(r1["rc"], 3)
        self.assertIn("h0_parent_receipt_hash_differs_from_config", r1["stderr"])
        r2 = W.run("H1_12", dict(base, sample_rows=2, expected_graph=exp))  # control: the right hash runs
        self.assertEqual(r2["rc"], 0, (r2["stdout"], r2["stderr"][-300:]))

    def test_B4_h3_needs_registered_transfer_record_and_config_sha(self):
        W = World(self.scr)
        rec = self.scr.path("transfer_record.json")
        open(rec, "w").write("{}")
        W.w["approval_sha"]
        cfg = {"transfer_record": rec, "transfer_record_sha256": "0" * 64, "district_source": rec, "population_source": rec}
        r = W.run("H3", cfg)
        self.assertEqual(r["rc"], 3)
        self.assertIn("transfer_record_sha_not_in_config", r["stderr"])
        self.assertIsNone(r["terminal"])


class InSlotAndWitnessFixes(unittest.TestCase):
    def setUp(self):
        self.scr = UScratch(self.id().split(".")[-1][5:24])

    def tearDown(self):
        self.scr.cleanup()

    def test_B1_slot_code_refuses_repository_paths_itself(self):
        marks = []
        for key in ("data_dir", "cache_dir"):
            cfg = {"slot": "H0", "out_dir": self.scr.mkdir("o" + key), "repo_root": C.REPO,
                   "data_dir": self.scr.mkdir("d" + key), "cache_dir": self.scr.path("c" + key)}
            cfg[key] = C.REPO + ("/data" if key == "data_dir" else "/outputs/cache")
            with self.assertRaises(NS["W0Stop"]) as cm:
                NS["run_slot_body"](cfg, lambda *a, **k: marks.append(a))
            self.assertEqual(cm.exception.code, "config_path_under_repository")
        self.assertFalse(os.path.exists(C.REPO + "/outputs"))

    def test_sweep_detects_stale_scratch_dirs_and_runner_has_results_dir(self):
        import run_selftests_4b as R
        d = self.scr.root.replace("n2-4b-", "n2-4b-stale-")
        os.mkdir(d)
        try:
            self.assertIn(d, R.stale_dirs())
        finally:
            os.rmdir(d)
        self.assertEqual(R.split_args(["--results-dir", "/x", "m05"])[:2], ("/x", ["m05"]))
        src = open(os.path.join(C.HARNESS, "run_selftests.py")).read()
        self.assertIn("--results-dir", src)
        self.assertIn("stale scratch directories", src)

    def test_N2_run_h0_checks_logger_enabled(self):
        data = C.make_data(self.scr)
        nh, _ = builder.load_highway_source(Path(data))
        old = builder.LOGGER.level
        builder.LOGGER.setLevel(logging.CRITICAL)
        try:
            with self.assertRaises(h0guard.CacheGuardStop) as cm:
                h0guard.run_h0(builder, nh, Path(self.scr.path("cache")), self.scr.mkdir("o"))
        finally:
            builder.LOGGER.setLevel(old)
        self.assertEqual(cm.exception.code, "logger_disabled")
        self.assertFalse(os.path.exists(self.scr.path("cache")), "nothing was built")

    def test_N3_h3_witness_requires_and_restores_the_real_helper(self):
        ctx = {"rows": [], "index": []}
        real = C.pio.district_population
        C.pio.district_population = lambda a, b: None
        try:
            with self.assertRaises(w0.W0Stop) as cm:
                w0.FullContextPopulation(C.pio, ctx, []).install()
            self.assertEqual(cm.exception.code, "module_attribute_not_real_helper")
        finally:
            C.pio.district_population = real
        wrapper = w0.FullContextPopulation(C.pio, ctx, [])
        wrapper.install()
        self.assertIsNot(C.pio.district_population, real)
        C.pio.district_population = lambda a, b: None  # someone replaces the attribute while installed
        with self.assertRaises(w0.W0Stop) as cm:
            wrapper.restore()
        self.assertEqual(cm.exception.code, "module_attribute_replaced_while_installed")
        self.assertIs(C.pio.district_population, real, "the real helper is put back")

    def test_N4_positions_and_registered_record_checked(self):
        ch = Chain(self.scr)
        rec = ch.transfer()
        troot = self.scr.path("transfer")
        kw = {"district_source": ch.district, "population_source": ch.pop, "sample_rows": 4}
        bad = dict(rec, sample_positions=[0, 1, 2, 4])
        with self.assertRaises(transfer.TransferStop) as cm:
            transfer.validate_transfer(bad, troot, **kw)
        self.assertEqual(cm.exception.code, "sample_positions_not_the_ordered_prefix")
        noposition = {k: v for k, v in rec.items() if k != "sample_positions"}
        transfer.validate_transfer(noposition, troot, **kw)  # allowed while building ...
        with self.assertRaises(transfer.TransferStop):  # ... but never at model entry
            transfer.validate_transfer(noposition, troot, require_positions=True, **kw)
        # H3 payload: the record must be the registered one
        cfg = {"slot": "H3", "out_dir": self.scr.mkdir("h3o"), "repo_root": C.REPO,
               "transfer_record": os.path.join(troot, "transfer_record.json"), "transfer_record_sha256": "0" * 64,
               "district_source": ch.district, "population_source": ch.pop}
        with self.assertRaises(NS["TransferStop"]) as cm:
            NS["run_slot_body"](cfg, lambda *a, **k: None)
        self.assertEqual(cm.exception.code, "transfer_record_not_the_registered_one")
        cfg["transfer_record_sha256"] = slotrun.sha256_file(cfg["transfer_record"])
        out = NS["run_slot_body"](cfg, lambda *a, **k: None)
        self.assertEqual(out["h3"]["freight_rows"], 4)
        self.assertIsInstance(pd.read_csv(out["h3"]["plan"]["run_port_gravity_model"]["output_csv"]), pd.DataFrame)


if __name__ == "__main__":
    unittest.main()
