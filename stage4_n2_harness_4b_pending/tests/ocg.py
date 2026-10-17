"""Owned real cgroups for accounting probes: a unit-test-created cgroup set + a gated stdlib child."""
import os
import subprocess
import time

from n2h import cg as cgmod
from n2h.fsio import RealFs
from tests import helpers as H


class OwnedCg:
    def __init__(self, mem_limit=128 << 20, pids_max=32):
        self.fs = RealFs()
        self.name = "n2-ut-" + os.urandom(4).hex()
        self.lay = cgmod.Layout(self.name, cgmod.controller_cgroup_paths(self.fs), use_v2=False)
        cgmod.create_all(self.lay, self.fs)
        cgmod.set_limits(self.lay, mem_limit, pids_max, self.fs)
        self.child = None

    def spawn(self, code):
        """Child runs `code` (which may call wait_go()) with helper preamble."""
        pre = ("import sys, os, time\n"
               "def wait_go():\n"
               "    sys.stdin.readline()\n"
               "def say(s):\n"
               "    print(s, flush=True)\n")
        self.child = subprocess.Popen([H.PY, "-I", "-S", "-B", "-u", "-c", pre + code], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return self.child

    def join(self):
        cgmod.join_and_verify(self.lay, self.child.pid, self.fs)

    def go(self):
        self.child.stdin.write("go\n")
        self.child.stdin.flush()

    def readline(self, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            line = self.child.stdout.readline()
            if line:
                return line.strip()
        raise TimeoutError("child silent")

    def mem(self):
        from n2h import sysfs
        return sysfs.read_memory_fields(self.fs, self.lay.abs["memory"])

    def close(self):
        if self.child is not None:
            try:
                self.child.kill()
            except OSError:
                pass
            self.child.wait()
            for f in (self.child.stdin, self.child.stdout, self.child.stderr):
                if f:
                    f.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not cgmod.is_empty(self.lay, self.fs):
            time.sleep(0.02)
        cgmod.remove(self.lay, self.fs)

