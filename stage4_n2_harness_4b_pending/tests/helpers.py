"""Shared test helpers. All scratch lives under n2_harness/scratch/<unique>; nothing outside."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

from n2h import cg as cgmod
from n2h import ledger as ledger_mod
from n2h import policy as policy_mod

SCRATCH_PARENT = os.path.join(HARNESS, "scratch")
PY = "/usr/bin/python3.11"
UNSHARE = "/usr/bin/unshare"
SETPRIV = "/usr/bin/setpriv"
REPO = "/home/user/gisindia4logistics"
DRIVER = os.path.join(HARNESS, "driver_entry.py")


class Scratch:
    """Private, disjoint scratch root per test."""

    def __init__(self, tag):
        os.makedirs(SCRATCH_PARENT, exist_ok=True)
        self.root = tempfile.mkdtemp(prefix=tag[:24] + "-", dir=SCRATCH_PARENT)
        self.leftover_layouts = []

    def path(self, *parts):
        p = os.path.join(self.root, *parts)
        return p

    def mkdir(self, *parts):
        p = self.path(*parts)
        os.makedirs(p, exist_ok=True)
        return p

    def cleanup(self):
        for lay in self.leftover_layouts:
            try:
                from n2h.controller import cleanup_test_orphan
                cleanup_test_orphan(lay)
            except (OSError, Exception):  # noqa: BLE001 - best-effort teardown of own cgroups
                pass
        shutil.rmtree(self.root, ignore_errors=True)


def read_cgroup_names():
    return [f["path"] for f in cgmod.scan_existing(__import__("n2h.fsio", fromlist=["x"]).RealFs())]


def make_world(scr, slot="H0", work=None, policy_over=None, synthetic_mock=True, approval_extra=None,
               scope_read=None, scope_write=None):
    """Mock control root + receipts + slot dirs + driver spec skeleton. Returns dict."""
    ctrl = scr.mkdir("control")
    ledger_mod.init_mock_control_root(ctrl, "mockroot-" + os.path.basename(scr.root), synthetic_mock)
    pol = policy_mod.synthetic_test_policy(**(policy_over or {}))
    rdir = scr.mkdir("receipts")
    pol_sha = ledger_mod.write_receipt(os.path.join(rdir, "policy.json"),
                                       {"kind": "policy", "policy": pol, "synthetic_mock": synthetic_mock})
    scope_sha = ledger_mod.write_receipt(os.path.join(rdir, "scope.json"),
                                         {"kind": "scope", "read_scope": scope_read if scope_read is not None else [],
                                          "write_scope": scope_write if scope_write is not None else [scr.root],
                                          "synthetic_mock": synthetic_mock})
    pkg = os.path.join(HARNESS, "n2h")
    hsha = ledger_mod.harness_hash(pkg)
    st_sha = ledger_mod.write_receipt(os.path.join(rdir, "selftest.json"),
                                      {"kind": "selftest_report", "harness_sha": hsha, "synthetic_mock": synthetic_mock,
                                       "run_id": os.path.basename(scr.root)})
    root_meta, _ = ledger_mod.read_receipt(os.path.join(ctrl, "ROOT.json"), "ledger_root")
    appr = {"kind": "approval", "policy_sha": pol_sha, "scope_sha": scope_sha, "harness_sha": hsha,
            "selftest_sha": st_sha, "control_root_id": root_meta["root_id"], "synthetic_mock": synthetic_mock,
            "label": "SYNTHETIC_MOCK_APPROVAL_NOT_REAL"}
    appr.update(approval_extra or {})
    appr_sha = ledger_mod.write_receipt(os.path.join(rdir, "approval.json"), appr)
    bindir = scr.mkdir("bin")
    link = os.path.join(bindir, "python3.11")
    os.symlink(PY, link)
    slot_root = scr.mkdir("slots", slot)
    return {
        "control_root": ctrl, "policy": pol, "policy_sha": pol_sha, "scope_sha": scope_sha,
        "harness_sha": hsha, "selftest_sha": st_sha, "approval_sha": appr_sha,
        "control_root_id": root_meta["root_id"], "slot": slot, "slot_root": slot_root,
        "scratch_base": scr.root, "unshare": UNSHARE, "interpreter": link, "setpriv": SETPRIV,
        "work": work or {"mode": "sleep", "seconds": 0.3}, "link": link, "receipts_dir": rdir,
        "paths": {k: os.path.join(rdir, k + ".json") for k in ("approval", "policy", "scope", "selftest")},
    }


def spec_path(world, extra=None):
    d = {k: world[k] for k in ("control_root", "policy", "policy_sha", "scope_sha", "harness_sha", "selftest_sha",
                                "approval_sha", "control_root_id", "slot", "slot_root", "scratch_base", "unshare",
                                "interpreter", "setpriv", "work")}
    d.update(extra or {})
    return d


def driver_cmd(spec_file, result_file):
    return [PY, "-I", "-S", "-B", "-u", DRIVER, spec_file, result_file]


def run_driver(scr, spec, timeout=120, tag="run", env=None):
    sf = scr.path(tag + ".spec.json")
    rf = scr.path(tag + ".result.json")
    with open(sf, "w") as f:
        json.dump(spec, f)
    p = subprocess.run(driver_cmd(sf, rf), capture_output=True, timeout=timeout, cwd=scr.root, env=env)
    res = None
    if os.path.exists(rf):
        with open(rf) as f:
            res = json.load(f)
    return res, p


def spawn_driver(scr, spec, tag="run", env=None):
    """Start a driver and return (Popen, result_file). Caller reads AT lines and kills."""
    sf = scr.path(tag + ".spec.json")
    rf = scr.path(tag + ".result.json")
    with open(sf, "w") as f:
        json.dump(spec, f)
    p = subprocess.Popen(driver_cmd(sf, rf), stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=scr.root, env=env)
    return p, rf


def attempt_name(world):
    from n2h import ledger as _l
    return "n2-" + _l.attempt_key(world["approval_sha"], world["slot"])[:20]


def register_orphan(scr, world):
    """Teardown guard: a test that kills its controller must still remove the attempt's own cgroups."""
    scr.leftover_layouts.append(layout_for(attempt_name(world)))


def copy_binary(scr, src, name):
    dst = os.path.join(scr.mkdir("bin"), name)
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)
    return dst


def pid_gone(pid, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not os.path.exists("/proc/%d" % pid):
            return True
        try:
            with open("/proc/%d/stat" % pid) as f:
                txt = f.read()
            if txt[txt.rindex(")") + 2] == "Z":
                return True
        except OSError:
            return True
        time.sleep(0.02)
    return False


class Sleepers:
    """Unrelated test-owned sleepers (adjacent PIDs). They must survive every termination path."""

    def __init__(self, n=3):
        self.procs = [subprocess.Popen([PY, "-c", "import time; time.sleep(600)"]) for _ in range(n)]

    def all_alive(self):
        return all(p.poll() is None and os.path.exists("/proc/%d" % p.pid) for p in self.procs)

    def pids(self):
        return [p.pid for p in self.procs]

    def close(self):
        for p in self.procs:
            p.kill()
            p.wait()


def read_until(p, prefix, timeout=60):
    """Read driver stdout until a line starting with prefix."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        line = p.stdout.readline()
        if not line:
            if p.poll() is not None:
                return None
            continue
        s = line.decode("ascii", "replace").strip()
        if s.startswith(prefix):
            return s
    return None


def layout_for(name):
    from n2h import fsio
    paths = cgmod.controller_cgroup_paths(fsio.RealFs())
    return cgmod.Layout(name, paths)


def no_n2_cgroups():
    from n2h import fsio
    return cgmod.scan_existing(fsio.RealFs())
