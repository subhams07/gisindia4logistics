"""L13 manual smoke: what does a Bash-tool timeout kill do to a controller and its contained tree?
  start  : launch a real controller (paused after release) as a child of THIS process, record the tree,
           then wait. Run it from a Bash call with a short timeout so the tool kills it.
  report : afterwards, say which recorded processes are alive, which signals the stand-ins recorded,
           and clean up only this test's own cgroups.
Only stdlib; scratch under n2_harness/scratch/l13."""
import json
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from tests import helpers as H

STATE = os.path.join(HERE, "scratch", "l13_state.json")
SIGLOG = os.path.join(HERE, "scratch", "l13_signals.log")


def snapshot(pid):
    try:
        st = open("/proc/%d/stat" % pid).read()
        rest = st[st.rindex(")") + 2:].split()
        return {"state": rest[0], "ppid": int(rest[1]), "pgid": int(rest[2]), "sid": int(rest[3])}
    except OSError:
        return None


def start():
    os.makedirs(os.path.join(HERE, "scratch"), exist_ok=True)
    for p in (STATE, SIGLOG):
        if os.path.exists(p):
            os.unlink(p)

    def rec(sig, frame):
        with open(SIGLOG, "a") as f:
            f.write("wrapper pid=%d sig=%d t=%.3f\n" % (os.getpid(), sig, time.time()))
    for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGUSR1):
        signal.signal(s, rec)
    scr = H.Scratch("l13")
    w = H.make_world(scr, work={"mode": "sleep", "seconds": 20}, policy_over={"per_slot_elapsed_cap_s": 25})
    spec = H.spec_path(w, {"pause_at": ["after_release"], "pause_s": 60, "record_signals": SIGLOG})
    p, rf = H.spawn_driver(scr, spec)
    line = H.read_until(p, "AT after_release")
    time.sleep(1.0)
    from n2h import cg as cgmod
    from n2h.fsio import RealFs
    lay = H.layout_for(H.attempt_name(w))
    members = cgmod.members(lay, RealFs())
    pids = {"wrapper": os.getpid(), "controller": p.pid, "members": members}
    state = {"scratch": scr.root, "name": H.attempt_name(w), "pids": pids, "line": line,
             "snap": {str(x): snapshot(x) for x in [os.getpid(), p.pid] + members},
             "own_pgid": os.getpgid(0), "own_sid": os.getsid(0), "t": time.time()}
    with open(STATE, "w") as f:
        json.dump(state, f)
    print("STARTED", json.dumps(state), flush=True)
    time.sleep(300)  # the Bash tool timeout is expected to end this


def report():
    st = json.load(open(STATE))
    out = {"elapsed_since_start_s": round(time.time() - st["t"], 1)}
    allp = [st["pids"]["wrapper"], st["pids"]["controller"]] + st["pids"]["members"]
    out["alive_now"] = {str(p): snapshot(p) for p in allp}
    out["signals_recorded"] = open(SIGLOG).read().splitlines() if os.path.exists(SIGLOG) else []
    from n2h import cg as cgmod
    from n2h.fsio import RealFs
    lay = H.layout_for(st["name"])
    out["cgroups_left"] = [f["path"] for f in cgmod.scan_existing(RealFs())]
    print(json.dumps(out, indent=1))
    if "--cleanup" in sys.argv:
        if os.path.isdir(lay.abs["memory"]):
            from n2h.controller import cleanup_test_orphan
            print("cleanup own orphan cgroups:", cleanup_test_orphan(lay, wait_s=60))
        import shutil
        shutil.rmtree(st["scratch"], ignore_errors=True)
        print("cgroups now:", [f["path"] for f in cgmod.scan_existing(RealFs())])


if __name__ == "__main__":
    {"start": start, "report": report}[sys.argv[1]]()
