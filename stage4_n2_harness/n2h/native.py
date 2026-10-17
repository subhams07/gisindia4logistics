"""ctypes helpers: prctl, subreaper, mincore residency, fadvise. Resolved BEFORE fork."""
import ctypes
import os

from .errors import N2Stop

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


def fadvise_dontneed_own(path, allowed_base):
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
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)
