"""Run the child stub directly inside a fresh PID(+mount) namespace, outside the controller flow.
Used for stub-level controls (fd check, mount-namespace guard, D-4). The caller's own cgroups are
used as the expected ones, so nothing is created or joined."""
import hashlib
import json
import os
import subprocess

from n2h import cg as cgmod
from n2h import launch, stubsrc
from n2h.fsio import RealFs
from tests import helpers as H


def build_expected(scr, work=None, patch=None, stage=0, mount_ns=True):
    fs = RealFs()
    cgp = cgmod.controller_cgroup_paths(fs)
    interp = launch.verify_binary(H.PY)
    setp = launch.verify_binary(H.SETPRIV)
    payload = scr.path("payload.py")
    if not os.path.exists(payload):
        with open(payload, "w") as f:
            f.write(stubsrc.WORKLOAD)
    psha = hashlib.sha256(stubsrc.WORKLOAD.encode()).hexdigest()
    st = os.stat("/proc/self/ns/mnt")
    env = launch.build_env(scr.mkdir("home"), scr.mkdir("tmp"))
    exp = {
        "nonce": "feedfacefeedface", "codes": stubsrc.S_CODES, "interp": {"path": H.PY, "ident": interp["ident"], "sha": interp["sha"]},
        "cgroups": {k: cgp[k] for k in ("memory", "pids", "v2") if k in cgp},
        "ctrl_nspid_fields": len(open("/proc/self/status").read().split("NSpid:")[1].split("\n")[0].split()),
        "nspid_last": 1, "env": env, "stage": stage, "ctrl_mntns": [st.st_dev, st.st_ino],
        "setpriv": {"path": H.SETPRIV, "ident": setp["ident"], "sha": setp["sha"]},
        "cgroup_probe_path": "/sys/fs/cgroup/memory" + cgp["memory"] + "/cgroup.procs",
        "payload": {"path": payload, "sha": psha}, "work": work or {"mode": "sleep", "seconds": 0.1},
        "site_main": False, "stub_sha": hashlib.sha256(stubsrc.STUB.encode()).hexdigest(),
        "cmdline_stub_index": stubsrc.CMDLINE_STUB_INDEX,
    }
    exp.update(patch or {})
    return exp, env


def run_stub(scr, work=None, patch=None, extra_fd=None, mount_ns=True, kill_child=True, env_extra=None,
             pre_args=(), timeout=30):
    exp, env = build_expected(scr, work, patch)
    env = dict(env, **(env_extra or {}))
    argv = ["unshare", "--pid", "--fork"]
    if kill_child:
        argv.append("--kill-child=SIGKILL")
    if mount_ns:
        argv.append("--mount")
    argv += ["--", H.PY, "-I", "-S", "-B", "-u"] + list(pre_args) + ["-c", stubsrc.STUB, json.dumps(exp, sort_keys=True, separators=(",", ":"))]
    pass_fds = ()
    rfd = None
    if extra_fd:
        rfd = os.open("/dev/null", os.O_RDONLY)
        pass_fds = (rfd,)
    try:
        p = subprocess.run(["/usr/bin/" + argv[0]] + argv[1:], capture_output=True, env=env, pass_fds=pass_fds, timeout=timeout)
    finally:
        if rfd is not None:
            os.close(rfd)
    events = []
    for line in p.stderr.decode("ascii", "replace").splitlines():
        parts = line.split(" ")
        if parts[0] == "N2M" and len(parts) >= 4 and parts[1] == exp["nonce"]:
            events.append((parts[3], dict(x.partition("=")[::2] for x in parts[4:])))
    return p.returncode, p.stderr.decode("ascii", "replace"), events
