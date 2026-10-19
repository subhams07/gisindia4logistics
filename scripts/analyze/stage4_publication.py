"""Validated multi-file publication with backups and verified rollback.

Individual replacements become visible through os.replace, without fsync or
power-loss durability. Readers can see a mixed generation during publication.
The manifest is replaced last. This is not a dataset transaction.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile

from scripts.analyze.producer_io import sha256_file

ARTIFACT_NAMES = (
    "nh_district_travel_time_summary.csv",
    "nh_district_travel_time_summary.parquet",
    "nh_district_port_matrix.csv",
    "district_freight_modal_split.csv",
    "district_port_hinterland_catchment.csv",
    "major_ports_isochrones.geojson",
    "mmlp_isochrones.geojson",
)
PUBLICATION_NAMES = (*ARTIFACT_NAMES, "producer_limitations.json", "producer_manifest.json")


class PublicationError(RuntimeError):
    """Publication failed; consult the persisted receipt for rollback status."""


def _replace_copy(source, target):
    fd, name = tempfile.mkstemp(dir=target.parent, prefix="." + target.name + ".")
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _receipt(path, receipt):
    # Receipts live only in the dedicated backup folder, not the dataset.
    content = json.dumps(receipt, indent=2, allow_nan=False) + "\n"
    fd, name = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    os.close(fd)
    temporary = Path(name)
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def promote_staged_outputs(stage_dir, target_dir, backup_dir, *, validate):
    """Require a successful validator before any target mutation.

    ``validate`` receives the analysis directory and returns a list of issues.
    The caller must supply all semantic/source/consumer validation, not just
    schema checks. Existing backup directories are never reused. No caller or
    concurrent process may mutate either generation during this operation.
    """
    stage, target, backup = (Path(value).resolve() for value in (stage_dir, target_dir, backup_dir))
    roots = (stage, target, backup)
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for i, a in enumerate(roots) for b in roots[i + 1:]):
        raise ValueError("Stage, target and backup roots must be disjoint")
    if not callable(validate):
        raise ValueError("A validation callback is required")
    if backup.exists():
        raise ValueError("Backup directory already exists")
    for name in PUBLICATION_NAMES:
        path = stage / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("Incomplete or unsafe staging generation")
        existing = target / name
        if existing.is_symlink() or (existing.exists() and not existing.is_file()):
            raise ValueError("Unsafe existing publication target")
    initial_stage = {name: sha256_file(stage / name) for name in PUBLICATION_NAMES}
    issues = validate(stage)
    if not isinstance(issues, list) or issues:
        raise ValueError("Staged validation did not succeed")
    if any(sha256_file(stage / name) != initial_stage[name] for name in PUBLICATION_NAMES):
        raise ValueError("Staging generation changed during validation")
    prior = {name: sha256_file(target / name) if (target / name).exists() else None
             for name in PUBLICATION_NAMES}
    journal = {"status": "backing_up", "prior_hashes": prior,
               "staged_hashes": initial_stage, "published": [], "attempting": None,
               "rollback_errors": [],
               "atomic_dataset_transaction": False}
    receipt_path = backup / "publication_receipt.json"
    backup.mkdir(parents=True, exist_ok=False)
    # Complete and verify every backup before creating or changing targets.
    try:
        _receipt(receipt_path, journal)
        for name, previous_hash in prior.items():
            if previous_hash is not None:
                journal["attempting"] = name
                _receipt(receipt_path, journal)
                shutil.copyfile(target / name, backup / name)
                if sha256_file(backup / name) != previous_hash:
                    raise PublicationError("Backup verification failed before publication")
        journal.update(status="backed_up", attempting=None)
        _receipt(receipt_path, journal)
    except BaseException as error:
        journal.update(status="not_published", attempting=None)
        receipt_error = False
        try:
            _receipt(receipt_path, journal)
        except BaseException:
            receipt_error = True
        if not isinstance(error, Exception):
            raise
        suffix = "; receipt unavailable" if receipt_error else ""
        raise PublicationError("Backup failed; not_published" + suffix) from error
    try:
        journal["status"] = "publishing"
        _receipt(receipt_path, journal)
        target.mkdir(parents=True, exist_ok=True)
        for name in PUBLICATION_NAMES:
            if sha256_file(stage / name) != initial_stage[name]:
                raise PublicationError("Staging generation changed during publication")
            journal["attempting"] = name
            _receipt(receipt_path, journal)
            _replace_copy(stage / name, target / name)
            if sha256_file(target / name) != initial_stage[name]:
                raise PublicationError("Published file failed hash verification")
            journal["published"].append(name)
            journal["attempting"] = None
            _receipt(receipt_path, journal)
        if any(sha256_file(target / name) != initial_stage[name] for name in PUBLICATION_NAMES):
            raise PublicationError("Published generation failed final hash verification")
        journal["status"] = "published"
        _receipt(receipt_path, journal)
        return journal
    except BaseException as error:
        journal.update(status="rolling_back", attempting=None)
        try:
            _receipt(receipt_path, journal)
        except BaseException:
            pass  # Receipt availability must never prevent restoration.
        # Restore the entire prior generation, not merely files replaced so far.
        # Withdraw the new manifest first; skip already-correct locked files.
        for name, previous_hash in reversed(list(prior.items())):
            path = target / name
            try:
                actual = sha256_file(path) if path.exists() else None
                if actual != previous_hash:
                    if previous_hash is None:
                        path.unlink(missing_ok=True)
                    else:
                        if sha256_file(backup / name) != previous_hash:
                            raise PublicationError("Backup changed before rollback")
                        _replace_copy(backup / name, path)
                actual = sha256_file(path) if path.exists() else None
                if actual != previous_hash:
                    raise PublicationError("Rollback hash verification failed")
            except BaseException:
                # A copy can raise after replacement (e.g. temp cleanup). Judge
                # restoration by observed content, not the copy return alone.
                try:
                    actual = sha256_file(path) if path.exists() else None
                    if actual != previous_hash:
                        journal["rollback_errors"].append(name)
                except BaseException:
                    journal["rollback_errors"].append(name)
        journal["rollback_errors"].sort(key=PUBLICATION_NAMES.index)
        journal["status"] = "rollback_incomplete" if journal["rollback_errors"] else "restored"
        receipt_error = False
        try:
            _receipt(receipt_path, journal)
        except BaseException:
            receipt_error = True
        if not isinstance(error, Exception):
            raise
        suffix = "; receipt unavailable" if receipt_error else ""
        raise PublicationError("Publication failed; " + journal["status"] + suffix) from error
