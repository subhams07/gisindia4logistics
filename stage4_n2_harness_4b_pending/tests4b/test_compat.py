"""Dependency compatibility record: the exact versions the M05-M07 tests ran against (results/venv_freeze.txt)."""
import json
import os
import re
import sys
import unittest
from importlib import metadata

from tests4b import common as C

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Versions observed on the original Windows checkpoint (operator correction 5): NOT the versions used here.
WINDOWS_OBSERVED = {"geopandas": "1.1.4", "pandas": "3.0.5", "shapely": "2.1.2", "pyarrow": "25.0.1"}


class Compat(unittest.TestCase):
    def freeze(self):
        out = {}
        for ln in open(os.path.join(HERE, "results", "venv_freeze.txt")):
            m = re.match(r"^([A-Za-z0-9_.\-]+)==(\S+)$", ln.strip())
            if m:
                out[m.group(1).lower().replace("_", "-")] = m.group(2)
        return out

    def test_4B_installed_versions_equal_the_recorded_freeze(self):
        frozen = self.freeze()
        self.assertGreater(len(frozen), 40)
        actual = {}
        for name, ver in frozen.items():
            actual[name] = metadata.version(name)
            self.assertEqual(actual[name], ver, name)
        key = {k: actual[k] for k in ("numpy", "pandas", "geopandas", "shapely", "pyarrow", "pyproj", "scipy", "pyogrio")}
        rec = {"python": sys.version.split()[0], "tested_against": key,
               "windows_observed": WINDOWS_OBSERVED,
               "differs_from_windows": {k: {"here": actual[k], "windows": v} for k, v in WINDOWS_OBSERVED.items()
                                        if actual[k] != v}}
        self.assertEqual(set(rec["differs_from_windows"]), set(WINDOWS_OBSERVED), "all four differ from the Windows record")
        with open(os.path.join(HERE, "results", "compat-4b.json"), "w") as f:
            json.dump(rec, f, indent=1, sort_keys=True)
        # the interpreter really is the project venv, without system site packages
        self.assertTrue(sys.prefix.endswith("/.venv"))
        C.assert_modules_in_repo(self)


if __name__ == "__main__":
    unittest.main()
