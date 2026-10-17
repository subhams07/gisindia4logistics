"""M12: cgroup lifecycle: foreign n2-* cgroups stop the start without cleanup; unique names; rmdir failure."""
import os
import re
import subprocess
import time
import unittest

from n2h import cg as cgmod
from n2h import ledger as L
from n2h.errors import CgroupStop
from n2h.fsio import RealFs
from tests import helpers as H


class M12(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch("m12")
        self.made = []
        self.sleepers = []

    def tearDown(self):
        for s in self.sleepers:
            s.kill()
            s.wait()
        for p in reversed(self.made):
            for _ in range(100):
                try:
                    os.rmdir(p)
                    break
                except OSError:
                    time.sleep(0.02)
        self.scr.cleanup()
        self.assertEqual(H.no_n2_cgroups(), [])

    def pre_layout(self):
        name = "n2-m12-" + os.urandom(4).hex()
        lay = cgmod.Layout(name, cgmod.controller_cgroup_paths(RealFs()))
        return lay

    def refused_start(self, w):
        res, p = H.run_driver(self.scr, H.spec_path(w), tag="r" + os.urandom(2).hex())
        self.assertIn("refused", res, (res, p.stderr))
        self.assertEqual(res["refused"]["code"], "existing_n2_cgroup")
        self.assertEqual(os.listdir(os.path.join(w["control_root"], "attempts")), [], "STOP at start does not consume")
        self.assertEqual(os.listdir(w["slot_root"]), [], "nothing was created for the attempt")
        return res

    def test_M12_existing_n2_cgroup_stops_no_cleanup(self):
        w = H.make_world(self.scr)
        # (a) empty memory cgroup
        lay = self.pre_layout()
        os.mkdir(lay.abs["memory"])
        self.made.append(lay.abs["memory"])
        self.refused_start(w)
        self.assertTrue(os.path.isdir(lay.abs["memory"]), "not cleaned up")
        # (b) populated pids cgroup (a unrelated sleeper placed in it by this test)
        os.mkdir(lay.abs["pids"])
        self.made.append(lay.abs["pids"])
        s = subprocess.Popen([H.PY, "-c", "import time; time.sleep(600)"])
        self.sleepers.append(s)
        with open(lay.abs["pids"] + "/cgroup.procs", "w") as f:
            f.write(str(s.pid))
        res = self.refused_start(w)
        self.assertIn("populated=True", res["refused"]["detail"])
        self.assertIsNone(s.poll(), "the populated cgroup's member was not touched")
        self.assertEqual(open(lay.abs["pids"] + "/cgroup.procs").read().split(), [str(s.pid)])
        s.kill()
        s.wait()
        # (c) v2 cgroup under the unified root
        os.mkdir(lay.abs["v2"])
        self.made.append(lay.abs["v2"])
        self.refused_start(w)
        found = cgmod.scan_existing(RealFs())
        self.assertEqual(len(found), 3)
        self.assertEqual(sorted(f["populated"] for f in found if f["populated"] is not None), [False, False, False])

    def test_M12_names_unique(self):
        a, b = os.urandom(16).hex(), os.urandom(16).hex()
        seen = set()
        for appr in (a, b):
            for slot in L.SLOTS:
                nm = "n2-" + L.attempt_key(appr, slot)[:20]
                self.assertRegex(nm, cgmod.NAME_RE)
                seen.add(nm)
        self.assertEqual(len(seen), 2 * len(L.SLOTS))
        paths = cgmod.controller_cgroup_paths(RealFs())
        for bad in ("N2-upper", "n2-", "n2-a b", "n2-../x", "x-n2-a", "n2-" + "a" * 60, "n2-a/b"):
            with self.assertRaises(CgroupStop) as cm:
                cgmod.Layout(bad, paths)
            self.assertEqual(cm.exception.code, "bad_cgroup_name")
        l1 = cgmod.Layout("n2-aaaa", paths)
        l2 = cgmod.Layout("n2-bbbb", paths)
        self.assertEqual(set(l1.abs.values()) & set(l2.abs.values()), set())

    def test_M12_failed_rmdir_stops(self):
        lay = self.pre_layout()
        fs = RealFs()
        cgmod.create_all(lay, fs)
        self.made.extend(lay.abs[h] for h in lay.join_order)
        s = subprocess.Popen([H.PY, "-c", "import time; time.sleep(600)"])
        self.sleepers.append(s)
        cgmod.set_limits(lay, 64 << 20, 16, fs)
        cgmod.join_and_verify(lay, s.pid, fs)
        with self.assertRaises(CgroupStop) as cm:
            cgmod.remove(lay, fs)
        self.assertEqual(cm.exception.code, "rmdir_failed")
        self.assertIn("Device or resource busy", cm.exception.detail)
        self.assertTrue(os.path.isdir(lay.abs["pids"]), "evidence stays in place")
        s.kill()
        s.wait()
        for _ in range(100):
            if cgmod.is_empty(lay, fs):
                break
            time.sleep(0.02)
        cgmod.remove(lay, fs)
        self.made = []
        # driver: an injected rmdir failure leaves empty cgroups AND blocks the next start (STOP, no cleanup)
        w = H.make_world(self.scr)
        H.register_orphan(self.scr, w)
        res, p = H.run_driver(self.scr, H.spec_path(w, {"fs_faults": {"rmdir|n2-": "INJECTED_E_RMDIR"}}))
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(t["stops"][0]["code"], "rmdir_failed")
        self.assertEqual(t["stops"][0]["injected"], "INJECTED_E_RMDIR")
        self.assertEqual(len(cgmod.scan_existing(fs)), 3, "left in place for the operator")
        scr2 = H.Scratch("m12b")
        try:
            w2 = H.make_world(scr2, slot="H0")
            res2, p2 = H.run_driver(scr2, H.spec_path(w2), tag="second")
            self.assertEqual(res2["refused"]["code"], "existing_n2_cgroup")
        finally:
            scr2.cleanup()

    def test_M12_controller_cgroup_path_differs_stops(self):
        w = H.make_world(self.scr)
        rec = dict(cgmod.controller_cgroup_paths(RealFs()))
        rec["memory"] = rec["memory"] + "/some-other-cgroup"
        res, p = H.run_driver(self.scr, H.spec_path(w, {"recorded_controller_paths": rec}))
        self.assertEqual(res["refused"]["code"], "controller_cgroup_changed")
        self.assertEqual(os.listdir(os.path.join(w["control_root"], "attempts")), [])
        res, p = H.run_driver(self.scr, H.spec_path(w, {"recorded_controller_paths": cgmod.controller_cgroup_paths(RealFs())}), tag="ok")
        self.assertEqual(res["terminal"]["status"], "success", (res["terminal"]["stops"], p.stderr))

    def test_M12_dying_memcg_reported(self):
        w = H.make_world(self.scr, work={"mode": "filecache", "mib": 16, "path": "", "seconds": 0.2})
        w["work"]["path"] = os.path.join(w["slot_root"], "cachefill.bin")
        res, p = H.run_driver(self.scr, H.spec_path(w))
        t = res["terminal"]
        self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
        post = t["post"]
        self.assertIsInstance(post["memcg_dying_residual"], int)
        self.assertIsInstance(post["memcg_dying_residual_before"], int)
        self.assertGreaterEqual(post["memcg_dying_residual"], 0)
        self.assertIs(post["cgroups_removed"], True)
        self.assertGreaterEqual(post["final_memory"]["cache_bytes"], 8 << 20, "residual cache is visible before rmdir")
        self.assertTrue(re.match(r"^\d+$", str(post["memcg_dying_residual"])))


if __name__ == "__main__":
    unittest.main()
