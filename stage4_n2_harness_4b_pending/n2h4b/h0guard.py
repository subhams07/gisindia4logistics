# ---- H0 cache guard (spec D) --------------------------------------------------------------------------
import hashlib
import json
import logging
import os
from pathlib import Path

import numpy as np

ARCHIVE_NAME = "canonical_nh_graph.npz"


class ForbiddenBuild(Exception):
    """Raised by the sentinel at graph_builder.build_canonical_highway_graph (H1/H2 must never build)."""


class CacheGuardStop(Exception):
    def __init__(self, code, detail=""):
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


def _latch(out_dir, name, text):
    """Out-of-band latch: a file in the slot's out dir the controller also inspects. Never raises."""
    try:
        fd = os.open(os.path.join(out_dir, name), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, (text[:400] + "\n").encode("ascii", "replace"))
        finally:
            os.close(fd)
        return True
    except Exception:  # noqa: BLE001 - a latch writer must never raise into the producer
        return False


def archive_state(cache_dir):
    """Files, bytes hash, and the version/fingerprint stored inside the (closed) archive."""
    cache_dir = Path(cache_dir)
    files = sorted(p.name for p in cache_dir.iterdir()) if cache_dir.is_dir() else []
    st = {"files": files, "present": ARCHIVE_NAME in files}
    if st["present"]:
        p = cache_dir / ARCHIVE_NAME
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        st["sha256"], st["bytes"] = h.hexdigest(), p.stat().st_size
        try:
            with np.load(p, allow_pickle=False) as data:
                meta = json.loads(str(data["metadata"][0]))
            st["fingerprint"], st["cache_version"] = meta, meta.get("cache_version")
        except Exception as e:  # noqa: BLE001 - an unreadable archive is reported as such, not repaired
            st["unreadable"] = type(e).__name__
    return st


def check_unchanged(cache_dir, expect, when):
    """Expected archive hash/version/fingerprint, and nothing else in the directory (no warming/replacement)."""
    st = archive_state(cache_dir)
    if not st["present"]:
        raise CacheGuardStop("cache_archive_missing_" + when, str(cache_dir))
    if st["files"] != [ARCHIVE_NAME]:
        raise CacheGuardStop("cache_dir_extra_files_" + when, ",".join(st["files"]))
    for key in ("sha256", "bytes", "cache_version"):
        if st.get(key) != expect.get(key):
            raise CacheGuardStop("cache_%s_mismatch_%s" % (key, when), "%r != %r" % (st.get(key), expect.get(key)))
    if st.get("fingerprint") != expect.get("fingerprint"):
        raise CacheGuardStop("cache_fingerprint_mismatch_" + when, "")
    return st


class LatchHandler(logging.Handler):
    """Non-raising WARNING-or-higher latch on the graph_builder logger."""

    def __init__(self, out_dir):
        super().__init__(level=logging.WARNING)
        self.out_dir = out_dir
        self.records = []
        self.write_failed = False

    def emit(self, record):
        try:
            self.records.append({"level": record.levelname, "msg": record.getMessage()[:200]})
            if not _latch(self.out_dir, "logging_latch", "%s %s" % (record.levelname, record.getMessage())):
                self.write_failed = True
        except Exception:  # noqa: BLE001 - swallowed on purpose; failure is remembered in memory
            self.write_failed = True

    def handleError(self, record):  # never print or raise
        self.write_failed = True


class H1H2Guard:
    """Installed for H1/H2: forbidden-build sentinel, both loader dispatch paths observed with real delegation,
    logging latch, archive verified before/after each load."""

    def __init__(self, builder, out_dir, cache_dir, expect, extra_loader_bindings=()):
        self.b = builder
        self.out_dir = str(out_dir)
        self.cache_dir = os.path.realpath(str(cache_dir))
        self.expect = expect
        self.bindings = list(extra_loader_bindings)  # [(module, "load_or_build_cached_graph")] by-value aliases
        self.calls = []
        self.sentinel_hits = 0
        self.seen_ids = set()
        self.handler = None
        self.installed = False

    def install(self):
        b = self.b
        if not b.LOGGER.isEnabledFor(logging.WARNING):
            raise CacheGuardStop("logger_disabled", "graph_builder WARNING would not be emitted")
        check_unchanged(self.cache_dir, self.expect, "before_install")
        self._real_loader = b.load_or_build_cached_graph
        self._real_build = b.build_canonical_highway_graph
        for mod, name in self.bindings:
            if getattr(mod, name) is not self._real_loader:
                raise CacheGuardStop("binding_not_real_loader", mod.__name__)
        self.handler = LatchHandler(self.out_dir)
        b.LOGGER.addHandler(self.handler)
        b.build_canonical_highway_graph = self._sentinel
        b.load_or_build_cached_graph = self._wrap("builder_attribute")
        for mod, name in self.bindings:
            setattr(mod, name, self._wrap("by_value:" + mod.__name__))
        self.installed = True
        return self

    def restore(self):
        if not self.installed:
            return
        b = self.b
        b.build_canonical_highway_graph = self._real_build
        b.load_or_build_cached_graph = self._real_loader
        for mod, name in self.bindings:
            setattr(mod, name, self._real_loader)
        b.LOGGER.removeHandler(self.handler)
        self.installed = False

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.restore()
        return False

    def _sentinel(self, *a, **k):
        self.sentinel_hits += 1
        _latch(self.out_dir, "sentinel_latch", "build_canonical_highway_graph called")
        raise ForbiddenBuild("graph build is forbidden in this slot")

    def _wrap(self, label):
        def loader(nh_gdf, cache_dir=None):
            rec = {"binding": label, "cache_dir": None if cache_dir is None else str(cache_dir)}
            self.calls.append(rec)
            if cache_dir is None or os.path.realpath(str(cache_dir)) != self.cache_dir:
                raise CacheGuardStop("loader_cache_dir_not_h0", str(cache_dir))
            check_unchanged(self.cache_dir, self.expect, "before_load")
            res = self._real_loader(nh_gdf, cache_dir=cache_dir)  # real load, never a cached fake capture
            check_unchanged(self.cache_dir, self.expect, "after_load")
            ids = {id(x) for x in res}
            if ids & self.seen_ids or len(res) != 6:
                raise CacheGuardStop("loader_returned_shared_capture", label)
            self.seen_ids |= ids
            rec["ok"] = True
            return res
        return loader

    def verdict(self):
        """Any latch fails the slot even with exit 0."""
        files = [n for n in ("sentinel_latch", "logging_latch") if os.path.exists(os.path.join(self.out_dir, n))]
        return {"sentinel_hits": self.sentinel_hits, "logging_records": list(self.handler.records) if self.handler else [],
                "latch_write_failed": bool(self.handler and self.handler.write_failed),
                "latch_files": files, "clean": not (files or self.sentinel_hits or (self.handler and
                                                    (self.handler.records or self.handler.write_failed))),
                "loader_calls": list(self.calls)}


def run_h0(builder, nh_gdf, cache_dir, out_dir):
    """H0: real cold build into an absent fresh scratch cache; real validate; archive closed and captured."""
    cache_dir = Path(cache_dir)
    if not builder.LOGGER.isEnabledFor(logging.WARNING):
        raise CacheGuardStop("logger_disabled", "graph_builder WARNING would not be emitted")
    if archive_state(cache_dir)["present"]:
        raise CacheGuardStop("h0_cache_not_absent", str(cache_dir))
    handler = LatchHandler(str(out_dir))
    builder.LOGGER.addHandler(handler)
    try:
        fields = builder.load_or_build_cached_graph(nh_gdf, cache_dir=cache_dir)
    finally:
        builder.LOGGER.removeHandler(handler)
    if handler.records or handler.write_failed:
        raise CacheGuardStop("h0_warning_latched", json.dumps(handler.records)[:300])
    builder.validate_graph_metadata(*fields)
    st = archive_state(cache_dir)
    if not st["present"]:
        raise CacheGuardStop("h0_archive_missing", str(cache_dir))
    if st["files"] != [ARCHIVE_NAME] or "unreadable" in st:
        raise CacheGuardStop("h0_archive_unclean", json.dumps(st)[:300])
    fingerprint = builder.compute_graph_fingerprint(nh_gdf)
    if st["fingerprint"] != fingerprint or st["cache_version"] != builder.CACHE_VERSION:
        raise CacheGuardStop("h0_fingerprint_mismatch", "")
    return {"archive": st, "fingerprint": fingerprint, "cache_version": builder.CACHE_VERSION,
            "nodes": len(fields[2])}
