"""Attempt cgroup set (the 'job'): v1 memory + v1 pids + optional v2 child of the unified root.

Every cgroup created is named n2-<id>, is created exclusively (EEXIST -> STOP), gets a
finite limit written BEFORE any process joins, and is removed by its owner. Limits are never
written to pre-existing cgroups.
"""
import os
import posixpath
import re

from . import sysfs
from .errors import CgroupStop

CG_ROOT = "/sys/fs/cgroup"
NAME_RE = re.compile(r"^n2-[a-z0-9][a-z0-9-]{0,48}$")


class Layout:
    """Computed paths for one attempt's cgroup set, nested under the controller's own cgroups."""

    def __init__(self, name, controller_paths, cg_root=CG_ROOT, use_v2=True):
        if not NAME_RE.match(name):
            raise CgroupStop("bad_cgroup_name", name)
        self.name = name
        self.cg_root = cg_root
        self.use_v2 = bool(use_v2 and "v2" in controller_paths)
        self.parent_rel = {}
        self.rel = {}
        self.abs = {}
        hierarchies = [("memory", "/memory"), ("pids", "/pids")]
        if self.use_v2:
            hierarchies.append(("v2", "/unified"))
        for h, mount in hierarchies:
            parent = controller_paths[h]
            rel = posixpath.join(parent, name) if parent != "/" else "/" + name
            self.parent_rel[h] = parent
            self.rel[h] = rel
            self.abs[h] = cg_root + mount + rel
        self.join_order = (["v2"] if self.use_v2 else []) + ["memory", "pids"]

    def attempt_paths(self):
        """Expected /proc/<pid>/cgroup paths for a member."""
        return dict(self.rel)


def controller_cgroup_paths(fs, pid="self"):
    return sysfs.cgroup_paths_of(fs.read("/proc/%s/cgroup" % pid))


def scan_existing(fs, cg_root=CG_ROOT):
    """Find every n2-* cgroup directory in memory/pids/unified. Never cleans up."""
    found = []
    for mount in ("memory", "pids", "unified"):
        top = posixpath.join(cg_root, mount)
        stack = [top]
        while stack:
            d = stack.pop()
            try:
                names = fs.listdir(d)
            except OSError:
                continue
            for n in names:
                p = posixpath.join(d, n)
                if os.path.isdir(p) and not os.path.islink(p):
                    if n.startswith("n2-"):
                        populated = None
                        try:
                            populated = bool(fs.read(posixpath.join(p, "cgroup.procs")).strip())
                        except OSError:
                            pass
                        found.append({"path": p, "populated": populated})
                    stack.append(p)
    return found


def assert_no_existing(fs, cg_root=CG_ROOT):
    found = scan_existing(fs, cg_root)
    if found:
        raise CgroupStop("existing_n2_cgroup", "; ".join(
            "%s populated=%s" % (f["path"], f["populated"]) for f in found))
    return True


def create_all(layout, fs):
    """Exclusive mkdir. On any failure, remove only what THIS call created (empty), then STOP."""
    made = []
    layout.created = {}
    try:
        for h in layout.join_order:
            p = layout.abs[h]
            try:
                fs.mkdir(p)
            except FileExistsError:
                raise CgroupStop("cgroup_exists", p)
            except OSError as e:
                raise CgroupStop("cgroup_mkdir_failed", "%s: %s" % (p, e), injected=getattr(e, "label", None))
            made.append(p)
        st_ids = {}
        for p in made:
            st = os.stat(p)
            st_ids[p] = (st.st_dev, st.st_ino)
        layout.created = st_ids
    except CgroupStop:
        for p in reversed(made):
            try:
                fs.rmdir(p)
            except OSError:
                pass
        raise
    return made


def _r(fs, layout, h, name):
    return fs.read(posixpath.join(layout.abs[h], name))


MEMSW_FACTOR = 2  # finite memsw limit = 2 x memory limit (see set_limits)


def set_limits(layout, mem_limit, pids_max, fs):
    """Write limits (memory then memsw), read both back; pids.max; assert empty zero state.

    The memsw limit is finite but 2x the memory limit. Measured on this host: with memsw == limit,
    memory.failcnt and memory.memsw.failcnt both stay 0 although the limit is hit (peak == limit), so
    failcnt would be blind; with memsw = 2x limit the plain memory counter binds first and
    memory.failcnt counts the hits. The host has no swap, so memsw usage cannot exceed memory usage.
    """
    page = sysfs.PAGE
    if mem_limit % page or mem_limit <= 0:
        raise CgroupStop("limit_not_page_aligned", str(mem_limit))
    if pids_max <= 0:
        raise CgroupStop("pids_max_not_finite", str(pids_max))
    mem = layout.abs["memory"]
    try:
        fs.write(posixpath.join(mem, "memory.limit_in_bytes"), str(mem_limit))
        fs.write(posixpath.join(mem, "memory.memsw.limit_in_bytes"), str(mem_limit * MEMSW_FACTOR))
        fs.write(posixpath.join(layout.abs["pids"], "pids.max"), str(pids_max))
    except OSError as e:
        raise CgroupStop("limit_write_failed", str(e), injected=getattr(e, "label", None))
    got_l = sysfs.parse_limit(_r(fs, layout, "memory", "memory.limit_in_bytes"))
    got_sw = sysfs.parse_limit(_r(fs, layout, "memory", "memory.memsw.limit_in_bytes"))
    got_p = _r(fs, layout, "pids", "pids.max").strip()
    if got_l != mem_limit or got_sw != mem_limit * MEMSW_FACTOR or got_p != str(pids_max):
        raise CgroupStop("limit_readback_mismatch", "%r %r %r" % (got_l, got_sw, got_p))
    assert_empty_zero(layout, fs)
    return {"memory_limit": got_l, "memsw_limit": got_sw, "pids_max": int(got_p)}


def assert_empty_zero(layout, fs):
    for h in layout.join_order:
        if _r(fs, layout, h, "cgroup.procs").strip():
            raise CgroupStop("cgroup_not_empty", h)
    for name in ("memory.usage_in_bytes", "memory.max_usage_in_bytes", "memory.failcnt",
                 "memory.memsw.usage_in_bytes"):
        if sysfs.parse_int(_r(fs, layout, "memory", name), name) != 0:
            raise CgroupStop("cgroup_counter_nonzero", name)
    if sysfs.parse_int(_r(fs, layout, "pids", "pids.current"), "pids.current") != 0:
        raise CgroupStop("cgroup_counter_nonzero", "pids.current")


def procs(layout, h, fs):
    txt = _r(fs, layout, h, "cgroup.procs")
    return [int(x) for x in txt.split()]


def members(layout, fs):
    """Union listing from every hierarchy of the set; all must agree."""
    sets = {h: set(procs(layout, h, fs)) for h in layout.join_order}
    base = sets[layout.join_order[0]]
    for h, s in sets.items():
        if s != base:
            raise CgroupStop("listing_disagrees_between_hierarchies", "%s %r vs %r" % (h, sorted(s), sorted(base)))
    return sorted(base)


def proc_cgroup_matches(layout, pid, fs, tid=None):
    """True iff /proc/<pid>[/task/<tid>]/cgroup shows exactly the attempt paths for each hierarchy."""
    path = "/proc/%d/cgroup" % pid if tid is None else "/proc/%d/task/%d/cgroup" % (pid, tid)
    got = sysfs.cgroup_paths_of(fs.read(path))
    for h, rel in layout.rel.items():
        if got.get(h) != rel:
            return False
    return True


def join_and_verify(layout, pid, fs):
    """Join in order; verify each join two ways. Returns state info.

    Raises CgroupStop with .code 'join_failed'/'join_verify_failed' and attribute
    first_joined (bool) telling whether the FIRST cgroup of the set may have been joined
    (the R-1 ambiguity input). The caller applies the ambiguity rule.
    """
    first = layout.join_order[0]
    joined = []
    for h in layout.join_order:
        try:
            fs.write(posixpath.join(layout.abs[h], "cgroup.procs"), str(pid))
        except OSError as e:
            err = CgroupStop("join_failed", "%s: %s" % (h, e), injected=getattr(e, "label", None))
            err.first_joined_maybe = (h == first)  # a failed write to first: unknown state
            err.joined = list(joined)
            raise err
        joined.append(h)
        try:
            cur = sysfs.cgroup_paths_of(fs.read("/proc/%d/cgroup" % pid))
            ok_proc = all(cur.get(x) == layout.rel[x] for x in joined)
            ok_list = procs(layout, h, fs) == [pid]
        except OSError as e:
            ok_proc = ok_list = False
            vi = getattr(e, "label", None)
        else:
            vi = None
        if not (ok_proc and ok_list):
            err = CgroupStop("join_verify_failed", "%s proc=%s list=%s" % (h, ok_proc, ok_list), injected=vi)
            err.first_joined_maybe = True
            err.joined = list(joined)
            raise err
    return joined


def counters(layout, fs):
    """pids.* and (where present) v2 events. Missing files are reported as None, never zero."""
    def opt(h, name):
        p = posixpath.join(layout.abs[h], name)
        if not fs.exists(p):
            return None
        return fs.read(p).strip()

    out = {
        "pids_current": sysfs.parse_int(_r(fs, layout, "pids", "pids.current"), "pids.current"),
        "pids_max": _r(fs, layout, "pids", "pids.max").strip(),
        "pids_peak": opt("pids", "pids.peak"),
        "pids_events": opt("pids", "pids.events"),
    }
    if layout.use_v2:
        out["v2_events"] = opt("v2", "cgroup.events")
    return out


def v2_populated(layout, fs):
    if not layout.use_v2:
        return None
    for line in _r(fs, layout, "v2", "cgroup.events").splitlines():
        k, _, v = line.partition(" ")
        if k == "populated":
            return v.strip() == "1"
    raise CgroupStop("v2_events_unparsed", "")


def v2_kill(layout, fs):
    """cgroup.kill write (option a+). Optional, probed path."""
    if not layout.use_v2:
        raise CgroupStop("v2_not_in_use", "")
    fs.write(posixpath.join(layout.abs["v2"], "cgroup.kill"), "1")


def is_empty(layout, fs):
    ok = True
    for h in layout.join_order:
        if procs(layout, h, fs):
            ok = False
    if sysfs.parse_int(_r(fs, layout, "pids", "pids.current")) != 0:
        ok = False
    if layout.use_v2 and v2_populated(layout, fs):
        ok = False
    return ok


def remove(layout, fs):
    """rmdir every own cgroup (reverse order). Failure -> STOP. Never force_empty."""
    created = getattr(layout, "created", {})
    for h in reversed(layout.join_order):
        p = layout.abs[h]
        if p not in created:
            continue  # never created by this attempt: never removed
        try:
            st = os.stat(p)
        except OSError as e:
            raise CgroupStop("rmdir_failed", "%s: %s" % (p, e)) from e
        if (st.st_dev, st.st_ino) != created[p]:
            raise CgroupStop("rmdir_identity_mismatch", "%s is not the directory this attempt created" % p)
        try:
            fs.rmdir(p)
        except OSError as e:
            raise CgroupStop("rmdir_failed", "%s: %s" % (layout.abs[h], e), injected=getattr(e, "label", None))


def memcg_counts():
    """(num_cgroups from /proc/cgroups for memory, number of n2-independent directories)"""
    with open("/proc/cgroups") as f:
        for line in f:
            p = line.split()
            if p and p[0] == "memory":
                return int(p[2])
    return None


def count_memory_dirs(cg_root=CG_ROOT):
    n = 1
    stack = [posixpath.join(cg_root, "memory")]
    while stack:
        d = stack.pop()
        for e in os.scandir(d):
            if e.is_dir(follow_symlinks=False):
                n += 1
                stack.append(e.path)
    return n


def probe_v2(fs, cg_root=CG_ROOT):
    """Smoke probe of the optional v2 `cgroup.kill` path (D-3 option a+): create an empty n2-probe-*
    child of the controller's v2 cgroup, check kill/events files, write cgroup.kill on the EMPTY cgroup,
    read cgroup.events, remove it. Never touches a pre-existing cgroup. Result is data, not a decision."""
    paths = controller_cgroup_paths(fs)
    if "v2" not in paths:
        return {"available": False, "why": "no v2 hierarchy in /proc/self/cgroup"}
    name = "n2-probe-" + os.urandom(4).hex()
    parent = paths["v2"]
    rel = posixpath.join(parent, name) if parent != "/" else "/" + name
    d = cg_root + "/unified" + rel
    out = {"available": False, "path": d}
    try:
        fs.mkdir(d)
    except OSError as e:
        out["why"] = "mkdir failed: %s" % e
        return out
    try:
        out["has_cgroup_kill"] = fs.exists(posixpath.join(d, "cgroup.kill"))
        out["has_cgroup_events"] = fs.exists(posixpath.join(d, "cgroup.events"))
        if out["has_cgroup_kill"]:
            fs.write(posixpath.join(d, "cgroup.kill"), "1")
        ev = fs.read(posixpath.join(d, "cgroup.events"))
        out["events_after_kill_on_empty"] = ev.split()
        out["available"] = bool(out["has_cgroup_kill"] and out["has_cgroup_events"])
    except OSError as e:
        out["why"] = str(e)
    finally:
        fs.rmdir(d)
    return out
