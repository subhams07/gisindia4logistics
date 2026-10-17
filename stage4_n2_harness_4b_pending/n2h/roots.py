"""Root and path safety (M04). Linux form of 'reparse rejection': symlinks, mount boundaries.

There are NO default roots anywhere: callers must pass every root explicitly.
"""
import os
import stat

from .errors import RootsStop

ALWAYS_FORBIDDEN = ("/sys", "/proc", "/dev")


def _is_within(path, parent):
    p = path.rstrip("/") + "/"
    q = parent.rstrip("/") + "/"
    return p.startswith(q)


def reject_forbidden(path, extra_forbidden=()):
    """Reject /sys, /proc, /dev and any caller-supplied forbidden tree (repo, national data)."""
    real = os.path.realpath(path)
    for pre in tuple(ALWAYS_FORBIDDEN) + tuple(os.path.realpath(x) for x in extra_forbidden):
        if _is_within(real, pre) or _is_within(path, pre):
            raise RootsStop("root_forbidden_tree", "%s under %s" % (path, pre))
    return real


def check_root(path, base, extra_forbidden=()):
    """Validate one directory root: absolute, no symlink in any component below `base`,
    no mount-boundary (st_dev change) below `base`, directory, owned by us. Returns identity."""
    if not os.path.isabs(path):
        raise RootsStop("root_not_absolute", path)
    if os.path.normpath(path) != path.rstrip("/") and path != "/":
        raise RootsStop("root_not_normalized", path)
    path = path.rstrip("/") or "/"
    reject_forbidden(path, extra_forbidden)
    base = base.rstrip("/") or "/"
    if not _is_within(path, base):
        raise RootsStop("root_outside_base", "%s not under %s" % (path, base))
    rel = path[len(base):].strip("/")
    cur = base
    try:
        parent_dev = os.lstat(cur).st_dev
        bst = os.lstat(cur)
    except OSError as e:
        raise RootsStop("root_base_missing", "%s: %s" % (base, e))
    if stat.S_ISLNK(bst.st_mode) or not stat.S_ISDIR(bst.st_mode):
        raise RootsStop("root_base_not_plain_dir", base)
    last = bst
    if rel:
        for comp in rel.split("/"):
            cur = cur + "/" + comp
            try:
                st = os.lstat(cur)
            except OSError as e:
                raise RootsStop("root_component_missing", "%s: %s" % (cur, e))
            if stat.S_ISLNK(st.st_mode):
                raise RootsStop("root_symlink_component", cur)
            if not stat.S_ISDIR(st.st_mode):
                raise RootsStop("root_component_not_dir", cur)
            if st.st_dev != parent_dev:
                raise RootsStop("root_mount_boundary", cur)
            parent_dev = st.st_dev
            last = st
    if last.st_uid != os.getuid():
        raise RootsStop("root_not_owned", path)
    return {"path": path, "dev": last.st_dev, "ino": last.st_ino}


def check_disjoint(roots):
    """roots: dict name -> path. No root equals, contains or is contained by another."""
    items = sorted(roots.items())
    reals = {k: os.path.realpath(v) for k, v in items}
    for i, (ka, _) in enumerate(items):
        for kb, _ in items[i + 1:]:
            a, b = reals[ka], reals[kb]
            if a == b or _is_within(a, b) or _is_within(b, a):
                raise RootsStop("roots_not_disjoint", "%s vs %s" % (ka, kb))
    return True


def resolve_roots(base, extra_forbidden=(), **named):
    """Explicit roots only. Missing/None root -> STOP (no fallback to repo/default/B0)."""
    if not named:
        raise RootsStop("no_roots_given", "explicit roots required; no defaults exist")
    out = {}
    for name, path in named.items():
        if not path:
            raise RootsStop("root_missing", name)
        out[name] = check_root(path, base, extra_forbidden)
    check_disjoint({k: v["path"] for k, v in out.items()})
    return out


def file_identity(path):
    """lstat-based identity of a regular file; symlink/nonregular/missing -> STOP."""
    try:
        st = os.lstat(path)
    except OSError as e:
        raise RootsStop("file_missing", "%s: %s" % (path, e))
    if stat.S_ISLNK(st.st_mode):
        raise RootsStop("file_symlink", path)
    if not stat.S_ISREG(st.st_mode):
        raise RootsStop("file_not_regular", path)
    return {"dev": st.st_dev, "ino": st.st_ino, "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}


def open_verified(path, identity):
    """Open O_NOFOLLOW and require the fd to have the recorded identity (swap detection)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as e:
        raise RootsStop("file_open_failed", "%s: %s" % (path, e))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise RootsStop("file_not_regular", path)
        got = {"dev": st.st_dev, "ino": st.st_ino, "size": st.st_size,
               "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}
        if got != identity:
            raise RootsStop("file_identity_changed", path)
        return fd
    except BaseException:
        os.close(fd)
        raise


def check_traversable_by(path, uid, gid):
    """Every directory from / down to `path` must grant search (x) to uid/gid (owner, group or other class).
    Needed so an unprivileged payload can reach its slot root; checked by mode bits (no chmod is ever done)."""
    path = os.path.realpath(path)
    parts = [p for p in path.split("/") if p]
    cur = "/"
    chain = ["/"]
    for comp in parts:
        cur = cur.rstrip("/") + "/" + comp
        chain.append(cur)
    for d in chain:
        st = os.stat(d)
        if st.st_uid == uid:
            ok = bool(st.st_mode & stat.S_IXUSR)
        elif st.st_gid == gid:
            ok = bool(st.st_mode & stat.S_IXGRP)
        else:
            ok = bool(st.st_mode & stat.S_IXOTH)
        if not ok:
            raise RootsStop("payload_uid_cannot_traverse", "%s mode %o" % (d, st.st_mode & 0o7777))
    return True
