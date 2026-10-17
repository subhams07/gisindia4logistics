"""Receipts, run gate and the attempt/launch/terminal ledger (M01, M02).

The real control root belongs to the coordinator. This module is exercised only on
synthetic mock control roots. Authority never comes from finding a file: every receipt
must be hash-valid, bound to the others, and (in 4a) flagged synthetic_mock.
"""
import hashlib
import json
import os
import stat

from . import policy as policy_mod
from .errors import FaultPlan, InjectedFault, LedgerStop, PolicyStop, RefusedRun
from .fsio import write_all

SLOTS = ("H0", "H1_12", "H1_64", "H1_192", "H2", "H3")
SLOT_DEPS = {
    "H0": (),
    "H1_12": ("H0",),
    "H1_64": ("H0",),
    "H1_192": ("H0",),
    "H2": ("H0",),
    "H3": ("H1_192",),
}
MOCK_MARKER = "SYNTHETIC_MOCK_CONTROL_ROOT"
REAL_DISPATCH_ENABLED = False  # 4b: the runner exists, real dispatch stays refused at the gate


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


ENTRY_SCRIPTS = ("n2_harness.py", "driver_entry.py", "run_selftests.py", "run_selftests_4b.py")


def harness_hash(pkg_dir):
    """Hash binding the harness source: sorted (name, sha256) of every *.py in the package plus the
    entry scripts that sit next to the package directory (they are executed, so they are bound too)."""
    items = []
    for name in sorted(os.listdir(pkg_dir)):
        if name.endswith(".py"):
            items.append(["n2h/" + name, sha256_file(os.path.join(pkg_dir, name))])
    parent = os.path.dirname(os.path.abspath(pkg_dir))
    d4b = os.path.join(parent, "n2h4b")  # 4b venv-only components and payload parts are bound as well
    if os.path.isdir(d4b):
        for name in sorted(os.listdir(d4b)):
            if name.endswith(".py"):
                items.append(["n2h4b/" + name, sha256_file(os.path.join(d4b, name))])
    for name in ENTRY_SCRIPTS:
        p = os.path.join(parent, name)
        items.append([name, sha256_file(p) if os.path.isfile(p) else None])
    return sha256_bytes(canonical(items))


def make_receipt(body):
    return {"body": body, "sha256": sha256_bytes(canonical(body))}


def write_receipt(path, body):
    rec = make_receipt(body)
    data = canonical(rec)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o444)
    try:
        write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return rec["sha256"]


def read_receipt(path, kind):
    """Parse + hash-verify. Missing/invalid -> RefusedRun with a stable code."""
    try:
        st = os.lstat(path)
    except OSError:
        raise RefusedRun("receipt_missing", kind)
    if not stat.S_ISREG(st.st_mode):
        raise RefusedRun("receipt_not_regular", kind)
    try:
        with open(path, "rb") as f:
            rec = json.loads(f.read().decode("ascii"))
        body, sha = rec["body"], rec["sha256"]
    except (ValueError, KeyError, UnicodeError, TypeError):
        raise RefusedRun("receipt_malformed", kind)
    if sha256_bytes(canonical(body)) != sha:
        raise RefusedRun("receipt_hash_invalid", kind)
    if body.get("kind") != kind:
        raise RefusedRun("receipt_wrong_kind", kind)
    return body, sha


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def init_mock_control_root(root, root_id, synthetic_mock=True):
    """Create the structure of a (mock) control root. Never used for a real ledger in 4a."""
    for sub in ("attempts", "launch", "terminal"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    if synthetic_mock:
        with open(os.path.join(root, MOCK_MARKER), "w") as f:
            f.write("synthetic mock control root; not a real ledger\n")
    write_receipt(os.path.join(root, "ROOT.json"), {
        "kind": "ledger_root", "root_id": root_id, "realpath": os.path.realpath(root),
        "synthetic_mock": bool(synthetic_mock)})


def gate_run(control_root, approval_path, policy_path, scope_path, selftest_report_path,
             pkg_dir, repo_root=None, scratch_base=None):
    """Run-mode gate. Returns a dict of verified bindings or raises RefusedRun.

    Called BEFORE any cgroup/child/attempt creation. Only synthetic_mock receipts in a
    synthetic mock control root can pass; non-mock receipts refuse (no approved policy/scope receipt).
    """
    if not all((control_root, approval_path, policy_path, scope_path, selftest_report_path)):
        raise RefusedRun("run_inputs_missing", "approval/policy/scope/selftest/ledger all required")
    approval, approval_sha = read_receipt(approval_path, "approval")
    pol_body, pol_sha = read_receipt(policy_path, "policy")
    scope, scope_sha = read_receipt(scope_path, "scope")
    st_body, st_sha = read_receipt(selftest_report_path, "selftest_report")
    if approval.get("policy_sha") != pol_sha:
        raise RefusedRun("binding_policy_mismatch", "")
    if approval.get("scope_sha") != scope_sha:
        raise RefusedRun("binding_scope_mismatch", "")
    if approval.get("selftest_sha") != st_sha:
        raise RefusedRun("binding_selftest_mismatch", "")
    now_hash = harness_hash(pkg_dir)
    if approval.get("harness_sha") != now_hash or st_body.get("harness_sha") != now_hash:
        raise RefusedRun("binding_harness_mismatch", "")
    if not (isinstance(scope.get("read_scope"), list) and isinstance(scope.get("write_scope"), list)):
        raise RefusedRun("scope_not_finite", "")
    for item in scope["read_scope"] + scope["write_scope"]:
        if (not isinstance(item, str) or not item.startswith("/") or item == "/" or any(c in item for c in "*?[]{}")
                or os.path.normpath(item) != item.rstrip("/")):
            raise RefusedRun("scope_not_finite", "non-finite or non-normalized scope entry: %r" % (item,))
    try:
        policy_mod.validate_policy(pol_body.get("policy"))
    except PolicyStop as e:
        raise RefusedRun("policy_invalid", str(e)) from e
    root_meta_path = os.path.join(control_root, "ROOT.json")
    if not os.path.isdir(control_root) or not os.path.exists(root_meta_path):
        raise RefusedRun("ledger_missing", control_root)
    root_meta, _ = read_receipt(root_meta_path, "ledger_root")
    if root_meta.get("root_id") != approval.get("control_root_id"):
        raise RefusedRun("ledger_root_id_mismatch", "")
    if root_meta.get("realpath") != os.path.realpath(control_root):
        raise RefusedRun("ledger_root_moved", "")
    flags = {approval.get("synthetic_mock"), pol_body.get("synthetic_mock"),
             scope.get("synthetic_mock"), root_meta.get("synthetic_mock"), st_body.get("synthetic_mock")}
    if flags != {True}:
        # No approved policy/scope receipt exists (numerical policy and read/write admission are unapproved,
        # step 5). Even receipts that CLAIM approval cannot be authenticated by this harness: real dispatch is
        # coordinator-owned and is compiled out of 4b (REAL_DISPATCH_ENABLED is False).
        claims = (pol_body.get("policy", {}).get("approved") is True
                  and isinstance(pol_body.get("operator_approval"), dict)
                  and isinstance(scope.get("operator_approval"), dict))
        if claims and not REAL_DISPATCH_ENABLED:
            raise RefusedRun("real_dispatch_disabled_pending_coordinator", "flags=%r" % sorted(map(str, flags)))
        raise RefusedRun("no_approved_policy_scope_receipt", "flags=%r" % sorted(map(str, flags)))
    if not os.path.exists(os.path.join(control_root, MOCK_MARKER)):
        raise RefusedRun("mock_marker_missing", "")
    if pol_body["policy"].get("approved") is not False:
        raise RefusedRun("mock_policy_must_be_unapproved_label", "")
    return {"approval_sha": approval_sha, "policy_sha": pol_sha, "scope_sha": scope_sha,
            "selftest_sha": st_sha, "harness_sha": now_hash, "root_id": root_meta["root_id"],
            "policy": pol_body["policy"], "approval": approval, "scope": scope, "synthetic_mock": True}


def attempt_key(approval_sha, slot):
    if slot not in SLOTS:
        raise LedgerStop("unknown_slot", slot)
    return sha256_bytes(("%s:%s" % (approval_sha, slot)).encode("ascii"))


class Ledger:
    """Attempt/launch/terminal records on a control root. Immutable, exclusive create."""

    def __init__(self, root, plan=None):
        self.root = root
        self.plan = plan or FaultPlan()
        self.blocked = os.path.exists(os.path.join(root, "DISPATCH_BLOCKED"))

    def _p(self, kind, key):
        return os.path.join(self.root, kind, key + ".json")

    def assert_dispatch_allowed(self):
        if self.blocked or os.path.exists(os.path.join(self.root, "DISPATCH_BLOCKED")):
            raise LedgerStop("dispatch_blocked", "consumption could not be persisted earlier")

    def _block(self, why):
        """Persist a DISPATCH_BLOCKED marker. If that write itself fails the block exists in memory only
        (this process); a later process then relies on the existing attempt record (still present, so the
        slot stays consumed) and on D-6 (consumption unknown -> no dispatch without a new ruling)."""
        self.blocked = True
        try:
            fd = os.open(os.path.join(self.root, "DISPATCH_BLOCKED"),
                         os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC, 0o444)
            os.write(fd, why.encode("ascii", "replace"))
            os.close(fd)
        except OSError:
            pass  # in-memory flag still blocks this process; next start sees no marker (D-6: new ruling)

    def block(self, why):
        """Public form of the DISPATCH_BLOCKED persistence (used when no terminal receipt can be written)."""
        self._block(why)

    def state(self, key):
        if os.path.exists(self._p("terminal", key)):
            return "terminal"
        if os.path.exists(self._p("launch", key)):
            return "launched"
        if os.path.exists(self._p("attempts", key)):
            return "consumed"
        return "none"

    def _create(self, kind, key, body):
        path = self._p(kind, key)
        try:
            if body.get("minimal"):
                self.plan.check("INJECTED_E_SERIALIZE_MINIMAL")
            data = canonical(make_receipt(body))  # serialise BEFORE touching the filesystem
        except (TypeError, ValueError, UnicodeError, InjectedFault) as e:
            raise LedgerStop("serialize_failed", "%s: %s" % (kind, e), injected=getattr(e, "label", None)) from e
        try:
            self.plan.check("INJECTED_E_LEDGER_WRITE")
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o444)
        except FileExistsError:
            raise LedgerStop("%s_exists" % kind, key)
        except OSError as e:
            self._block("create failed: %s" % e)
            raise LedgerStop("persist_failed", "%s create: %s" % (kind, e), injected=getattr(e, "label", None))
        try:
            write_all(fd, data)
            self.plan.check("INJECTED_E_FSYNC")
            os.fsync(fd)
            closing, fd = fd, -1  # forget the number first: a failing close must not be retried in the finally
            os.close(closing)
            _fsync_dir(os.path.dirname(path))
        except OSError as e:
            self._block("persist failed: %s" % e)
            raise LedgerStop("persist_failed", "%s fsync: %s" % (kind, e), injected=getattr(e, "label", None)) from e
        finally:
            if fd >= 0:  # never leak the descriptor, whatever went wrong
                try:
                    os.close(fd)
                except OSError:
                    pass  # must not replace the LedgerStop already in flight
        try:
            return sha256_file(path)
        except OSError as e:
            self._block("readback failed: %s" % e)
            raise LedgerStop("persist_failed", "%s readback: %s" % (kind, e), injected=getattr(e, "label", None)) from e

    def create_attempt(self, approval_sha, slot, body):
        """Exclusive create BEFORE any cgroup/child exists. No PID allowed in an attempt record."""
        self.assert_dispatch_allowed()
        if "pid" in body or "child_pid" in body:
            raise LedgerStop("attempt_record_has_pid", "")
        key = attempt_key(approval_sha, slot)
        full = dict(body)
        full.update({"kind": "attempt", "slot": slot, "key": key, "approval_sha": approval_sha})
        sha = self._create("attempts", key, full)
        return key, sha

    def create_launch(self, key, body):
        if self.state(key) != "consumed":
            raise LedgerStop("launch_without_attempt_or_duplicate", key)
        full = dict(body)
        full.update({"kind": "launch", "key": key})
        return self._create("launch", key, full)

    def create_terminal(self, key, body):
        if self.state(key) not in ("consumed", "launched"):
            raise LedgerStop("terminal_without_attempt_or_duplicate", key)
        full = dict(body)
        full.update({"kind": "terminal", "key": key})
        return self._create("terminal", key, full)

    def read(self, kind, key):
        sub = {"attempt": "attempts", "launch": "launch", "terminal": "terminal"}[kind]
        body, _sha = read_receipt(self._p(sub, key), kind)
        return body

    # ---- parent-side verification receipts (4b): hash-bound, exclusive, immutable --------------------------
    def _parent_key(self, approval_sha, name):
        return sha256_bytes(("%s:parent:%s" % (approval_sha, name)).encode("ascii"))

    def create_parent_receipt(self, approval_sha, name, body):
        os.makedirs(os.path.join(self.root, "parent"), exist_ok=True)
        full = dict(body)
        full.update({"kind": "parent_verification", "name": name, "approval_sha": approval_sha})
        return self._create("parent", self._parent_key(approval_sha, name), full)

    def read_parent_receipt(self, approval_sha, name):
        p = self._p("parent", self._parent_key(approval_sha, name))
        if not os.path.exists(p):
            return None
        body, _sha = read_receipt(p, "parent_verification")
        return body

    def _parent_gate(self, approval_sha, slot, parent):
        """Parent-side verification gates (runner path): H0 and every later cache-reading slot must carry a passing
        parent receipt before the next slot may start; H1*/H2 need the H0 archive hash to equal the CONFIG one;
        H3 needs the parent-built, hash-bound transfer record."""
        idx = SLOTS.index(slot)
        for earlier in SLOTS[:idx]:
            if earlier == "H3":
                continue
            r = self.read_parent_receipt(approval_sha, earlier)
            if r is None or r.get("ok") is not True:
                return False, "parent_verification_%s_missing_or_failed" % earlier
        if slot.startswith("H1") or slot == "H2":
            h0 = self.read_parent_receipt(approval_sha, "H0")
            if h0 is None or not parent.get("expected_graph_sha") or h0.get("archive_sha256") != parent["expected_graph_sha"]:
                return False, "h0_parent_receipt_hash_differs_from_config"
        if slot == "H3":
            t = self.read_parent_receipt(approval_sha, "TRANSFER")
            if t is None or t.get("ok") is not True or t.get("record_sha256") != parent.get("transfer_record_sha"):
                return False, "transfer_receipt_missing_or_differs_from_config"
        return True, "ok"

    def can_dispatch_slot(self, approval_sha, slot, parent=None):
        """Serial order and dependency rule. `parent` (runner path) adds the parent-verification gates.
        Returns (ok, reason)."""
        if self.blocked:
            return False, "dispatch_blocked"
        if parent is not None:
            ok, why = self._parent_gate(approval_sha, slot, parent)
            if not ok:
                return False, why
        key = attempt_key(approval_sha, slot)
        if self.state(key) != "none":
            return False, "slot_already_consumed"
        idx = SLOTS.index(slot)
        for earlier in SLOTS[:idx]:
            ek = attempt_key(approval_sha, earlier)
            st = self.state(ek)
            if st != "terminal":
                return False, "earlier_slot_%s_not_terminal(%s)" % (earlier, st)
            term = self.read("terminal", ek)
            if term.get("status") != "success":
                return False, "earlier_slot_%s_not_success_needs_ruling" % earlier
        for dep in SLOT_DEPS[slot]:
            ek = attempt_key(approval_sha, dep)
            if self.state(ek) != "terminal" or self.read("terminal", ek).get("status") != "success":
                return False, "dependency_%s_not_success" % dep
        return True, "ok"
