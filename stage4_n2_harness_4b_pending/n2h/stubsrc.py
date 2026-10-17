"""Child stub and synthetic workload sources. ASCII only, < 128 KiB (MAX_ARG_STRLEN).

The stub is passed to the interpreter as `-c <source>` and its SHA-256 is bound in the
attempt record. NOTE (reviewer note 1): the stub re-hashing its own source argument proves
argv integrity only; the TOCTOU at the unshare->python hop is closed by (a) the controller
building argv from the exact hash-verified bytes and exec'ing a hash-verified unshare, and
(b) the stub verifying /proc/self/exe identity and hash. The source is read from the FIXED
/proc/self/cmdline index CMDLINE_STUB_INDEX. In -c mode sys.argv is ['-c', EXPECTED_JSON],
so sys.argv does NOT hold the source. No secrets may ever be placed in argv (world-readable).
The nonce is framing for marker lines, not a security boundary.
"""

CMDLINE_STUB_INDEX = 6  # [python, -I, -S, -B, -u, -c, <stub>, <expected-json>]

# Stub exit codes (fixed, distinct from the pre-exec child codes in launch.py).
S_CODES = {
    "STUBHASH": 81, "EXE": 82, "CG": 83, "NSPID": 84, "PDEATHSIG": 85, "FLAGS": 86,
    "ENV": 87, "FD": 88, "MNTNS": 89, "REMOUNT": 90, "SETPRIV": 91, "CAPS": 92,
    "RO": 93, "PAYLOAD": 94, "INTERNAL": 95, "UID": 96,
}

STUB = r'''
import sys, os, json, hashlib, time, signal, ctypes, fcntl
E = json.loads(sys.argv[1])
NONCE = E["nonce"]
C = E["codes"]

def mark(ev, **kw):
    parts = ["N2M", NONCE, str(time.monotonic_ns()), ev]
    parts += ["%s=%s" % (k, str(kw[k]).replace(" ", "_").replace("\n", "_")[:200]) for k in sorted(kw)]
    try:
        os.write(2, (" ".join(parts) + "\n").encode("ascii", "replace"))
    except OSError:
        pass

def refuse(name, why):
    mark("refuse", code=C[name], name=name, why=why)
    os._exit(C[name])

def sha_fd(fd):
    h = hashlib.sha256()
    while True:
        b = os.read(fd, 1 << 20)
        if not b:
            break
        h.update(b)
    return h.hexdigest()

def ident(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

try:
    # 1. own source argument (argv integrity only)
    fd = os.open("/proc/self/cmdline", os.O_RDONLY)
    cl = b""
    while True:
        b = os.read(fd, 1 << 16)
        if not b:
            break
        cl += b
    os.close(fd)
    parts = cl.split(b"\0")
    idx = E["cmdline_stub_index"]
    if len(parts) < idx + 2 or hashlib.sha256(parts[idx]).hexdigest() != E["stub_sha"]:
        refuse("STUBHASH", "source argument hash differs")
    SRC = parts[idx]
    # 2. the interpreter that is actually executing
    st = os.stat("/proc/self/exe")
    want = E["interp"]
    if ident(st) != want["ident"]:
        refuse("EXE", "identity differs")
    fd = os.open("/proc/self/exe", os.O_RDONLY)
    got = sha_fd(fd)
    os.close(fd)
    if got != want["sha"]:
        refuse("EXE", "sha256 differs")
    # 3. cgroups
    cur = {}
    for line in open("/proc/self/cgroup").read().splitlines():
        h, c, p = line.split(":", 2)
        names = c.split(",") if c else []
        if h == "0" and not c:
            cur["v2"] = p
        if "memory" in names:
            cur["memory"] = p
        if "pids" in names:
            cur["pids"] = p
    for k, v in E["cgroups"].items():
        if cur.get(k) != v:
            refuse("CG", "%s=%s want %s" % (k, cur.get(k), v))
    # 4. pid namespace
    nsp = None
    for line in open("/proc/self/status").read().splitlines():
        if line.startswith("NSpid:"):
            nsp = [int(x) for x in line.split()[1:]]
    if nsp is None or len(nsp) != E["ctrl_nspid_fields"] + 1:
        refuse("NSPID", "field count %r" % (nsp,))
    if nsp[-1] != E["nspid_last"]:
        refuse("NSPID", "last field %r" % (nsp,))
    # 5. parent death signal (set by unshare --kill-child)
    libc = ctypes.CDLL(None, use_errno=True)
    v = ctypes.c_int(-1)
    libc.prctl(2, ctypes.byref(v), 0, 0, 0)
    if v.value != int(signal.SIGKILL):
        refuse("PDEATHSIG", "pdeathsig=%d" % v.value)
    # 6. flags
    f = sys.flags
    if not (f.isolated == 1 and f.no_site == 1 and f.dont_write_bytecode == 1):
        refuse("FLAGS", "isolated/no_site/dont_write_bytecode")
    # 7. environment equals the allowlist exactly
    if dict(os.environ) != E["env"]:
        extra = sorted(set(os.environ) ^ set(E["env"]))
        refuse("ENV", "differs in keys %s" % ",".join(extra)[:80])
    # 8. only fds 0-2 (the /proc/self/fd directory fd and the listdir duplicate are excluded)
    d = os.open("/proc/self/fd", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    lst = [int(x) for x in os.listdir(d)]
    openfds = set()
    for n in lst:
        try:
            fcntl.fcntl(n, fcntl.F_GETFD)
            openfds.add(n)
        except OSError:
            pass
    os.close(d)
    openfds.discard(d)
    if openfds != {0, 1, 2}:
        refuse("FD", "open fds %r" % sorted(openfds))
    stage = E["stage"]
    # 9. stage 0 only: read-only cgroup mounts in a PRIVATE mount namespace, then cap drop
    if stage == 0:
        mnt = os.stat("/proc/self/ns/mnt")
        if [mnt.st_dev, mnt.st_ino] == E["ctrl_mntns"]:
            refuse("MNTNS", "same mount namespace as controller; refusing to remount")
        libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
        libc.mount.restype = ctypes.c_int
        mps = []
        for line in open("/proc/self/mountinfo").read().splitlines():
            left, _sep, right = line.partition(" - ")
            if right.split()[0] in ("cgroup", "cgroup2"):
                mps.append(left.split()[4])
        for mp in mps:
            r = libc.mount(None, mp.encode(), None, 1 | 32 | 4096, None)  # RDONLY|REMOUNT|BIND
            if r != 0:
                refuse("REMOUNT", "%s errno=%d" % (mp, ctypes.get_errno()))
        mark("remounted_ro", n=len(mps))
        sp = E["setpriv"]
        sfd = os.open(sp["path"], os.O_RDONLY | os.O_CLOEXEC)
        if ident(os.fstat(sfd)) != sp["ident"] or sha_fd(sfd) != sp["sha"]:
            refuse("SETPRIV", "identity or hash differs")
        E2 = dict(E)
        E2["stage"] = 1
        arg = json.dumps(E2, sort_keys=True, separators=(",", ":"))
        pre = [sp["path"]]
        if E.get("uid") is not None:  # operator item (c): the payload runs as an unprivileged numeric uid/gid
            pre += ["--reuid=%d" % E["uid"], "--regid=%d" % E["gid"], "--clear-groups", "--pdeathsig=keep"]
        argv = pre + ["--bounding-set=-all", "--inh-caps=-all", "--no-new-privs", "--",
                E["interp"]["path"], "-I", "-S", "-B", "-u", "-c", SRC.decode("ascii"), arg]
        mark("exec_setpriv")
        os.execve(sfd, argv, dict(os.environ))
        os._exit(C["INTERNAL"])
    # 10. stage 1: capabilities gone, no_new_privs, cgroupfs read-only
    caps = {}
    for line in open("/proc/self/status").read().splitlines():
        for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb", "NoNewPrivs"):
            if line.startswith(key + ":"):
                caps[key] = line.split()[1]
    for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        if int(caps.get(key, "1"), 16) != 0:
            refuse("CAPS", "%s=%s" % (key, caps.get(key)))
    if caps.get("NoNewPrivs") != "1":
        refuse("CAPS", "no_new_privs not set")
    try:
        wfd = os.open(E["cgroup_probe_path"], os.O_WRONLY)
        os.close(wfd)
        refuse("RO", "cgroup.procs opened for write")
    except OSError as e:
        ro_errno = e.errno
    if E.get("uid") is None:
        if ro_errno != 30:  # EROFS specifically; EACCES/EPERM/ENOENT would not prove a read-only mount
            refuse("RO", "expected EROFS got errno %d" % ro_errno)
    else:
        # an unprivileged opener meets the DAC check (EACCES) before the read-only check, so EROFS cannot be
        # observed; the read-only mount is proven from the mount flags instead
        if ro_errno != 13 or not (os.statvfs(E["cgroup_probe_path"]).f_flag & os.ST_RDONLY):
            refuse("RO", "uid mode: errno %d or mount not read-only" % ro_errno)
    if E.get("uid") is not None:
        ids = (os.getresuid(), os.getresgid())
        if ids != ((E["uid"],) * 3, (E["gid"],) * 3) or os.getgroups() != []:
            refuse("UID", "ids %r groups %r" % (ids, os.getgroups()))
    elif os.getuid() != 0:
        refuse("UID", "unexpected non-root without a configured payload uid")
    mark("selfcheck_ok", stage=stage, pid=os.getpid(), ro_errno=ro_errno, uid=os.getuid())
    # 11. SIGTERM handler (PID 1 ignores signals it has no handler for), then optional site
    def _term(sig, frm):
        mark("sigterm")
        mark("exiting", code=0)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _term)
    if E.get("site_main"):
        import site
        site.main()
    pl = E["payload"]
    pfd = os.open(pl["path"], os.O_RDONLY | os.O_CLOEXEC)
    psrc = b""
    while True:
        b = os.read(pfd, 1 << 20)
        if not b:
            break
        psrc += b
    os.close(pfd)
    if hashlib.sha256(psrc).hexdigest() != pl["sha"]:
        refuse("PAYLOAD", "payload sha256 differs")
    mark("ready", pid=os.getpid())
    g = {"__name__": "n2_payload", "mark": mark, "CONFIG": E["work"], "NONCE": NONCE, "E": E}
    exec(compile(psrc, "n2_payload", "exec"), g)
    mark("exiting", code=0)
except SystemExit:
    raise
except BaseException as exc:
    mark("stub_exception", type=type(exc).__name__)
    mark("exiting", code=C["INTERNAL"])
    os._exit(C["INTERNAL"])
'''

WORKLOAD = r'''
import os, sys, time, signal, json
MODE = CONFIG.get("mode", "sleep")
MIB = 1 << 20
SECS = float(CONFIG.get("seconds", 30))

def idle(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(0.02)

def finish():
    rp = CONFIG.get("receipt_path")
    if rp:
        fd = os.open(rp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, json.dumps({"ok": True, "nonce": NONCE}).encode("ascii"))
        os.close(fd)

if MODE == "sleep":
    mark("phase", name="setup")
    idle(SECS)
    mark("phase", name="done")
    finish()
elif MODE == "exit":
    if int(CONFIG.get("code", 0)) == 0:
        finish()
    if not CONFIG.get("silent"):
        mark("exiting", code=int(CONFIG.get("code", 0)))
    os._exit(int(CONFIG.get("code", 0)))
elif MODE == "emit_error":
    mark("error", kind=CONFIG["kind"])
    idle(SECS)
elif MODE == "forge_marker":
    os.write(2, ("N2M deadbeefdeadbeef 1 error kind=EAGAIN\n").encode("ascii"))
    os.write(2, b"plain unmarked output\n")
    idle(SECS)
    finish()
elif MODE == "latch":
    fd = os.open(CONFIG["latch_path"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    idle(SECS)
    finish()
elif MODE == "latch_at_exit":
    idle(SECS)
    fd = os.open(CONFIG["latch_path"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    finish()
elif MODE == "ignore_sigterm":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    mark("sigterm_ignored")
    idle(SECS)
elif MODE == "alloc":
    keep = []
    for i in range(int(CONFIG["mib"])):
        b = bytearray(MIB)
        for j in range(0, MIB, 4096):
            b[j] = 1
        keep.append(b)
    mark("allocated", mib=len(keep))
    idle(SECS)
    finish()
elif MODE == "alloc_until_killed":
    keep = []
    n = 0
    mark("alloc_begin")
    while True:
        b = bytearray(MIB)
        for j in range(0, MIB, 4096):
            b[j] = 1
        keep.append(b)
        n += 1
        if n % 8 == 0:
            mark("alloc_progress", mib=n)
            time.sleep(0.005)
elif MODE == "filecache":
    path = CONFIG["path"]
    total = int(CONFIG["mib"])
    mark("filecache_begin", mib=total)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    chunk = b"\x5a" * MIB
    for i in range(total):
        os.write(fd, chunk)
        if i % 8 == 7:
            mark("filecache_progress", mib=i + 1)
    os.close(fd)
    mark("filecache_done")
    idle(SECS)
    finish()
elif MODE == "spawn":
    if CONFIG.get("init_ignores_sigterm"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    kids = []
    for i in range(int(CONFIG.get("n", 2))):
        pid = os.fork()
        if pid == 0:
            try:
                os.setsid()
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                if CONFIG.get("grandchild_nested", False):
                    pid2 = os.fork()
                    if pid2 == 0:
                        os.setsid()
                        signal.signal(signal.SIGTERM, signal.SIG_IGN)
                        time.sleep(600)
                        os._exit(0)
                time.sleep(600)
            finally:
                os._exit(0)
        kids.append(pid)
    mark("spawned", n=len(kids))
    idle(SECS)
    finish()
elif MODE == "d4_probe":
    import ctypes
    res = {}
    try:
        fd = os.open(CONFIG["own_procs"], os.O_WRONLY)
        os.close(fd)
        res["open_wr"] = "OPENED"
    except OSError as e:
        res["open_wr"] = e.errno
    try:
        os.mkdir(CONFIG["own_dir"] + "/n2-child-escape-probe")
        res["mkdir"] = "CREATED"
    except OSError as e:
        res["mkdir"] = e.errno
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
    r = libc.mount(None, b"/sys/fs/cgroup/memory", None, 32 | 4096, None)  # REMOUNT|BIND, read-write
    res["remount_rw"] = "OK" if r == 0 else ctypes.get_errno()
    caps = {}
    for line in open("/proc/self/status").read().splitlines():
        for key in ("CapEff", "CapBnd", "NoNewPrivs"):
            if line.startswith(key + ":"):
                caps[key] = line.split()[1]
    mark("d4_probe", open_wr=res["open_wr"], mkdir=res["mkdir"], remount_rw=res["remount_rw"],
         capeff=caps.get("CapEff"), capbnd=caps.get("CapBnd"), nnp=caps.get("NoNewPrivs"))
    idle(SECS)
    finish()
elif MODE == "uid_probe":
    res = {"uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(), "egid": os.getegid(),
           "groups": ",".join(str(g) for g in os.getgroups()) or "none"}
    for i, path in enumerate(CONFIG.get("must_not_write", [])):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND)
            os.close(fd)
            res["w%d" % i] = "OPENED"
        except OSError as e:
            res["w%d" % i] = e.errno
    try:
        fd = os.open(os.path.join(CONFIG["slot_root"], "created_by_payload"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        res["create_in_slot_root"] = "CREATED"
    except OSError as e:
        res["create_in_slot_root"] = e.errno
    try:
        fd = os.open(os.path.join(CONFIG["out_dir"], "payload_wrote_here"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        res["create_in_out_dir"] = "CREATED"
    except OSError as e:
        res["create_in_out_dir"] = e.errno
    try:
        os.setuid(0)
        res["setuid0"] = "OK"
    except OSError as e:
        res["setuid0"] = e.errno
    try:
        open("/etc/shadow", "rb").close()
        res["shadow"] = "OPENED"
    except OSError as e:
        res["shadow"] = e.errno
    mark("uid_probe", **res)
    idle(SECS)
    finish()
elif MODE == "phases":
    for name in CONFIG["names"]:
        mark("phase", name=name)
        time.sleep(0.05)
    idle(SECS)
    finish()
elif MODE == "staged":
    keep = []
    chunk = b"\x5a" * MIB
    for st in CONFIG["stages"]:
        mark("phase", name=st["name"])
        for i in range(int(st.get("alloc_mib", 0))):
            b = bytearray(MIB)
            for j in range(0, MIB, 4096):
                b[j] = 1
            keep.append(b)
        if st.get("write_mib"):
            fd = os.open(st["path"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            for i in range(int(st["write_mib"])):
                os.write(fd, chunk)
            os.fsync(fd)
            os.close(fd)
        time.sleep(float(st.get("sleep", 0.15)))
    idle(SECS)
    finish()
elif MODE == "report":
    mark("report", env=",".join(sorted(os.environ)), openblas=os.environ.get("OPENBLAS_NUM_THREADS", ""),
         omp=os.environ.get("OMP_NUM_THREADS", ""), mkl=os.environ.get("MKL_NUM_THREADS", ""),
         gdal=os.environ.get("GDAL_NUM_THREADS", ""))
    idle(SECS)
    finish()
else:
    os._exit(97)
'''
