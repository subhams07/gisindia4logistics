"""M14: accounting semantics with real owned children inside unit-created n2-ut-* cgroups."""
import json
import os
import subprocess
import time
import unittest

from n2h import controller, native
from n2h import telemetry as tel
from tests import helpers as H
from tests.ocg import OwnedCg

MIB = 1 << 20
FINDINGS = os.path.join(H.HARNESS, "results", "findings-m14.json")


def record(key, value):
    os.makedirs(os.path.dirname(FINDINGS), exist_ok=True)
    try:
        with open(FINDINGS) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d[key] = value
    with open(FINDINGS, "w") as f:
        json.dump(d, f, indent=1, sort_keys=True)


class M14(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch("m14")
        self.cg = None

    def tearDown(self):
        if self.cg:
            self.cg.close()
        self.scr.cleanup()
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_M14_charges_before_join_excluded(self):
        self.cg = OwnedCg()
        self.cg.spawn("b = bytearray(24 << 20)\n"
                      "for i in range(0, len(b), 4096): b[i] = 1\n"
                      "say('pre_alloc_done')\nwait_go()\nsay('after_go')\ntime.sleep(30)\n")
        self.assertEqual(self.cg.readline(), "pre_alloc_done")
        self.cg.join()
        self.cg.go()
        self.assertEqual(self.cg.readline(), "after_go")
        m = self.cg.mem()
        rss = int(open("/proc/%d/status" % self.cg.child.pid).read().split("VmRSS:")[1].split()[0]) * 1024
        self.assertGreater(rss, 24 * MIB, "the child really holds the memory")
        self.assertLess(m["cg_charged_peak_bytes"], 8 * MIB, "charges made before joining stay with the old cgroup")
        self.assertLess(m["cg_anon_bytes"], 8 * MIB)
        record("charges_before_join", {"rss_bytes": rss, "attempt_peak_bytes": m["cg_charged_peak_bytes"]})

    def test_M14_alloc_after_join_in_peak_and_anon(self):
        self.cg = OwnedCg()
        self.cg.spawn("wait_go()\nb = bytearray(32 << 20)\n"
                      "for i in range(0, len(b), 4096): b[i] = 1\nsay('alloc_done')\ntime.sleep(30)\n")
        self.cg.join()
        self.cg.go()
        self.assertEqual(self.cg.readline(), "alloc_done")
        m = self.cg.mem()
        self.assertGreaterEqual(m["cg_charged_peak_bytes"], 32 * MIB)
        self.assertGreaterEqual(m["cg_anon_bytes"], 31 * MIB)
        self.assertLess(m["cg_anon_bytes"], 48 * MIB)
        self.assertGreaterEqual(m["cg_usage_bytes"], 32 * MIB)
        record("alloc_after_join_32MiB", {k: m[k] for k in ("cg_charged_peak_bytes", "cg_anon_bytes", "cg_usage_bytes",
                                                            "cg_working_set_bytes", "kmem_usage_bytes")})

    def test_M14_file_io_cache_not_anon(self):
        self.cg = OwnedCg(mem_limit=256 * MIB)
        path = self.scr.path("cachefile")
        self.cg.spawn("wait_go()\nfd = os.open(%r, os.O_WRONLY | os.O_CREAT, 0o600)\n"
                      "c = b'z' * (1 << 20)\nfor i in range(32): os.write(fd, c)\nos.close(fd)\n"
                      "say('written')\ntime.sleep(30)\n" % path)
        self.cg.join()
        before = self.cg.mem()
        self.cg.go()
        self.assertEqual(self.cg.readline(), "written")
        m = self.cg.mem()
        self.assertGreaterEqual(m["cache_bytes"] - before["cache_bytes"], 28 * MIB, "file pages are charged as cache")
        self.assertLess(m["cg_anon_bytes"], 8 * MIB, "file I/O does not raise the anonymous field")
        self.assertGreaterEqual(m["cg_charged_peak_bytes"], 28 * MIB)
        record("file_io_32MiB", {k: m[k] for k in ("cache_bytes", "cg_anon_bytes", "dirty_bytes", "cg_working_set_bytes",
                                                   "inactive_file_bytes", "cg_usage_bytes")})

    def make_file(self, name, mib, mode=0o644, chown=None):
        p = self.scr.path(name)
        with open(p, "wb") as f:
            f.write(b"x" * (mib * MIB))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(p, mode)
        if chown is not None:
            os.chown(p, chown, chown)
        return p

    def test_M14_fadvise_mincore_drop_measurable(self):
        p = self.make_file("res.bin", 8)
        just_written = native.residency(p)
        self.assertGreater(just_written["fraction"], 0.9, "positive control: written+fsynced pages are resident")
        native.fadvise_dontneed_own(p, self.scr.root)
        dropped = native.residency(p)
        self.assertLess(dropped["fraction"], 0.2, "DONTNEED on our own clean file drops residency measurably")
        with open(p, "rb") as f:
            while f.read(1 << 20):
                pass
        reread = native.residency(p)
        self.assertGreater(reread["fraction"], 0.9, "positive control: reading brings it back")
        record("fadvise_mincore_8MiB", {"written": just_written["fraction"], "after_dontneed": dropped["fraction"],
                                        "after_reread": reread["fraction"]})
        with self.assertRaises(Exception) as cm:
            native.fadvise_dontneed_own("/usr/bin/python3.11", self.scr.root)  # not ours / outside scratch
        self.assertEqual(getattr(cm.exception, "code", None), "fadvise_outside_scratch")

    def test_M14_mincore_positive_control(self):
        sparse = self.scr.path("sparse.bin")
        with open(sparse, "wb") as f:
            f.truncate(8 * MIB)
        neg = native.residency(sparse)
        self.assertEqual(neg["resident_bytes"], 0, "negative control: a hole has no resident pages")
        p = self.make_file("pos.bin", 8)
        pos = native.residency(p)
        self.assertGreater(pos["resident_bytes"], 7 * MIB, "positive control: mincore is not silently all-absent")
        self.assertNotEqual(pos["resident_bytes"], neg["resident_bytes"])
        vec, size = native.mincore_vector(p)
        self.assertEqual(len(vec), size // native.PAGE)
        self.assertEqual(sum(vec), len(vec))

    def cold_file(self, name, mib, mode, chown=None):
        """write, fsync, DONTNEED (while still ours), then change owner/mode: truly cold pages."""
        p = self.scr.path(name)
        with open(p, "wb") as f:
            f.write(b"x" * (mib * MIB))
            f.flush()
            os.fsync(f.fileno())
        native.fadvise_dontneed_own(p, self.scr.root)
        os.chmod(p, mode)
        if chown is not None:
            os.chown(p, chown, chown)
        return p

    def capless_fraction(self, path):
        code = ("import sys; sys.path.insert(0, %r)\nfrom n2h import native\n"
                "print(native.residency(%r)['fraction'])\n") % (H.HARNESS, path)
        r = subprocess.run([H.SETPRIV, "--bounding-set=-all", "--inh-caps=-all", "--no-new-privs", "--", H.PY, "-I", "-S", "-B", "-c", code],
                           capture_output=True, timeout=30, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return float(r.stdout)

    def test_M14_mincore_ownership_mode_control(self):
        doc = "/home/user/gisindia4logistics/docs/stage4_n2_resource_spec.md"
        st = os.lstat(doc)  # metadata only: records the class of the real inputs (owner/mode)
        self.assertEqual((st.st_uid, st.st_mode & 0o777), (0, 0o644))
        same_class = self.cold_file("input_like.bin", 4, 0o644)
        s2 = os.lstat(same_class)
        self.assertEqual((s2.st_uid, s2.st_mode & 0o777), (st.st_uid, st.st_mode & 0o777))
        foreign = self.cold_file("foreign.bin", 4, 0o444, chown=65534)
        # controller (owner / CAP_FOWNER): cold files read as cold. This is the control that can fail: a file that
        # was just written reads resident under real mincore AND under the kernel fallback, so only cold files discriminate.
        self.assertLess(native.residency(same_class)["fraction"], 0.2)
        self.assertLess(native.residency(foreign)["fraction"], 0.2)
        # capability-less process: for a file it does not own and cannot write, the kernel's can_do_mincore() is false
        # and mincore() reports ALL pages resident (a fallback that OVERSTATES residency, i.e. fails closed).
        fb = self.capless_fraction(foreign)
        self.assertEqual(fb, 1.0, "fallback: every page reported resident")
        own = self.capless_fraction(same_class)  # root owns it: real answer even without capabilities
        self.assertLess(own, 0.2)
        # reading the files warms them: controller reading is then ~1.0, so the control distinguishes cold from warm
        for pth in (same_class, foreign):
            with open(pth, "rb") as f:
                while f.read(1 << 20):
                    pass
            self.assertGreater(native.residency(pth)["fraction"], 0.9)
        record("mincore_cold_file_controls", {"controller_cold_root_owned": 0.0, "controller_cold_foreign": 0.0,
                                              "capless_foreign_fallback": fb, "capless_root_owned_cold": own,
                                              "meaning": "capless reading 1.0 IS the can_do_mincore fallback; residency must be measured only by the controller; the fallback overstates, so it fails closed"})

    def test_M14_resident_elsewhere_nonzero_after_import(self):
        import decimal
        files = controller.default_elsewhere_files()
        rep = tel.residency_report(files)
        self.assertGreater(len(files), 10)
        self.assertGreater(rep["resident_bytes"], 0)
        dec = getattr(decimal, "__file__", None)
        pyc = controller._pyc_of(dec) if dec else None
        self.assertTrue(pyc and os.path.isfile(pyc), "decimal was loaded from cached bytecode")
        self.assertGreater(native.residency(pyc)["resident_bytes"], 0, "the imported module's bytecode file is resident")
        self.assertIn(os.path.realpath(pyc), files)
        self.assertTrue(any(f.endswith(".so") or ".so." in f for f in files), "shared libraries mapped by the process are in the set")
        record("resident_elsewhere_default_set", {"n_files": len(files), "resident_bytes": rep["resident_bytes"],
                                                  "size": rep["size"], "fraction": rep["fraction"]})

    def test_M14_kmem_included_in_usage_probe(self):
        self.cg = OwnedCg(mem_limit=256 * MIB)
        d = self.scr.mkdir("dentries")
        self.cg.spawn("wait_go()\nfor i in range(30000):\n    open(os.path.join(%r, 'f%%d' %% i), 'w').close()\n"
                      "say('made')\ntime.sleep(30)\n" % d)
        self.cg.join()
        b = self.cg.mem()
        self.cg.go()
        self.assertEqual(self.cg.readline(timeout=120), "made")
        a = self.cg.mem()
        d_kmem = a["kmem_usage_bytes"] - b["kmem_usage_bytes"]
        d_usage = a["cg_usage_bytes"] - b["cg_usage_bytes"]
        d_rest = (a["cg_anon_bytes"] + a["cache_bytes"]) - (b["cg_anon_bytes"] + b["cache_bytes"])
        res = {"delta_kmem_usage": d_kmem, "delta_usage": d_usage, "delta_anon_plus_cache": d_rest,
               "kmem_accounting_active": d_kmem > 0}
        if d_kmem > 0:
            res["kmem_included_in_usage"] = bool(d_usage - d_rest >= 0.5 * d_kmem)
        record("kmem_probe_30000_files", res)
        self.assertEqual(len(os.listdir(d)), 30000, "the probe workload ran")
        self.assertGreater(d_kmem, MIB, "kmem accounting is active in this cgroup")
        self.assertTrue(res["kmem_included_in_usage"], "usage_in_bytes includes kmem on this host (the gate metric covers it)")

    def test_M14_residual_after_exit(self):
        self.cg = OwnedCg(mem_limit=256 * MIB)
        path = self.scr.path("resid")
        self.cg.spawn("wait_go()\nfd = os.open(%r, os.O_WRONLY | os.O_CREAT, 0o600)\n"
                      "for i in range(8): os.write(fd, b'q' * (1 << 20))\nos.close(fd)\nsay('done')\n" % path)
        self.cg.join()
        self.cg.go()
        self.assertEqual(self.cg.readline(), "done")
        self.cg.child.wait(timeout=10)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not __import__("n2h.cg", fromlist=["x"]).is_empty(self.cg.lay, self.cg.fs):
            time.sleep(0.02)
        m = self.cg.mem()
        record("residual_after_exit_8MiB_written", {"usage_after_exit": m["cg_usage_bytes"], "cache_after_exit": m["cache_bytes"],
                                                    "anon_after_exit": m["cg_anon_bytes"], "peak": m["cg_charged_peak_bytes"]})
        self.assertEqual(m["cg_anon_bytes"], 0, "no anonymous memory remains after exit")
        self.assertGreaterEqual(m["cg_charged_peak_bytes"], 8 * MIB)


if __name__ == "__main__":
    unittest.main()
