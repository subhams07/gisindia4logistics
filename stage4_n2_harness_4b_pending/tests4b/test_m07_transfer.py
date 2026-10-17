"""M07: H1(192)->H3 transfer, two domains, membership, exact real H3 arguments (spec E). Venv interpreter."""
import json
import os
import unittest

import numpy as np
import pandas as pd

from n2h import slots, transfer
from tests4b import common as C
from tests4b.helpers4b import UScratch

REPO = C.REPO


def load_ns():
    ns = {"__name__": "n2_payload_inprocess"}
    exec(compile(slots.build_payload_text(with_footer=False), "payload", "exec"), ns)  # noqa: S102 - our own text
    return ns


NS = load_ns()


class Chain:
    """H0 -> H1(sample) in-process through the SAME slot code the contained child runs."""

    def __init__(self, scr, sample=4, **fx):
        self.scr = scr
        self.data = C.make_data(scr, **fx)
        self.cache = scr.path("cache")
        self.h0out = scr.mkdir("h0out")
        self.h1out = scr.mkdir("h1out")
        self.marks = []
        mark = lambda *a, **k: self.marks.append((a, k))
        base = {"repo_root": REPO, "data_dir": self.data, "cache_dir": self.cache}
        self.h0 = NS["run_slot_body"](dict(base, slot="H0", out_dir=self.h0out), mark)
        exp = self.h0["h0"]["archive"]
        self.h1 = NS["run_slot_body"](dict(base, slot="H1_192", out_dir=self.h1out, sample_rows=sample,
                                           expected_graph=exp), mark)
        self.district = os.path.join(self.data, "administrative", "india_districts_lgd.geojson")
        self.pop = os.path.join(self.data, "demographic", "district_population_estimates.csv")
        self.sample = sample

    def transfer(self, root=None, **kw):
        return transfer.build_transfer(h1_dir=self.h1out, transfer_root=root or self.scr.path("transfer"),
                                       sample_rows=self.sample, district_source=self.district,
                                       population_source=self.pop, forbidden_roots=[REPO], **kw)


class M07(unittest.TestCase):
    def setUp(self):
        self.scr = UScratch(self.id().split(".")[-1][5:24])

    def tearDown(self):
        self.scr.cleanup()

    def enter(self, ch, rec, out=None, calls=None):
        calls = calls if calls is not None else []

        def call(name, fn, *a, **k):
            calls.append((name, a, k))
            return fn(*a, **k)
        return NS["h3_enter_models"](rec, out or self.scr.path("h3out"), ch.district, ch.pop, C.ice, C.phm, C.pio,
                                     call=call), calls

    def test_M07_two_domains_membership_and_exact_h3_arguments(self):
        ch = Chain(self.scr)
        rec = ch.transfer()
        # two domains: 4 sample rows vs 8 full-context identities; population complete; NOT required equal
        self.assertEqual(rec["sample_rows"], 4)
        self.assertEqual(rec["full_context"]["rows"], 8)
        self.assertEqual(rec["population"]["rows"], 7)
        self.assertEqual(rec["sample_positions"], [0, 1, 2, 3])
        for k, meta in rec["files"].items():
            self.assertTrue(meta["regular"] and meta["copy_complete"] and meta["readback_ok"], k)
            self.assertEqual(len(meta["sha256"]), 64)
        res, calls = self.enter(ch, rec)
        self.assertEqual([c[0] for c in calls], ["compute_modal_costs", "run_port_gravity_model"])
        plan = transfer.h3_call_plan(rec, self.scr.path("h3out"))
        name, a, k = calls[0]
        self.assertEqual(k, plan["compute_modal_costs"])
        self.assertEqual(type(a[0]).__name__, "FreightCostParameters")
        self.assertEqual(calls[1][2], plan["run_port_gravity_model"])
        self.assertEqual(calls[1][2]["alpha"], 1.2)
        self.assertEqual(calls[1][2]["beta"], 1.5)
        self.assertEqual(calls[1][2]["data_dir"], os.path.realpath(self.scr.path("transfer")))
        for key in ("output_csv",):
            for v in (calls[0][2][key], calls[1][2][key]):
                self.assertTrue(v.startswith(self.scr.path("h3out")))
        self.assertEqual((res["freight_rows"], res["gravity_rows"]), (4, 4))
        self.assertEqual(res["gravity_witness_events"][0]["full_rows"], 8)

    def test_M07_gravity_population_uses_full_context_and_keeps_nulls(self):
        ch = Chain(self.scr)
        rec = ch.transfer()
        res, _ = self.enter(ch, rec)
        grav = pd.read_csv(res["plan"]["run_port_gravity_model"]["output_csv"])
        self.assertEqual(grav["district"].tolist(), ["Alpha", "Beta", "Gamma ", "Delta"])
        self.assertEqual(grav.loc[0, "pop_2011"], 1000.0)
        self.assertTrue(np.isnan(grav.loc[3, "pop_2011"]), "Delta/600 collides with Gujarat/600 in the FULL context")
        # control: the real helper on the sample alone would have produced 1700 for Delta
        sample = pd.read_csv(rec["files"]["matrix"]["dest"])
        naive = C.pio.district_population(sample, pd.read_csv(ch.pop))
        self.assertEqual(naive.iloc[3], 1700.0)
        self.assertEqual(C.pio.district_population.__module__, "scripts.analyze.producer_io")  # restored after the witness
        # intentional null population survives the copy byte-for-byte
        self.assertEqual(rec["files"]["population"]["sha256"], C.w0.sha256_path(ch.pop))
        self.assertTrue(pd.read_csv(rec["files"]["population"]["dest"])["pop_2011"].isna().sum() == 1)

    def test_M07_sample_outputs_identities_match_exactly(self):
        ch = Chain(self.scr)
        rec = ch.transfer()
        res, _ = self.enter(ch, rec)
        for name in ("summary", "matrix"):
            transfer.check_outputs_identity(rec, rec["files"][name]["dest"], name)
        self.assertEqual(transfer.check_outputs_identity(rec, res["plan"]["compute_modal_costs"]["output_csv"], "freight"), 4)
        # drift in a model output is detected
        out = res["plan"]["run_port_gravity_model"]["output_csv"]
        df = pd.read_csv(out)
        df.iloc[[0, 1]] = df.iloc[[1, 0]].to_numpy()
        df.to_csv(out, index=False)
        with self.assertRaises(transfer.TransferStop) as cm:
            transfer.check_outputs_identity(rec, out, "gravity")
        self.assertEqual(cm.exception.code, "sample_identity_drift")

    def refuse_before_models(self, ch, rec, code, mutate=None):
        if mutate:
            mutate()
        calls = []
        with self.assertRaises(NS["TransferStop"]) as cm:
            self.enter(ch, rec, calls=calls)
        self.assertEqual(cm.exception.code, code)
        self.assertEqual(calls, [], "no model may be entered when validation fails")
        self.assertFalse(os.path.exists(self.scr.path("h3out", "district_freight_modal_split.csv")))

    def writable(self, path):
        os.chmod(path, 0o644)
        return path

    def test_M07_tampered_summary_refused_before_model_entry(self):
        ch = Chain(self.scr)
        rec = ch.transfer()

        def flip():
            p = self.writable(rec["files"]["summary"]["dest"])
            raw = bytearray(open(p, "rb").read())
            raw[-3] ^= 1
            open(p, "wb").write(bytes(raw))
        self.refuse_before_models(ch, rec, "transfer_file_tampered", flip)

    def test_M07_missing_matrix_refused(self):
        ch = Chain(self.scr)
        rec = ch.transfer()
        self.refuse_before_models(ch, rec, "file_missing", lambda: os.unlink(rec["files"]["matrix"]["dest"]))

    def test_M07_null_population_drift_refused(self):
        ch = Chain(self.scr)
        rec = ch.transfer()

        def fill_null():
            p = self.writable(rec["files"]["population"]["dest"])
            txt = open(p).read().replace("Karnataka,Epsilon,\n", "Karnataka,Epsilon,999.0\n")
            open(p, "w").write(txt)
        self.refuse_before_models(ch, rec, "transfer_file_tampered", fill_null)

    def test_M07_order_drift_in_h1_summary_refused_at_transfer(self):
        ch = Chain(self.scr)
        p = os.path.join(ch.h1out, "analysis", "nh_district_travel_time_summary.csv")
        df = pd.read_csv(p)
        df.iloc[[0, 1]] = df.iloc[[1, 0]].to_numpy()
        df.to_csv(p, index=False)
        with self.assertRaises(transfer.TransferStop) as cm:
            ch.transfer()
        self.assertEqual(cm.exception.code, "sample_identity_drift")

    def test_M07_trimmed_full_context_or_population_refused(self):
        ch = Chain(self.scr)
        # context trimmed to the sample (and re-hashed consistently): not the complete original identities
        p = os.path.join(ch.h1out, "context", "full_identity_context.json")
        ctx = json.load(open(p))
        ctx["rows"], ctx["index"], ctx["n"] = ctx["rows"][:4], ctx["index"][:4], 4
        os.chmod(p, 0o644)
        json.dump(ctx, open(p, "w"))
        with self.assertRaises(transfer.TransferStop) as cm:
            ch.transfer()
        self.assertEqual(cm.exception.code, "context_trimmed")

    def test_M07_context_identity_not_original_refused(self):
        ch = Chain(self.scr)
        p = os.path.join(ch.h1out, "context", "full_identity_context.json")
        ctx = json.load(open(p))
        ctx["rows"][6][1] = ["s", "Forged"]
        os.chmod(p, 0o644)
        json.dump(ctx, open(p, "w"))
        with self.assertRaises(transfer.TransferStop) as cm:
            ch.transfer(root=self.scr.path("t2"))
        self.assertEqual(cm.exception.code, "context_not_original")

    def test_M07_population_not_original_refused(self):
        ch = Chain(self.scr)
        rec = ch.transfer()
        with self.assertRaises(transfer.TransferStop) as cm:  # the original changed after the copy
            transfer.validate_transfer(rec, self.scr.path("transfer"), district_source=ch.district,
                                       population_source=self.write_other_population(ch), sample_rows=4)
        self.assertEqual(cm.exception.code, "population_not_original")

    def write_other_population(self, ch):
        p = self.scr.path("other_population.csv")
        pd.read_csv(ch.pop).iloc[:3].to_csv(p, index=False)
        return p

    def test_M07_transfer_root_rules(self):
        ch = Chain(self.scr)
        with self.assertRaises(transfer.TransferStop) as cm:   # overlapping H1's own directory
            ch.transfer(root=os.path.join(ch.h1out, "inner"))
        self.assertEqual(cm.exception.code, "transfer_root_not_disjoint")
        with self.assertRaises(transfer.TransferStop) as cm:   # never the live analysis tree / repository
            ch.transfer(root=os.path.join(REPO, "data", "analysis", "n2_transfer_must_not_exist"))
        self.assertEqual(cm.exception.code, "transfer_root_forbidden")
        self.assertFalse(os.path.exists(os.path.join(REPO, "data", "analysis", "n2_transfer_must_not_exist")))
        os.makedirs(self.scr.path("busy"))
        open(self.scr.path("busy", "x"), "w").write("x")
        with self.assertRaises(transfer.TransferStop) as cm:
            ch.transfer(root=self.scr.path("busy"))
        self.assertEqual(cm.exception.code, "transfer_root_not_empty")

    def test_M07_rec_revalidated_at_model_entry(self):
        ch = Chain(self.scr)
        rec = ch.transfer()
        res, calls = self.enter(ch, rec, out=self.scr.path("h3a"))
        self.assertEqual(len(calls), 2)
        self.refuse_before_models(ch, rec, "transfer_file_tampered", lambda: open(self.writable(rec["files"]["context"]["dest"]), "a").write(" "))


if __name__ == "__main__":
    unittest.main()
