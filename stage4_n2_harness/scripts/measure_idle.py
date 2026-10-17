"""Measurements on this host with SYNTHETIC test policy values. These numbers are MEASUREMENTS to inform the
D-5/D-11 policy discussion; they are not approved values and not a proposal."""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from tests import helpers as H


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def run(work, over=None, extra=None):
    scr = H.Scratch("meas")
    try:
        w = H.make_world(scr, work=work, policy_over=over)
        res, p = H.run_driver(scr, H.spec_path(w, extra))
        t = res["terminal"]
        rows = []
        tp = os.path.join(w["slot_root"], "telemetry.jsonl")
        if os.path.exists(tp):
            with open(tp) as f:
                rows = [json.loads(line) for line in f]
        return t, rows
    finally:
        scr.cleanup()


def main():
    out = {"label": "MEASUREMENTS_NOT_APPROVED_VALUES", "host": {"uname": os.uname().release, "nproc": os.cpu_count()}}
    # 1. idle series
    t, rows = run({"mode": "sleep", "seconds": 15}, {"sample_interval_ms": 50, "per_slot_elapsed_cap_s": 40})
    iv = [r["interval_ms"] for r in rows if r["interval_ms"] is not None]
    ea = [r["effective_available_bytes"] for r in rows]
    hm = [r["available_terms"]["host_mem_available"] for r in rows]
    bash = [r["bash_usage_excluding_attempt_bytes"] for r in rows]
    out["idle_series"] = {
        "samples": len(rows), "configured_interval_ms": 50,
        "interval_ms": {"mean": round(statistics.mean(iv), 2), "p50": round(pct(iv, 0.5), 2), "p95": round(pct(iv, 0.95), 2),
                        "p99": round(pct(iv, 0.99), 2), "max": round(max(iv), 2)},
        "effective_available_bytes": {"min": min(ea), "max": max(ea), "range": max(ea) - min(ea)},
        "host_mem_available_bytes": {"min": min(hm), "max": max(hm), "range": max(hm) - min(hm)},
        "bash_usage_excluding_attempt_bytes": {"min": min(bash), "max": max(bash), "range": max(bash) - min(bash)},
        "attempt_peak_bytes_idle_stub": t["post"]["final_memory"]["cg_charged_peak_bytes"],
        "attempt_working_set_last": rows[-1]["memory"]["cg_working_set_bytes"],
        "pids_peak_after_exit": t["post"]["final_counters"].get("pids_peak"),
        "pids_current_during": max(r["counters"]["pids_current"] for r in rows),
        "resident_bytes_elsewhere": t["elsewhere"]["pre_release"]["resident_bytes_elsewhere"],
        "memsw_delta_bytes": t["post"].get("memsw_delta_bytes"),
        "member_rss_bytes_max": max(m["status"]["VmRSS"] for r in rows for m in r["members"].values() if m.get("status")),
    }
    # 2. release -> ready latency and graceful/hard termination latencies, 5 repetitions each
    ready, graceful, hard, ckill = [], [], [], []
    for _ in range(5):
        t, _rows = run({"mode": "sleep", "seconds": 0.2})
        tl = t["timeline_ns"]
        ready.append((tl["ready"] - tl["released_S2"]) / 1e6)
        t, _rows = run({"mode": "sleep", "seconds": 60}, {"per_slot_elapsed_cap_s": 1})
        graceful.append(next(s["t_ms"] for s in t["termination"]["steps"] if s["step"] == "graceful_wait"))
        t, _rows = run({"mode": "ignore_sigterm", "seconds": 60}, {"per_slot_elapsed_cap_s": 1, "graceful_wait_s": 1})
        steps = {s["step"]: s["t_ms"] for s in t["termination"]["steps"]}
        hard.append({"sigkill_to_unshare_exit_ms": round(steps["wait_unshare_exit"] - steps["sigkill_ns_init"], 1),
                     "sigkill_to_empty_ms": round(steps["wait_cgroup_empty"] - steps["sigkill_ns_init"], 1)})
        t, _rows = run({"mode": "ignore_sigterm", "seconds": 60}, {"per_slot_elapsed_cap_s": 1, "graceful_wait_s": 1},
                       {"faults": ["INJECTED_E_SIGKILL_VIA_FD"]})
        steps = {s["step"]: s["t_ms"] for s in t["termination"]["steps"]}
        ckill.append({"cgroup_kill_to_empty_ms": round(steps["wait_cgroup_empty"] - steps["cgroup_kill_written"], 1),
                      "reaped_ns_init_by_controller": bool((t["termination"]["reap"].get("ns_init") or {}).get("reaped_by_controller")),
                      "direct_status": t["termination"]["reap"]["direct"]["status"]})
    out["release_to_ready_ms"] = {"samples": [round(x, 1) for x in ready], "max": round(max(ready), 1)}
    out["sigterm_to_empty_ms_graceful"] = {"samples": graceful, "max": max(graceful)}
    out["hard_path"] = hard
    out["cgroup_kill_backstop"] = ckill
    # 3. unshare exit-status propagation
    prop = {}
    for code in (0, 7):
        t, _r = run({"mode": "exit", "code": code})
        prop["ns_init_exit_%d" % code] = (t["exit"].get("direct") or {}).get("status")
    out["unshare_status_propagation"] = prop
    p = os.path.join(HERE, "results", "measurements.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
