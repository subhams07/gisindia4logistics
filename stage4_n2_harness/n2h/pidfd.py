"""Verified pidfd members, signalling only through verified fds, exit observation, reaping.

Never: kill(pid), killpg, ps/pgrep/proc scans as signal targets, or any wait-for-any-child call.
"""
import os
import select
import signal
import time

from .errors import ContainmentStop


def read_starttime(fs, pid):
    """Field 22 of /proc/<pid>/stat (clock ticks since boot). Parse after the last ')'."""
    txt = fs.read("/proc/%d/stat" % pid)
    rest = txt[txt.rindex(")") + 2:].split()
    return int(rest[19])  # field 22 overall: rest begins at field 3 -> index 19


def read_state_ppid(fs, pid):
    txt = fs.read("/proc/%d/stat" % pid)
    rest = txt[txt.rindex(")") + 2:].split()
    return rest[0], int(rest[1])


def pidns_identity(pid):
    st = os.stat("/proc/%d/ns/pid" % pid)
    return (st.st_dev, st.st_ino)


def nspid_fields(fs, pid):
    for line in fs.read("/proc/%d/status" % pid).splitlines():
        if line.startswith("NSpid:"):
            return [int(x) for x in line.split()[1:]]
    raise ContainmentStop("nspid_missing", str(pid))


class Member:
    def __init__(self, pid, fd, starttime, pidns, role, first_seen):
        self.pid = pid
        self.fd = fd
        self.starttime = starttime
        self.pidns = pidns
        self.role = role
        self.first_seen = first_seen
        self.closed = False
        self.reap_result = None  # set once the exit has been collected (or found not to be ours)

    def close(self):
        if not self.closed:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.closed = True


def readable(fd):
    """A pidfd is readable once the process has exited (zombie or reaped)."""
    p = select.poll()
    p.register(fd, select.POLLIN)
    return bool(p.poll(0))


def wait_readable(fds, timeout_s):
    """Wait until ALL fds are readable or timeout. Returns True if all readable."""
    deadline = time.monotonic() + timeout_s
    while True:
        if all(readable(f) for f in fds):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def open_verified(pid, layout, fs, role, plan=None, expect_starttime=None, cg_check=True):
    """Section 3.1: pidfd_open; cgroup shows attempt paths; starttime stable; signal(0) works."""
    from . import cg as cgmod
    try:
        st0 = read_starttime(fs, pid)
    except (OSError, ValueError) as e:
        raise ContainmentStop("member_vanished_before_open", "%d: %s" % (pid, e))
    if expect_starttime is not None and st0 != expect_starttime:
        raise ContainmentStop("member_starttime_changed", str(pid))
    try:
        if plan is not None:
            plan.check("INJECTED_E_PIDFD_OPEN")
        fd = os.pidfd_open(pid)
    except OSError as e:
        raise ContainmentStop("pidfd_open_failed", "%d: %s" % (pid, e), injected=getattr(e, "label", None))
    try:
        if read_starttime(fs, pid) != st0:
            raise ContainmentStop("member_starttime_changed", str(pid))
        if cg_check and not cgmod.proc_cgroup_matches(layout, pid, fs):
            raise ContainmentStop("member_outside_attempt_cgroup", str(pid))
        signal.pidfd_send_signal(fd, 0)
        pns = pidns_identity(pid)
    except ContainmentStop:
        os.close(fd)
        raise
    except (OSError, ValueError) as e:
        os.close(fd)
        raise ContainmentStop("member_verification_failed", "%d: %s" % (pid, e))
    return Member(pid, fd, st0, pns, role, time.monotonic_ns())


def send(member, sig, ns_init=None):
    """Signal ONLY through the member's verified fd.

    While ns-init is alive, a non-ns-init in-namespace member additionally needs its pid-ns
    (st_dev, st_ino) to equal ns-init's, and the fd is re-confirmed with signal 0 immediately
    before the real signal (note 5). Role 'direct' (unshare) lives in the controller's ns.
    """
    if member.closed:
        raise ContainmentStop("signal_closed_member", str(member.pid))
    if (member.role not in ("direct", "ns_init") and ns_init is not None and not readable(ns_init.fd)
            and member.pidns != ns_init.pidns):
        raise ContainmentStop("member_pidns_mismatch_not_signalled", str(member.pid))
    if readable(member.fd):
        return False  # already exited; nothing to signal
    signal.pidfd_send_signal(member.fd, 0)
    signal.pidfd_send_signal(member.fd, sig)
    return True


def observe_exit(fd):
    """Observe exit without reaping (waitid WNOWAIT). Raises ChildProcessError for non-children."""
    return os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOWAIT)


def reap(pid):
    """Reap exactly this pid (a still-unreaped zombie pins the pid). Returns (status, rusage)."""
    _p, status, ru = os.wait4(pid, 0)
    return status, ru


def reap_if_exited(member):
    """If the member has exited and not been collected: observe (WNOWAIT) then wait4 exactly its pid.

    A zombie child stays in cgroup.procs and keeps pids.current > 0 until reaped, so the
    controller must collect owned children before it can see an empty cgroup.
    Returns the (cached) result dict, or None if the member is still alive.
    """
    if member.reap_result is not None:
        return member.reap_result
    if not readable(member.fd):
        return None
    try:
        observe_exit(member.fd)
    except ChildProcessError:
        member.reap_result = {"pid": member.pid, "status": None, "reaped_by_controller": False,
                              "note": "not a child of the controller (normally reaped by its parent, "
                                      "e.g. unshare reaping ns-init); direct status unavailable"}
        return member.reap_result
    status, ru = reap(member.pid)
    member.reap_result = {"pid": member.pid, "status": decode_status(status), "ru_maxrss_kb": ru.ru_maxrss,
                          "reaped_by_controller": True}
    return member.reap_result


def decode_status(status):
    if os.WIFEXITED(status):
        return {"kind": "exit", "code": os.WEXITSTATUS(status), "raw": status}
    if os.WIFSIGNALED(status):
        return {"kind": "signal", "signal": os.WTERMSIG(status), "raw": status}
    return {"kind": "other", "raw": status}


def children_of(fs, pid):
    """Direct children of every thread of pid (detection only, never a signal target)."""
    out = []
    try:
        tids = fs.listdir("/proc/%d/task" % pid)
    except OSError:
        return out
    for tid in tids:
        try:
            txt = fs.read("/proc/%d/task/%s/children" % (pid, tid))
        except OSError:
            continue
        out.extend(int(x) for x in txt.split())
    return out


def descendants(fs, root_pid, limit=4096):
    seen = set()
    stack = [root_pid]
    while stack:
        p = stack.pop()
        for c in children_of(fs, p):
            if c not in seen:
                seen.add(c)
                stack.append(c)
                if len(seen) > limit:
                    raise ContainmentStop("descendant_walk_overflow", "")
    return seen


def thread_ids(fs, pid):
    return [int(t) for t in fs.listdir("/proc/%d/task" % pid)]



PF_EXITING = 0x4


def pid_is_exiting(fs, pid):
    """True if the pid is gone or a zombie/dead task. An exiting task is moved to the root css set by
    the kernel before it is reaped, so its /proc/<pid>/cgroup no longer shows the attempt paths:
    that must not be read as an escape."""
    try:
        txt = fs.read("/proc/%d/stat" % pid)
        rest = txt[txt.rindex(")") + 2:].split()
        state, flags = rest[0], int(rest[6])  # field 3 = state, field 9 = task flags
    except (OSError, ValueError, IndexError):
        return True
    return state in ("Z", "X") or bool(flags & PF_EXITING)
