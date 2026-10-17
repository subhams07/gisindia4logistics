"""Optional smoke (addendum section 4): does an OOM eventfd via cgroup.event_control work on this host?
Uses a unit-created n2-ut-* cgroup with a 32 MiB limit and a stdlib child. Not part of the gating path."""
import json
import os
import select
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from tests.ocg import OwnedCg


def main():
    o = OwnedCg(mem_limit=32 << 20)
    res = {}
    try:
        efd = os.eventfd(0, os.EFD_NONBLOCK)
        ofd = os.open(o.lay.abs["memory"] + "/memory.oom_control", os.O_RDONLY)
        try:
            with open(o.lay.abs["memory"] + "/cgroup.event_control", "w") as f:
                f.write("%d %d" % (efd, ofd))
            res["registered"] = True
        except OSError as e:
            res["registered"] = False
            res["error"] = str(e)
        o.spawn("wait_go()\nkeep = []\nwhile True:\n    b = bytearray(1 << 20)\n    for j in range(0, len(b), 4096): b[j] = 1\n    keep.append(b)\n")
        o.join()
        o.go()
        r, _, _ = select.select([efd], [], [], 10)
        res["eventfd_fired"] = bool(r)
        if r:
            res["eventfd_value"] = int.from_bytes(os.read(efd, 8), "little")
        o.child.wait(timeout=20)
        res["child_returncode"] = o.child.returncode
        res["oom_kill"] = o.mem()["oom_kill"]
    finally:
        o.close()
    print(json.dumps(res))
    with open(os.path.join(HERE, "results", "oom_eventfd_smoke.json"), "w") as f:
        json.dump(res, f)


main()
