"""M05/M06/M07 inside the 4a containment: the REAL helpers run in a contained child (pid/mount namespace, cgroups,
caps dropped, unprivileged uid) on SYNTHETIC inputs, dispatched through the real slot runner by a synthetic-mock
receipt. Also the L10 venv site equivalence. Venv interpreter."""
import json
import os
import subprocess
import unittest

from n2h import cli, slotrun, transfer
from n2h import ledger as L
from tests import helpers as H
from tests4b import common as C
from tests4b.helpers4b import BIG_POLICY, UID, VENV_PY, UScratch

CLI = os.path.join(H.HARNESS, "n2_harness.py")
ARCH = C.h0guard.ARCHIVE_NAME


class World:
    """One mock approval/ledger and the six slot roots."""

    def __init__(self, scr, **fx):
        self.scr = scr
        self.w = H.make_world(scr, slot="H0", policy_over=BIG_POLICY)
        self.data = C.make_data(scr, **fx)
        logs = scr.mkdir("logs")
        self.prep_path = scr.path("prepare.json")
        rec = cli.prepare({"scratch_base": scr.root, "control_root": scr.mkdir("prep_control"), "log_root": logs,
                           "binaries": {"unshare": H.UNSHARE, "interpreter": VENV_PY, "setpriv": H.SETPRIV}})
        json.dump(rec, open(self.prep_path, "w"), default=str)
        self.prep = rec
        self.cache_parent = scr.mkdir("cache_parent")
        os.chown(self.cache_parent, UID, UID)
        self.cache = os.path.join(self.cache_parent, "cache")
        self.slot_roots, self.results = {}, {}

    def out(self, slot):
        return os.path.join(self.slot_roots[slot], "out")

    def run(self, slot, config, timeout=300):
        root = self.scr.mkdir("slots", slot)
        self.slot_roots[slot] = root
        config = dict({"archive_name": ARCH}, **config, slot=slot, out_dir=os.path.join(root, "out"), repo_root=C.REPO)
        w = self.w
        args = [H.PY, "-I", "-S", "-B", "-u", CLI, "run", "--control-root", w["control_root"], "--approval", w["paths"]["approval"],
                "--policy", w["paths"]["policy"], "--scope", w["paths"]["scope"], "--selftest-report", w["paths"]["selftest"],
                "--slot", slot, "--slot-root", root, "--scratch-base", self.scr.root, "--interpreter", VENV_PY,
                "--prepare-record", self.prep_path, "--payload-uid", str(UID), "--slot-config-json", json.dumps(config)]
        p = subprocess.run(args, capture_output=True, timeout=timeout)
        term = None
        key = L.attempt_key(w["approval_sha"], slot)
        led = L.Ledger(w["control_root"])
        if led.state(key) == "terminal":
            term = led.read("terminal", key)
        r = {"rc": p.returncode, "stdout": p.stdout.decode("ascii", "replace"), "stderr": p.stderr.decode("ascii", "replace"),
             "terminal": term, "root": root, "out": os.path.join(root, "out")}
        self.results[slot] = r
        return r

    def slot_json(self, slot, name="slot_result.json"):
        with open(os.path.join(self.out(slot), name)) as f:
            return json.load(f)

    def expect_graph(self):
        return self.slot_json("H0")["h0"]["archive"]


class Chain(unittest.TestCase):
    """The full H0..H3 chain once; individual tests assert on the recorded results."""

    @classmethod
    def setUpClass(cls):
        cls.scr = UScratch("chain")
        try:
            cls._build()
        except BaseException:
            cls.scr.cleanup()  # tearDownClass is not called when setUpClass fails
            raise

    @classmethod
    def _build(cls):
        cls.W = World(cls.scr)
        W = cls.W
        cls.failures = []
        base = {"data_dir": W.data, "cache_dir": W.cache}
        cls.r0 = W.run("H0", base)
        cls.pv0 = slotrun.parent_verify("H0", W.out("H0"), W.cache, archive_name=ARCH)
        sha = W.expect_graph()["sha256"] if cls.r0["rc"] == 0 else None
        exp = W.expect_graph() if cls.r0["rc"] == 0 else None
        for slot, n in (("H1_12", 2), ("H1_64", 3), ("H1_192", 4)):
            setattr(cls, "r_" + slot, W.run(slot, dict(base, sample_rows=n, expected_graph=exp)))
        cls.pv1 = slotrun.parent_verify("H1_192", W.out("H1_192"), W.cache, sha, archive_name=ARCH)
        cls.r2 = W.run("H2", dict(base, expected_graph=exp))
        cls.pv2 = slotrun.parent_verify("H2", W.out("H2"), W.cache, sha, archive_name=ARCH)
        cls.rec = transfer.build_transfer(
            h1_dir=W.out("H1_192"), transfer_root=cls.scr.path("transfer"), sample_rows=4,
            district_source=os.path.join(W.data, "administrative", "india_districts_lgd.geojson"),
            population_source=os.path.join(W.data, "demographic", "district_population_estimates.csv"), forbidden_roots=[C.REPO])
        rec_path = os.path.join(cls.scr.path("transfer"), "transfer_record.json")
        cls.rec_sha = slotrun.register_transfer(W.w["control_root"], W.w["approval_sha"], rec_path)
        cls.r3 = W.run("H3", {"transfer_record": rec_path, "transfer_record_sha256": cls.rec_sha,
                              "district_source": os.path.join(W.data, "administrative", "india_districts_lgd.geojson"),
                              "population_source": os.path.join(W.data, "demographic", "district_population_estimates.csv")})

    @classmethod
    def tearDownClass(cls):
        cls.scr.cleanup()

    def assertOk(self, r):
        self.assertEqual(r["rc"], 0, (r["stdout"], r["stderr"][-600:], r["terminal"] and r["terminal"]["stops"]))
        self.assertEqual(r["terminal"]["status"], "success", r["terminal"]["stops"])

    def test_M06_contained_h0_cold_build_and_parent_hash(self):
        self.assertOk(self.r0)
        self.assertTrue(self.pv0["ok"], self.pv0)
        res = self.W.slot_json("H0")
        self.assertEqual(res["h0"]["archive"]["files"], ["canonical_nh_graph.npz"])
        self.assertEqual(res["h0"]["cache_version"], C.builder.CACHE_VERSION)
        self.assertEqual(self.pv0["archive_sha256"], res["h0"]["archive"]["sha256"])

    def test_M05_contained_h1_witness_inside_containment(self):
        for slot in ("H1_12", "H1_64", "H1_192"):
            self.assertOk(getattr(self, "r_" + slot))
        res = self.W.slot_json("H1_192")
        h1 = res["h1"]
        self.assertEqual(h1["rows"], 4)
        self.assertEqual(h1["district_rows_full"], 8)
        self.assertEqual([e["event"] for e in h1["witness_events"]], ["installed", "canonical_read", "population", "restored"])
        self.assertEqual(h1["readback"]["csv"]["rows"], 4)
        self.assertEqual(h1["readback"]["parquet"]["rows"], 4)
        for s, n in (("H1_12", 2), ("H1_64", 3)):
            self.assertEqual(self.W.slot_json(s)["h1"]["rows"], n)
            # nested prefixes: the same frozen full context in every H1 slot
            self.assertEqual(self.W.slot_json(s)["h1"]["context_sha256"], h1["context_sha256"])

    def test_M06_contained_h1_h2_guard_clean_and_archive_unchanged(self):
        self.assertOk(self.r2)
        for pv in (self.pv1, self.pv2):
            self.assertTrue(pv["ok"], pv)
        g = self.W.slot_json("H1_192")["h1"]["guard"]
        self.assertTrue(g["clean"])
        self.assertEqual(sorted({c["binding"] for c in g["loader_calls"]}), ["by_value:scripts.analyze.nh_travel_matrix"])
        g2 = self.W.slot_json("H2")["h2"]["guard"]
        self.assertTrue(g2["clean"])
        self.assertEqual({c["binding"] for c in g2["loader_calls"]}, {"builder_attribute"})
        self.assertEqual(self.W.slot_json("H2")["h2"]["ports_features"], 48)
        self.assertFalse(os.path.exists(os.path.join(self.W.out("H1_192"), "sentinel_latch")))

    def test_M07_contained_h3_two_domain_transfer_then_models(self):
        self.assertOk(self.r3)
        res = self.W.slot_json("H3")["h3"]
        self.assertEqual((res["freight_rows"], res["gravity_rows"]), (4, 4))
        self.assertEqual(res["plan"], transfer.h3_call_plan(self.rec, os.path.join(self.W.out("H3"), "h3")))
        self.assertEqual(self.rec["full_context"]["rows"], 8)
        self.assertEqual(self.rec["sample_rows"], 4)

    def test_4B_contained_runs_as_unprivileged_uid_with_site_main(self):
        att = L.Ledger(self.W.w["control_root"]).read("attempt", L.attempt_key(self.W.w["approval_sha"], "H1_192"))
        self.assertEqual((att["payload_uid"], att["payload_gid"]), (UID, UID))
        self.assertIs(att["site_main"], True)
        self.assertEqual(att["controller_cgroup_paths"], self.W.prep["controller_cgroup_paths"])
        self.assertIs(att["synthetic_mock"], True)
        err = open(os.path.join(self.W.slot_roots["H1_192"], "stderr.log")).read()
        self.assertTrue(any("selfcheck_ok" in ln and "uid=%d" % UID in ln for ln in err.splitlines()))
        out_owner = os.stat(self.W.out("H1_192")).st_uid
        self.assertEqual(out_owner, UID)

    def test_M10_contained_only_declared_phase_boundaries(self):
        for slot in ("H0", "H1_192", "H2", "H3"):
            t = self.W.results[slot]["terminal"]
            names = [b["phase"] for b in t["phases"]["boundaries"]]
            self.assertTrue(names, slot)
            self.assertEqual(t["phases"]["undeclared"], [], slot)
        self.assertEqual(C.H.no_n2_cgroups(), [])

    def test_4B_module_origin_and_versions_recorded_in_child(self):
        res = self.W.slot_json("H1_192")
        for name, v in res["module_origin"].items():
            self.assertTrue(v["inside_repo"], (name, v))
        self.assertEqual(res["versions"]["numpy"], "2.4.6")
        self.assertEqual(res["versions"]["pandas"], "3.0.6")


class ContainedStops(unittest.TestCase):
    def setUp(self):
        self.scr = UScratch(self.id().split(".")[-1][5:24])

    def tearDown(self):
        self.scr.cleanup()

    def test_M05_contained_lossy_code_roundtrip_stops_slot(self):
        W = World(self.scr, variant="big_code")
        base = {"data_dir": W.data, "cache_dir": W.cache}
        r0 = W.run("H0", base)
        self.assertEqual(r0["terminal"]["status"], "success", r0["terminal"]["stops"])
        exp = W.expect_graph()
        r1 = W.run("H1_12", dict(base, sample_rows=3, expected_graph=exp))
        self.assertNotEqual(r1["rc"], 0)
        self.assertEqual(r1["terminal"]["status"], "stopped")
        codes = [s["code"] for s in r1["terminal"]["stops"]]
        self.assertTrue(any("lossy_code_roundtrip" in c for c in codes), codes)
        self.assertEqual(W.slot_json("H1_12", "slot_stop.json")["code"], "lossy_code_roundtrip")
        # the earlier slot's consumption is final; the failed slot blocks the dependent slots
        led = L.Ledger(W.w["control_root"])
        ok, why = led.can_dispatch_slot(W.w["approval_sha"], "H1_64")
        self.assertFalse(ok)
        self.assertEqual(C.H.no_n2_cgroups(), [])

    def test_M06_contained_tampered_cache_stops_before_any_work(self):
        W = World(self.scr)
        base = {"data_dir": W.data, "cache_dir": W.cache}
        W.run("H0", base)
        exp = W.expect_graph()
        arch = os.path.join(W.cache, "canonical_nh_graph.npz")
        raw = bytearray(open(arch, "rb").read())
        raw[100] ^= 0xFF
        os.chmod(arch, 0o644)
        open(arch, "wb").write(bytes(raw))
        r1 = W.run("H1_12", dict(base, sample_rows=3, expected_graph=exp))
        self.assertEqual(r1["terminal"]["status"], "stopped")
        self.assertEqual(W.slot_json("H1_12", "slot_stop.json")["code"], "cache_sha256_mismatch_before_install")
        self.assertFalse(os.path.exists(os.path.join(W.out("H1_12"), "analysis")), "no producer output was written")
        pv = slotrun.parent_verify("H1_12", W.out("H1_12"), W.cache, exp["sha256"], archive_name=ARCH)
        self.assertIn("archive_hash_changed", pv["problems"])

    def test_M06_contained_missing_cache_never_rebuilds(self):
        W = World(self.scr)
        base = {"data_dir": W.data, "cache_dir": W.cache}
        W.run("H0", base)
        exp = W.expect_graph()
        os.unlink(os.path.join(W.cache, "canonical_nh_graph.npz"))
        r1 = W.run("H1_12", dict(base, sample_rows=3, expected_graph=exp))
        self.assertEqual(r1["terminal"]["status"], "stopped")
        self.assertEqual(W.slot_json("H1_12", "slot_stop.json")["code"], "cache_archive_missing_before_install")
        self.assertEqual(os.listdir(W.cache), [], "nothing was rebuilt")


class L10Venv(unittest.TestCase):
    def test_M03_L10_site_equivalence_venv(self):
        probe = ("import sys, json\nimport site\n%s\nprint(json.dumps({'path': sys.path, 'prefix': sys.prefix, "
                 "'flags': [sys.flags.isolated, sys.flags.dont_write_bytecode, sys.flags.ignore_environment, sys.flags.safe_path], "
                 "'user_site': site.ENABLE_USER_SITE, 'sp': any(p.endswith('site-packages') and '.venv' in p for p in sys.path), "
                 "'system_dist_packages': any('dist-packages' in p for p in sys.path)}))")

        def run(args, extra):
            r = subprocess.run([VENV_PY] + args + ["-c", probe % extra], capture_output=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)
        normal = run(["-I", "-B"], "")
        s_only = run(["-I", "-S", "-B"], "")
        s_main = run(["-I", "-S", "-B"], "site.main()")
        self.assertEqual(s_main["path"], normal["path"])
        self.assertEqual(s_main["prefix"], normal["prefix"])
        self.assertTrue(s_main["prefix"].endswith("/.venv"))
        self.assertEqual(s_main["flags"], normal["flags"])
        self.assertEqual(s_main["user_site"], normal["user_site"])
        self.assertTrue(s_main["sp"])
        self.assertFalse(s_only["sp"], "-S alone leaves site-packages out: the stub must call site.main()")
        self.assertFalse(s_main["system_dist_packages"], "the venv has no system site packages")
        cfg = open(os.path.join(C.REPO, ".venv", "pyvenv.cfg")).read()
        self.assertIn("include-system-site-packages = false", cfg)
        # and the stub itself enables it for the venv interpreter
        from n2h import stubsrc
        self.assertIn("site.main()", stubsrc.STUB)
        self.assertIn('E.get("site_main")', stubsrc.STUB)


if __name__ == "__main__":
    unittest.main()
