"""Termination (section 3, D-3 option a+): freeze-free verified-fd escalation plus PID-namespace
teardown; v2 cgroup.kill is an optional probed backstop, used only while ns-init is still alive.

Signals are sent ONLY through verified pidfds (pidfd.send) or by writing cgroup.kill.
A member left after ns-init has exited and the direct child has been reaped is NOT owned:
it is reported and the run STOPs; it is never killed.
"""
import os
import signal
import time

from . import cg as cgmod
from . import pidfd as pidfd_mod
from . import policy as policy_mod
from .errors import InjectedFault, N2Stop


def _all_fds(ctx):
    return [m.fd for m in ctx.members.values() if not m.closed]


def _reap_ready(ctx):
    for m in list(ctx.members.values()):
        if not m.closed:
            pidfd_mod.reap_if_exited(m)


def _empty(layout, fs, errs):
    """is_empty that treats a read/parse error as 'not empty' (keep escalating) and records the error once."""
    try:
        return cgmod.is_empty(layout, fs)
    except (N2Stop, OSError, ValueError) as e:
        msg = "%s: %s" % (type(e).__name__, str(e)[:100])
        if msg not in errs:
            errs.append(msg)
        return False


def _wait_all_exit_and_empty(ctx, wait_s, fs, layout, errs):
    deadline = time.monotonic() + wait_s
    while True:
        _reap_ready(ctx)
        if all(pidfd_mod.readable(f) for f in _all_fds(ctx)) and _empty(layout, fs, errs):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def _wait_cg_empty(ctx, layout, fs, wait_s, errs):
    deadline = time.monotonic() + wait_s
    while True:
        _reap_ready(ctx)
        if _empty(layout, fs, errs):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def _observe_zombie(ctx, member, until_s, fs, out):
    """Record (state, ppid) of a member while it is a zombie, to learn who reaps an orphan."""
    deadline = time.monotonic() + until_s
    seen = []
    while time.monotonic() < deadline:
        try:
            st, ppid = pidfd_mod.read_state_ppid(fs, member.pid)
        except (OSError, ValueError):
            break
        seen.append((st, ppid))
        if len(seen) >= 3 or (st == "Z" and len(seen) > 1):
            break
        time.sleep(0.005)
    out.append({"pid": member.pid, "role": member.role, "observations": seen[:4]})


def terminate(ctx, layout, fs, policy, plan, reason, discover=None):
    """Run the escalation. Returns a result dict; never raises on a kill-path problem."""
    pv = policy_mod.value
    res = {"reason": reason, "steps": [], "unowned": [], "zombie_observations": [],
           "used_cgroup_kill": False, "injected": [], "read_errors": []}
    t0 = time.monotonic()

    def step(name, **kw):
        res["steps"].append({"step": name, "t_ms": round((time.monotonic() - t0) * 1000, 1), **kw})

    direct, nsi = ctx.direct, ctx.ns_init

    def refresh():
        if discover is not None:
            try:
                discover()
            except (N2Stop, OSError, ValueError) as e:  # discovery during teardown must not abort the escalation
                step("discover_failed", err=type(e).__name__)

    refresh()
    nsi = ctx.ns_init
    # --- graceful: SIGTERM to ns-init (PID 1 only honours it because the stub installed a handler)
    if nsi is not None and ctx.alive(nsi):
        try:
            pidfd_mod.send(nsi, signal.SIGTERM, ns_init=nsi)
            step("sigterm_ns_init")
        except (OSError, InjectedFault) as e:
            step("sigterm_failed", err=str(e))
        ok = _wait_all_exit_and_empty(ctx, pv(policy, "graceful_wait_s"), fs, layout, res["read_errors"])
        step("graceful_wait", emptied=ok)
        res["graceful"] = ok
        if ok:
            _finish(ctx, layout, fs, res, step)
            return res
    else:
        res["graceful"] = None
    # --- hard (option a): SIGKILL ns-init, then bounded wait for unshare to reap and exit
    sent = False
    if nsi is not None and ctx.alive(nsi):
        try:
            plan.check("INJECTED_E_SIGKILL_VIA_FD")
            pidfd_mod.send(nsi, signal.SIGKILL, ns_init=nsi)
            sent = True
            step("sigkill_ns_init")
        except InjectedFault as e:
            res["injected"].append(e.label)
            step("sigkill_ns_init_fault", label=e.label)
        except OSError as e:
            step("sigkill_ns_init_failed", err=str(e))
        if sent:
            _observe_zombie(ctx, nsi, 0.05, fs, res["zombie_observations"])
    if sent or nsi is None:
        if nsi is None and ctx.alive(direct):
            # ns-init never seen: the only member is the direct child (pre-release or unshare stage)
            try:
                pidfd_mod.send(direct, signal.SIGKILL)
                step("sigkill_direct_no_ns_init")
            except OSError as e:
                step("sigkill_direct_failed", err=str(e))
        else:
            ok = pidfd_mod.wait_readable([direct.fd], pv(policy, "unshare_reap_wait_s"))
            step("wait_unshare_exit", exited=ok)
            if not ok:
                try:
                    pidfd_mod.send(direct, signal.SIGKILL)
                    step("sigkill_direct")
                except OSError as e:
                    step("sigkill_direct_failed", err=str(e))
    # --- backstop (option a+): v2 cgroup.kill, ONLY while ns-init is still alive (see module doc)
    if nsi is not None and ctx.alive(nsi) and layout.use_v2:
        strangers = unowned_in_cgroup(ctx, layout, fs)
        if strangers:
            # cgroup.kill would kill them too: an unowned member is reported, never killed
            res["unowned"] = strangers
            step("cgroup_kill_skipped_unowned_present", pids=strangers)
        else:
            try:
                cgmod.v2_kill(layout, fs)
                res["used_cgroup_kill"] = True
                step("cgroup_kill_written")
            except OSError as e:
                step("cgroup_kill_failed", err=str(e))
    emptied = _wait_cg_empty(ctx, layout, fs, pv(policy, "cgroup_kill_wait_s"), res["read_errors"])
    step("wait_cgroup_empty", emptied=emptied)
    _finish(ctx, layout, fs, res, step)
    return res


def unowned_in_cgroup(ctx, layout, fs):
    """Listed processes that are not the direct child, ns-init or reachable from them by parentage."""
    try:
        listing = cgmod.members(layout, fs)
    except (N2Stop, OSError, ValueError):
        return []
    known = {ctx.direct.pid}
    if ctx.ns_init is not None:
        known.add(ctx.ns_init.pid)
        known |= pidfd_mod.descendants(fs, ctx.ns_init.pid)
    ok = known | {os.getpid()}
    pending = [p for p in listing if p not in known]
    progress = True
    while pending and progress:
        progress = False
        for pid in list(pending):
            try:
                _st, ppid = pidfd_mod.read_state_ppid(fs, pid)
            except (OSError, ValueError, IndexError):
                pending.remove(pid)
                progress = True
                continue
            if ppid in ok:
                ok.add(pid)
                pending.remove(pid)
                progress = True
    return [p for p in pending if not pidfd_mod.pid_is_exiting(fs, p)]


def reap_adopted_zombies(layout, fs, res):
    """Reap zombies in the attempt cgroup whose kernel parent is THIS controller (adopted through
    PR_SET_CHILD_SUBREAPER) when no pidfd was ever opened for them. Exact pid only; a zombie pins
    its pid so there is no reuse hazard; never wait(-1)."""
    done = []
    try:
        listing = cgmod.members(layout, fs)
    except Exception:  # noqa: BLE001 - listing failure is reported by the caller's own listing
        return done
    for pid in listing:
        try:
            st, ppid = pidfd_mod.read_state_ppid(fs, pid)
        except (OSError, ValueError):
            continue
        if st == "Z" and ppid == os.getpid():
            try:
                os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
                _p, status, _ru = os.wait4(pid, 0)
                done.append({"pid": pid, "status": pidfd_mod.decode_status(status)})
            except ChildProcessError:
                pass
    if done:
        res["reaped_adopted_zombies_by_pid"] = done
    return done


def _finish(ctx, layout, fs, res, step):
    """After the escalation: reap what is ours, report anything left as unowned (never kill)."""
    direct, nsi = ctx.direct, ctx.ns_init
    res.setdefault("unowned", [])
    res["reap"] = {}
    for m in (nsi, direct):
        if m is None or m.closed:
            continue
        r = pidfd_mod.reap_if_exited(m)
        if r is not None:
            res["reap"][m.role] = r
    reap_adopted_zombies(layout, fs, res)
    try:
        left = cgmod.members(layout, fs)
    except (N2Stop, OSError, ValueError) as e:
        left = None
        step("final_listing_failed", err=type(e).__name__)
    ns_gone = nsi is None or pidfd_mod.readable(nsi.fd)
    direct_gone = pidfd_mod.readable(direct.fd)
    if left:
        if ns_gone and direct_gone:
            res["unowned"] = list(left)
            step("unowned_members_reported_not_killed", pids=list(left))
        else:
            step("members_remain_while_owners_alive", pids=list(left))
    try:
        res["pids_current"] = cgmod.counters(layout, fs)["pids_current"]
    except (N2Stop, OSError, ValueError):
        res["pids_current"] = None
    res["emptied"] = bool(not left) and res.get("pids_current") == 0
    zombies = []
    for pid in (left or []):
        try:
            st, pp = pidfd_mod.read_state_ppid(fs, pid)
            if st == "Z":
                zombies.append({"pid": pid, "ppid": pp})
        except (OSError, ValueError):
            pass
    res["zombies"] = zombies
