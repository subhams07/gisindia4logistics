"""Parsers and memory model over cgroup v1 / procfs text. All reads go through a fs object.

Field naming: no Linux field is called "private commit" or "commit" (addendum section 4).
Gate metrics:
  cg_charged_peak_bytes  memory.max_usage_in_bytes (peak of pages charged to the attempt)
  cg_working_set_bytes   usage_in_bytes - total_inactive_file
  cg_anon_bytes          total_rss + total_shmem
  cg_usage_bytes         memory.usage_in_bytes (current)
"""
import os
import posixpath

from .errors import TelemetryStop

V1_UNLIMITED_FLOOR = 9223372036854771712  # values >= this mean "no limit"
PAGE = os.sysconf("SC_PAGE_SIZE")


def parse_int(text, what="value"):
    t = text.strip()
    if not t or not t.lstrip("-").isdigit():
        raise TelemetryStop("parse_error", "%s: %r" % (what, t[:40]))
    return int(t)


def parse_limit(text, what="limit"):
    """v1 limit or v2 'max' token -> int, or None meaning unlimited."""
    t = text.strip()
    if t == "max":
        return None
    v = parse_int(t, what)
    if v >= V1_UNLIMITED_FLOOR:
        return None
    return v


def parse_kv(text, what="kv"):
    out = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 2:
            raise TelemetryStop("parse_error", "%s line %r" % (what, line[:60]))
        out[parts[0]] = parse_int(parts[1], "%s.%s" % (what, parts[0]))
    return out


def parse_meminfo_bytes(text, key):
    """/proc/meminfo reports kB. Reject any other unit; convert to bytes."""
    for line in text.splitlines():
        if line.startswith(key + ":"):
            parts = line.split()
            if len(parts) != 3 or parts[2] != "kB":
                raise TelemetryStop("parse_error", "meminfo unit for %s: %r" % (key, line[:60]))
            return parse_int(parts[1], key) * 1024
    raise TelemetryStop("parse_error", "meminfo key missing: " + key)


def swap_total_bytes(meminfo_text):
    return parse_meminfo_bytes(meminfo_text, "SwapTotal")


def parse_oom_control(text):
    d = parse_kv(text, "oom_control")
    for k in ("under_oom",):
        if k not in d:
            raise TelemetryStop("parse_error", "oom_control missing " + k)
    d.setdefault("oom_kill", 0)
    return d


def read_memory_fields(fs, cgdir):
    """Read all recorded memory fields of one v1 memory cgroup directory."""
    def f(name):
        return fs.read(posixpath.join(cgdir, name))

    stat = parse_kv(f("memory.stat"), "memory.stat")
    usage = parse_int(f("memory.usage_in_bytes"), "usage")
    peak = parse_int(f("memory.max_usage_in_bytes"), "max_usage")
    need = ("total_cache", "total_inactive_file", "total_mapped_file", "total_dirty",
            "total_writeback", "total_rss", "total_shmem")
    for k in need:
        if k not in stat:
            raise TelemetryStop("parse_error", "memory.stat missing " + k)
    inactive = stat["total_inactive_file"]
    out = {
        "cg_usage_bytes": usage,
        "cg_charged_peak_bytes": peak,
        "cg_working_set_bytes": max(usage - inactive, 0),
        "cg_anon_bytes": stat["total_rss"] + stat["total_shmem"],
        "cache_bytes": stat["total_cache"],
        "inactive_file_bytes": inactive,
        "mapped_file_bytes": stat["total_mapped_file"],
        "dirty_bytes": stat["total_dirty"],
        "writeback_bytes": stat["total_writeback"],
        "limit_bytes": parse_limit(f("memory.limit_in_bytes")),
        "failcnt": parse_int(f("memory.failcnt"), "failcnt"),
        "kmem_usage_bytes": parse_int(f("memory.kmem.usage_in_bytes"), "kmem"),
        "kmem_max_usage_bytes": parse_int(f("memory.kmem.max_usage_in_bytes"), "kmem_max"),
    }
    oc = parse_oom_control(f("memory.oom_control"))
    out["under_oom"] = oc["under_oom"]
    out["oom_kill"] = oc["oom_kill"]
    return out


def read_memsw(fs, cgdir):
    return {
        "memsw_failcnt": parse_int(fs.read(posixpath.join(cgdir, "memory.memsw.failcnt"))),
        "memsw_usage_bytes": parse_int(fs.read(posixpath.join(cgdir, "memory.memsw.usage_in_bytes"))),
        "memsw_max_usage_bytes": parse_int(fs.read(posixpath.join(cgdir, "memory.memsw.max_usage_in_bytes"))),
    }


def memsw_check(max_usage, memsw_max_usage, tolerance, exited):
    """Cross-check allowed ONLY after exit. Returns delta; raises if not exited."""
    if not exited:
        raise TelemetryStop("memsw_check_before_exit", "memsw cross-check only after exit")
    delta = abs(memsw_max_usage - max_usage)
    return delta, delta > tolerance


def ancestors_up(rel):
    """'/a/b/c' -> ['/a/b/c', '/a/b', '/a', '/'] (cgroup-relative paths)."""
    rel = posixpath.normpath("/" + rel.strip("/"))
    out = [rel]
    while rel != "/":
        rel = posixpath.dirname(rel) or "/"
        out.append(rel)
    return out


def effective_available(fs, mem_root, parent_rel, meminfo_text):
    """min(host MemAvailable, min over finite-limit ancestors of (limit - usage)).

    parent_rel is the cgroup-relative path of the attempt's PARENT (the controller's own
    memory cgroup). Each term is returned separately. Limit terms with v1-unlimited are skipped.
    """
    terms = {"host_mem_available": parse_meminfo_bytes(meminfo_text, "MemAvailable")}
    ancestors = []
    for rel in ancestors_up(parent_rel):
        d = mem_root if rel == "/" else mem_root + rel
        limit = parse_limit(fs.read(posixpath.join(d, "memory.limit_in_bytes")))
        usage = parse_int(fs.read(posixpath.join(d, "memory.usage_in_bytes")), "usage")
        entry = {"rel": rel, "limit": limit, "usage": usage}
        if limit is not None:
            entry["headroom"] = limit - usage
            terms["ancestor:" + rel] = limit - usage
        ancestors.append(entry)
    eff = min(terms.values())
    return {"effective_available_bytes": eff, "terms": terms, "ancestors": ancestors}


def bash_usage_excluding_attempt(fs, mem_root, parent_rel, attempt_name):
    """Usage of the controller's (shared Bash) cgroup excluding the attempt's own charges."""
    pd = mem_root if parent_rel in ("", "/") else mem_root + posixpath.normpath("/" + parent_rel.strip("/"))
    parent = parse_int(fs.read(posixpath.join(pd, "memory.usage_in_bytes")), "parent usage")
    attempt = 0
    adir = posixpath.join(pd, attempt_name)
    if fs.exists(adir):
        attempt = parse_int(fs.read(posixpath.join(adir, "memory.usage_in_bytes")), "attempt usage")
    return max(parent - attempt, 0)


def disk_free_bytes(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def read_cgroup_file_lines(fs, path):
    """Return [(hier, ctrls, path)] from a /proc/<pid>/cgroup text."""
    out = []
    for line in fs.read(path).splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            raise TelemetryStop("parse_error", "cgroup line %r" % line[:60])
        out.append((parts[0], parts[1], parts[2]))
    return out


def cgroup_paths_of(text):
    """Parse /proc/*/cgroup text -> {'memory': path, 'pids': path, 'v2': path}."""
    res = {}
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            raise TelemetryStop("parse_error", "cgroup line %r" % line[:60])
        hier, ctrls, path = parts
        names = ctrls.split(",") if ctrls else []
        if hier == "0" and not ctrls:
            res["v2"] = path
        if "memory" in names:
            res["memory"] = path
        if "pids" in names:
            res["pids"] = path
    return res
