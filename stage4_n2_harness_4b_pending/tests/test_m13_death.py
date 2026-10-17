"""M13: parent death at each launch state, namespace teardown of stubborn descendants, reaping order."""
import json
import os
import signal
import subprocess
import time
import unittest

from n2h import cg as cgmod
from n2h import ledger as L
from n2h import pidfd, term
from n2h.fsio import RealFs
from tests import helpers as H

FINDINGS = os.path.join(H.HARNESS, "results", "findings-m13.json")


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


def stat_of(pid):
    try:
        txt = open("/proc/%d/stat" % pid).read()
        rest = txt[txt.rindex(")") + 2:].split()
        return rest[0], int(rest[1])
    except (OSError, ValueError):
        return None


class Base(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:30])
        self.extra_scr = []
        self.sleepers = H.Sleepers(3)

    def tearDown(self):
        alive = self.sleepers.all_alive()
        self.sleepers.close()
        for s in self.extra_scr:
            s.cleanup()
        self.scr.cleanup()
        self.assertTrue(alive, "unrelated sleepers survived")
        self.assertEqual(H.no_n2_cgroups(), [])

    def kill_at(self, point, extra=None, work=None, settle=0.0, policy_over=None, cleanup=True):
        scr = H.Scratch("m13k")
        self.extra_scr.append(scr)
        w = H.make_world(scr, work=work or {"mode": "sleep", "seconds": 60}, policy_over=policy_over)
        H.register_orphan(scr, w)
        lay = H.layout_for(H.attempt_name(w))
        spec = H.spec_path(w, dict({"pause_at": [point], "pause_s": 90}, **(extra or {})))
        p, rf = H.spawn_driver(scr, spec)
        try:
            line = H.read_until(p, "AT " + point)
            self.assertIsNotNone(line, "driver never reached %s: %r" % (point, p.stderr.read(2000) if p.poll() is not None else ""))
            parts = dict(x.split("=") for x in line.split()[2:])
            ctrl = int(parts["ctrl"])
            child = int(parts["child"])
            time.sleep(settle)
            fs = RealFs()
            members = cgmod.members(lay, fs) if os.path.isdir(lay.abs["memory"]) else []
            pre = {pid: stat_of(pid) for pid in members}
            t0 = time.monotonic()
            p.kill()
            p.wait()
            series = {pid: [] for pid in set(members) | {child}}
            end = time.monotonic() + 10
            last = {}
            while time.monotonic() < end:
                for pid in series:
                    cur = stat_of(pid)
                    if cur != last.get(pid):
                        series[pid].append((round((time.monotonic() - t0) * 1000, 1), cur))
                        last[pid] = cur
                if all(stat_of(pid) is None for pid in series):
                    break
                time.sleep(0.002)
            gone_ms = round((time.monotonic() - t0) * 1000, 1)
            all_gone = all(stat_of(pid) is None for pid in series)
            empty = cgmod.is_empty(lay, fs) if os.path.isdir(lay.abs["memory"]) else None
            pids_current = cgmod.counters(lay, fs)["pids_current"] if os.path.isdir(lay.abs["memory"]) else None
            if cleanup and os.path.isdir(lay.abs["memory"]):
                from n2h.controller import cleanup_test_orphan
                self.assertTrue(cleanup_test_orphan(lay))
            return {"w": w, "lay": lay, "ctrl": ctrl, "child": child, "members_before": members, "pre": pre,
                    "series": {str(k): v for k, v in series.items()}, "gone_ms": gone_ms, "all_gone": all_gone,
                    "empty": empty, "pids_current": pids_current, "stderr": open(os.path.join(w["slot_root"], "stderr.log")).read()
                    if os.path.exists(os.path.join(w["slot_root"], "stderr.log")) else ""}
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            p.stdout.close()
            p.stderr.close()


class ParentDeath(Base):
    def test_M13_kill_controller_at_S0(self):
        r = self.kill_at("at_S0")
        self.assertTrue(r["all_gone"], r["series"])
        self.assertLess(r["gone_ms"], 6000, "bound is generous: host PID 1 reaps orphans ~1.9 s after death")
        self.assertNotIn("N2M", r["stderr"], "the gated child never exec'd")
        self.assertTrue(r["empty"])
        record("S0_pdeathsig", {"gone_ms": r["gone_ms"], "series": r["series"]})
        # gate EOF alone (PDEATHSIG disabled by injection): proves the child's copy of gate_w was closed
        r2 = self.kill_at("at_S0", extra={"inject": {"skip_pdeathsig": True}})
        self.assertTrue(r2["all_gone"], r2["series"])
        self.assertNotIn("N2M", r2["stderr"])
        self.assertLess(r2["gone_ms"], 6000)
        record("S0_gate_eof_only", {"gone_ms": r2["gone_ms"], "series": r2["series"]})

    def test_M13_kill_controller_at_S1(self):
        r = self.kill_at("at_S1")
        self.assertTrue(r["all_gone"], r["series"])
        self.assertNotIn("N2M", r["stderr"])
        self.assertEqual(len(r["members_before"]), 1, "only the gated child is a member at S1")
        self.assertTrue(r["empty"])
        record("S1_pdeathsig", {"gone_ms": r["gone_ms"], "series": r["series"]})
        r2 = self.kill_at("at_S1", extra={"inject": {"skip_pdeathsig": True}})
        self.assertTrue(r2["all_gone"], r2["series"])
        self.assertNotIn("N2M", r2["stderr"])
        record("S1_gate_eof_only", {"gone_ms": r2["gone_ms"]})

    def test_M13_kill_controller_after_release(self):
        r = self.kill_at("after_release", settle=0.8, cleanup=False)
        self.assertGreaterEqual(len(r["members_before"]), 2, r["members_before"])  # unshare + ns-init
        self.assertIn("ready", r["stderr"])
        self.assertTrue(r["all_gone"], r["series"])
        self.assertTrue(r["empty"], "the whole namespace is gone and the cgroup is empty")
        self.assertLess(r["gone_ms"], 6000)
        record("after_release_controller_killed", {"gone_ms": r["gone_ms"], "members_before": r["members_before"],
                                                   "pre": {str(k): v for k, v in r["pre"].items()}, "series": r["series"]})
        # leftover empty cgroups block the next start (M12), and nothing cleans them behind our back
        scr2 = H.Scratch("m13next")
        self.extra_scr.append(scr2)
        w2 = H.make_world(scr2, slot="H0")
        res, p = H.run_driver(scr2, H.spec_path(w2), tag="next")
        self.assertEqual(res["refused"]["code"], "existing_n2_cgroup")
        from n2h.controller import cleanup_test_orphan
        self.assertTrue(cleanup_test_orphan(r["lay"]))

    def test_M13_main_thread_exit_pdeathsig(self):
        probe = os.path.join(H.HARNESS, "tests", "pdeath_probe.py")
        out = {}
        for mode in ("forker_thread_exits", "main_exits"):
            p = subprocess.Popen([H.PY, "-I", "-S", "-B", "-u", probe, mode], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                ppid, cpid = [int(x) for x in p.stdout.readline().split()]
                t0 = time.monotonic()
                died = H.pid_gone(cpid, 4.0)
                dt = round((time.monotonic() - t0) * 1000, 1)
                parent_alive_then = stat_of(ppid) is not None and p.poll() is None
                out[mode] = {"child_killed": died, "ms": dt, "probe_process_still_alive": parent_alive_then,
                             "probe_stderr": ""}
                self.assertTrue(died, mode)
            finally:
                p.kill()
                p.wait()
                out[mode]["probe_stderr"] = p.stderr.read()[:200]
                p.stdout.close()
                p.stderr.close()
        self.assertTrue(out["forker_thread_exits"]["probe_process_still_alive"],
                        "PDEATHSIG fired although the process lived: it follows the forking THREAD")
        record("pdeathsig_follows_forking_thread", out)
        # design consequence: the controller is single-threaded (sampler runs in the forking main thread)
        pkg = os.path.join(H.HARNESS, "n2h")
        for fn in os.listdir(pkg):
            if fn.endswith(".py") and fn not in ("launch.py", "stubsrc.py"):
                self.assertNotIn("threading.Thread(", open(os.path.join(pkg, fn)).read(), fn)


class ExternalSignal(Base):
    def test_M13_sigterm_to_process_group_graceful_terminal_receipt(self):
        # L13 premise (measured by the manual smoke): a stopped Bash task gets SIGTERM for the whole process group
        w = H.make_world(self.scr, work={"mode": "sleep", "seconds": 60})
        spec = H.spec_path(w)
        sf, rf = self.scr.path("g.spec.json"), self.scr.path("g.result.json")
        with open(sf, "w") as f:
            json.dump(spec, f)
        p = subprocess.Popen(H.driver_cmd(sf, rf), stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.scr.root,
                             start_new_session=True)  # its own group, created by this test
        try:
            err = os.path.join(w["slot_root"], "stderr.log")
            end = time.monotonic() + 30
            while time.monotonic() < end and not (os.path.exists(err) and " ready " in open(err).read()):
                time.sleep(0.02)
            self.assertIn(" ready ", open(err).read())
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=60)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            p.stdout.close()
            p.stderr.close()
        res = json.load(open(rf))
        t = res["terminal"]
        self.assertIn(t["status"], ("stopped", "fail"))
        self.assertIn(t["stops"][0]["code"], ("external_termination_request", "child_nonzero_exit", "ns_init_killed_or_unknown_exit"), t["stops"])
        self.assertIs(t["post"]["cgroups_removed"], True)
        self.assertTrue(t["external_signals"] in ([int(signal.SIGTERM)], []))
        key = L.attempt_key(w["approval_sha"], "H0")
        self.assertEqual(L.Ledger(w["control_root"]).state(key), "terminal", "a durable terminal receipt exists")
        self.assertEqual(H.no_n2_cgroups(), [])
        record("sigterm_to_group", {"status": t["status"], "stops": t["stops"], "external_signals": t["external_signals"]})


class Teardown(Base):
    def test_M13_grandchild_setsid_ignoring_sigterm_killed(self):
        for init_ignores in (False, True):
            scr = H.Scratch("m13gc%d" % init_ignores)
            try:
                w = H.make_world(scr, work={"mode": "spawn", "n": 2, "grandchild_nested": True, "seconds": 60,
                                            "init_ignores_sigterm": init_ignores},
                                 policy_over={"per_slot_elapsed_cap_s": 1.5, "graceful_wait_s": 1})
                res, p = H.run_driver(scr, H.spec_path(w))
                t = res["terminal"]
                self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
                rows = [json.loads(line) for line in open(os.path.join(w["slot_root"], "telemetry.jsonl"))]
                ever = set()
                for r in rows:
                    ever.update(r["listing"])
                    ever.update(r["walk"]["descendants"])
                self.assertGreaterEqual(len(ever), 6, "unshare, ns-init, 2 children and 2 nested grandchildren were observed")
                for pid in ever:
                    self.assertTrue(H.pid_gone(pid, 3.0), "pid %d survived" % pid)
                tm = t["termination"]
                self.assertTrue(tm["emptied"])
                self.assertEqual(tm["zombies"], [])
                self.assertEqual(tm["unowned"], [])
                self.assertEqual(bool(tm["graceful"]), not init_ignores)
                record("stubborn_descendants_init_ignores_%s" % init_ignores, {"pids_observed": len(ever), "steps": tm["steps"],
                                                                                 "reap": tm["reap"], "zombie_obs": tm["zombie_observations"]})
                self.assertTrue(self.sleepers.all_alive())
                self.assertEqual(H.no_n2_cgroups(), [])
            finally:
                scr.cleanup()

    def test_M13_reaping_order_parent_death_path(self):
        r = self.kill_at("after_release", settle=0.8)
        self.assertTrue(r["all_gone"])
        obs = {}
        for pid, series in r["series"].items():
            ppids = [s[1][1] for s in series if s[1]]
            states = [s[1][0] for s in series if s[1]]
            obs[pid] = {"ppids_seen": sorted(set(ppids)), "states_seen": states, "gone": series[-1][1] is None}
        zombies = [pid for pid, o in obs.items() if "Z" in o["states_seen"]]
        record("parent_death_reaping", {"observations": obs, "zombie_pids_seen": zombies, "gone_ms": r["gone_ms"],
                                        "who_reaps": "see ppids_seen of the ns-init: the new parent after the controller died"})
        self.assertTrue(r["empty"], "no zombie or nonzero pids.current persisted")
        self.assertEqual(r["pids_current"], 0)

    def test_M13_reaping_order_hard_path(self):
        w = H.make_world(self.scr, work={"mode": "ignore_sigterm", "seconds": 60},
                         policy_over={"per_slot_elapsed_cap_s": 1, "graceful_wait_s": 1})
        res, p = H.run_driver(self.scr, H.spec_path(w))
        t = res["terminal"]
        tm = t["termination"]
        self.assertEqual(t["status"], "stopped", (t["stops"], p.stderr))
        self.assertEqual(tm["pids_current"], 0)
        self.assertEqual(tm["zombies"], [])
        self.assertTrue(tm["emptied"])
        ns = tm["reap"].get("ns_init")
        d = tm["reap"]["direct"]
        self.assertTrue(d["reaped_by_controller"])
        record("hard_path_reaping", {
            "direct_unshare_status": d["status"], "ns_init": ns, "zombie_observations": tm["zombie_observations"],
            "steps": tm["steps"], "ns_init_exit_status_survives_in_unshare_status": d["status"],
            "launch_subreaper": 1})
        self.assertIn(d["status"]["kind"], ("exit", "signal"))

    def test_M13_mock_unowned_member_reported_not_killed(self):
        from tests.mockcg import MockWorld
        mw = MockWorld(self.scr)
        try:
            a = mw.spawn("pass")
            b = mw.spawn("pass")
            ma = pidfd.open_verified(a.pid, mw.layout, mw.fs, "direct", cg_check=False)
            mb = pidfd.open_verified(b.pid, mw.layout, mw.fs, "ns_init", cg_check=False)
            a.wait()
            b.wait()
            stranger = mw.spawn()
            mw.set_listing([stranger.pid])  # something is still in the cgroup after both owners are gone
            ctx = mw.telemetry(ma, ns_init=mb).ctx
            res = {}
            steps = []
            term._finish(ctx, mw.layout, mw.fs, res, lambda name, **kw: steps.append(name))
            self.assertEqual(res["unowned"], [stranger.pid])
            self.assertIn("unowned_members_reported_not_killed", steps)
            self.assertIsNone(stranger.poll(), "an unowned member is reported, never killed")
            self.assertTrue(stat_of(stranger.pid) is not None)
            # while an owner is still alive, members that remain are merely 'remaining', not unowned
            alive_owner = mw.spawn()
            mo = pidfd.open_verified(alive_owner.pid, mw.layout, mw.fs, "direct", cg_check=False)
            ctx2 = mw.telemetry(mo, ns_init=None).ctx
            res2 = {}
            term._finish(ctx2, mw.layout, mw.fs, res2, lambda *a, **k: None)
            self.assertEqual(res2["unowned"], [])
        finally:
            mw.close()

    def test_M13_cgroup_kill_skipped_when_unowned_present(self):
        from tests.mockcg import MockWorld
        mw = MockWorld(self.scr)
        try:
            policy = __import__("n2h.policy", fromlist=["x"]).synthetic_test_policy(graceful_wait_s=1)
            stubborn = ("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('up', flush=True)\n"
                        "time.sleep(60)\n")
            kill_file = mw.layout.abs["v2"] + "/cgroup.kill"
            for with_stranger in (False, True):
                open(kill_file, "w").close()
                a = mw.spawn(stubborn)
                a.stdout.readline()
                nsi = pidfd.open_verified(a.pid, mw.layout, mw.fs, "ns_init", cg_check=False)
                direct = pidfd.open_verified(mw.spawn("pass").pid, mw.layout, mw.fs, "direct", cg_check=False)
                listing = [a.pid]
                stranger = None
                if with_stranger:
                    helper = mw.spawn("import os, time\nif os.fork() == 0:\n    os.setsid()\n    print(os.getpid(), flush=True)\n"
                                      "    time.sleep(60)\n    os._exit(0)\nos._exit(0)\n")
                    stranger = int(helper.stdout.readline())
                    helper.wait()
                    listing.append(stranger)
                mw.set_listing(listing)
                ctx = mw.telemetry(direct, ns_init=nsi).ctx
                mw.policy = policy
                from n2h.errors import FaultPlan
                res = term.terminate(ctx, mw.layout, mw.fs, policy, FaultPlan(["INJECTED_E_SIGKILL_VIA_FD"]), "mock")
                written = open(kill_file).read()
                if with_stranger:
                    self.assertEqual(written, "", "cgroup.kill must not be written while an unowned process is listed")
                    self.assertFalse(res["used_cgroup_kill"])
                    self.assertEqual(res["unowned"], [stranger])
                    self.assertIn("cgroup_kill_skipped_unowned_present", [s["step"] for s in res["steps"]])
                    self.assertTrue(stat_of(stranger) is not None)
                    os.kill(stranger, signal.SIGKILL)  # created by this test
                else:
                    self.assertEqual(written, "1")
                    self.assertTrue(res["used_cgroup_kill"])
                a.kill()
                a.wait()
        finally:
            mw.close()

    def test_M13_unshare_killed_only_after_bounded_wait(self):
        from n2h import policy as policy_mod
        from n2h.errors import FaultPlan
        from tests.mockcg import MockWorld, w
        mw = MockWorld(self.scr)
        try:
            pol = policy_mod.synthetic_test_policy(graceful_wait_s=1, unshare_reap_wait_s=1, cgroup_kill_wait_s=1)
            stubborn = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('up', flush=True)\ntime.sleep(60)\n"
            nsi_p, direct_p = mw.spawn(stubborn), mw.spawn(stubborn)
            nsi_p.stdout.readline()
            direct_p.stdout.readline()
            nsi = pidfd.open_verified(nsi_p.pid, mw.layout, mw.fs, "ns_init", cg_check=False)
            direct = pidfd.open_verified(direct_p.pid, mw.layout, mw.fs, "direct", cg_check=False)
            mw.set_listing([])
            w(mw.layout.abs["v2"] + "/cgroup.events", "populated 0\nfrozen 0\n")
            ctx = mw.telemetry(direct, ns_init=nsi).ctx
            res = term.terminate(ctx, mw.layout, mw.fs, pol, FaultPlan(), "mock")
            names = [x["step"] for x in res["steps"]]
            self.assertEqual(names[:5], ["sigterm_ns_init", "graceful_wait", "sigkill_ns_init", "wait_unshare_exit", "sigkill_direct"])
            wait = [x for x in res["steps"] if x["step"] == "wait_unshare_exit"][0]
            self.assertIs(wait["exited"], False, "unshare is given unshare_reap_wait_s to exit by itself first")
            d, n = res["steps"][4]["t_ms"], res["steps"][2]["t_ms"]
            self.assertGreaterEqual(d - n, 900, "SIGKILL to unshare only after the bounded wait")
            self.assertTrue(pidfd.readable(nsi.fd) and pidfd.readable(direct.fd))
        finally:
            mw.close()

    def test_M13_mock_pidns_mismatch_not_signalled(self):
        from tests.mockcg import MockWorld
        mw = MockWorld(self.scr)
        try:
            a, b = mw.spawn(), mw.spawn()
            nsi = pidfd.open_verified(a.pid, mw.layout, mw.fs, "ns_init", cg_check=False)
            other = pidfd.open_verified(b.pid, mw.layout, mw.fs, "member", cg_check=False)
            other.pidns = (other.pidns[0], other.pidns[1] + 7)
            with self.assertRaises(Exception) as cm:
                pidfd.send(other, signal.SIGKILL, ns_init=nsi)
            self.assertEqual(cm.exception.code, "member_pidns_mismatch_not_signalled")
            self.assertIsNone(b.poll())
            self.assertTrue(stat_of(b.pid) is not None and stat_of(b.pid)[0] != "Z")
            _ = L
        finally:
            mw.close()


if __name__ == "__main__":
    unittest.main()
