"""M08-L: native layout / unit conversion, distinct memory fields, STOP on failures, series integrity."""
import json
import os
import unittest

from n2h import native, sysfs
from n2h import policy as policy_mod
from n2h import telemetry as tel
from n2h.errors import ContainmentStop, InjectedFault, N2Stop, TelemetryStop
from n2h.fsio import RealFs
from tests import helpers as H
from tests.mockcg import STAT, MockWorld, w


class M08(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch("m08")
        self.mw = MockWorld(self.scr)

    def tearDown(self):
        self.mw.close()
        self.scr.cleanup()

    def test_M08_unit_conversions_kb_bytes_max_token_sentinel(self):
        self.assertEqual(sysfs.parse_meminfo_bytes("MemAvailable:  2048 kB\n", "MemAvailable"), 2048 * 1024)
        with self.assertRaises(TelemetryStop):
            sysfs.parse_meminfo_bytes("MemAvailable:  2048 MB\n", "MemAvailable")
        with self.assertRaises(TelemetryStop):
            sysfs.parse_meminfo_bytes("MemAvailable:  2048\n", "MemAvailable")
        with self.assertRaises(TelemetryStop):
            sysfs.parse_meminfo_bytes("Other: 1 kB\n", "MemAvailable")
        self.assertIsNone(sysfs.parse_limit("max\n"))
        self.assertIsNone(sysfs.parse_limit("9223372036854771712\n"))
        self.assertIsNone(sysfs.parse_limit("9223372036854775807\n"))
        self.assertEqual(sysfs.parse_limit("12345\n"), 12345)
        with self.assertRaises(TelemetryStop):
            sysfs.parse_limit("12x\n")
        with self.assertRaises(TelemetryStop):
            sysfs.parse_kv("a 1 2\n")
        self.assertEqual(sysfs.parse_kv("a 1\nb 2\n"), {"a": 1, "b": 2})
        st = tel.parse_status_fields("VmRSS:\t 100 kB\nVmHWM:\t 200 kB\nRssAnon:\t 1 kB\nRssFile:\t 2 kB\n"
                                     "RssShmem:\t 3 kB\nVmData:\t 4 kB\nVmPeak:\t 5 kB\nThreads:\t 7\n")
        self.assertEqual((st["VmRSS"], st["Threads"]), (100 * 1024, 7))
        with self.assertRaises(TelemetryStop):
            tel.parse_status_fields("VmRSS:\t 100 B\n")
        self.assertEqual(sysfs.PAGE, os.sysconf("SC_PAGE_SIZE"))

    def test_M08_four_memory_fields_distinct_no_commit_name(self):
        self.mw.set_mem(usage=100 << 20, peak=150 << 20, limit=192 << 20)
        m = sysfs.read_memory_fields(self.mw.fs, self.mw.layout.abs["memory"])
        self.assertEqual(m["cg_usage_bytes"], 100 << 20)
        self.assertEqual(m["cg_charged_peak_bytes"], 150 << 20)
        self.assertEqual(m["cg_working_set_bytes"], (100 << 20) - 1024)  # usage - inactive_file
        self.assertEqual(m["cg_anon_bytes"], 8192)  # rss + shmem
        self.assertEqual(len({m["cg_usage_bytes"], m["cg_charged_peak_bytes"], m["cg_working_set_bytes"],
                              m["cg_anon_bytes"]}), 4)
        for k in m:
            self.assertNotIn("commit", k.lower())
            self.assertNotIn("pagefile", k.lower())
        p = self.mw.spawn()
        d = self.mw.member(p)
        self.mw.set_listing([p.pid])
        t = self.mw.telemetry(d)
        s = t.sample()
        self.assertEqual(s["host_commit_unenforced"]["label"], "not_enforced")
        self.assertEqual(s["members"][str(p.pid)]["status"]["VmRSS"] > 0, True)
        for key in ("VmRSS", "VmHWM", "RssAnon", "RssFile", "RssShmem", "VmData", "VmPeak", "Threads"):
            self.assertIn(key, s["members"][str(p.pid)]["status"])

    def test_M08_read_failure_alive_child_stops(self):
        p = self.mw.spawn()
        d = self.mw.member(p)
        self.mw.set_listing([p.pid])
        t = self.mw.telemetry(d)
        t.sample()
        self.mw.fs.raise_on["memory.stat"] = InjectedFault("INJECTED_E_READ")
        with self.assertRaises(TelemetryStop) as cm:
            t.sample()
        self.assertEqual(cm.exception.code, "sample_read_failed")
        self.assertEqual(cm.exception.injected, "INJECTED_E_READ")
        del self.mw.fs.raise_on["memory.stat"]
        w(self.mw.layout.abs["memory"] + "/memory.stat", "total_cache notanumber\n")
        with self.assertRaises(TelemetryStop) as cm:
            t.sample()
        self.assertEqual(cm.exception.code, "parse_error")
        w(self.mw.layout.abs["memory"] + "/memory.stat", "total_cache 1\n")  # missing keys: also STOP, not zero
        with self.assertRaises(TelemetryStop):
            t.sample()
        w(self.mw.layout.abs["memory"] + "/memory.stat", STAT)
        # member status read failure while alive
        self.mw.fs.raise_on["/status"] = InjectedFault("INJECTED_E_READ")
        with self.assertRaises(TelemetryStop) as cm:
            t.sample()
        self.assertEqual(cm.exception.code, "member_read_failed")

    def test_M08_missing_live_member_containment_stop(self):
        p = self.mw.spawn()
        d = self.mw.member(p)
        self.mw.set_listing([])  # alive but absent: a live member that left the cgroup
        t = self.mw.telemetry(d)
        with self.assertRaises(ContainmentStop) as cm:
            t.sample()
        self.assertEqual(cm.exception.code, "live_member_absent_from_listing")
        # exit is only an exit when the pidfd is readable
        self.mw.set_listing([p.pid])
        t2 = self.mw.telemetry(d)
        t2.sample()
        p.kill()
        p.wait()
        self.mw.set_listing([])
        t3 = self.mw.telemetry(d)
        s = t3.sample()  # exited member absent from listing is fine
        self.assertFalse(s["members"][str(p.pid)]["alive"])

    def test_M08_descendant_outside_listing_stop(self):
        code = ("import os,time\npid=os.fork()\nif pid==0:\n time.sleep(60)\nelse:\n"
                " print(pid, flush=True)\n time.sleep(60)\n")
        p = self.mw.spawn(code)
        gc = int(p.stdout.readline())
        nsi = self.mw.member(p, role="ns_init")
        self.mw.set_listing([p.pid])  # listing lacks the grandchild
        self.mw.contain_overlay(gc)  # grandchild still in the attempt cgroup: forked after listing -> tolerated
        t = self.mw.telemetry(nsi, ns_init=nsi)
        s = t.sample()
        self.assertIn(gc, s["walk"]["unlisted"])
        self.mw.contain_overlay(gc, self.mw.outside_cgroup_text())  # now it escaped
        with self.assertRaises(ContainmentStop) as cm:
            self.mw.telemetry(nsi, ns_init=nsi).sample()
        self.assertEqual(cm.exception.code, "descendant_outside_attempt_cgroup")
        os_pids = [gc]
        for q in os_pids:
            try:
                os.kill(q, 9)  # test-created grandchild of a test-created process
            except OSError:
                pass

    def test_M08_sample_sequence_missed_dup_out_of_order_clock_back(self):
        p = self.mw.spawn()
        d = self.mw.member(p)
        self.mw.set_listing([p.pid])
        ticks = iter([1_000_000_000, 1_020_000_000, 1_010_000_000])
        t = self.mw.telemetry(d, mono=lambda: next(ticks), real=lambda: 5)
        t.sample()
        t.sample()
        with self.assertRaises(TelemetryStop) as cm:
            t.sample()
        self.assertEqual(cm.exception.code, "clock_went_backwards")
        gap = iter([0, 5_000_000_000])
        t2 = self.mw.telemetry(d, mono=lambda: next(gap), real=lambda: 5)
        t2.sample()
        with self.assertRaises(TelemetryStop) as cm:
            t2.sample()
        self.assertEqual(cm.exception.code, "sample_gap_exceeded")
        # persisted-series validation
        base = [{"seq": i, "mono_ns": i * 10_000_000} for i in (1, 2, 3)]
        self.assertEqual(tel.validate_series(base), [])
        self.assertEqual(tel.validate_series([base[0], base[2]]), [(1, "missed_samples")])
        self.assertEqual(tel.validate_series([base[0], dict(base[0])]), [(1, "duplicate_seq")])
        self.assertEqual(tel.validate_series([base[1], base[0]])[0], (1, "out_of_order_seq"))
        back = [base[0], {"seq": 2, "mono_ns": 1}]
        self.assertIn((1, "clock_backwards"), tel.validate_series(back))
        self.assertIn((1, "gap_exceeded"), tel.validate_series([base[0], {"seq": 2, "mono_ns": 9_000_000_000}], 2000))

    def test_M08_effective_available_mock_sysfs(self):
        fs = RealFs()
        self.mw.set_meminfo(8 << 30)
        r = sysfs.effective_available(fs, self.mw.mem_root, "/ctrl", self.mw.fs.over["/proc/meminfo"])
        # ctrl limit 14e9 - usage 1e8 = 13.9e9 ; host 8 GiB ; root unlimited skipped
        self.assertEqual(r["terms"]["host_mem_available"], 8 << 30)
        self.assertEqual(r["terms"]["ancestor:/ctrl"], 14_000_000_000 - 100_000_000)
        self.assertNotIn("ancestor:/", r["terms"])
        self.assertEqual(r["effective_available_bytes"], 8 << 30)
        w(self.mw.mem_root + "/ctrl/memory.limit_in_bytes", "1000000000\n")
        r = sysfs.effective_available(fs, self.mw.mem_root, "/ctrl", self.mw.fs.over["/proc/meminfo"])
        self.assertEqual(r["effective_available_bytes"], 900_000_000)
        w(self.mw.mem_root + "/memory.limit_in_bytes", "500000000\n")
        w(self.mw.mem_root + "/memory.usage_in_bytes", "450000000\n")
        r = sysfs.effective_available(fs, self.mw.mem_root, "/ctrl", self.mw.fs.over["/proc/meminfo"])
        self.assertEqual(r["effective_available_bytes"], 50_000_000)
        self.assertEqual(len(r["ancestors"]), 2)
        # the nested sentinel in v2 style
        w(self.mw.mem_root + "/ctrl/memory.limit_in_bytes", "max\n")
        r = sysfs.effective_available(fs, self.mw.mem_root, "/ctrl", self.mw.fs.over["/proc/meminfo"])
        self.assertNotIn("ancestor:/ctrl", r["terms"])

    def test_M08_bash_growth_mock_sysfs(self):
        fs = RealFs()
        w(self.mw.mem_root + "/ctrl/memory.usage_in_bytes", "300000000\n")
        w(self.mw.layout.abs["memory"] + "/memory.usage_in_bytes", "50000000\n")
        v = sysfs.bash_usage_excluding_attempt(fs, self.mw.mem_root, "/ctrl", "n2-mock")
        self.assertEqual(v, 250_000_000)
        base = 100_000_000
        self.mw.policy = policy_mod.synthetic_test_policy(bash_cgroup_growth_max_bytes=100 << 20,
                                                          attempt_memory_limit_bytes=192 << 20)
        p = self.mw.spawn()
        d = self.mw.member(p)
        self.mw.set_listing([p.pid])
        t = self.mw.telemetry(d)
        t.ctx.baseline = {"bash_usage_excluding_attempt_bytes": base}
        s = t.sample()
        br = tel.evaluate_breaches(t.ctx, s, 1.0)
        self.assertIn(("bash_cgroup_growth", 150_000_000), br)  # 250M - 100M > 100 MiB (strict cap)
        t.ctx.baseline = {"bash_usage_excluding_attempt_bytes": 200_000_000}
        s2 = t.sample()
        self.assertNotIn("bash_cgroup_growth", [b[0] for b in tel.evaluate_breaches(t.ctx, s2, 1.0)])

    def test_M08_memsw_check_only_after_exit(self):
        with self.assertRaises(TelemetryStop) as cm:
            sysfs.memsw_check(100, 100, 10, exited=False)
        self.assertEqual(cm.exception.code, "memsw_check_before_exit")
        self.assertEqual(sysfs.memsw_check(100, 105, 10, exited=True), (5, False))
        self.assertEqual(sysfs.memsw_check(100, 150, 10, exited=True), (50, True))

    def test_M08_residency_from_mock_mincore_vectors(self):
        pg = native.PAGE
        r = native.residency_from_vector([1, 0, 1, 1], 4 * pg)
        self.assertEqual((r["resident_bytes"], r["fraction"]), (3 * pg, 0.75))
        r = native.residency_from_vector([0, 0, 1], 2 * pg + 100)  # partial last page counts its real bytes
        self.assertEqual(r["resident_bytes"], 100)
        self.assertEqual(native.residency_from_vector([], 0)["fraction"], 0.0)
        with self.assertRaises(N2Stop):
            native.residency_from_vector([1, 1], 5 * pg)
        pol = self.mw.policy  # synthetic threshold 0.5, strict
        tel.check_cold_inputs({"fraction": 0.5}, pol)
        with self.assertRaises(N2Stop) as cm:
            tel.check_cold_inputs({"fraction": 0.51}, pol)
        self.assertEqual(cm.exception.code, "cold_input_residency_above_threshold")

    def test_M08_real_sample_fields_complete(self):
        scr = self.scr
        wd = H.make_world(scr, work={"mode": "sleep", "seconds": 0.8})
        res, p = H.run_driver(scr, H.spec_path(wd))
        t = res["terminal"]
        self.assertEqual(t["status"], "success", (t["stops"], p.stderr))
        rows = [json.loads(line) for line in open(os.path.join(wd["slot_root"], "telemetry.jsonl"))]
        self.assertGreater(len(rows), 10)
        self.assertEqual(tel.validate_series(rows, 2000), [])
        s = rows[len(rows) // 2]
        for k in ("seq", "mono_ns", "real_ns", "listing", "walk", "memory", "counters", "effective_available_bytes",
                  "available_terms", "bash_usage_excluding_attempt_bytes", "disk_free_bytes", "members",
                  "host_commit_unenforced"):
            self.assertIn(k, s)
        for k in ("cg_usage_bytes", "cg_charged_peak_bytes", "cg_working_set_bytes", "cg_anon_bytes", "failcnt",
                  "kmem_usage_bytes", "oom_kill", "under_oom", "cache_bytes", "dirty_bytes"):
            self.assertIn(k, s["memory"])
        self.assertIn("pids_current", s["counters"])
        alive = [m for m in s["members"].values() if m["alive"]]
        self.assertTrue(alive)
        for m in alive:
            self.assertIn("RssAnon", m["status"])
        self.assertEqual(s["host_commit_unenforced"]["label"], "not_enforced")


if __name__ == "__main__":
    unittest.main()
