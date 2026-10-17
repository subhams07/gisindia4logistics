"""Scratch manifests (M11). They are never publication manifests and grant no authority."""
import json
import os

from . import ledger
from .errors import RootsStop

FORBIDDEN_NAMES = ("producer_manifest.json", "producer_limitations.json")
SCRATCH_NAME = "n2_scratch_manifest.json"


def write_scratch_manifest(path, scratch_base, files, repo_root=None):
    """Write an unambiguous scratch manifest. Refuses publication-manifest names and any path
    outside the private scratch base (or inside the repository)."""
    name = os.path.basename(path)
    if name in FORBIDDEN_NAMES or name.startswith("producer_") or name != SCRATCH_NAME:
        raise RootsStop("manifest_name_not_scratch", name)
    real = os.path.realpath(path)
    base = os.path.realpath(scratch_base).rstrip("/") + "/"
    if not real.startswith(base):
        raise RootsStop("manifest_outside_scratch", path)
    if repo_root and real.startswith(os.path.realpath(repo_root).rstrip("/") + "/"):
        raise RootsStop("manifest_inside_repository", path)
    rows = []
    for f in sorted(files):
        rows.append({"path": f, "sha256": ledger.sha256_file(f), "bytes": os.path.getsize(f)})
    doc = {"kind": "n2_scratch_manifest", "publication": False, "grants_authority": False,
           "note": "scratch evidence only; not a producer/publication manifest", "files": rows}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    os.write(fd, json.dumps(doc, sort_keys=True).encode("ascii"))
    os.close(fd)
    return doc


def verify_logs(terminal_body, slot_root):
    """Compare the hashes/sizes recorded in a terminal receipt with the files on disk."""
    problems = []
    for name, meta in sorted(terminal_body.get("logs", {}).items()):
        p = os.path.join(slot_root, name)
        if not os.path.isfile(p):
            problems.append((name, "missing"))
            continue
        if os.path.getsize(p) != meta["bytes"]:
            problems.append((name, "size"))
        if ledger.sha256_file(p) != meta["sha256"]:
            problems.append((name, "sha256"))
    return problems
