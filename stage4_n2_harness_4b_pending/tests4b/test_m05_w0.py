"""M05: real tiny W0 helper parity on synthetic inputs (spec C). Venv interpreter."""
import gc
import os
import unittest
import weakref

import geopandas as gpd
import numpy as np
import pandas as pd

from tests4b import common as C
from tests4b.helpers4b import UScratch

F, w0, matrix, pio = C.F, C.w0, C.matrix, C.pio


class M05(unittest.TestCase):
    def setUp(self):
        self.scr = UScratch(self.id().split(".")[-1][5:24])

    def tearDown(self):
        self.scr.cleanup()

    def full_expected(self, data):
        """The real helper on the FULL frames: the policy the sample must inherit."""
        d = gpd.read_file(os.path.join(data, "administrative", "india_districts_lgd.geojson"))
        p = pd.read_csv(os.path.join(data, "demographic", "district_population_estimates.csv"))
        return d, p, pio.district_population(d, p)

    def test_M05_imports_resolve_inside_repository(self):
        rep = C.assert_modules_in_repo(self)
        self.assertEqual(set(rep), {m.__name__ for m in C.PRODUCER_MODULES})

    def test_M05_parity_cross_prefix_collision(self):
        data = C.make_data(self.scr)
        cache = self.scr.path("cache")
        C.warm_cache(data, cache)
        d, p, full = self.full_expected(data)
        s, pm, prov, wit = C.witness_run(data, cache, 4)
        self.assertEqual(len(s), 4)
        got = s["pop_2011"].tolist()
        exp = full.iloc[:4].tolist()
        for a, b in zip(got, exp):
            self.assertTrue((np.isnan(a) and np.isnan(b)) or a == b, (got, exp))
        self.assertEqual(exp[0], 1000.0)
        self.assertTrue(np.isnan(exp[3]), "full-source policy: Delta/600 collides with Gujarat/600 outside the prefix")
        # control: the real helper on the PREFIX ALONE silently takes the single population value (the hazard)
        naive = pio.district_population(d.iloc[:4], p)
        self.assertEqual(naive.iloc[3], 1700.0)
        self.assertNotEqual(naive.iloc[3], got[3])
        ev = [e["event"] for e in wit.events]
        self.assertEqual(ev, ["installed", "canonical_read", "population", "restored"])

    def test_M05_same_state_duplicate_outside_prefix_stops(self):
        data = C.make_data(self.scr, variant="same_state_dup")
        cache = self.scr.path("cache")
        C.warm_cache(data, cache)
        with self.assertRaises(ValueError) as cm:
            C.witness_run(data, cache, 4)
        self.assertIn("Duplicate primary district code", str(cm.exception))
        # control: a prefix that does not see the duplicate would NOT have failed
        dd = gpd.read_file(os.path.join(data, "administrative", "india_districts_lgd.geojson"))
        pp = pd.read_csv(os.path.join(data, "demographic", "district_population_estimates.csv"))
        pio.district_population(dd.iloc[:4], pp)

    def test_M05_uncoded_and_null_population_stay_literal(self):
        data = C.make_data(self.scr)
        cache = self.scr.path("cache")
        C.warm_cache(data, cache)
        s, pm, prov, wit = C.witness_run(data, cache, 8)
        pops = dict(zip(s["district"], s["pop_2011"]))
        self.assertEqual(pops["Gamma "], 1200.0)  # uncoded row: unique state/name fallback
        self.assertTrue(np.isnan(pops["Epsilon"]), "null population stays null")
        self.assertEqual(pops["Eta"], 1300.0)
        self.assertTrue(np.isnan(pops["Iota"]) and np.isnan(pops["Delta"]))
        self.assertTrue(s["district_code"].isna().sum() == 2)

    def test_M05_literal_labels_through_csv_and_parquet(self):
        data = C.make_data(self.scr)
        cache = self.scr.path("cache")
        C.warm_cache(data, cache)
        s, pm, prov, wit = C.witness_run(data, cache, 4)
        out = self.scr.mkdir("out")
        matrix.write_travel_outputs(s, pm, summary_csv=out + "/s.csv", summary_parquet=out + "/s.parquet",
                                    port_matrix_csv=out + "/m.csv")
        rb = w0.verify_readback(wit.ctx, wit.positions, csv_path=out + "/s.csv", parquet_path=out + "/s.parquet")
        self.assertEqual(rb["csv"]["rows"], 4)
        self.assertEqual(rb["parquet"]["rows"], 4)
        self.assertEqual(w0.verify_readback(wit.ctx, wit.positions, csv_path=out + "/m.csv")["csv"]["rows"], 4)
        self.assertEqual(s["state"].tolist()[2], "MAHARASHTRA")
        self.assertEqual(s["district"].tolist()[2], "Gamma ")
        # normalisation of a label on disk is detected as drift (not silently accepted)
        txt = open(out + "/s.csv").read().replace("Gamma ", "Gamma")
        open(out + "/s2.csv", "w").write(txt)
        with self.assertRaises(w0.W0Stop) as cm:
            w0.verify_readback(wit.ctx, wit.positions, csv_path=out + "/s2.csv")
        self.assertEqual(cm.exception.code, "readback_identity_mismatch")

    def test_M05_lossy_large_code_roundtrip_stops(self):
        data = C.make_data(self.scr, variant="big_code")
        cache = self.scr.path("cache")
        C.warm_cache(data, cache)
        s, pm, prov, wit = C.witness_run(data, cache, 4)
        self.assertEqual(wit.ctx.rows[1][2], ["i", "9007199254740993"], "frozen BEFORE the float64 cast")
        out = self.scr.mkdir("out")
        matrix.write_travel_outputs(s, pm, summary_csv=out + "/s.csv", summary_parquet=out + "/s.parquet",
                                    port_matrix_csv=out + "/m.csv")
        for kw in ({"csv_path": out + "/s.csv"}, {"parquet_path": out + "/s.parquet"}, {"csv_path": out + "/m.csv"}):
            with self.assertRaises(w0.W0Stop) as cm:
                w0.verify_readback(wit.ctx, wit.positions, **kw)
            self.assertEqual(cm.exception.code, "lossy_code_roundtrip", kw)

    def test_M05_non_default_index_restored_and_checked(self):
        data = C.make_data(self.scr)
        cache = self.scr.path("cache")
        C.warm_cache(data, cache)
        real = gpd.read_file

        def odd_index(path, *a, **k):
            g = real(path, *a, **k)
            if str(path).endswith("india_districts_lgd.geojson"):
                g.index = pd.Index([40, 30, 20, 10, 11, 12, 13, 14])
            return g
        dpath = os.path.join(data, "administrative", "india_districts_lgd.geojson")
        wit = w0.W0Witness(matrix, pio, dpath, 4, real_read_file=odd_index)
        with wit:
            s, pm, prov = matrix.build_travel_outputs(data_dir=data, cache_dir=cache)
        self.assertEqual(len(s), 4)
        self.assertEqual(wit.ctx.index[:4], [["i", "40"], ["i", "30"], ["i", "20"], ["i", "10"]])
        # the witness returns the population Series on the SAMPLED index; resetting the index is a mismatch
        d, p, _ = self.full_expected(data)
        wit2 = w0.W0Witness(matrix, pio, dpath, 4, real_read_file=odd_index).install()
        try:
            prefix = gpd.read_file(dpath)
            self.assertEqual(prefix.index.tolist(), [40, 30, 20, 10])
            ser = matrix.district_population(prefix, p)
            self.assertEqual(ser.index.tolist(), [40, 30, 20, 10])
            with self.assertRaises(w0.W0Stop) as cm:
                matrix.district_population(prefix.reset_index(drop=True), p)
            self.assertEqual(cm.exception.code, "incoming_index_mismatch")
        finally:
            wit2.restore()

    def test_M05_both_bindings_wrapped_delegated_and_restored(self):
        data = C.make_data(self.scr)
        dpath = os.path.join(data, "administrative", "india_districts_lgd.geojson")
        real_read, real_pop = gpd.read_file, pio.district_population
        self.assertIs(matrix.gpd, gpd)
        self.assertIs(matrix.district_population, real_pop)
        wit = w0.W0Witness(matrix, pio, dpath, 4)
        with wit:
            self.assertIsNot(gpd.read_file, real_read)
            self.assertIsNot(matrix.district_population, real_pop)
            self.assertIs(pio.district_population, real_pop, "the producer_io attribute is NOT touched for H1")
            other = gpd.read_file(os.path.join(data, "roads", "india_nh_network.geojson"))  # shared module: delegated
            self.assertEqual(len(other), 8)
            self.assertIsNone(wit.ctx, "a non-canonical read must not create a context")
            full4 = gpd.read_file(dpath)
            self.assertEqual(len(full4), 4)
            self.assertEqual(wit.ctx.n, 8)
            with self.assertRaises(w0.W0Stop):
                gpd.read_file(dpath)  # only one canonical read per slot
        self.assertIs(gpd.read_file, real_read)
        self.assertIs(matrix.district_population, real_pop)
        # a stand-in that is not the real helper is refused at install
        fake = lambda a, b: None
        matrix.district_population = fake
        try:
            with self.assertRaises(w0.W0Stop) as cm:
                w0.W0Witness(matrix, pio, dpath, 4).install()
            self.assertEqual(cm.exception.code, "matrix_binding_not_real_helper")
        finally:
            matrix.district_population = real_pop

    def test_M05_incoming_mismatch_and_trimmed_population_stop(self):
        data = C.make_data(self.scr)
        dpath = os.path.join(data, "administrative", "india_districts_lgd.geojson")
        pop = pd.read_csv(os.path.join(data, "demographic", "district_population_estimates.csv"))
        wit = w0.W0Witness(matrix, pio, dpath, 4, population_rows_expected=len(pop))
        with wit:
            prefix = gpd.read_file(dpath)
            bad = prefix.copy()
            bad.loc[bad.index[1], "district"] = "Different"
            with self.assertRaises(w0.W0Stop) as cm:
                matrix.district_population(bad, pop)
            self.assertEqual(cm.exception.code, "incoming_identity_mismatch")
            with self.assertRaises(w0.W0Stop) as cm:
                matrix.district_population(prefix.iloc[::-1], pop)
            self.assertIn(cm.exception.code, ("incoming_identity_mismatch", "incoming_index_mismatch"))
            with self.assertRaises(w0.W0Stop) as cm:
                matrix.district_population(prefix, pop.iloc[:3])
            self.assertEqual(cm.exception.code, "population_trimmed")
            ok = matrix.district_population(prefix, pop)  # delegates to the real helper on the full context
            self.assertEqual(len(ok), 4)

    def test_M05_no_retained_geometry(self):
        data = C.make_data(self.scr)
        dpath = os.path.join(data, "administrative", "india_districts_lgd.geojson")
        refs = []
        real = gpd.read_file

        def spy(path, *a, **k):
            g = real(path, *a, **k)
            if str(path) == dpath:
                refs.append(weakref.ref(g))
            return g
        wit = w0.W0Witness(matrix, pio, dpath, 4, real_read_file=spy)
        with wit:
            prefix = gpd.read_file(dpath)
        gc.collect()
        self.assertTrue(refs and refs[0]() is None, "the full frame must not be kept alive by the witness")
        seen = [wit.ctx.frame] + list(vars(wit.ctx).values()) + list(vars(wit).values())
        self.assertFalse(any(isinstance(x, (gpd.GeoDataFrame, gpd.GeoSeries)) for x in seen))
        self.assertNotIn("geometry", wit.ctx.frame.columns)
        self.assertEqual(list(wit.ctx.frame.columns), ["state", "district", "district_code"])
        self.assertEqual(len(prefix), 4)


if __name__ == "__main__":
    unittest.main()
