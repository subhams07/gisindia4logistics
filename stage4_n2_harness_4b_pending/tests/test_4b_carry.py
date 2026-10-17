"""Step 4b carry-forward closures, operator item (c) uid drop and item (d) private-copy inputs (stdlib suite)."""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from unittest import mock

from n2h import cg as cgmod
from n2h import cli, driver, fsio, native, slotrun, slots
from n2h import ledger as L
from n2h.errors import FaultPlan, LedgerStop, N2Stop, RefusedRun
from tests import helpers as H

sys.path.insert(0, H.HARNESS)
from tests4b.helpers4b import UID, VENV_PY, UScratch


def terminal_of(w):
    key = L.attempt_key(w["approval_sha"], w["slot"])
    led = L.Ledger(w["control_root"])
    return led, key, (led.read("terminal", key) if led.state(key) == "terminal" else None)


class Carry(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:28])

    def tearDown(self):
        self.scr.cleanup()

    def run_world(self, work=None, policy_over=None, extra=None, tag="run"):
        w = H.make_world(self.scr, work=work, policy_over=policy_over)
        res, p = H.run_driver(self.scr, H.spec_path(w, extra or {}), tag=tag)
        return w, res, p

    def test_4B_after_attempt_record_hook_inside_guarded_try(self):
        w, res, p = self.run_world(extra={"raise_in_hook": {"hook": "after_attempt_record"}})
        self.assertIn("terminal", res, (res, p.stderr))
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped")
        self.assertIn("harness_exception", [s["code"] for s in t["stops"]])
        led, key, term = terminal_of(w)
        self.assertEqual(led.state(key), "terminal")
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_4B_hostile_str_does_not_replace_outcome(self):
        w, res, p = self.run_world(extra={"raise_in_hook": {"hook": "after_attempt_record", "kind": "hostile_str"}})
        self.assertIn("terminal", res, (res, p.stderr))
        stops = {s["code"]: s["detail"] for s in res["terminal"]["stops"]}
        self.assertIn("unprintable", stops["harness_exception"])
        self.assertEqual(terminal_of(w)[0].state(terminal_of(w)[1]), "terminal")

    def test_4B_member_close_failure_is_guarded(self):
        w, res, p = self.run_world(work={"mode": "sleep", "seconds": 0.2}, extra={"member_close_fault": True})
        self.assertIn("terminal", res, (res, p.stderr))
        t = res["terminal"]
        self.assertEqual(t["status"], "stopped")
        self.assertIn("member_close_failed", [s["code"] for s in t["stops"]])
        self.assertEqual(terminal_of(w)[0].state(terminal_of(w)[1]), "terminal")
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_4B_ledger_close_oserror_does_not_replace_ledgerstop(self):
        root = self.scr.mkdir("ctl")
        L.init_mock_control_root(root, "x")
        led = L.Ledger(root, FaultPlan(["INJECTED_E_FSYNC"]))
        real_close = os.close
        state = {"n": 0}

        def bad_close(fd):
            real_close(fd)
            state["n"] += 1
            raise OSError(5, "INJECTED_E_CLOSE_EIO")
        with mock.patch.object(L.os, "close", bad_close):
            with self.assertRaises(LedgerStop) as cm:
                led.create_attempt("a" * 64, "H0", {"x": 1})
        self.assertEqual(cm.exception.code, "persist_failed")
        self.assertGreaterEqual(state["n"], 1)
        self.assertTrue(led.blocked)

    def test_4B_short_write_is_completed_and_zero_write_fails(self):
        real_write = os.write
        calls = []

        def short(fd, data):
            n = real_write(fd, bytes(data)[:5])
            calls.append(n)
            return n
        root = self.scr.mkdir("ctl")
        L.init_mock_control_root(root, "x")
        led = L.Ledger(root)
        with mock.patch.object(fsio.os, "write", short):
            led.create_attempt("b" * 64, "H0", {"x": "y" * 200})
        self.assertGreater(len(calls), 10)
        key = L.attempt_key("b" * 64, "H0")
        self.assertEqual(led.read("attempt", key)["x"], "y" * 200)  # complete and hash-valid
        root2 = self.scr.mkdir("ctl2")
        L.init_mock_control_root(root2, "x")
        led2 = L.Ledger(root2)
        with mock.patch.object(fsio.os, "write", lambda fd, data: 0):
            with self.assertRaises(LedgerStop) as cm:
                led2.create_attempt("c" * 64, "H0", {"x": 1})
        self.assertEqual(cm.exception.code, "persist_failed")
        self.assertTrue(os.path.exists(os.path.join(root2, "DISPATCH_BLOCKED")))

    def test_4B_minimal_body_serialize_failure_blocks_dispatch(self):
        w, res, p = self.run_world(extra={"finalize_unserializable": True, "ledger_faults": ["INJECTED_E_SERIALIZE_MINIMAL"]})
        self.assertIn("terminal", res, (res, p.stderr))
        codes = [s["code"] for s in res["terminal"]["stops"]]
        self.assertIn("serialize_failed", codes)
        led = L.Ledger(w["control_root"])
        key = L.attempt_key(w["approval_sha"], w["slot"])
        self.assertNotEqual(led.state(key), "terminal")  # no terminal receipt could be written
        self.assertTrue(os.path.exists(os.path.join(w["control_root"], "DISPATCH_BLOCKED")))
        with self.assertRaises(LedgerStop):
            L.Ledger(w["control_root"]).assert_dispatch_allowed()
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_4B_hooks_compose_not_overwrite(self):
        hooks, order = {}, []
        driver.add_hook(hooks, "x", lambda a: order.append(("one", a)))
        driver.add_hook(hooks, "x", lambda a: order.append(("two", a)))
        driver.add_hook(hooks, "x", lambda a: order.append(("three", a)))
        hooks["x"]("A")
        self.assertEqual(order, [("one", "A"), ("two", "A"), ("three", "A")])
        # and in a real driver run: two options that use the same hook point both take effect
        w, res, p = self.run_world(work={"mode": "sleep", "seconds": 0.2},
                                   extra={"member_close_fault": True, "marker_read_fault": True})
        self.assertIn("terminal", res, (res, p.stderr))
        codes = [s["code"] for s in res["terminal"]["stops"]]
        self.assertIn("member_close_failed", codes)
        self.assertIn("final_marker_read_failed", codes)

    def test_4B_elapsed_cap_evaluated_post_exit(self):
        w, res, p = self.run_world(work={"mode": "sleep", "seconds": 0.3}, policy_over={"per_slot_elapsed_cap_s": 2},
                                   extra={"sleep_before_monitor": 3.0})
        self.assertIn("terminal", res, (res, p.stderr))
        t = res["terminal"]
        self.assertFalse(t["exit"].get("terminated_by_controller"), t["exit"])
        self.assertEqual(t["status"], "stopped")
        self.assertIn("breach_elapsed_cap", [s["code"] for s in t["stops"]])
        # control: a quick child under the same cap is a success
        scr2 = H.Scratch("elapsed-ok")
        try:
            w2 = H.make_world(scr2, work={"mode": "sleep", "seconds": 0.2}, policy_over={"per_slot_elapsed_cap_s": 30})
            res2, p2 = H.run_driver(scr2, H.spec_path(w2))
            self.assertEqual(res2["terminal"]["status"], "success", (res2, p2.stderr))
        finally:
            scr2.cleanup()


class ReviewFixes(unittest.TestCase):
    """Opus review 1 (4b): B1 CONFIG validation, B2 symlink-safe root helpers, B4 parent receipts, N1, N6."""

    def setUp(self):
        self.scr = UScratch(self.id().split(".")[-1][5:24])
        self.arch = "archive.bin"

    def tearDown(self):
        self.scr.cleanup()

    # ---- B1
    def cli_slot(self, config, uid=True, slot="H0"):
        w = H.make_world(self.scr, slot=slot)
        root = self.scr.mkdir("slots", slot)
        args = [H.PY, "-I", "-S", "-B", "-u", os.path.join(H.HARNESS, "n2_harness.py"), "run", "--control-root", w["control_root"],
                "--approval", w["paths"]["approval"], "--policy", w["paths"]["policy"], "--scope", w["paths"]["scope"],
                "--selftest-report", w["paths"]["selftest"], "--slot", slot, "--slot-root", root, "--scratch-base", self.scr.root,
                "--interpreter", VENV_PY, "--slot-config-json",
                json.dumps(dict(config, slot=slot, repo_root=H.REPO, archive_name=self.arch,
                                out_dir=os.path.join(root, "out")))]
        if uid:
            args += ["--payload-uid", str(UID)]
        p = subprocess.run(args, capture_output=True, timeout=60, check=False)
        return w, root, p

    def assert_refused_clean(self, w, root, p, code):
        self.assertEqual(p.returncode, 3, (p.stdout, p.stderr))
        self.assertIn(code.encode(), p.stderr)
        self.assertEqual(os.listdir(os.path.join(w["control_root"], "attempts")), [], "no attempt record")
        self.assertEqual(os.listdir(root), [], "nothing created in the slot root")
        self.assertEqual(H.no_n2_cgroups(), [], "no cgroup")

    def test_4B_config_repo_data_dir_refused_before_any_attempt(self):
        w, root, p = self.cli_slot({"data_dir": H.REPO + "/data", "cache_dir": self.scr.path("c")})
        self.assert_refused_clean(w, root, p, "root_forbidden_tree")
        self.assertFalse(os.path.exists(H.REPO + "/outputs"))

    def test_4B_config_repo_cache_dir_refused_before_any_attempt(self):
        d = self.scr.mkdir("d")
        w, root, p = self.cli_slot({"data_dir": d, "cache_dir": H.REPO + "/outputs/cache"})
        self.assert_refused_clean(w, root, p, "root_forbidden_tree")
        self.assertFalse(os.path.exists(H.REPO + "/outputs"))

    def test_4B_config_missing_payload_uid_refused(self):
        d = self.scr.mkdir("d")
        w, root, p = self.cli_slot({"data_dir": d, "cache_dir": self.scr.path("c")}, uid=False)
        self.assert_refused_clean(w, root, p, "payload_uid_required")

    def test_4B_config_other_refusals(self):
        d = self.scr.mkdir("d")
        ok_base = {"data_dir": d, "cache_dir": self.scr.path("c")}
        for cfg, code in (
                (dict(ok_base, extra_knob=1), "config_unknown_key"),
                (dict(ok_base, data_dir="/etc"), "config_path_outside_scratch"),
                (dict(ok_base, data_dir="relative/dir"), "config_path_invalid"),
                (dict(ok_base, transfer_record="/etc/passwd"), "config_path_outside_scratch")):
            w, root, p = self.cli_slot(cfg)
            self.assert_refused_clean(w, root, p, code)
            for sub in ("control", "receipts", "slots", "bin"):

                shutil.rmtree(self.scr.path(sub), ignore_errors=True)
        # outside the receipt's scope but inside scratch: a data dir under a sibling that the scope does not admit
        scope = {"read_scope": [self.scr.path("only_here")], "write_scope": [self.scr.path("only_here")]}
        with self.assertRaises(N2Stop) as cm:
            slotrun.validate_slot_config(dict(ok_base, slot="H0", repo_root=H.REPO, out_dir=self.scr.path("o"),
                                              archive_name="a"), "H0", scratch_base=self.scr.root,
                                         repo_root=H.REPO, scope=scope, mock=True)
        self.assertEqual(cm.exception.code, "config_path_outside_receipt_scope")
        scope = {"read_scope": [], "write_scope": [self.scr.root]}
        with self.assertRaises(N2Stop) as cm:  # repo_root must be the verified repository
            slotrun.validate_slot_config(dict(ok_base, slot="H0", repo_root=self.scr.root, out_dir=self.scr.path("o")),
                                         "H0", scratch_base=self.scr.root, repo_root=H.REPO, scope=scope, mock=True)
        self.assertEqual(cm.exception.code, "config_repo_root_not_verified_repo")
        good = dict(ok_base, slot="H1_12", repo_root=H.REPO, out_dir=self.scr.path("o"), sample_rows=3)
        self.assertTrue(slotrun.validate_slot_config(good, "H1_12", scratch_base=self.scr.root, repo_root=H.REPO,
                                                     scope=scope, mock=True))
        with self.assertRaises(N2Stop) as cm:  # a non-mock slot binds sample_rows to the slot name
            slotrun.validate_slot_config(good, "H1_12", scratch_base=self.scr.root, repo_root=H.REPO, scope=scope,
                                         mock=False)
        self.assertEqual(cm.exception.code, "config_sample_rows_not_bound_to_slot")
        self.assertTrue(slotrun.validate_slot_config(dict(good, sample_rows=12), "H1_12", scratch_base=self.scr.root,
                                                     repo_root=H.REPO, scope=scope, mock=False))

    # ---- B2
    def cache(self, files=(), link=None):
        d = self.scr.mkdir("cache")
        os.chmod(d, 0o777)
        for n in files:
            with open(os.path.join(d, n), "wb") as f:
                f.write(b"archive-bytes")
        return d

    def test_4B_symlinked_archive_refused_and_target_untouched(self):
        d = self.cache()
        target = self.scr.path("victim")
        open(target, "w").write("secret")
        os.chmod(target, 0o600)
        os.chown(target, UID, UID)
        os.symlink(target, os.path.join(d, self.arch))
        before = os.stat(target)
        pv = slotrun.parent_verify("H1_12", self.scr.mkdir("out"), d, None, self.arch)
        self.assertFalse(pv["ok"], pv)
        with self.assertRaises((N2Stop, OSError)):
            slotrun.seal_cache(d, self.arch)
        after = os.stat(target)
        self.assertEqual((before.st_mode, before.st_uid, before.st_gid), (after.st_mode, after.st_uid, after.st_gid))

    def test_4B_cache_dir_content_rules(self):
        out = self.scr.mkdir("out")
        # control: exactly one plain regular file passes and is sealed read-only, root-owned
        d = self.cache([self.arch])
        pv = slotrun.parent_verify("H1_12", out, d, None, self.arch)
        self.assertTrue(pv["ok"], pv)
        slotrun.seal_cache(d, self.arch)
        st = os.stat(os.path.join(d, self.arch))
        self.assertEqual((st.st_uid, stat.S_IMODE(st.st_mode)), (0, 0o444))
        for label, setup in (
                ("extra_file", lambda dd: open(os.path.join(dd, "stray"), "w").write("x")),
                ("hardlink", lambda dd: os.link(os.path.join(dd, self.arch), os.path.join(dd, "second"))),
                ("directory_entry", lambda dd: (os.unlink(os.path.join(dd, self.arch)), os.mkdir(os.path.join(dd, self.arch)))),
                ("fifo", lambda dd: (os.unlink(os.path.join(dd, self.arch)), os.mkfifo(os.path.join(dd, self.arch))))):

            shutil.rmtree(self.scr.path("cache"), ignore_errors=True)
            dd = self.cache([self.arch])
            setup(dd)
            pv = slotrun.parent_verify("H1_12", out, dd, None, self.arch)
            self.assertFalse(pv["ok"], (label, pv))
            with self.assertRaises((N2Stop, OSError)):
                slotrun.seal_cache(dd, self.arch)
        shutil.rmtree(self.scr.path("cache"), ignore_errors=True)
        real = self.cache([self.arch])
        link = self.scr.path("cache_link")
        os.symlink(real, link)  # the cache directory itself replaced by a symlink
        pv = slotrun.parent_verify("H1_12", out, link, None, self.arch)
        self.assertFalse(pv["ok"], pv)
        with self.assertRaises((N2Stop, OSError)):
            slotrun.seal_cache(link, self.arch)

    def test_4B_other_root_side_reads_refuse_symlinks(self):
        from n2h import controller, transfer
        target = self.scr.path("t.json")
        open(target, "w").write(json.dumps({"ok": True, "nonce": "n"}))
        link = self.scr.path("completion_receipt.json")
        os.symlink(target, link)
        self.assertFalse(controller.verify_completion_receipt(link, "n"))
        self.assertTrue(controller.verify_completion_receipt(target, "n"))  # control
        out = self.scr.mkdir("out")
        os.symlink(target, os.path.join(out, "slot_result.json"))
        d = self.cache([self.arch])
        pv = slotrun.parent_verify("H0", out, d, None, self.arch)
        self.assertIn("h0_result_unreadable", pv["problems"])
        with self.assertRaises(transfer.TransferStop):
            transfer._sha(link)
        with self.assertRaises(OSError):
            slotrun.sha256_file(link)

    # ---- B4
    def test_4B_parent_receipts_gate_later_slots(self):
        root = self.scr.mkdir("ctl")
        L.init_mock_control_root(root, "x")
        led = L.Ledger(root)
        a = "e" * 64
        # terminal success for H0 alone is not enough on the runner path
        for slot in ("H0",):
            key, _ = led.create_attempt(a, slot, {"x": 1})
            led.create_terminal(key, {"status": "success"})
        self.assertTrue(led.can_dispatch_slot(a, "H1_12")[0], "terminal-only rule (stdlib workloads) unchanged")
        gate = {"expected_graph_sha": "h" * 64}
        ok, why = led.can_dispatch_slot(a, "H1_12", gate)
        self.assertFalse(ok)
        self.assertIn("parent_verification_H0_missing_or_failed", why)
        led.create_parent_receipt(a, "H0", {"ok": False, "problems": ["archive_hash_changed"], "archive_sha256": "h" * 64})
        self.assertIn("missing_or_failed", led.can_dispatch_slot(a, "H1_12", gate)[1])
        led2 = L.Ledger(self.scr.mkdir("ctl2"))
        L.init_mock_control_root(led2.root, "y")
        key, _ = led2.create_attempt(a, "H0", {"x": 1})
        led2.create_terminal(key, {"status": "success"})
        led2.create_parent_receipt(a, "H0", {"ok": True, "problems": [], "archive_sha256": "h" * 64})
        self.assertTrue(led2.can_dispatch_slot(a, "H1_12", gate)[0])
        ok, why = led2.can_dispatch_slot(a, "H1_12", {"expected_graph_sha": "z" * 64})
        self.assertEqual((ok, why), (False, "h0_parent_receipt_hash_differs_from_config"))
        self.assertFalse(led2.can_dispatch_slot(a, "H1_12", {})[0])
        with self.assertRaises(L.LedgerStop):  # receipts are immutable
            led2.create_parent_receipt(a, "H0", {"ok": True})

    def test_4B_h3_needs_registered_transfer_record(self):
        root = self.scr.mkdir("ctl")
        L.init_mock_control_root(root, "x")
        led = L.Ledger(root)
        a = "d" * 64
        for slot in L.SLOTS[:-1]:
            key, _ = led.create_attempt(a, slot, {"x": 1})
            led.create_terminal(key, {"status": "success"})
            if slot != "H3":
                led.create_parent_receipt(a, slot, {"ok": True, "problems": [], "archive_sha256": "h" * 64})
        gate = {"expected_graph_sha": "h" * 64, "transfer_record_sha": "t" * 64}
        self.assertFalse(led.can_dispatch_slot(a, "H3", gate)[0])
        rec = self.scr.path("transfer_record.json")
        open(rec, "w").write("{}")
        sha = slotrun.register_transfer(root, a, rec)
        self.assertFalse(led.can_dispatch_slot(a, "H3", gate)[0], "a different record hash is refused")
        self.assertTrue(led.can_dispatch_slot(a, "H3", dict(gate, transfer_record_sha=sha))[0])

    # ---- N1
    def test_4B_membership_transient_disagreement_counted(self):
        class Lay:
            join_order = ("memory", "pids")
        lay = Lay()
        seq = iter([[1, 2], [1], [1, 2], [1, 2]])  # first read: hierarchies disagree; second read: agree

        def fake(layout, h, fs):
            return next(seq)
        with mock.patch.object(cgmod, "procs", fake):
            self.assertEqual(cgmod.members(lay, None, pause_s=0), [1, 2])
        self.assertEqual(lay.transient_disagreements, 1)
        with mock.patch.object(cgmod, "procs", lambda layout, h, fs: [1, 2] if h == "memory" else [1]):
            with self.assertRaises(N2Stop) as cm:  # persistent disagreement still stops
                cgmod.members(Lay(), None, pause_s=0)
        self.assertEqual(cm.exception.code, "listing_disagrees_between_hierarchies")

    def test_4B_terminal_body_carries_transient_disagreement_count(self):
        w = H.make_world(self.scr, work={"mode": "sleep", "seconds": 0.2})
        res, p = H.run_driver(self.scr, H.spec_path(w))
        self.assertEqual(res["terminal"]["membership_transient_disagreements"], 0)

    # ---- N6
    def test_4B_fadvise_refuses_inodes_the_attempt_did_not_create(self):
        mine, other = self.scr.path("mine"), self.scr.path("other")
        for p in (mine, other):
            open(p, "w").write("x")
        st = os.stat(mine)
        created = {(st.st_dev, st.st_ino)}
        native.fadvise_dontneed_own(mine, self.scr.root, created)
        with self.assertRaises(N2Stop) as cm:
            native.fadvise_dontneed_own(other, self.scr.root, created)
        self.assertEqual(cm.exception.code, "fadvise_not_created_by_attempt")
        self.assertIn("cold_inputs", slotrun.DRIVER_ONLY_KEYS)


class Static4b(unittest.TestCase):
    def test_4B_n2h4b_parts_ascii_no_raw_kill_no_repo_data_paths(self):
        d = os.path.join(H.HARNESS, "n2h4b")
        names = [n for n in sorted(os.listdir(d)) if n.endswith(".py")]
        self.assertEqual(set(names), {"__init__.py", "h0guard.py", "slotmain.py", "w0.py"})
        for n in names + ["../n2h/transfer.py"]:
            raw = open(os.path.join(d, n), "rb").read()
            raw.decode("ascii")
            code = raw.decode("ascii")
            for bad in ("os.kill(", "killpg", "subprocess", "os.system(", "posix_fadvise", "data/analysis", "outputs/cache"):
                self.assertNotIn(bad, code, "%s: %s" % (n, bad))
        for part in slots.PAYLOAD_PARTS:
            self.assertTrue(os.path.isfile(os.path.join(H.HARNESS, part)), part)
        text = slots.build_payload_text()
        self.assertLess(len(text), 400000)
        self.assertEqual(hashlib.sha256(text.encode("ascii")).hexdigest(), hashlib.sha256(slots.real_payload("H1_12").encode("ascii")).hexdigest())

    def test_4B_harness_hash_binds_n2h4b_and_transfer(self):
        import re
        src = open(os.path.join(H.HARNESS, "n2h", "ledger.py")).read()
        self.assertIn("n2h4b", src)
        base = L.harness_hash(os.path.join(H.HARNESS, "n2h"))
        self.assertRegex(base, r"^[0-9a-f]{64}$")
        self.assertIsNotNone(re.search(r"def harness_hash", src))


class RunnerAndGate(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch(self.id().split(".")[-1][5:28])

    def tearDown(self):
        self.scr.cleanup()

    def cli_args(self, w, extra=()):
        return [H.PY, "-I", "-S", "-B", "-u", os.path.join(H.HARNESS, "n2_harness.py"), "run",
                "--control-root", w["control_root"], "--approval", w["paths"]["approval"], "--policy", w["paths"]["policy"],
                "--scope", w["paths"]["scope"], "--selftest-report", w["paths"]["selftest"], "--slot", "H0",
                "--slot-root", w["slot_root"], "--scratch-base", self.scr.root, "--interpreter", w["link"],
                *extra]

    def prepare_record(self, w, path_name="prepare.json"):
        logs = self.scr.mkdir("logs")
        cfg = {"scratch_base": self.scr.root, "control_root": self.scr.mkdir("prep_control"), "log_root": logs,
               "binaries": {"unshare": H.UNSHARE, "interpreter": H.PY, "setpriv": H.SETPRIV}}
        rec = cli.prepare(cfg)
        p = self.scr.path(path_name)
        with open(p, "w") as f:
            json.dump(rec, f, default=str)
        return p, rec

    def test_4B_real_dispatch_refused_at_gate_no_approved_receipt(self):
        w = H.make_world(self.scr, synthetic_mock=False)
        p = subprocess.run(self.cli_args(w), capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn(b"no_approved_policy_scope_receipt", p.stderr)
        self.assertEqual(os.listdir(os.path.join(w["control_root"], "attempts")), [])
        self.assertEqual(os.listdir(w["slot_root"]), [])
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_4B_real_claimed_approval_still_refused_pending_coordinator(self):
        w = H.make_world(self.scr, synthetic_mock=False)
        # a CLAIM of approval inside the receipts cannot be authenticated by this harness
        scr2 = H.Scratch("claim")
        try:
            w2 = H.make_world(scr2, synthetic_mock=False)
            gate_opts = {"control-root": w2["control_root"], "approval": w2["paths"]["approval"],
                         "policy": w2["paths"]["policy"], "scope": w2["paths"]["scope"],
                         "selftest-report": w2["paths"]["selftest"]}
            with self.assertRaises(RefusedRun) as cm:
                cli.run_gate(gate_opts)
            self.assertEqual(cm.exception.code, "no_approved_policy_scope_receipt")
        finally:
            scr2.cleanup()
        self.assertEqual(H.no_n2_cgroups(), [])
        del w

    def test_4B_runner_refuses_non_mock_gate_by_itself(self):
        gate = {"synthetic_mock": False, "policy": {}, "approval_sha": "a", "policy_sha": "b", "harness_sha": "c",
                "scope_sha": "d", "selftest_sha": "e", "root_id": "f"}
        with self.assertRaises(RefusedRun) as cm:
            slotrun.dispatch(gate, control_root=self.scr.root, slot="H0", slot_root=self.scr.root,
                             scratch_base=self.scr.root, interpreter=H.PY, unshare=H.UNSHARE, setpriv=H.SETPRIV,
                             recorded_controller_paths={}, payload_source="", work={})
        self.assertEqual(cm.exception.code, "real_dispatch_disabled_pending_coordinator")
        self.assertEqual(H.no_n2_cgroups(), [])
        gate2 = dict(gate, synthetic_mock="yes")  # a non-bool flag is never accepted as "explicit"
        with self.assertRaises(RefusedRun):
            slotrun.dispatch(gate2, control_root=self.scr.root, slot="H0", slot_root=self.scr.root,
                             scratch_base=self.scr.root, interpreter=H.PY, unshare=H.UNSHARE, setpriv=H.SETPRIV,
                             recorded_controller_paths={}, payload_source="", work={})

    def test_4B_runner_config_has_no_driver_only_knobs(self):
        w = H.make_world(self.scr)
        gate = L.gate_run(w["control_root"], w["paths"]["approval"], w["paths"]["policy"], w["paths"]["scope"],
                          w["paths"]["selftest"], os.path.join(H.HARNESS, "n2h"))
        self.assertIs(gate["synthetic_mock"], True)
        cfg = slotrun.build_attempt_config(
            gate, control_root=w["control_root"], slot="H0", slot_root=w["slot_root"], scratch_base=self.scr.root,
            interpreter=H.PY, unshare=H.UNSHARE, setpriv=H.SETPRIV, recorded_controller_paths={"memory": "/x"},
            payload_source="x", work={})
        self.assertEqual(set(cfg) & slotrun.DRIVER_ONLY_KEYS, set())
        self.assertIs(cfg["synthetic_mock"], True)
        self.assertEqual(cfg["recorded_controller_paths"], {"memory": "/x"})
        for key in slotrun.DRIVER_ONLY_KEYS:
            with self.assertRaises(N2Stop) as cm:
                slotrun.assert_no_driver_only(dict(cfg, **{key: 1}))
            self.assertEqual(cm.exception.code, "driver_only_knob_present")
        for hook in ("at_S0", "before_finalize", "after_attempt_record"):
            self.assertIn(hook, slotrun.HOOK_NAMES)
        # the documented 4a list is fully covered
        for k in ("pause_at", "pause_s", "finalize_boom", "finalize_unserializable", "close_gate_instead_of_release",
                  "rewrite_exec_at", "extra_allow_modules", "no_subreaper", "fs_faults", "ledger_faults", "nonce", "fs",
                  "cg_root", "use_v2", "hooks", "plan", "inject", "faults", "stub_actual", "env_extra_actual"):
            self.assertIn(k, slotrun.DRIVER_ONLY_KEYS)
        src = open(os.path.join(H.HARNESS, "n2h", "cli.py")).read()
        body = src[src.index("def run_after_gate"):]
        self.assertNotIn("run_spec", body)
        self.assertNotIn("import driver", body)
        self.assertNotIn("from . import driver", body)
        real_req = dict(cfg, synthetic_mock=False)
        with self.assertRaises(N2Stop):  # a non-mock config without uid/site_main/paths is rejected
            slotrun.assert_real_requirements(dict(real_req, payload_uid=None))

    def test_4B_cli_passes_prepare_recorded_controller_paths(self):
        w = H.make_world(self.scr)
        path, rec = self.prepare_record(w)
        p = subprocess.run(self.cli_args(w, ["--prepare-record", path]), capture_output=True, timeout=120)
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertEqual(json.loads(p.stdout)["status"], "success")
        key = L.attempt_key(w["approval_sha"], "H0")
        att = L.Ledger(w["control_root"]).read("attempt", key)
        self.assertEqual(att["controller_cgroup_paths"], rec["controller_cgroup_paths"])
        # a record whose paths differ is refused by the attempt preconditions, before any record or cgroup exists
        scr2 = H.Scratch("cgdiff")
        try:
            w2 = H.make_world(scr2)
            bad = dict(rec, controller_cgroup_paths={k: v + "/other" for k, v in rec["controller_cgroup_paths"].items()})
            bp = scr2.path("bad.json")
            json.dump(bad, open(bp, "w"), default=str)
            args = self.cli_args(w2, ["--prepare-record", bp])
            args[args.index("--scratch-base") + 1] = scr2.root
            p2 = subprocess.run(args, capture_output=True, timeout=120)
            self.assertNotEqual(p2.returncode, 0)
            self.assertIn(b"controller_cgroup_changed", p2.stdout + p2.stderr)
            self.assertEqual(os.listdir(os.path.join(w2["control_root"], "attempts")), [])
            self.assertEqual(H.no_n2_cgroups(), [])
            wrong = dict(rec, harness_sha="0" * 64)  # a record bound to another harness hash
            wp = scr2.path("wrong.json")
            json.dump(wrong, open(wp, "w"), default=str)
            args = self.cli_args(w2, ["--prepare-record", wp])
            args[args.index("--scratch-base") + 1] = scr2.root
            p3 = subprocess.run(args, capture_output=True, timeout=60)
            self.assertEqual(p3.returncode, 3)
            self.assertIn(b"prepare_record_not_bound", p3.stderr)
        finally:
            scr2.cleanup()


class UidDrop(unittest.TestCase):
    """Operator item (c): the payload runs as an unprivileged numeric uid/gid (setpriv --reuid/--regid)."""

    def setUp(self):
        self.scr = UScratch("uid")

    def tearDown(self):
        self.scr.cleanup()

    def test_4B_payload_uid_not_root_and_cannot_write_root_owned(self):
        w = H.make_world(self.scr, work={"mode": "uid_probe", "seconds": 0.3})
        out = os.path.join(w["slot_root"], "out")
        attempts = os.path.join(w["control_root"], "attempts")
        w["work"] = {"mode": "uid_probe", "seconds": 0.3, "slot_root": w["slot_root"], "out_dir": out,
                     "must_not_write": [os.path.join(w["slot_root"], "payload.py"), os.path.join(w["slot_root"], "stdout.log"),
                                        os.path.join(w["control_root"], "ROOT.json"), attempts + "/x"]}
        res, p = H.run_driver(self.scr, H.spec_path(w, {"payload_uid": UID, "payload_gid": UID}))
        self.assertIn("terminal", res, (res, p.stderr))
        self.assertEqual(res["terminal"]["status"], "success", res["terminal"]["stops"])
        err = open(os.path.join(w["slot_root"], "stderr.log")).read()
        line = [ln for ln in err.splitlines() if " uid_probe " in ln][0]
        kv = dict(x.partition("=")[::2] for x in line.split()[4:])
        self.assertEqual((kv["uid"], kv["euid"], kv["gid"], kv["egid"], kv["groups"]), (str(UID), str(UID), str(UID), str(UID), "none"))
        self.assertNotEqual(kv["uid"], "0")
        self.assertEqual(kv["w0"], "13")  # payload.py (root-owned 0444): EACCES
        self.assertEqual(kv["w1"], "13")  # stdout.log written by the controller
        self.assertEqual(kv["w2"], "13")  # ledger root receipt
        self.assertEqual(kv["w3"], "2")   # ENOENT, not writable either way
        self.assertEqual(kv["create_in_slot_root"], "13")
        self.assertEqual(kv["create_in_out_dir"], "CREATED")  # the one place it may write
        self.assertEqual(kv["setuid0"], "1")  # EPERM
        self.assertEqual(kv["shadow"], "13")
        a = json.load(open(os.path.join(attempts, os.listdir(attempts)[0])))["body"]
        self.assertEqual((a["payload_uid"], a["payload_gid"]), (UID, UID))
        self.assertEqual(os.stat(out).st_uid, UID)
        self.assertEqual(os.stat(os.path.join(w["slot_root"], "payload.py")).st_uid, 0)
        self.assertEqual(H.no_n2_cgroups(), [])

    def test_4B_uid_untraversable_scratch_refused_before_attempt(self):
        scr = H.Scratch("uidtrav")  # the harness scratch sits under a 0700 chain
        try:
            w = H.make_world(scr, work={"mode": "sleep", "seconds": 0.2})
            res, p = H.run_driver(scr, H.spec_path(w, {"payload_uid": UID, "payload_gid": UID}))
            self.assertIn("refused", res, (res, p.stderr))
            self.assertEqual(res["refused"]["code"], "payload_uid_cannot_traverse")
            self.assertEqual(os.listdir(os.path.join(w["control_root"], "attempts")), [])
            self.assertEqual(H.no_n2_cgroups(), [])
        finally:
            scr.cleanup()

    def test_4B_uid_zero_or_unpaired_refused(self):
        for over, code in (({"payload_uid": 0, "payload_gid": 0}, "payload_uid_invalid"),
                           ({"payload_uid": UID}, "payload_uid_gid_pair")):
            scr = UScratch("uid0")
            try:
                w = H.make_world(scr, work={"mode": "sleep", "seconds": 0.2})
                res, p = H.run_driver(scr, H.spec_path(w, over))
                self.assertEqual(res["refused"]["code"], code, (res, p.stderr))
                self.assertEqual(H.no_n2_cgroups(), [])
            finally:
                scr.cleanup()

    def test_4B_latch_in_uid_out_dir_with_exit_zero_fails(self):
        scr = self.scr
        w = H.make_world(scr, work={"mode": "latch_at_exit", "seconds": 0.3})
        out = os.path.join(w["slot_root"], "out")
        w["work"] = {"mode": "latch_at_exit", "seconds": 0.3, "latch_path": os.path.join(out, "sentinel_latch"),
                     "receipt_path": os.path.join(out, "completion_receipt.json")}
        # the child exits (code 0, valid receipt) before the first monitor sample; only the latch remains
        res, p = H.run_driver(scr, H.spec_path(w, {"payload_uid": UID, "payload_gid": UID, "require_receipt": True,
                                                    "sleep_before_monitor": 1.5}))
        self.assertIn("terminal", res, (res, p.stderr))
        t = res["terminal"]
        codes = [s_["code"] for s_ in t["stops"]]
        self.assertIn("cache_latch_with_exit_zero", codes, t["stops"])
        self.assertNotEqual(t["status"], "success")
        self.assertEqual(t["exit"]["direct"]["status"]["code"], 0)
        self.assertFalse(t["exit"].get("terminated_by_controller"))


class PrivateInputs(unittest.TestCase):
    """Operator item (d), corrected: no eviction of shared/repository inputs. Attempt-private copy, hash-verified,
    evicted and measured; shared residency recorded as disclosed uncertainty."""

    def setUp(self):
        self.scr = H.Scratch("pi")
        self.src = self.scr.mkdir("shared_src")
        self.files = {}
        blob = os.urandom(3 << 20)
        for name, uid in (("rootowned.bin", 0), ("foreign.bin", UID)):
            p = os.path.join(self.src, name)
            with open(p, "wb") as f:
                f.write(blob)
            os.chmod(p, 0o444)
            os.chown(p, uid, uid)
            self.files[name] = p
        os.chmod(self.src, 0o555)
        self.private = self.scr.path("private")

    def tearDown(self):
        os.chmod(self.src, 0o755)
        self.scr.cleanup()

    @staticmethod
    def ident(p):
        st = os.stat(p)
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode, st.st_uid, st.st_gid)

    def test_4B_private_copy_leaves_shared_sources_untouched_and_unevicted(self):
        before = {n: self.ident(p) for n, p in self.files.items()}
        advised = []
        real_adv = os.posix_fadvise

        def spy(fd, off, ln, adv):
            advised.append(os.fstat(fd).st_ino)
            return real_adv(fd, off, ln, adv)
        with mock.patch.object(native.os, "posix_fadvise", spy):
            rec = native.prepare_private_inputs(self.src, list(self.files), self.private, self.scr.root, [self.src])
        self.assertEqual(before, {n: self.ident(p) for n, p in self.files.items()}, "inputs must not be modified")
        src_inos = {os.stat(p).st_ino for p in self.files.values()}
        self.assertTrue(advised, "the private copies are evicted")
        self.assertEqual(set(advised) & src_inos, set(), "no shared input is ever passed to fadvise")
        for c in rec["copies"]:
            self.assertEqual(c["sha256"], hashlib.sha256(open(c["source"], "rb").read()).hexdigest())
            self.assertEqual(hashlib.sha256(open(c["copy"], "rb").read()).hexdigest(), c["sha256"])
            self.assertEqual(os.stat(c["copy"]).st_uid, os.getuid())
            self.assertEqual(stat.S_IMODE(os.stat(c["copy"]).st_mode), 0o444)
        self.assertGreater(rec["shared_source_resident_bytes"], 2 * (2 << 20), "shared residency is disclosed, not hidden")
        self.assertLess(rec["private_resident_bytes"], 0.2 * rec["private_size"], "only the private copies are cold")

    def test_4B_private_inputs_refusals(self):
        other = self.scr.mkdir("outside")
        p = os.path.join(other, "f.bin")
        open(p, "wb").write(b"x" * 10)
        with self.assertRaises(N2Stop) as cm:
            native.copy_input_private(p, self.scr.path("c1"), [self.src])
        self.assertEqual(cm.exception.code, "input_outside_read_scope")
        link = os.path.join(self.scr.mkdir("lnk"), "l.bin")
        os.symlink(self.files["rootowned.bin"], link)
        with self.assertRaises(N2Stop) as cm:
            native.copy_input_private(link, self.scr.path("c2"), [os.path.dirname(link), self.src])
        self.assertEqual(cm.exception.code, "input_path_not_canonical")
        with self.assertRaises(N2Stop) as cm:
            native.copy_input_private(self.src, self.scr.path("c3"), [os.path.dirname(self.src)])
        self.assertEqual(cm.exception.code, "input_not_regular")
        with self.assertRaises(N2Stop):
            native.prepare_private_inputs(self.src, ["../x"], self.private, self.scr.root, [self.src])
        with self.assertRaises(N2Stop) as cm:
            native.prepare_private_inputs(self.src, list(self.files), "/tmp/n2-outside-scratch", self.scr.root, [self.src])
        self.assertEqual(cm.exception.code, "private_root_outside_scratch")
        with self.assertRaises(N2Stop):  # the legacy helper still refuses anything that is not our own scratch file
            native.fadvise_dontneed_own(self.files["foreign.bin"], self.scr.root)

    def test_4B_controller_records_shared_residency_in_conservative_figure(self):
        w = H.make_world(self.scr, work={"mode": "sleep", "seconds": 0.3})
        spec = H.spec_path(w, {"private_inputs": {"source_root": self.src, "rel_paths": list(self.files),
                                                   "private_root": self.private, "read_roots": [self.src]}})
        res, p = H.run_driver(self.scr, spec)
        self.assertIn("terminal", res, (res, p.stderr))
        t = res["terminal"]
        self.assertEqual(t["status"], "success", t["stops"])
        shared = t["elsewhere"]["pre_release"]["shared_input_resident_bytes"]
        self.assertGreater(shared, 2 * (2 << 20))
        self.assertGreaterEqual(t["peak"]["resident_bytes_elsewhere"], shared)
        key = L.attempt_key(w["approval_sha"], w["slot"])
        att = L.Ledger(w["control_root"]).read("attempt", key)
        self.assertEqual(len(att["private_inputs"]["copies"]), 2)
        self.assertEqual(t["cold_report"]["pre_release"]["files"].__len__(), 2)
        self.assertLess(t["cold_report"]["pre_release"]["fraction"], 0.5)
        self.assertEqual(H.no_n2_cgroups(), [])


if __name__ == "__main__":
    unittest.main()
