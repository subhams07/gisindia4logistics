"""Mock cgroup tree + overlay fs for sampler/containment logic tests. Real owned sleeper processes
provide pidfds; kernel files are replaced by files in a private scratch tree."""
import os
import subprocess
import sys

from n2h import cg as cgmod
from n2h import pidfd as pidfd_mod
from n2h import policy as policy_mod
from n2h import telemetry as tel
from n2h.fsio import RealFs
from tests import helpers as H

MEMINFO = "MemTotal: 16000000 kB\nMemAvailable: %d kB\nSwapTotal: 0 kB\nCommitLimit: 8000000 kB\nCommitted_AS: 1000000 kB\n"
STAT = ("total_cache 4096\ntotal_rss 8192\ntotal_shmem 0\ntotal_mapped_file 0\ntotal_dirty 0\n"
        "total_writeback 0\ntotal_inactive_file 1024\n")


class OverlayFs(RealFs):
    def __init__(self):
        self.over = {}
        self.raise_on = {}

    def read(self, path):
        if path in self.over:
            return self.over[path]
        for sub, exc in self.raise_on.items():
            if sub in path:
                raise exc
        return super().read(path)


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class MockWorld:
    def __init__(self, scr, policy=None):
        self.scr = scr
        self.root = scr.mkdir("cgroot")
        self.mem_root = self.root + "/memory"
        self.fs = OverlayFs()
        self.procs = []
        self.layout = cgmod.Layout("n2-mock", {"memory": "/ctrl", "pids": "/", "v2": "/"}, cg_root=self.root)
        self.policy = policy or policy_mod.synthetic_test_policy(attempt_memory_limit_bytes=192 << 20)
        mdir, pdir, udir = self.layout.abs["memory"], self.layout.abs["pids"], self.layout.abs["v2"]
        for d in (mdir, pdir, udir):
            os.makedirs(d, exist_ok=True)
        w(self.mem_root + "/memory.limit_in_bytes", "9223372036854771712\n")
        w(self.mem_root + "/memory.usage_in_bytes", "400000000\n")
        w(self.mem_root + "/ctrl/memory.limit_in_bytes", "14000000000\n")
        w(self.mem_root + "/ctrl/memory.usage_in_bytes", "100000000\n")
        self.set_mem(usage=50 << 20, peak=60 << 20, limit=192 << 20)
        w(pdir + "/pids.current", "1\n")
        w(pdir + "/pids.max", "48\n")
        w(pdir + "/pids.peak", "1\n")
        w(pdir + "/pids.events", "max 0\n")
        w(udir + "/cgroup.events", "populated 1\nfrozen 0\n")
        self.set_meminfo(8 << 30)

    def set_mem(self, usage, peak, limit, failcnt=0, oom_kill=0, under_oom=0, stat=STAT):
        d = self.layout.abs["memory"]
        for name, val in (("memory.usage_in_bytes", usage), ("memory.max_usage_in_bytes", peak),
                          ("memory.limit_in_bytes", limit), ("memory.failcnt", failcnt),
                          ("memory.kmem.usage_in_bytes", 1000), ("memory.kmem.max_usage_in_bytes", 2000),
                          ("memory.memsw.usage_in_bytes", usage), ("memory.memsw.max_usage_in_bytes", peak),
                          ("memory.memsw.limit_in_bytes", limit)):
            w(d + "/" + name, "%d\n" % val)
        w(d + "/memory.stat", stat)
        w(d + "/memory.oom_control", "oom_kill_disable 0\nunder_oom %d\noom_kill %d\n" % (under_oom, oom_kill))

    def set_meminfo(self, avail_bytes):
        self.fs.over["/proc/meminfo"] = MEMINFO % (avail_bytes // 1024)

    def set_listing(self, pids):
        txt = "".join("%d\n" % p for p in pids)
        for h in self.layout.join_order:
            w(self.layout.abs[h] + "/cgroup.procs", txt)

    def attempt_cgroup_text(self):
        return "4:memory:%s\n8:pids:%s\n0::%s\n" % (self.layout.rel["memory"], self.layout.rel["pids"], self.layout.rel["v2"])

    def outside_cgroup_text(self):
        return "4:memory:/other\n8:pids:/\n0::/\n"

    def spawn(self, code="import time; time.sleep(60)"):
        p = subprocess.Popen([H.PY, "-c", code], stdout=subprocess.PIPE)
        self.procs.append(p)
        return p

    def contain_overlay(self, pid, text=None):
        text = text or self.attempt_cgroup_text()
        self.fs.over["/proc/%d/cgroup" % pid] = text
        for tid in os.listdir("/proc/%d/task" % pid):
            self.fs.over["/proc/%d/task/%s/cgroup" % (pid, tid)] = text

    def member(self, popen, role="direct", contain=True):
        if contain:
            self.contain_overlay(popen.pid)
        return pidfd_mod.open_verified(popen.pid, self.layout, self.fs, role, cg_check=contain)

    def telemetry(self, direct, mono=None, real=None, ns_init=None):
        ctx = tel.SampleCtx(self.fs, self.layout, direct, self.policy, self.mem_root, "/ctrl", self.scr.root,
                            baseline={"bash_usage_excluding_attempt_bytes": 100000000 - 50 * 1024 * 1024})
        ctx.ns_init = ns_init
        if ns_init is not None:
            ctx.members[ns_init.pid] = ns_init
        kw = {"persist": False}
        if mono:
            kw["clock_mono"] = mono
        if real:
            kw["clock_real"] = real
        return tel.Telemetry(ctx, os.devnull, **kw)

    def close(self):
        for p in self.procs:
            try:
                p.kill()
            except OSError:
                pass
            p.wait()
            if p.stdout:
                p.stdout.close()


_ = sys
