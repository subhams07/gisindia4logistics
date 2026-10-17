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
from .errors import FaultPlan, LedgerStop, PolicyStop, RefusedRun

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


ENTRY_SCRIPTS = ("n2_harness.py", "driver_entry.py", "run_selftests.py")


def harness_hash(pkg_dir):
    """Hash binding the harness source: sorted (name, sha256) of every *.py in the package plus the
    entry scripts that sit next to the package directory (they are executed, so they are bound too)."""
    items = []
    for name in sorted(os.listdir(pkg_dir)):
        if name.endswith(".py"):
            items.append(["n2h/" + name, sha256_file(os.path.join(pkg_dir, name))])
    parent = os.path.dirname(os.path.abspath(pkg_dir))
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
        os.write(fd, data)
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

    Called BEFORE any cgroup/child/attempt creation. In 4a only synthetic_mock receipts
    in a synthetic mock control root can pass; valid non-mock receipts still refuse
    because real dispatch is not implemented here.
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
        # Real receipts cannot dispatch in 4a: no real approval exists and no real producer
        # launch is implemented in this stdlib-only step.
        raise RefusedRun("real_dispatch_not_implemented_4a", "flags=%r" % sorted(map(str, flags)))
    if not os.path.exists(os.path.join(control_root, MOCK_MARKER)):
        raise RefusedRun("mock_marker_missing", "")
    if pol_body["policy"].get("approved") is not False:
        raise RefusedRun("mock_policy_must_be_unapproved_label", "")
    return {"approval_sha": approval_sha, "policy_sha": pol_sha, "scope_sha": scope_sha,
            "selftest_sha": st_sha, "harness_sha": now_hash, "root_id": root_meta["root_id"],
            "policy": pol_body["policy"], "approval": approval}


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
            self.plan.check("INJECTED_E_LEDGER_WRITE")
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o444)
        except FileExistsError:
            raise LedgerStop("%s_exists" % kind, key)
        except OSError as e:
            self._block("create failed: %s" % e)
            raise LedgerStop("persist_failed", "%s create: %s" % (kind, e), injected=getattr(e, "label", None))
        try:
            os.write(fd, canonical(make_receipt(body)))
            self.plan.check("INJECTED_E_FSYNC")
            os.fsync(fd)
            os.close(fd)
            fd = -1
            _fsync_dir(os.path.dirname(path))
        except OSError as e:
            if fd >= 0:
                os.close(fd)
            self._block("persist failed: %s" % e)
            raise LedgerStop("persist_failed", "%s fsync: %s" % (kind, e), injected=getattr(e, "label", None))
        return sha256_file(path)

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

    def can_dispatch_slot(self, approval_sha, slot):
        """Serial order and dependency rule. Returns (ok, reason)."""
        if self.blocked:
            return False, "dispatch_blocked"
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
