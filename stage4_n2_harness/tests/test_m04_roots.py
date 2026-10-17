"""M04: disjoint roots, symlink/mountpoint rejection, nonregular/swapped paths, no fallbacks."""
import inspect
import os
import subprocess
import unittest

from n2h import roots as R
from n2h.errors import RootsStop
from tests import helpers as H


class M04(unittest.TestCase):
    def setUp(self):
        self.scr = H.Scratch("m04")

    def tearDown(self):
        self.scr.cleanup()

    def test_M04_disjoint_roots(self):
        a, b = self.scr.mkdir("a"), self.scr.mkdir("b")
        out = R.resolve_roots(self.scr.root, control=a, logs=b)
        self.assertEqual(set(out), {"control", "logs"})
        inner = self.scr.mkdir("a", "inner")
        with self.assertRaises(RootsStop) as cm:
            R.resolve_roots(self.scr.root, control=a, logs=inner)
        self.assertEqual(cm.exception.code, "roots_not_disjoint")
        with self.assertRaises(RootsStop):
            R.resolve_roots(self.scr.root, control=a, logs=a)
        with self.assertRaises(RootsStop):
            R.resolve_roots(self.scr.root, control=a, logs=self.scr.root)  # parent contains child

    def test_M04_symlink_ancestor_and_leaf_rejected(self):
        real = self.scr.mkdir("real", "leaf")
        link_anc = self.scr.path("linkdir")
        os.symlink(self.scr.path("real"), link_anc)
        with self.assertRaises(RootsStop) as cm:
            R.check_root(link_anc + "/leaf", self.scr.root)
        self.assertEqual(cm.exception.code, "root_symlink_component")
        link_leaf = self.scr.path("leaflink")
        os.symlink(real, link_leaf)
        with self.assertRaises(RootsStop) as cm:
            R.check_root(link_leaf, self.scr.root)
        self.assertEqual(cm.exception.code, "root_symlink_component")
        f = self.scr.path("realfile")
        open(f, "w").write("x")
        fl = self.scr.path("filelink")
        os.symlink(f, fl)
        with self.assertRaises(RootsStop) as cm:
            R.file_identity(fl)
        self.assertEqual(cm.exception.code, "file_symlink")

    def test_M04_mountpoint_ancestor_rejected(self):
        mnt = self.scr.mkdir("mnt")
        self.scr.mkdir("mnt", "inner")
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "import subprocess\n"
            "from n2h import roots as R\n"
            "from n2h.errors import RootsStop\n"
            "subprocess.run(['mount','-t','tmpfs','tmpfs',%r],check=True)\n"
            "import os; os.mkdir(%r)\n"
            "try:\n R.check_root(%r, %r); print('NOT_REJECTED')\n"
            "except RootsStop as e: print('REJECTED', e.code)\n"
        ) % (H.HARNESS, mnt, mnt + "/x", mnt + "/x", self.scr.root)
        p = subprocess.run(["unshare", "--mount", "--", H.PY, "-I", "-B", "-c", script], capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(b"REJECTED root_mount_boundary", p.stdout)
        # the mount lived only in the unshared namespace
        self.assertFalse(os.path.exists(mnt + "/x"))

    def test_M04_sys_proc_dev_rejected(self):
        for p in ("/sys/fs/cgroup", "/proc/self", "/dev/shm", "/dev"):
            with self.assertRaises(RootsStop) as cm:
                R.check_root(p, "/")
            self.assertEqual(cm.exception.code, "root_forbidden_tree", p)
        # a symlink that resolves into /proc is rejected by resolution, not by name
        lk = self.scr.path("toproc")
        os.symlink("/proc", lk)
        with self.assertRaises(RootsStop):
            R.reject_forbidden(lk)

    def test_M04_missing_nonregular_swapped_paths(self):
        with self.assertRaises(RootsStop) as cm:
            R.file_identity(self.scr.path("absent"))
        self.assertEqual(cm.exception.code, "file_missing")
        d = self.scr.mkdir("adir")
        with self.assertRaises(RootsStop) as cm:
            R.file_identity(d)
        self.assertEqual(cm.exception.code, "file_not_regular")
        fifo = self.scr.path("fifo")
        os.mkfifo(fifo)
        with self.assertRaises(RootsStop) as cm:
            R.file_identity(fifo)
        self.assertEqual(cm.exception.code, "file_not_regular")
        f = self.scr.path("f")
        open(f, "w").write("one")
        ident = R.file_identity(f)
        fd = R.open_verified(f, ident)
        os.close(fd)
        # swap: different inode at the same path
        os.unlink(f)
        open(f, "w").write("two")
        with self.assertRaises(RootsStop) as cm:
            R.open_verified(f, ident)
        self.assertEqual(cm.exception.code, "file_identity_changed")
        # in-place rewrite changes mtime/ctime/size
        ident2 = R.file_identity(f)
        with open(f, "a") as fh:
            fh.write("more")
        with self.assertRaises(RootsStop):
            R.open_verified(f, ident2)
        # swapped for a symlink: O_NOFOLLOW refuses
        os.unlink(f)
        os.symlink(self.scr.path("fifo"), f)
        with self.assertRaises(RootsStop):
            R.open_verified(f, ident2)

    def test_M04_no_fallback_roots(self):
        with self.assertRaises(RootsStop) as cm:
            R.resolve_roots(self.scr.root)
        self.assertEqual(cm.exception.code, "no_roots_given")
        with self.assertRaises(RootsStop) as cm:
            R.resolve_roots(self.scr.root, control="", logs=self.scr.mkdir("x"))
        self.assertEqual(cm.exception.code, "root_missing")
        for fn in (R.check_root, R.resolve_roots):
            sig = inspect.signature(fn)
            for name, prm in sig.parameters.items():
                if name in ("path", "base"):
                    self.assertIs(prm.default, inspect.Parameter.empty, "%s.%s must have no default" % (fn.__name__, name))
        # nothing resolves to the repository / default caches / B0
        for bad in (H.REPO, os.path.expanduser("~/.cache"), H.REPO + "/outputs/cache"):
            with self.assertRaises(RootsStop):
                R.check_root(bad, self.scr.root, extra_forbidden=[H.REPO])


if __name__ == "__main__":
    unittest.main()
