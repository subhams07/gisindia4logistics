"""Thin filesystem layer so cgroup/sysfs operations can be faulted in tests."""
import os
import re

from .errors import InjectedFault


class RealFs:
    def read(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            chunks = []
            while True:
                b = os.read(fd, 1 << 16)
                if not b:
                    break
                chunks.append(b)
        finally:
            os.close(fd)
        return b"".join(chunks).decode("ascii", "strict")

    def write(self, path, text):
        data = text.encode("ascii")
        fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            n = os.write(fd, data)
        finally:
            os.close(fd)
        if n != len(data):
            raise OSError("short write to %s" % path)

    def mkdir(self, path):
        os.mkdir(path, 0o755)

    def rmdir(self, path):
        os.rmdir(path)

    def listdir(self, path):
        return os.listdir(path)

    def exists(self, path):
        return os.path.exists(path)


class FaultFs:
    """Wraps another fs; raises InjectedFault when (op, path-suffix) is armed.

    `armed` maps (op, substring-of-path) -> label. "" matches any path; "re:<regex>" is a regex.
    """

    def __init__(self, inner, armed):
        self.inner = inner
        self.armed = dict(armed)
        self.log = []

    def _maybe(self, op, path):
        for (aop, suffix), label in self.armed.items():
            if aop == op and ((suffix.startswith("re:") and re.search(suffix[3:], path)) or
                              (not suffix.startswith("re:") and suffix in path)):
                self.log.append((op, path, label))
                raise InjectedFault(label)

    def read(self, path):
        self._maybe("read", path)
        return self.inner.read(path)

    def write(self, path, text):
        self._maybe("write", path)
        self.log.append(("write", path, text))
        return self.inner.write(path, text)

    def mkdir(self, path):
        self._maybe("mkdir", path)
        return self.inner.mkdir(path)

    def rmdir(self, path):
        self._maybe("rmdir", path)
        return self.inner.rmdir(path)

    def listdir(self, path):
        self._maybe("listdir", path)
        return self.inner.listdir(path)

    def exists(self, path):
        return self.inner.exists(path)
