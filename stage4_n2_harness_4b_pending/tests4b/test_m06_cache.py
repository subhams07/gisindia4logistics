"""M06: tiny real cold graph build and warm real loads through both dispatch paths; H0 cache guard (spec D)."""
import os
import unittest
from pathlib import Path

import numpy as np

from tests4b import common as C
from tests4b.helpers4b import UScratch

builder, h0guard, matrix, iso = C.builder, C.h0guard, C.matrix, C.iso


class M06(unittest.TestCase):
    def setUp(self):
        self.scr = UScratch(self.id().split(".")[-1][5:24])
        self.data = C.make_data(self.scr)
        self.out = self.scr.mkdir("out")
        self.cache = Path(self.scr.path("cache"))
        self.nh, _ = builder.load_highway_source(Path(self.data))

    def tearDown(self):
        self.scr.cleanup()

    def guard(self, expect, bindings=True):
        return h0guard.H1H2Guard(builder, self.out, self.cache, expect,
                                 [(matrix, "load_or_build_cached_graph")] if bindings else [])

    def test_M06_h0_cold_build_closes_archive_and_captures_fingerprint(self):
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        self.assertEqual(res["archive"]["files"], [h0guard.ARCHIVE_NAME])
        self.assertEqual(res["cache_version"], builder.CACHE_VERSION)
        self.assertEqual(res["fingerprint"], builder.compute_graph_fingerprint(self.nh))
        # the PARENT hashes the closed archive bytes independently
        from n2h import slotrun
        self.assertEqual(slotrun.sha256_file(os.path.join(self.cache, h0guard.ARCHIVE_NAME)), res["archive"]["sha256"])
        pv = slotrun.parent_verify("H0", self.out, self.cache, archive_name=h0guard.ARCHIVE_NAME)
        self.assertFalse(pv["ok"], "H0 without the child's result file cannot be verified")
        self.assertIn("h0_result_unreadable", pv["problems"])
        self.assertEqual(os.listdir(self.out), [])  # no latch anywhere

    def test_M06_h0_refuses_present_cache_and_latched_warning(self):
        h0guard.run_h0(builder, self.nh, self.cache, self.out)
        with self.assertRaises(h0guard.CacheGuardStop) as cm:
            h0guard.run_h0(builder, self.nh, self.cache, self.out)
        self.assertEqual(cm.exception.code, "h0_cache_not_absent")
        # a write failure makes the real builder WARN and fall back to in-memory: H0 must fail on the latch
        blocker = self.scr.path("blocker")
        open(blocker, "w").write("x")
        with self.assertRaises(h0guard.CacheGuardStop) as cm:
            h0guard.run_h0(builder, self.nh, Path(blocker) / "sub", self.out)
        self.assertIn(cm.exception.code, ("h0_warning_latched",))
        self.assertTrue(os.path.exists(os.path.join(self.out, "logging_latch")))

    def test_M06_warm_loads_through_both_dispatch_paths(self):
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        expect = res["archive"]
        direct = builder.load_or_build_cached_graph(self.nh, cache_dir=self.cache)
        g = self.guard(expect)
        with g:
            self.assertIsNot(matrix.load_or_build_cached_graph, builder.load_or_build_cached_graph)
            a = matrix.load_or_build_cached_graph(self.nh, cache_dir=self.cache)       # matrix by-value alias
            b = builder.load_or_build_cached_graph(self.nh, cache_dir=self.cache)      # builder module attribute
            via_iso = iso.builder.load_or_build_cached_graph(self.nh, cache_dir=self.cache)  # what H2 reaches
        self.assertEqual([c["binding"] for c in g.calls], ["by_value:" + matrix.__name__, "builder_attribute", "builder_attribute"])
        for got in (a, b, via_iso):
            self.assertTrue(np.array_equal(got[2], direct[2]))
            self.assertEqual((got[0] != direct[0]).nnz, 0)
        self.assertEqual(len({id(x) for x in (*a, *b, *via_iso)}), 18, "every load is a fresh real load")
        self.assertTrue(g.verdict()["clean"])
        self.assertIs(matrix.load_or_build_cached_graph, builder.load_or_build_cached_graph)  # restored
        self.assertEqual(h0guard.archive_state(self.cache)["sha256"], expect["sha256"])

    def test_M06_h1_and_h2_producers_run_through_the_guard(self):
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        g = self.guard(res["archive"])
        dpath = os.path.join(self.data, "administrative", "india_districts_lgd.geojson")
        with g, C.w0.W0Witness(matrix, C.pio, dpath, 4):
            s, pm, prov = matrix.build_travel_outputs(data_dir=self.data, cache_dir=self.cache)
            ports, mmlps, prov2 = iso.build_isochrones(data_dir=self.data, cache_dir=self.cache)
        self.assertEqual(len(s), 4)
        self.assertEqual(len(ports), 12 * 4)
        self.assertEqual(len(mmlps), 2 * 3)
        self.assertGreater(int((ports.status == "ok").sum()), 0)
        self.assertEqual(sorted({c["binding"] for c in g.calls}), ["builder_attribute", "by_value:" + matrix.__name__])
        self.assertTrue(g.verdict()["clean"])

    def test_M06_missing_cache_hits_sentinel_and_latches(self):
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        os.unlink(os.path.join(self.cache, h0guard.ARCHIVE_NAME))
        g = self.guard(res["archive"])
        with self.assertRaises(h0guard.CacheGuardStop) as cm:
            g.install()
        self.assertEqual(cm.exception.code, "cache_archive_missing_before_install")
        # a cache that vanishes AFTER install: the loader would rebuild; the sentinel stops it before it writes
        os.makedirs(self.cache, exist_ok=True)
        g2 = h0guard.H1H2Guard(builder, self.out, self.cache, res["archive"], [])
        g2.expect = dict(res["archive"])
        orig = h0guard.check_unchanged
        h0guard.check_unchanged = lambda *a, **k: None  # take the guard's own pre-checks out to reach the builder
        try:
            with g2:
                with self.assertRaises(h0guard.ForbiddenBuild):
                    builder.load_or_build_cached_graph(self.nh, cache_dir=self.cache)
        finally:
            h0guard.check_unchanged = orig
        self.assertEqual(g2.sentinel_hits, 1)
        self.assertTrue(os.path.exists(os.path.join(self.out, "sentinel_latch")))
        self.assertEqual(os.listdir(self.cache), [], "nothing was built or written")
        self.assertFalse(g2.verdict()["clean"])

    def test_M06_tampered_cache_refused_before_and_after(self):
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        arch = os.path.join(self.cache, h0guard.ARCHIVE_NAME)
        raw = bytearray(open(arch, "rb").read())
        raw[len(raw) // 2] ^= 0xFF
        open(arch, "wb").write(bytes(raw))
        with self.assertRaises(h0guard.CacheGuardStop) as cm:
            self.guard(res["archive"]).install()
        self.assertIn("cache_sha256_mismatch", cm.exception.code)
        # extra files in the cache directory (warming / replacement) are refused as well
        open(arch, "wb").write(bytes(bytearray(raw)))  # still tampered; restore a clean archive below
        os.unlink(arch)
        res2 = h0guard.run_h0(builder, self.nh, Path(str(self.cache) + "2"), self.out)
        os.rename(os.path.join(Path(str(self.cache) + "2"), h0guard.ARCHIVE_NAME), arch)
        open(os.path.join(self.cache, "stray.tmp"), "w").write("x")
        with self.assertRaises(h0guard.CacheGuardStop) as cm:
            self.guard(res2["archive"]).install()
        self.assertIn("cache_dir_extra_files", cm.exception.code)

    def test_M06_other_source_fingerprint_forces_rebuild_which_is_forbidden(self):
        import geopandas as gpd
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        other = gpd.GeoDataFrame(self.nh.iloc[:-1].copy())  # a different source than the cache was built from
        g = self.guard(res["archive"], bindings=False)
        with g:
            with self.assertRaises(h0guard.ForbiddenBuild):
                builder.load_or_build_cached_graph(other, cache_dir=self.cache)
        self.assertEqual(g.sentinel_hits, 1)
        self.assertTrue(os.path.exists(os.path.join(self.out, "sentinel_latch")))
        self.assertEqual(h0guard.archive_state(self.cache)["sha256"], res["archive"]["sha256"], "archive untouched")

    def test_M06_corrupt_archive_accepted_by_hash_still_warns_and_latches(self):
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        arch = os.path.join(self.cache, h0guard.ARCHIVE_NAME)
        open(arch, "wb").write(b"not an npz")
        st = h0guard.archive_state(self.cache)
        self.assertIn("unreadable", st)
        expect = dict(res["archive"], sha256=st["sha256"], bytes=st["bytes"])
        g = self.guard(expect, bindings=False)
        g.expect = expect
        orig = h0guard.check_unchanged
        h0guard.check_unchanged = lambda *a, **k: None
        try:
            with g:
                with self.assertRaises(h0guard.ForbiddenBuild):
                    builder.load_or_build_cached_graph(self.nh, cache_dir=self.cache)
        finally:
            h0guard.check_unchanged = orig
        v = g.verdict()
        self.assertTrue(any("Could not load graph cache" in r["msg"] for r in v["logging_records"]), v)
        self.assertTrue(os.path.exists(os.path.join(self.out, "logging_latch")))
        self.assertFalse(v["clean"])

    def test_M06_warning_latch_is_nonraising_and_severity_filtered(self):
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        g = self.guard(res["archive"])
        with g:
            builder.LOGGER.info("informational: not latched")
            self.assertFalse(os.path.exists(os.path.join(self.out, "logging_latch")))
            builder.LOGGER.warning("a swallowed problem")   # exit would still be 0 in a real slot
            builder.LOGGER.error("worse")
        self.assertTrue(os.path.exists(os.path.join(self.out, "logging_latch")))
        v = g.verdict()
        self.assertEqual([r["level"] for r in v["logging_records"]], ["WARNING", "ERROR"])
        self.assertFalse(v["clean"])
        self.assertNotIn(g.handler, builder.LOGGER.handlers, "handler removed on exit")

    def test_M06_latch_write_failure_is_remembered_not_raised(self):
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        g = h0guard.H1H2Guard(builder, os.path.join(self.out, "missing_dir"), self.cache, res["archive"], [])
        with g:
            builder.LOGGER.warning("cannot be written out-of-band")  # must not raise into the producer
        v = g.verdict()
        self.assertTrue(v["latch_write_failed"])
        self.assertFalse(v["clean"], "an unwritable latch fails the slot too")

    def test_M06_swallowed_sentinel_exception_still_fails_the_slot(self):
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        os.unlink(os.path.join(self.cache, h0guard.ARCHIVE_NAME))
        os.rmdir(self.cache)
        g = h0guard.H1H2Guard(builder, self.out, self.cache, res["archive"], [])
        orig = h0guard.check_unchanged
        h0guard.check_unchanged = lambda *a, **k: None
        try:
            with g:
                try:
                    builder.load_or_build_cached_graph(self.nh, cache_dir=self.cache)
                except Exception:  # noqa: BLE001 - the producer (or a wrapper) swallowing the failure
                    pass
        finally:
            h0guard.check_unchanged = orig
        v = g.verdict()
        self.assertEqual(v["sentinel_hits"], 1)
        self.assertFalse(v["clean"], "the out-of-band latch survives a swallowed exception")
        from n2h import slotrun
        pv = slotrun.parent_verify("H1_12", self.out)
        self.assertFalse(pv["ok"])
        self.assertIn("latch_sentinel_latch", pv["problems"])

    def test_M06_no_repository_or_default_cache_fallback(self):
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        g = self.guard(res["archive"])
        with g:
            for bad in (None, Path(C.REPO) / "outputs" / "cache", Path(self.scr.path("elsewhere"))):
                with self.assertRaises(h0guard.CacheGuardStop) as cm:
                    builder.load_or_build_cached_graph(self.nh, cache_dir=bad)
                self.assertEqual(cm.exception.code, "loader_cache_dir_not_h0")

    def test_M06_non_real_loader_binding_refused(self):
        res = h0guard.run_h0(builder, self.nh, self.cache, self.out)
        real = matrix.load_or_build_cached_graph
        matrix.load_or_build_cached_graph = lambda *a, **k: None
        try:
            with self.assertRaises(h0guard.CacheGuardStop) as cm:
                self.guard(res["archive"]).install()
            self.assertEqual(cm.exception.code, "binding_not_real_loader")
        finally:
            matrix.load_or_build_cached_graph = real
        self.assertTrue(builder.build_canonical_highway_graph.__module__.endswith("graph_builder"))


if __name__ == "__main__":
    unittest.main()
