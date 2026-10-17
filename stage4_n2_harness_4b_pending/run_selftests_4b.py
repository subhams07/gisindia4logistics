"""4b suite runner (venv interpreter):
    .venv/bin/python -I -B -u run_selftests_4b.py [--results-dir DIR] [module_substring ...]

The runner re-executes itself inside `unshare --mount` and mounts an empty read-only tmpfs over <repo>/data (and
<repo>/outputs when it exists), so that ANY read of repository data by any library (GDAL/pyogrio, pyarrow, plain
open) fails; the Python audit hook stays as a secondary record. Children of the suite inherit the masked view.
The outer process checks the repository (git status, HEAD, no outputs/ directory) before and after. It refuses
to start when stale /tmp/n2-4b-* directories exist. Never run it concurrently with the stdlib suite."""
import glob
import json
import os
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = "/home/user/gisindia4logistics"
MASKED = (REPO + "/data", REPO + "/outputs")
SENTINELS = (REPO + "/data/administrative/india_districts_lgd.geojson", REPO + "/data/roads/india_nh_network.parquet")


class Result(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.rows = []

    def _row(self, test, status, extra=""):
        self.rows.append({"test": test.id().split("tests4b.")[-1], "status": status, "extra": extra})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._row(test, "PASS")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._row(test, "FAIL", str(err[1])[:300])

    def addError(self, test, err):
        super().addError(test, err)
        self._row(test, "ERROR", repr(err[1])[:300])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._row(test, "SKIP", reason)


def _audit_hook(event, args):
    if event in ("open", "os.listdir", "os.scandir") and args and isinstance(args[0], (str, bytes, os.PathLike)):
        try:
            p = os.path.realpath(os.fsdecode(args[0]))
        except (TypeError, ValueError):
            return
        if p == REPO + "/data" or p.startswith(REPO + "/data/"):
            sys._n2_data_opens.append((event, p))


def _git(*a):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, check=False).stdout.decode()


def stale_dirs():
    return sorted(glob.glob("/tmp/n2-4b-*"))


def split_args(argv):
    results_dir, pats, masked, exists, i = os.path.join(HERE, "results"), [], False, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--results-dir":
            results_dir = argv[i + 1]
            i += 2
        elif a == "--masked":
            masked = True
            i += 1
        elif a == "--exists":
            exists.append(argv[i + 1])
            i += 2
        else:
            pats.append(a)
            i += 1
    return results_dir, pats, masked, exists


def check_interpreter():
    if sys.flags.optimize != 0 or sys.flags.isolated != 1 or sys.flags.dont_write_bytecode != 1:
        sys.stderr.write("run with -I -B (never -O)\n")
        return 3
    if not sys.prefix.startswith(REPO + "/.venv"):
        sys.stderr.write("not the project venv: sys.prefix=%s\n" % sys.prefix)
        return 3
    return 0


def outer(argv):
    rc = check_interpreter()
    if rc:
        return rc
    stale = stale_dirs()
    if stale:
        sys.stderr.write("REFUSED: stale scratch directories from an earlier run exist (remove them first): %s\n" % stale)
        return 3
    before = (_git("status", "--porcelain"), _git("rev-parse", "HEAD"), os.path.exists(REPO + "/outputs"))
    exists = [p for p in SENTINELS if os.path.lexists(p)]  # lstat only
    cmd = ["/usr/bin/unshare", "--mount", "--propagation", "private", "--", sys.executable, "-I", "-B", "-u",
           os.path.abspath(__file__), "--masked"] + [x for p in exists for x in ("--exists", p)] + argv
    rc = subprocess.run(cmd, check=False).returncode
    after = (_git("status", "--porcelain"), _git("rev-parse", "HEAD"), os.path.exists(REPO + "/outputs"))
    print("REPO_UNCHANGED %s (status empty=%s, HEAD same=%s, outputs/ absent=%s)" % (
        before == after and before[0] == "" and not after[2], before[0] == "" and after[0] == "", before[1] == after[1], not after[2]))
    if before != after or after[0] != "" or after[2]:
        return rc or 1
    if stale_dirs():
        sys.stderr.write("stale scratch directories left behind: %s\n" % stale_dirs())
        return rc or 1
    return rc


def mask_repo_data():
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
    done = []
    for d in MASKED:
        if not os.path.isdir(d):
            continue
        # MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC
        if libc.mount(b"tmpfs", d.encode(), b"tmpfs", 1 | 2 | 4 | 8, b"size=4k,mode=0555") != 0:
            raise OSError(ctypes.get_errno(), "mask mount failed for " + d)
        if os.listdir(d) != []:
            raise OSError("mask is not empty for " + d)
        done.append(d)
    return done


def inner(argv):
    rc = check_interpreter()
    if rc:
        return rc
    results_dir, pats, _masked, exists = split_args(argv)
    sys._n2_masked = mask_repo_data()
    sys._n2_existed_unmasked = exists
    import scripts.clean.graph_builder as gb
    if not os.path.realpath(gb.__file__).startswith(REPO + "/"):
        sys.stderr.write("producer modules do not resolve inside the repository\n")
        return 3
    sys._n2_data_opens = []
    sys._n2_audit_installed = True
    sys.addaudithook(_audit_hook)
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    tdir = os.path.join(HERE, "tests4b")
    for name in sorted(os.listdir(tdir)):
        if name.startswith("test_") and name.endswith(".py") and (not pats or any(p in name for p in pats)):
            suite.addTests(loader.loadTestsFromName("tests4b." + name[:-3]))
    runner = unittest.TextTestRunner(verbosity=2, resultclass=Result, stream=sys.stderr)
    t0 = time.time()
    res = runner.run(suite)
    out = {"ran": res.testsRun, "failures": len(res.failures), "errors": len(res.errors), "skipped": len(res.skipped),
           "seconds": round(time.time() - t0, 1), "python": sys.version.split()[0], "masked": sys._n2_masked,
           "rows": res.rows}
    os.makedirs(results_dir, exist_ok=True)
    path = os.path.join(results_dir, "results-4b-%d.json" % int(t0))
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print("RESULT4B ran=%d fail=%d err=%d skip=%d secs=%.1f masked=%s file=%s" % (
        out["ran"], out["failures"], out["errors"], out["skipped"], out["seconds"], sys._n2_masked, path))
    return 0 if res.wasSuccessful() else 1


def main(argv):
    return inner(argv[1:]) if "--masked" in argv else outer(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
