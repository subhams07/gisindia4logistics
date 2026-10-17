"""Modes: selftest (implemented), prepare (metadata only), run (refuses unless receipts valid).

Argument parsing is hand-rolled on purpose: argparse would load modules outside the pinned
pre-fork allowlist in a process that later forks (run mode).
"""
import json
import os
import sys

from . import cg as cgmod
from . import ledger as ledger_mod
from . import policy as policy_mod
from . import roots as roots_mod
from . import sysfs
from .errors import N2Stop, RefusedRun, RootsStop
from .fsio import RealFs
from .launch import verify_binary

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = HERE
HARNESS_DIR = os.path.dirname(HERE)


def parse_opts(argv):
    opts = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if not a.startswith("--") or i + 1 >= len(argv):
            raise RefusedRun("bad_arguments", a)
        opts[a[2:]] = argv[i + 1]
        i += 2
    return opts


def prepare(cfg):
    """Metadata only. No primary/population/cache content is opened, warmed or transferred.

    cfg keys: scratch_base, control_root, log_root, repo_root (forbidden tree), binaries
    {unshare, interpreter, setpriv}, metadata_paths [paths to lstat only], policy (dict)
    Returns the prepare record (also what run later compares the controller cgroup paths to).
    """
    fs = RealFs()
    base = cfg["scratch_base"]
    forbid = [cfg["repo_root"]] if cfg.get("repo_root") else []
    roots = roots_mod.resolve_roots(base, extra_forbidden=forbid, control=cfg["control_root"], logs=cfg["log_root"])
    if os.listdir(cfg["control_root"]):
        raise RootsStop("control_root_not_empty", cfg["control_root"])
    for p in cfg.get("metadata_paths", []):
        roots_mod.reject_forbidden(p, [x for x in forbid if not cfg.get("allow_repo_metadata")])
    meta = {}
    for p in cfg.get("metadata_paths", []):
        st = os.lstat(p)  # stat only: never open
        meta[p] = {"exists": True, "mode": st.st_mode, "size": st.st_size, "dev": st.st_dev, "ino": st.st_ino}
    bins = {k: {kk: v[kk] for kk in ("path", "real", "ident", "sha")}
            for k, v in ((k, verify_binary(p)) for k, p in cfg["binaries"].items())}
    if cfg.get("policy") is not None:
        policy_mod.validate_policy(cfg["policy"])
    swap = sysfs.swap_total_bytes(fs.read("/proc/meminfo"))
    if swap != 0:
        raise RefusedRun("swap_present", "SwapTotal=%d; memsw=2x limit relies on no swap" % swap)
    return {
        "kind": "prepare_record", "mode": "prepare", "roots": roots,
        "controller_cgroup_paths": cgmod.controller_cgroup_paths(fs),
        "boot_id": fs.read("/proc/sys/kernel/random/boot_id").strip(), "kernel_release": os.uname().release,
        "binaries": bins, "metadata_stat_only": meta, "content_reads": 0,
        "harness_sha": ledger_mod.harness_hash(PKG_DIR), "swap_total_bytes": swap,
    }


def run_gate(opts):
    """Returns verified bindings or raises RefusedRun. Never creates anything."""
    return ledger_mod.gate_run(
        opts.get("control-root"), opts.get("approval"), opts.get("policy"), opts.get("scope"),
        opts.get("selftest-report"), PKG_DIR)


def selftest(argv):
    """Run the unittest suite via run_selftests.py (same code path as the documented command)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("n2_run_selftests", os.path.join(HARNESS_DIR, "run_selftests.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main(["run_selftests.py"] + list(argv))


def check_interpreter_flags(require_no_site=False):
    """Run with -I -B (and -u); never -O (asserts and __debug__ code must run as written).
    The controller modes (prepare, run, driver) additionally require -S: no system .pth may run."""
    f = sys.flags
    if f.optimize != 0 or f.isolated != 1 or f.dont_write_bytecode != 1 or (require_no_site and f.no_site != 1):
        raise RefusedRun("bad_interpreter_flags", "optimize=%d isolated=%d dont_write_bytecode=%d no_site=%d" % (
            f.optimize, f.isolated, f.dont_write_bytecode, f.no_site))


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: n2_harness.py selftest|prepare|run [--key value ...]\n")
        return 2
    mode, rest = argv[1], argv[2:]
    try:
        check_interpreter_flags(require_no_site=mode in ("prepare", "run"))
    except RefusedRun as e:
        sys.stderr.write("REFUSED %s: %s\n" % (e.code, e.detail))
        return 3
    if mode == "selftest":
        return selftest(rest)
    try:
        if mode == "prepare":
            opts = parse_opts(rest)
            with open(opts["config"]) as f:
                cfg = json.load(f)
            rec = prepare(cfg)
            sys.stdout.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
            return 0
        if mode == "run":
            opts = parse_opts(rest)
            gate = run_gate(opts)
            return run_after_gate(opts, gate)
    except N2Stop as e:
        sys.stderr.write("REFUSED %s: %s\n" % (e.code, e.detail))
        return 3
    sys.stderr.write("unknown mode\n")
    return 2


def run_after_gate(opts, gate):
    """Dispatch through the slot runner (slotrun): never the test driver, no driver-only knobs, synthetic_mock
    passed explicitly, prepare-recorded controller cgroup paths passed on. Non-mock gates cannot reach here
    (the gate refuses them) and the runner refuses them again."""
    from . import slotrun
    paths = None
    if "prepare-record" in opts:
        paths = slotrun.load_prepare_record(opts["prepare-record"], gate)["controller_cgroup_paths"]
    elif gate.get("synthetic_mock") is not True:
        raise RefusedRun("prepare_record_required", "real dispatch needs the prepare record")
    if "slot-config-json" in opts:  # a real slot payload (H0..H3) on synthetic data
        payload, phases = slotrun.payload_and_phases(opts["slot"])
        work = json.loads(opts["slot-config-json"])
        site_main = True
    else:  # the 4a synthetic stdlib workload
        from . import stubsrc
        payload, phases = stubsrc.WORKLOAD, ()
        work = json.loads(opts.get("work-json", '{"mode":"sleep","seconds":0.2}'))
        site_main = False
    uid = int(opts["payload-uid"]) if "payload-uid" in opts else None
    if "slot-config-json" in opts and uid is None:
        raise RefusedRun("payload_uid_required", "a slot payload never runs as root")
    kw = {"control_root": opts["control-root"], "slot": opts["slot"], "slot_root": opts["slot-root"],
          "scratch_base": opts["scratch-base"], "interpreter": opts["interpreter"], "unshare": "/usr/bin/unshare",
          "setpriv": "/usr/bin/setpriv", "recorded_controller_paths": paths, "payload_source": payload, "work": work,
          "site_main": site_main, "declared_phases": phases, "require_receipt": "slot-config-json" in opts}
    if uid is not None:
        kw.update(payload_uid=uid, payload_gid=uid)
    else:
        kw.update(payload_uid=None, payload_gid=None)  # 4a-compatible root payload (mock only)
    term, _cfg = slotrun.dispatch(gate, slot_run="slot-config-json" in opts, **kw)
    pv = term.get("parent_verification")
    sys.stdout.write(json.dumps({"status": term["status"], "stops": term["stops"], "parent_verification": pv},
                                sort_keys=True) + "\n")
    return 0 if term["status"] == "success" and (pv is None or pv.get("receipt_ok")) else 4
