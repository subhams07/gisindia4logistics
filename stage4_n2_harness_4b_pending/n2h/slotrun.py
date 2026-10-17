"""The slot runner a real H0/H1/H2/H3 dispatch would use (step 4b). Stdlib only (runs in the controller).

It builds the Attempt config WITHOUT any driver-only hook or knob, asserts their absence, never goes through
the test driver, passes synthetic_mock explicitly and the prepare-recorded controller cgroup paths. It is
reached only after the run gate; real (non-mock) receipts are refused there because no approved
policy/scope receipt exists, and the runner refuses again by itself.
"""
import hashlib
import json
import os
import stat

from . import ledger as ledger_mod
from . import roots as roots_mod
from . import slots
from .controller import Attempt, default_elsewhere_files
from .errors import N2Stop, RefusedRun
from .ledger import Ledger

# Keys that exist only for tests (driver.run_spec / hooks) or that would let a caller replace a safety default.
DRIVER_ONLY_KEYS = frozenset([
    "hooks", "plan", "inject", "faults", "fs_faults", "ledger_faults", "expected_patch", "stub_actual",
    "env_extra_actual", "no_subreaper", "extra_allow_modules", "stub_suffix",
    "pause_at", "pause_s", "sleep_before_monitor", "fault_final_sample", "telemetry_dev_full", "finalize_boom",
    "finalize_unserializable", "marker_read_fault", "post_exit_typeerror", "record_signals", "install_handlers",
    "sigchld_ign", "import_before_launch", "buffered_stdout_probe", "swap_symlink_after_verify",
    "rewrite_exec_after_verify", "rewrite_exec_at", "precreate_after_attempt", "close_gate_instead_of_release",
    "wrong_gate_byte",
    # Attempt-level overrides decided in 4b: excluded for dispatch and asserted absent (defaults are the only
    # legitimate values: random nonce, RealFs, the real cgroup root, cgroup v2 enabled)
    "nonce", "fs", "cg_root", "use_v2", "cold_inputs",
])
HOOK_NAMES = ("at_cgroups_ready", "at_S0", "at_S1", "before_release", "after_release", "after_attempt_record",
              "after_verify", "before_monitor", "before_final_sample", "before_finalize")
PAYLOAD_UID = 65534  # numeric nobody; no system user is created
PAYLOAD_GID = 65534


def assert_no_driver_only(cfg):
    bad = sorted(set(cfg) & DRIVER_ONLY_KEYS)
    if bad:
        raise N2Stop("driver_only_knob_present", ",".join(bad))
    return True


def venv_shared_objects(interpreter, cap=6000):
    """Shared objects under a venv's site-packages: additive cache-uncertainty set for the child (D-11)."""
    bindir = os.path.dirname(os.path.abspath(interpreter))
    venv = os.path.dirname(bindir)
    if not os.path.isfile(os.path.join(venv, "pyvenv.cfg")):
        return []
    out = []
    for dirpath, _dirs, files in os.walk(os.path.join(venv, "lib")):
        for n in files:
            if ".so" in n:
                p = os.path.join(dirpath, n)
                if os.path.isfile(p) and not os.path.islink(p):
                    out.append(os.path.realpath(p))
                    if len(out) >= cap:
                        return sorted(set(out))
    return sorted(set(out))


def build_attempt_config(gate, *, control_root, slot, slot_root, scratch_base, interpreter, unshare, setpriv,
                         recorded_controller_paths, payload_source, work, payload_uid=PAYLOAD_UID,
                         payload_gid=PAYLOAD_GID, site_main=False, private_inputs=None, declared_phases=(),
                         require_receipt=True, parent_gating=None):
    mock = gate.get("synthetic_mock")
    if not isinstance(mock, bool):
        raise N2Stop("synthetic_mock_not_explicit", repr(mock))
    cfg = {
        "ledger": Ledger(control_root), "policy": gate["policy"], "approval_sha": gate["approval_sha"],
        "policy_sha": gate["policy_sha"], "harness_sha": gate["harness_sha"], "scope_sha": gate["scope_sha"],
        "selftest_sha": gate["selftest_sha"], "control_root_id": gate["root_id"], "slot": slot,
        "slot_root": slot_root, "scratch_base": scratch_base, "unshare": unshare, "interpreter": interpreter,
        "setpriv": setpriv, "work": work, "synthetic_mock": mock, "require_receipt": bool(require_receipt),
        "declared_phases": tuple(declared_phases), "recorded_controller_paths": recorded_controller_paths,
        "payload_source": payload_source, "payload_uid": payload_uid, "payload_gid": payload_gid,
        "site_main": bool(site_main),
        "elsewhere_files": sorted(set(default_elsewhere_files()) | set(venv_shared_objects(interpreter))),
    }
    if private_inputs:
        cfg["private_inputs"] = private_inputs
    if parent_gating is not None:
        cfg["parent_gating"] = parent_gating
    assert_no_driver_only(cfg)
    return cfg


def assert_real_requirements(cfg):
    """What a non-mock dispatch must have (in addition to the gate): everything the 4a reviews carried forward."""
    for key, ok in (("recorded_controller_paths", cfg.get("recorded_controller_paths") is not None),
                    ("payload_uid", cfg.get("payload_uid") not in (None, 0)),
                    ("site_main", cfg.get("site_main") is True),
                    ("require_receipt", cfg.get("require_receipt") is True),
                    ("synthetic_mock_false", cfg.get("synthetic_mock") is False)):
        if not ok:
            raise N2Stop("real_dispatch_requirement_missing", key)


def load_prepare_record(path, gate):
    try:
        with open(path, "rb") as f:
            rec = json.loads(f.read().decode("ascii"))
    except (OSError, ValueError, UnicodeError) as e:
        raise RefusedRun("prepare_record_unreadable", str(e)) from e
    if rec.get("kind") != "prepare_record" or rec.get("harness_sha") != gate["harness_sha"]:
        raise RefusedRun("prepare_record_not_bound", "")
    with open("/proc/sys/kernel/random/boot_id") as f:
        if rec.get("boot_id") != f.read().strip():
            raise RefusedRun("prepare_record_other_boot", "")
    if not isinstance(rec.get("controller_cgroup_paths"), dict):
        raise RefusedRun("prepare_record_no_cgroup_paths", "")
    return rec


# ---- CONFIG validation: runs BEFORE any attempt record or cgroup exists ------------------------------------
CONFIG_KEYS = frozenset(["slot", "out_dir", "repo_root", "data_dir", "cache_dir", "sample_rows", "expected_graph",
                         "transfer_record", "transfer_record_sha256", "district_source", "population_source", "archive_name"])
READ_PATH_KEYS = ("data_dir", "transfer_record", "district_source", "population_source")
WRITE_PATH_KEYS = ("cache_dir", "out_dir")
H1_ROWS = {"H1_12": 12, "H1_64": 64, "H1_192": 192}


def _scoped(path, key, scratch_base, repo_root, roots):
    if not isinstance(path, str) or not os.path.isabs(path) or os.path.normpath(path) != path:
        raise N2Stop("config_path_invalid", key)
    roots_mod.reject_forbidden(path, [repo_root])
    parent = os.path.dirname(path)
    # existing prefix must be canonical (no symlink anywhere) and the whole path inside the scratch base
    probe = path if os.path.lexists(path) else parent
    if os.path.realpath(probe) != probe:
        raise N2Stop("config_path_not_canonical", key)
    if not roots_mod._is_within(path, os.path.realpath(scratch_base)):
        raise N2Stop("config_path_outside_scratch", key)
    if not any(roots_mod._is_within(path, os.path.realpath(r)) for r in roots):
        raise N2Stop("config_path_outside_receipt_scope", key)


def validate_slot_config(config, slot, *, scratch_base, repo_root, scope, mock):
    """Every path-valued CONFIG key must be inside the scratch base, outside the repository and inside the receipt's
    read/write scope; unknown keys are refused; sample_rows is bound to the slot name for a non-mock dispatch."""
    unknown = sorted(set(config) - CONFIG_KEYS)
    if unknown:
        raise N2Stop("config_unknown_key", ",".join(unknown))
    if config.get("slot") != slot:
        raise N2Stop("config_slot_mismatch", "%r != %r" % (config.get("slot"), slot))
    if config.get("repo_root") != repo_root or not os.path.isdir(os.path.join(repo_root, ".git")):
        raise N2Stop("config_repo_root_not_verified_repo", str(config.get("repo_root")))
    reads = list(scope.get("read_scope", [])) + list(scope.get("write_scope", []))
    writes = list(scope.get("write_scope", []))
    for key in READ_PATH_KEYS:
        if key in config:
            _scoped(config[key], key, scratch_base, repo_root, reads)
    for key in WRITE_PATH_KEYS:
        if key in config:
            _scoped(config[key], key, scratch_base, repo_root, writes)
    if "out_dir" not in config:
        raise N2Stop("config_out_dir_required", slot)
    if slot.startswith("H1"):
        n = config.get("sample_rows")
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise N2Stop("config_sample_rows_invalid", repr(n))
        if not mock and n != H1_ROWS[slot]:
            raise N2Stop("config_sample_rows_not_bound_to_slot", "%s needs %d" % (slot, H1_ROWS[slot]))
    return True


# ---- symlink-safe root-side operations inside payload-writable directories (B2) -------------------------------
def _open_dir(path):
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def safe_archive(cache_dir, archive_name):
    """Open the cache archive without following symlinks. The directory must contain exactly [archive_name]; the
    entry must be a regular file with a single link. Returns (dir_fd, file_fd); the caller closes both."""
    dfd = _open_dir(cache_dir)
    try:
        names = sorted(os.listdir(dfd))
        if names != [archive_name]:
            raise N2Stop("cache_dir_listing", ",".join(names))
        fd = os.open(archive_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    except BaseException:
        os.close(dfd)
        raise
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(fd)
        os.close(dfd)
        raise N2Stop("cache_entry_not_plain_regular_file", archive_name)
    return dfd, fd


def _hash_fd(fd):
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        b = os.read(fd, 1 << 20)
        if not b:
            return h.hexdigest()
        h.update(b)


def _read_json_nofollow(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not regular")
        data = b""
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            data += b
    finally:
        os.close(fd)
    return json.loads(data.decode("ascii"))


def parent_verify(slot, out_dir, cache_dir=None, expected_cache_sha=None, archive_name=None):
    """Parent-side checks after the terminal receipt: the PARENT hashes the cache archive bytes (from an
    O_NOFOLLOW descriptor); latches fail the slot even with exit 0; nothing else may be in the cache directory."""
    problems = []
    for name in ("sentinel_latch", "logging_latch"):
        if os.path.lexists(os.path.join(out_dir, name)):
            problems.append("latch_" + name)
    rec = {}
    if cache_dir is not None:
        if not archive_name:
            raise N2Stop("archive_name_required", "the caller names the archive; this module carries no cache path literals")
        try:
            dfd, fd = safe_archive(cache_dir, archive_name)
        except (N2Stop, OSError) as e:
            problems.append("cache:" + (e.code if isinstance(e, N2Stop) else "%s" % type(e).__name__))
            rec["cache_files"] = sorted(os.listdir(cache_dir)) if os.path.isdir(cache_dir) and not os.path.islink(cache_dir) else None
        else:
            try:
                rec["cache_files"] = [archive_name]
                rec["archive_sha256"] = _hash_fd(fd)
            finally:
                os.close(fd)
                os.close(dfd)
            if expected_cache_sha is not None and rec["archive_sha256"] != expected_cache_sha:
                problems.append("archive_hash_changed")
            if slot == "H0":
                try:
                    child = _read_json_nofollow(os.path.join(out_dir, "slot_result.json"))
                    if child["h0"]["archive"]["sha256"] != rec["archive_sha256"]:
                        problems.append("child_archive_hash_differs_from_parent")
                except (OSError, ValueError, KeyError):
                    problems.append("h0_result_unreadable")
    elif slot == "H0":
        problems.append("h0_cache_dir_required")
    return {"ok": not problems, "problems": problems, **rec}


def seal_cache(cache_dir, archive_name):
    """After the parent has hashed the H0 archive: root-own it and make it read-only so that the unprivileged
    payloads of H1/H2 cannot write, replace or warm it. Everything goes through descriptors opened with O_NOFOLLOW:
    a payload-planted symlink, hard link, extra entry or non-regular file is refused and nothing is chmod-ed."""
    dfd, fd = safe_archive(cache_dir, archive_name)
    try:
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o444)
        os.fchown(dfd, 0, 0)
        os.fchmod(dfd, 0o755)
    finally:
        os.close(fd)
        os.close(dfd)


def sha256_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise N2Stop("not_regular", path)
        return _hash_fd(fd)
    finally:
        os.close(fd)


def register_transfer(control_root, approval_sha, record_path, ok=True):
    """The PARENT-built transfer record is bound into the ledger; H3 may only start with the same record hash."""
    rec_sha = sha256_file(record_path)
    Ledger(control_root).create_parent_receipt(approval_sha, "TRANSFER", {"ok": bool(ok), "record_sha256": rec_sha})
    return rec_sha


def slot_config(slot, *, out_dir, repo_root, **kw):
    """CONFIG (JSON) for a slot payload. Paths only; validate_slot_config decides what they may point at."""
    cfg = {"slot": slot, "out_dir": out_dir, "repo_root": repo_root}
    cfg.update(kw)
    return cfg


def payload_and_phases(slot):
    return slots.real_payload(slot), slots.phases_for(slot)


def dispatch(gate, *, slot_run=False, **kw):
    """One slot attempt through the Attempt controller. Refuses real (non-mock) dispatch by itself.

    slot_run=True (a real slot payload H0..H3): validates the CONFIG before anything exists, gates the attempt on
    the parent-verification receipts, and after the terminal receipt runs the parent-side verification, persists a
    hash-bound parent_verification receipt and seals the H0 cache."""
    if gate.get("synthetic_mock") is not True and not ledger_mod.REAL_DISPATCH_ENABLED:
        raise RefusedRun("real_dispatch_disabled_pending_coordinator", "no approved policy/scope receipt")
    work, slot = kw["work"], kw["slot"]
    if slot_run:
        archive_name = work.get("archive_name")
        if not archive_name or "/" in archive_name:
            raise N2Stop("archive_name_required", "CONFIG names the cache archive file")
        if kw.get("payload_uid") in (None, 0):
            raise N2Stop("payload_uid_required", "a slot payload never runs as root")
        validate_slot_config(work, slot, scratch_base=kw["scratch_base"], repo_root=slots.verified_repo_root(kw["interpreter"]),
                             scope=gate.get("scope") or {}, mock=gate["synthetic_mock"])
        parent = {"expected_graph_sha": (work.get("expected_graph") or {}).get("sha256")}
        if slot == "H3":
            parent["transfer_record_sha"] = sha256_file(work["transfer_record"])
            if work.get("transfer_record_sha256") != parent["transfer_record_sha"]:
                raise N2Stop("transfer_record_sha_not_in_config", "")
        kw["parent_gating"] = parent
    cfg = build_attempt_config(gate, **kw)
    if gate["synthetic_mock"] is False:
        assert_real_requirements(cfg)
    term = Attempt(cfg).run()
    if slot_run:
        term["parent_verification"] = _after_terminal(gate, cfg, term, slot, work)
    return term, cfg


def _after_terminal(gate, cfg, term, slot, work):
    led = Ledger(cfg["ledger"].root)
    out = work["out_dir"]
    cache = work.get("cache_dir") if slot != "H3" else None
    archive_name = work["archive_name"]
    pv = parent_verify(slot, out, cache, (work.get("expected_graph") or {}).get("sha256"), archive_name)
    pv["terminal_status"] = term["status"]
    ok = pv["ok"] and term["status"] == "success"
    body = {"ok": ok, "problems": pv["problems"], "terminal_status": term["status"],
            "archive_sha256": pv.get("archive_sha256")}
    led.create_parent_receipt(gate["approval_sha"], slot, body)
    if slot == "H0" and ok:
        seal_cache(cache, archive_name)
        pv["sealed"] = True
    pv["receipt_ok"] = ok
    return pv
