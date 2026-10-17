"""Fork-gated launch (the Linux CREATE_SUSPENDED analogue). Section 1 / L-3 / L-4.

Controller side runs stdlib-only, single-threaded. The forked child runs ONLY syscalls:
no imports, no threads, everything inside try/except BaseException -> os._exit(E_INTERNAL).
"""
import fcntl
import gc
import hashlib
import json
import os
import signal
import stat
import sys
import threading

from . import native, stubsrc
from . import pidfd as pidfd_mod
from .errors import FaultPlan, LaunchStop

# Pre-exec child exit codes (L-4). Distinct from stub codes (81..95).
E_INTERNAL = 70
E_PARENT = 71
E_FD = 72
E_EXEC = 73
E_GATE = 74
E_PRCTL = 75
E_CG = 76
CHILD_CODES = {"E_INTERNAL": E_INTERNAL, "E_PARENT": E_PARENT, "E_FD": E_FD, "E_EXEC": E_EXEC,
               "E_GATE": E_GATE, "E_PRCTL": E_PRCTL, "E_CG": E_CG}

ENV_THREAD_VARS = {  # D-1: pinned to one thread
    "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "GDAL_NUM_THREADS": "1",
}


def build_env(home, tmpdir):
    """Explicit environment allowlist. Nothing is inherited; no proxy/credential/PYTHON* except below."""
    env = {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TZ": "UTC",
        "HOME": home, "TMPDIR": tmpdir, "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(ENV_THREAD_VARS)
    return env


def sha256_fd(fd):
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        b = os.read(fd, 1 << 20)
        if not b:
            break
        h.update(b)
    os.lseek(fd, 0, os.SEEK_SET)
    return h.hexdigest()


def fd_ident(fd):
    st = os.fstat(fd)
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def verify_binary(path):
    """Identity + SHA-256 of the file a path RESOLVES to (symlinks followed, as exec would)."""
    real = os.path.realpath(path)
    fd = os.open(real, os.O_RDONLY | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise LaunchStop("binary_not_regular", path)
        return {"path": path, "real": real, "ident": fd_ident(fd), "sha": sha256_fd(fd)}
    finally:
        os.close(fd)


def check_fork_preconditions(allow, extra_allow=()):
    """L-3. Raises LaunchStop unless safe to fork: one thread, only allowlisted modules,
    SIGCHLD not SIG_IGN, no unreaped children."""
    if threading.current_thread() is not threading.main_thread():
        raise LaunchStop("fork_not_from_main_thread", "")
    if threading.active_count() != 1:
        raise LaunchStop("fork_multiple_threads", str(threading.active_count()))
    tasks = os.listdir("/proc/self/task")
    if len(tasks) != 1:
        raise LaunchStop("fork_multiple_tasks", str(len(tasks)))
    if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
        raise LaunchStop("fork_sigchld_ignored", "")
    ok = set(allow) | set(extra_allow)
    bad = sorted(m for m in sys.modules if m not in ok)
    if bad:
        raise LaunchStop("fork_module_outside_allowlist", ",".join(bad[:12]))
    for t in tasks:
        try:
            with open("/proc/self/task/%s/children" % t) as f:
                kids = f.read().strip()
        except OSError:
            raise LaunchStop("fork_children_unreadable", "")
        if kids:
            raise LaunchStop("fork_unreaped_children", kids[:60])
    return True


def fd_snapshot():
    """Open fd set via an explicitly opened /proc/self/fd dir fd (reviewer fix R3-B1).

    CPython's os.listdir(fd) duplicates the fd, so the raw listing contains one transient
    number that is EBADF once listing returns. Each listed number is probed with F_GETFD and
    those now EBADF are discarded. Returns (alive_set_without_dir_fd, discarded_count, raw_listing).
    """
    d = os.open("/proc/self/fd", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    listed = [int(x) for x in os.listdir(d)]
    alive = set()
    discarded = 0
    for n in listed:
        try:
            fcntl.fcntl(n, fcntl.F_GETFD)
            alive.add(n)
        except OSError:
            discarded += 1
    alive.discard(d)
    os.close(d)
    return alive, discarded, listed


def fd_assert_ok(alive, discarded, keep):
    """Exactly one discarded number (the listdir duplicate) and exactly the keep set."""
    return discarded == 1 and alive == set(keep)


class Gated:
    """Controller-side handle of a forked, gated, never-exec'd child (state S0)."""

    def __init__(self, pid, pidfd, starttime, gate_w, out_path, err_path, snapshot, stub_sha, argv_sha):
        self.pid = pid
        self.pidfd = pidfd
        self.starttime = starttime
        self.gate_w = gate_w
        self.out_path = out_path
        self.err_path = err_path
        self.snapshot = snapshot
        self.stub_sha = stub_sha
        self.argv_sha = argv_sha
        self.released = False
        self.reaped = False

    def release(self):
        if self.released:
            return
        os.write(self.gate_w, b"G")
        os.close(self.gate_w)
        self.gate_w = -1
        self.released = True

    def close_gate_without_release(self):
        if self.gate_w >= 0:
            os.close(self.gate_w)
            self.gate_w = -1


def _child_main(gate_r, gate_w, exec_fd, out_fd, err_fd, null_fd, expect_exec, ctrl_pid, argv, env,
                expected_cgroups, prctl, inject):
    """Runs in the forked child. No imports. Never returns."""
    try:
        # step 1: parent-death signal (survives exec of non-setuid unshare); default handlers
        if inject.get("raise_at_step") == 1:
            raise RuntimeError("INJECTED_E_CHILD_STEP")
        if not inject.get("skip_pdeathsig") and prctl(native.PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0) != 0:
            os._exit(E_PRCTL)
        for s in range(1, signal.NSIG):
            if s in (int(signal.SIGKILL), int(signal.SIGSTOP)):
                continue
            try:
                signal.signal(s, signal.SIG_DFL)
            except (OSError, ValueError, RuntimeError):
                pass
        # step 2
        if os.getppid() != ctrl_pid:
            os._exit(E_PARENT)
        # step 3: fds 0-2, close everything else except gate_r and exec_fd (also closes gate_w)
        if inject.get("raise_at_step") == 3:
            raise RuntimeError("INJECTED_E_CHILD_STEP")
        os.dup2(null_fd, 0)
        os.dup2(out_fd, 1)
        os.dup2(err_fd, 2)
        if inject.get("report_gc"):
            os.write(2, b"N2CHILD gc_enabled=" + (b"1" if gc.isenabled() else b"0") + b"\n")
        keep = sorted(k for k in (gate_r, exec_fd) if k > 2)
        lo = 3
        for k in keep:
            os.closerange(lo, k)
            lo = k + 1
        os.closerange(lo, 1 << 20)
        # step 4: fd-set assertion
        try:
            fcntl.fcntl(gate_w, fcntl.F_GETFD)
            os._exit(E_FD)  # gate_w number still open
        except OSError:
            pass
        if inject.get("extra_fd"):
            os.open("/dev/null", os.O_RDONLY)
        alive, discarded, _raw = fd_snapshot()
        if not fd_assert_ok(alive, discarded, (0, 1, 2, gate_r, exec_fd)):
            os._exit(E_FD)
        if inject.get("swap_exec_fd_to"):
            other = os.open(inject["swap_exec_fd_to"], os.O_RDONLY)
            os.dup2(other, exec_fd, inheritable=False)
            os.close(other)
        # step 5: exec fd identity re-check
        st = os.fstat(exec_fd)
        if [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns] != expect_exec:
            os._exit(E_EXEC)
        # step 6: gate
        if inject.get("raise_at_step") == 6:
            raise RuntimeError("INJECTED_E_CHILD_STEP")
        b = os.read(gate_r, 1)
        if b != b"G":
            os._exit(E_GATE)
        # step 7: cgroup re-check; make this process the preferred OOM victim
        cur = {}
        fh = os.open("/proc/self/cgroup", os.O_RDONLY)
        txt = os.read(fh, 1 << 16).decode("ascii")
        os.close(fh)
        for line in txt.splitlines():
            h, c, p = line.split(":", 2)
            names = c.split(",") if c else []
            if h == "0" and not c:
                cur["v2"] = p
            if "memory" in names:
                cur["memory"] = p
            if "pids" in names:
                cur["pids"] = p
        for k, v in expected_cgroups.items():
            if cur.get(k) != v:
                os._exit(E_CG)
        oh = os.open("/proc/self/oom_score_adj", os.O_WRONLY)
        os.write(oh, b"1000")
        os.close(oh)
        # step 8
        if inject.get("drop_sysadmin"):
            prctl(native.PR_CAPBSET_DROP, native.CAP_SYS_ADMIN, 0, 0, 0)
        os.execve(exec_fd, argv, env)
    except BaseException:  # noqa: BLE001 - child: ANY failure must end in os._exit, never return into controller code
        os._exit(E_INTERNAL)
    os._exit(E_INTERNAL)


def fork_gated(spec, plan=None, allow=(), extra_allow=(), inject=None, hooks=None):
    """Fork the gated child. spec keys: unshare, interpreter, stub, expected (dict minus stub_sha),
    env, out_path, err_path, cgroups, ctrl_pid.

    Returns Gated. Raises LaunchStop (before any fork) on any precondition/hash failure.
    """
    plan = plan or FaultPlan()
    inject = dict(inject or {})
    hooks = hooks or {}
    prctl = native.prctl_fn()  # resolved before fork
    unshare = spec["unshare"]
    exec_fd = os.open(os.path.realpath(unshare["path"]), os.O_RDONLY | os.O_CLOEXEC)
    gate_r = gate_w = out_fd = err_fd = null_fd = -1
    forked = False
    try:
        if fd_ident(exec_fd) != unshare["ident"] or sha256_fd(exec_fd) != unshare["sha"]:
            raise LaunchStop("exec_fd_hash_mismatch", unshare["path"])
        expect_exec = fd_ident(exec_fd)
        stub = spec["stub"]
        stub_sha = hashlib.sha256(stub.encode("ascii")).hexdigest()
        exp = dict(spec["expected"])
        exp["stub_sha"] = stub_sha
        exp["cmdline_stub_index"] = stubsrc.CMDLINE_STUB_INDEX
        expected_json = json.dumps(exp, sort_keys=True, separators=(",", ":"))
        interp_path = spec["interpreter"]["path"]
        argv = ["unshare", "--pid", "--fork"]
        if not inject.get("no_kill_child"):
            argv.append("--kill-child=SIGKILL")
        stub_in_argv = spec.get("stub_actual", stub)  # injection point: argv text differing from the hashed text
        argv += ["--mount", "--", interp_path, "-I", "-S", "-B", "-u", "-c", stub_in_argv, expected_json]
        ip = argv.index(interp_path)
        if argv[ip + stubsrc.CMDLINE_STUB_INDEX] != stub_in_argv or argv[ip + stubsrc.CMDLINE_STUB_INDEX + 1] != expected_json:
            raise LaunchStop("argv_layout", "stub is not at the fixed cmdline index")
        if hooks.get("after_verify"):
            hooks["after_verify"]()
        gate_r, gate_w = os.pipe2(os.O_CLOEXEC)
        out_fd = os.open(spec["out_path"], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        err_fd = os.open(spec["err_path"], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        null_fd = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
        for name, fd in (("gate_r", gate_r), ("gate_w", gate_w), ("exec_fd", exec_fd),
                         ("out", out_fd), ("err", err_fd), ("null", null_fd)):
            if fd <= 2:
                raise LaunchStop("low_fd_number", "%s=%d (fds 0-2 must be preserved)" % (name, fd))
        plan.check("INJECTED_E_FORK_PRECHECK")
        check_fork_preconditions(allow, extra_allow)
        sys.stdout.flush()
        sys.stderr.flush()
        ctrl_pid = os.getpid()
        gc.disable()
        try:
            pid = os.fork()
            if pid == 0:
                _child_main(gate_r, gate_w, exec_fd, out_fd, err_fd, null_fd, expect_exec, ctrl_pid,
                            argv, spec["env"], spec["cgroups"], prctl, inject)
                os._exit(E_INTERNAL)
        finally:
            gc.enable()
        forked = True
    except BaseException:
        for fd in (gate_r, gate_w, out_fd, err_fd, null_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        os.close(exec_fd)
        raise
    # parent
    os.close(gate_r)
    os.close(exec_fd)
    os.close(out_fd)
    os.close(err_fd)
    os.close(null_fd)
    if not forked:
        raise LaunchStop("fork_state_inconsistent", "")
    try:
        plan.check("INJECTED_E_PIDFD_OPEN_S0")
        pfd = os.pidfd_open(pid)
    except OSError as e:
        # cannot signal safely without a pidfd: EOF the gate (child exits E_GATE, never execs), reap exactly pid
        os.close(gate_w)
        try:
            os.wait4(pid, 0)
        except ChildProcessError:
            pass
        raise LaunchStop("pidfd_open_failed_s0", "child pid %d gate closed, reaped by exact pid: %s" % (pid, e),
                         injected=getattr(e, "label", None))
    fs = _Fs()
    starttime = pidfd_mod.read_starttime(fs, pid)
    snapshot = {}
    try:
        snapshot["exe"] = os.readlink("/proc/%d/exe" % pid)
        with open("/proc/%d/cmdline" % pid, "rb") as f1, open("/proc/self/cmdline", "rb") as f2:
            snapshot["cmdline_equals_controller"] = f1.read() == f2.read()
        snapshot["state"] = pidfd_mod.read_state_ppid(fs, pid)[0]
    except OSError as e:
        snapshot["error"] = str(e)
    return Gated(pid, pfd, starttime, gate_w, spec["out_path"], spec["err_path"], snapshot, stub_sha,
                 hashlib.sha256(json.dumps(argv).encode("ascii")).hexdigest())


class _Fs:
    @staticmethod
    def read(path):
        with open(path) as f:
            return f.read()
