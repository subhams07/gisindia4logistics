"""M11 for 4b: repository data is MASKED (empty read-only tmpfs in the suite's private mount namespace), so any read
by any library fails; the Python audit hook is a secondary record. The repository itself is checked by the outer runner."""
import os
import subprocess
import sys
import unittest

from tests4b import common as C

DATA = C.REPO + "/data"


class Preservation(unittest.TestCase):
    def test_M11_repository_data_is_masked_for_every_reader(self):
        self.assertTrue(getattr(sys, "_n2_audit_installed", False), "run through run_selftests_4b.py")
        self.assertIn(DATA, sys._n2_masked)
        self.assertEqual(os.listdir(DATA), [], "the mask is an empty directory")
        self.assertTrue(os.statvfs(DATA).f_flag & os.ST_RDONLY, "and it is a read-only mount")
        with self.assertRaises(OSError):
            open(DATA + "/x", "w")
        # CONTROL: the sentinels exist in the unmasked view (the outer runner lstat-ed them), yet every reader fails
        self.assertGreaterEqual(len(sys._n2_existed_unmasked), 1, "controls need existing sentinel files")
        import geopandas as gpd
        for p in sys._n2_existed_unmasked:
            self.assertFalse(os.path.exists(p), p)
            with self.assertRaises(Exception):  # noqa: B017 - GDAL/pyarrow/open each raise their own type
                (gpd.read_parquet if p.endswith(".parquet") else gpd.read_file)(p)
            with self.assertRaises(OSError):
                open(p, "rb")
        # a reader pointed at the masked directory itself finds nothing either
        self.assertEqual([n for n in os.listdir(DATA)], [])

    def test_M11_no_repository_data_opened_by_python_in_the_suite(self):
        self.assertEqual(sys._n2_data_opens, [], "secondary record: Python-level opens under repository data/")

    def test_M11_repository_working_tree_unchanged_outside_masked_dirs(self):
        def git(*a):
            return subprocess.run(["git", "-C", C.REPO, *a], capture_output=True, check=False).stdout.decode()
        self.assertEqual(git("status", "--porcelain", "--", ".", ":(exclude)data"), "")
        self.assertFalse(os.path.exists(C.REPO + "/outputs"))
        self.assertFalse(any(d == "__pycache__" and "stage4_n2_harness" in r for r, ds, _ in os.walk(C.HARNESS) for d in ds))


if __name__ == "__main__":
    unittest.main()
