"""Controller driver run as a separate `python3.11 -I -S -B -u` process by the self-tests.

The self-test process (unittest, many modules loaded) can never satisfy the strict L-3
pre-fork module allowlist, so every real launch happens in this small controller process.
The scenario spec is a JSON file; the result is written exclusively to a result file.
"""
import json
import os
import sys
import time

from . import cg as cgmod
from . import ledger as ledger_mod
from . import policy as policy_mod
from .allowlist import (
    STDLIB_ALLOWLIST,  # noqa: F401  (pinned: imported so it is in sys.modules)
)
from .controller import Attempt
from .errors import FaultPlan, N2Stop
from .fsio import RealFs


def _say(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def make_pause_hook(point, spec):
    """Print `AT <point> <pid>` and block so the test can SIGKILL this controller there."""
    def hook(attempt):
        gated = getattr(attempt, "gated", None)
        _say("AT %s ctrl=%d child=%s" % (point, os.getpid(), gated.pid if gated else "-"))
        time.sleep(spec.get("pause_s", 30))
    return hook


class _Probe:
    """Buffered stdout stand-in used to prove stdio is flushed before fork (L-3)."""

    def __init__(self, real):
        self.real = real
        self.pending = False
        self.flushes = 0

    def write(self, s):
        self.pending = True
        return len(s)

    def flush(self):
        self.pending = False
        self.flushes += 1


def run_spec(spec, extras=None):
    extras = extras if extras is not None else {}
    fs = RealFs()
    policy = spec["policy"]
    policy_mod.validate_policy(policy)
    ledger = ledger_mod.Ledger(spec["control_root"], FaultPlan(spec.get("ledger_faults", [])))
    plan = FaultPlan(spec.get("faults", []))
    hooks = {}
    for point in spec.get("pause_at", []):
        hooks[point] = make_pause_hook(point, spec)
    after_verify = []
    if spec.get("swap_symlink_after_verify"):
        sw = spec["swap_symlink_after_verify"]

        def swap():
            os.unlink(sw["link"])
            os.symlink(sw["to"], sw["link"])
        after_verify.append(swap)
    if spec.get("rewrite_exec_after_verify"):
        path = spec["rewrite_exec_after_verify"]
        after_verify.append(lambda: _append_byte(path))
    if after_verify:
        hooks["after_verify"] = lambda: [f() for f in after_verify]
    if spec.get("rewrite_exec_at"):
        r = spec["rewrite_exec_at"]
        hooks[r["hook"]] = lambda a: _append_byte(r["path"])
    if spec.get("precreate_after_attempt"):
        def precreate(attempt):
            from . import cg as _cg
            lay = _cg.Layout(attempt.name, attempt.ctrl_paths, attempt.cg_root, attempt.cfg.get("use_v2", True))
            os.mkdir(lay.abs["memory"])
            extras["precreated"] = lay.abs["memory"]
        hooks["after_attempt_record"] = precreate
    if spec.get("close_gate_instead_of_release") or spec.get("wrong_gate_byte"):
        def tamper(attempt):
            g = attempt.gated
            if spec.get("wrong_gate_byte"):
                os.write(g.gate_w, b"X")
            os.close(g.gate_w)
            g.gate_w = -1
            g.released = True
        hooks["before_release"] = tamper
    if spec.get("import_before_launch"):
        __import__(spec["import_before_launch"])
    if spec.get("sigchld_ign"):
        import signal
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    if spec.get("record_signals"):
        import signal
        rec_path = spec["record_signals"]

        def rec(sig, frame):
            fd = os.open(rec_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.write(fd, ("pid=%d sig=%d t=%.3f\n" % (os.getpid(), sig, time.time())).encode("ascii"))
            os.close(fd)
        for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGUSR1, signal.SIGUSR2):
            signal.signal(s, rec)
    if spec.get("install_handlers"):
        import signal
        signal.signal(signal.SIGUSR1, lambda s, f: None)
        signal.signal(signal.SIGTERM, lambda s, f: None)
    probe = None
    if spec.get("buffered_stdout_probe"):
        probe = _Probe(sys.stdout)
        sys.stdout = probe
        sys.stdout.write("BUFFERED_BEFORE_FORK\n")
        inner = hooks.get("at_S0")

        def at_s0(attempt):
            extras["stdout_pending_at_S0"] = probe.pending
            extras["stdout_flushes"] = probe.flushes
            import gc
            extras["parent_gc_enabled_after_fork"] = gc.isenabled()
            if inner:
                inner(attempt)
        hooks["at_S0"] = at_s0
    cfg = {
        "ledger": ledger, "policy": policy, "plan": plan, "hooks": hooks, "fs": fs,
        "approval_sha": spec["approval_sha"], "policy_sha": spec["policy_sha"],
        "harness_sha": spec["harness_sha"], "scope_sha": spec["scope_sha"],
        "selftest_sha": spec["selftest_sha"], "control_root_id": spec["control_root_id"],
        "slot": spec["slot"], "slot_root": spec["slot_root"], "scratch_base": spec["scratch_base"],
        "unshare": spec["unshare"], "interpreter": spec["interpreter"], "setpriv": spec["setpriv"],
        "work": spec["work"], "synthetic_mock": True,
        "inject": spec.get("inject"), "expected_patch": spec.get("expected_patch"),
        "env_extra_actual": spec.get("env_extra_actual", {}), "declared_phases": spec.get("declared_phases", ()),
        "require_receipt": spec.get("require_receipt", False),
        "cold_inputs": spec.get("cold_inputs"), "elsewhere_files": spec.get("elsewhere_files"),
        "use_v2": spec.get("use_v2", True), "cg_root": spec.get("cg_root", cgmod.CG_ROOT),
        "recorded_controller_paths": spec.get("recorded_controller_paths"),
        "fs_faults": {tuple(k.split("|", 1)): v for k, v in spec.get("fs_faults", {}).items()} or None,
        "no_subreaper": spec.get("no_subreaper", False),
    }
    if spec.get("stub_suffix"):
        from . import stubsrc
        cfg["stub_actual"] = stubsrc.STUB + spec["stub_suffix"]
    if spec.get("extra_allow_modules"):
        cfg["extra_allow_modules"] = spec["extra_allow_modules"]
    attempt = Attempt(cfg)
    return attempt.run()


def _append_byte(path):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    os.write(fd, b"\0")
    os.close(fd)


def main(argv):
    from .cli import check_interpreter_flags
    check_interpreter_flags(require_no_site=True)
    spec_path, result_path = argv[1], argv[2]
    with open(spec_path) as f:
        spec = json.load(f)
    out = {"ctrl_pid": os.getpid()}
    extras = {}
    try:
        out["terminal"] = run_spec(spec, extras)
    except N2Stop as e:
        out["refused"] = {"code": e.code, "detail": e.detail, "injected": e.injected}
    except BaseException as e:  # noqa: BLE001 - driver-level failure is reported as data to the test, not hidden
        out["driver_exception"] = type(e).__name__ + ": " + str(e)[:300]
    out["extras"] = extras
    fd = os.open(result_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, json.dumps(out, sort_keys=True, default=str).encode("ascii"))
    os.close(fd)
    os.write(1, ("DONE %d\n" % os.getpid()).encode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
