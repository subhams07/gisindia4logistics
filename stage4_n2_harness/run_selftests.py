"""Runs the self-tests: python3.11 -I -B -u run_selftests.py [module_substring ...]
Writes results JSON to scratch/results-<ts>.json. Skips are reported separately (non-coverage)."""
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


class Result(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.rows = []

    def _row(self, test, status, extra=""):
        self.rows.append({"test": test.id().split("tests.")[-1], "status": status, "extra": extra})

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


def main(argv):
    args = argv[1:]
    report_out = None
    if "--report-out" in args:
        i = args.index("--report-out")
        report_out = args[i + 1]
        del args[i:i + 2]
    pats = args
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    tdir = os.path.join(HERE, "tests")
    for name in sorted(os.listdir(tdir)):
        if name.startswith("test_") and name.endswith(".py") and (not pats or any(p in name for p in pats)):
            suite.addTests(loader.loadTestsFromName("tests." + name[:-3]))
    runner = unittest.TextTestRunner(verbosity=2, resultclass=Result, stream=sys.stderr)
    t0 = time.time()
    res = runner.run(suite)
    out = {"ran": res.testsRun, "failures": len(res.failures), "errors": len(res.errors),
           "skipped": len(res.skipped), "seconds": round(time.time() - t0, 1), "rows": res.rows}
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    path = os.path.join(HERE, "results", "results-%d.json" % int(t0))
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    if report_out:
        from n2h import ledger
        body = {"kind": "selftest_report", "harness_sha": ledger.harness_hash(os.path.join(HERE, "n2h")),
                "synthetic_mock": True, "ran": out["ran"], "failures": out["failures"], "errors": out["errors"],
                "skipped": out["skipped"], "run_id": str(int(t0)), "note": "SYNTHETIC SELF-TEST REPORT, NOT AN APPROVAL"}
        ledger.write_receipt(report_out, body)
    print("RESULT ran=%d fail=%d err=%d skip=%d secs=%.1f file=%s" % (
        out["ran"], out["failures"], out["errors"], out["skipped"], out["seconds"], path))
    return 0 if res.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
