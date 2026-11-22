"""refresh_version_manifest.py — Bring data/version_manifest.json up to date.

The API reports data_version, api_version and per-dataset feature counts from
this file. This script recounts every listed dataset from its file, sets
api_version to the package version and data_version to --data-version, adds
the village layer, and stamps generated_at_utc. Other fields (sources,
licences, legal text) are kept.

Usage: python scripts/clean/refresh_version_manifest.py --data-version 2026.11
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import pathlib
import sys

import pandas as pd
import pyarrow.parquet as pq

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from gisindia4logistics import __version__  # noqa: E402

MANIFEST = ROOT / "data" / "version_manifest.json"
VILLAGES = {
    "vintage": "Survey of India village boundaries (27 states via the SoI portal, 9 via the National Water Data Portal)",
    "source": "Survey of India",
    "path": "data/administrative/villages_parquet",
}


def count(path: str) -> int:
    target = ROOT / path
    files = sorted(glob.glob(str(target / "*.parquet"))) if target.is_dir() else sorted(glob.glob(str(target)))
    if not files:
        raise SystemExit(f"no file for {path}")
    total = 0
    for f in files:
        if f.endswith(".parquet"):
            total += pq.read_metadata(f).num_rows
        elif f.endswith(".csv"):
            total += len(pd.read_csv(f))
        elif f.endswith((".geojson", ".gpkg")):
            import pyogrio
            total += pyogrio.read_info(f)["features"]
        else:
            raise SystemExit(f"cannot count {f}")
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-version", required=True, help="release label, e.g. 2026.11")
    args = ap.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["data_version"] = args.data_version
    manifest["api_version"] = __version__
    manifest["generated_at_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["datasets"].setdefault("villages", dict(VILLAGES))
    for entry in manifest["datasets"].values():
        entry["feature_count"] = count(entry["path"])
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{MANIFEST}: data {args.data_version}, api {__version__}, {len(manifest['datasets'])} datasets")


if __name__ == "__main__":
    main()
