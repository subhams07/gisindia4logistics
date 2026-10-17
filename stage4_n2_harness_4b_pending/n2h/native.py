"""ctypes helpers: prctl, subreaper, mincore residency, fadvise. Resolved BEFORE fork."""
import ctypes
import hashlib
import os
import stat

from .errors import N2Stop
from .fsio import write_all

PR_SET_PDEATHSIG = 1
PR_GET_PDEATHSIG = 2
PR_SET_CHILD_SUBREAPER = 36
PR_GET_CHILD_SUBREAPER = 37
PR_CAPBSET_DROP = 24
CAP_SYS_ADMIN = 21

_libc = ctypes.CDLL(None, use_errno=True)
_prctl = _libc.prctl
_prctl.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong)
_prctl.restype = ctypes.c_int


def prctl_fn():
    """Return the pre-resolved prctl callable (argtypes set). Child code uses this object."""
    return _prctl


# separate function pointer object for the pointer-argument variants of prctl
_libc2 = ctypes.CDLL(None, use_errno=True)
_prctl_ptr = _libc2.prctl
_prctl_ptr.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong)
_prctl_ptr.restype = ctypes.c_int


def set_subreaper():
    """PR_SET_CHILD_SUBREAPER on the calling process; verified by PR_GET."""
    if _prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise N2Stop("subreaper_failed", os.strerror(ctypes.get_errno()))
    if get_subreaper() != 1:
        raise N2Stop("subreaper_not_set", "")
    return True


def get_subreaper():
    v = ctypes.c_int(0)
    if _prctl_ptr(PR_GET_CHILD_SUBREAPER, ctypes.addressof(v), 0, 0, 0) != 0:
        raise N2Stop("subreaper_get_failed", os.strerror(ctypes.get_errno()))
    return v.value


_libc.mmap.restype = ctypes.c_void_p
_libc.mmap.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long)
_libc.munmap.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
_libc.mincore.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p)
PAGE = os.sysconf("SC_PAGE_SIZE")
PROT_READ = 1
MAP_SHARED = 1


def mincore_vector(path):
    """Return (list of 0/1 per page, file_size). Read-only mapping via libc mmap.

    mincore reliability depends on kernel can_do_mincore (owner, write permission or
    CAP_FOWNER): callers must run a positive control (M14) before trusting it.
    """
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        size = os.fstat(fd).st_size
        if size == 0:
            return [], 0
        addr = _libc.mmap(None, size, PROT_READ, MAP_SHARED, fd, 0)
        if addr in (None, ctypes.c_void_p(-1).value):
            raise N2Stop("mmap_failed", os.strerror(ctypes.get_errno()))
        try:
            npages = (size + PAGE - 1) // PAGE
            vec = (ctypes.c_ubyte * npages)()
            if _libc.mincore(addr, size, ctypes.cast(vec, ctypes.c_void_p)) != 0:
                raise N2Stop("mincore_failed", os.strerror(ctypes.get_errno()))
            return [v & 1 for v in vec], size
        finally:
            _libc.munmap(addr, size)
    finally:
        os.close(fd)


def residency_from_vector(vec, size):
    """Resident bytes and fraction from a mincore vector (pure function; used with mocks)."""
    if size < 0 or (vec and len(vec) != (size + PAGE - 1) // PAGE):
        raise N2Stop("residency_vector_mismatch", "")
    if size == 0:
        return {"resident_bytes": 0, "size": 0, "fraction": 0.0}
    resident = 0
    for i, v in enumerate(vec):
        if v:
            resident += min(PAGE, size - i * PAGE)
    return {"resident_bytes": resident, "size": size, "fraction": resident / size}


def residency(path):
    vec, size = mincore_vector(path)
    return residency_from_vector(vec, size)


def fadvise_dontneed_own(path, allowed_base, created=None):
    """POSIX_FADV_DONTNEED only on a regular file WE created under our scratch base.

    Host-global in effect (D-11): evicts that file's clean pages for every cgroup.
    Dirty pages are not evicted: the creator must fsync the file first.
    """
    real = os.path.realpath(path)
    base = os.path.realpath(allowed_base).rstrip("/") + "/"
    if not real.startswith(base):
        raise N2Stop("fadvise_outside_scratch", path)
    fd = os.open(real, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid():
            raise N2Stop("fadvise_not_own_file", path)
        if created is not None and (st.st_dev, st.st_ino) not in created:
            raise N2Stop("fadvise_not_created_by_attempt", path)  # only inodes this attempt created or declared
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


# --- operator item (d), as corrected: attempt-private copies; shared inputs are never evicted -------------
def _ident(st):
    return {"dev": st.st_dev, "ino": st.st_ino, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "ctime_ns": st.st_ctime_ns, "mode": st.st_mode, "uid": st.st_uid, "gid": st.st_gid}


def _open_ro(path):
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        return os.open(path, flags | getattr(os, "O_NOATIME", 0))  # O_NOATIME needs owner/CAP_FOWNER
    except PermissionError:
        return os.open(path, flags)


def _within(path, roots):
    p = path.rstrip("/") + "/"
    return any(p.startswith(r.rstrip("/") + "/") for r in roots)


def copy_input_private(src, dst, read_roots):
    """Copy one admitted input into an attempt-private file (exclusive create, mode 0444, owned by us).

    The source is opened read-only, never modified (identity compared before and after, atime excepted), and
    NEVER passed to fadvise: evicting shared page cache is a host-wide effect that read-only access does not
    remove. The copy is hash-verified against the bytes read from the source and then re-read from disk.
    """
    real = os.path.realpath(src)
    if real != os.path.normpath(src):
        raise N2Stop("input_path_not_canonical", src)  # a symlink somewhere in the path
    if not _within(real, read_roots):
        raise N2Stop("input_outside_read_scope", src)
    for forbidden in ("/proc", "/sys", "/dev"):
        if _within(real, [forbidden]):
            raise N2Stop("input_forbidden_tree", src)
    lst = os.lstat(real)
    if not stat.S_ISREG(lst.st_mode):
        raise N2Stop("input_not_regular", src)
    sfd = _open_ro(real)
    try:
        before = _ident(os.fstat(sfd))
        if before["dev"] != lst.st_dev or before["ino"] != lst.st_ino:
            raise N2Stop("input_swapped", src)
        h = hashlib.sha256()
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        dfd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o444)
        try:
            while True:
                b = os.read(sfd, 1 << 20)
                if not b:
                    break
                h.update(b)
                write_all(dfd, b)
            os.fsync(dfd)  # dirty pages are not evictable: flush before the copy is evicted
        finally:
            os.close(dfd)
        after = _ident(os.fstat(sfd))
    finally:
        os.close(sfd)
    if before != after:
        raise N2Stop("input_changed_while_copying", src)
    got = hashlib.sha256()
    with open(dst, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            got.update(b)
    if got.hexdigest() != h.hexdigest():
        raise N2Stop("private_copy_hash_mismatch", src)
    cst = os.stat(dst)
    return {"source": real, "copy": dst, "sha256": h.hexdigest(), "bytes": before["size"], "source_identity": before,
            "copy_inode": [cst.st_dev, cst.st_ino]}


def prepare_private_inputs(source_root, rel_paths, private_root, scratch_base, read_roots, created=None):
    """Copy `rel_paths` (relative to source_root) to private_root with the same layout, hash-verify each copy,
    evict ONLY the copies (they are our own scratch files), and measure the residency of both. The shared
    sources' residency is returned separately as disclosed uncertainty (added to resident_bytes_elsewhere)."""
    real_private = os.path.realpath(private_root)
    if not _within(real_private, [os.path.realpath(scratch_base)]):
        raise N2Stop("private_root_outside_scratch", private_root)
    copies = []
    for rel in rel_paths:
        if os.path.isabs(rel) or ".." in rel.split("/"):
            raise N2Stop("input_relpath_invalid", rel)
        rec = copy_input_private(os.path.join(source_root, rel), os.path.join(private_root, rel), read_roots)
        if created is not None:
            created.add(tuple(rec["copy_inode"]))
        fadvise_dontneed_own(rec["copy"], scratch_base, created)  # own scratch file only
        copies.append(rec)
    shared = [residency(c["source"]) for c in copies]
    private = [residency(c["copy"]) for c in copies]
    return {
        "copies": copies,
        "shared_source_residency": [{"path": c["source"], **r} for c, r in zip(copies, shared)],
        "shared_source_resident_bytes": sum(r["resident_bytes"] for r in shared),
        "private_copy_residency": [{"path": c["copy"], **r} for c, r in zip(copies, private)],
        "private_resident_bytes": sum(r["resident_bytes"] for r in private),
        "private_size": sum(r["size"] for r in private),
        "note": "shared sources are never evicted; their residency is disclosed cache uncertainty",
    }
