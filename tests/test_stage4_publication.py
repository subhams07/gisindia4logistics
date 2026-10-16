import json

import pytest

from scripts.analyze import stage4_publication as publication
from scripts.analyze.producer_io import sha256_file


def make_generation(root, prefix, names=publication.PUBLICATION_NAMES):
    root.mkdir(parents=True)
    for name in names:
        (root / name).write_bytes((prefix + name).encode())


def hashes(root):
    return {name: sha256_file(root / name) if (root / name).exists() else None
            for name in publication.PUBLICATION_NAMES}


def test_success_manifest_last_and_verified_backups(tmp_path, monkeypatch):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    make_generation(target, "old")
    prior = hashes(target)
    original, order = publication._replace_copy, []
    def tracked(source, destination):
        order.append(destination.name)
        original(source, destination)
    monkeypatch.setattr(publication, "_replace_copy", tracked)
    result = publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert result["status"] == "published"
    assert result["atomic_dataset_transaction"] is False
    assert order == list(publication.PUBLICATION_NAMES)
    assert hashes(target) == hashes(stage)
    assert hashes(backup) == prior
    assert json.loads((backup / "publication_receipt.json").read_text())["status"] == "published"


@pytest.mark.parametrize("existing_manifest", [True, False])
@pytest.mark.parametrize("fail_index", [0, 3, 7])
def test_complete_rollback_on_replace_failure(tmp_path, monkeypatch, existing_manifest, fail_index):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    make_generation(target, "old", publication.PUBLICATION_NAMES if existing_manifest else publication.ARTIFACT_NAMES)
    prior = hashes(target)
    original = publication._replace_copy
    count = 0
    def interrupted(source, destination):
        nonlocal count
        count += 1
        if count == fail_index + 1:
            raise PermissionError("locked target")
        original(source, destination)
    monkeypatch.setattr(publication, "_replace_copy", interrupted)
    with pytest.raises(publication.PublicationError, match="restored"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert hashes(target) == prior
    receipt = json.loads((backup / "publication_receipt.json").read_text())
    assert receipt["status"] == "restored" and receipt["rollback_errors"] == []
    assert sorted(path.name for path in target.iterdir()) == sorted(name for name, value in prior.items() if value)


def test_empty_prior_generation_restores_absence(tmp_path, monkeypatch):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    original = publication._replace_copy
    def interrupted(source, destination):
        if destination.name == publication.ARTIFACT_NAMES[2] and source.parent == stage:
            raise PermissionError("locked")
        original(source, destination)
    monkeypatch.setattr(publication, "_replace_copy", interrupted)
    with pytest.raises(publication.PublicationError, match="restored"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert all(value is None for value in hashes(target).values())


@pytest.mark.parametrize("validation_result", [["bad hash"], None, True])
def test_validation_failure_changes_nothing(tmp_path, validation_result):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    make_generation(target, "old")
    prior = hashes(target)
    with pytest.raises(ValueError):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: validation_result)
    assert hashes(target) == prior and not backup.exists()


def test_changed_stage_during_validation_rejected(tmp_path):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    def mutate(path):
        (path / publication.ARTIFACT_NAMES[0]).write_bytes(b"changed")
        return []
    with pytest.raises(ValueError, match="changed"):
        publication.promote_staged_outputs(stage, target, backup, validate=mutate)
    assert not target.exists() and not backup.exists()


def test_unsafe_or_incomplete_inputs_rejected(tmp_path):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    for args in ((stage, stage, backup), (stage, stage / "child", backup)):
        with pytest.raises(ValueError, match="disjoint"):
            publication.promote_staged_outputs(*args, validate=lambda path: [])
    (stage / "producer_manifest.json").unlink()
    with pytest.raises(ValueError, match="Incomplete"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert not target.exists()


def test_existing_backup_never_overwritten(tmp_path):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    make_generation(backup, "precious")
    before = hashes(backup)
    with pytest.raises(ValueError, match="already exists"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert hashes(backup) == before and not target.exists()


def test_incomplete_rollback_never_claims_restored(tmp_path, monkeypatch):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    make_generation(target, "old")
    original = publication._replace_copy
    def partly_locked(source, destination):
        if source.parent == backup or destination.name == publication.ARTIFACT_NAMES[3]:
            raise PermissionError("locked")
        original(source, destination)
    monkeypatch.setattr(publication, "_replace_copy", partly_locked)
    with pytest.raises(publication.PublicationError, match="rollback_incomplete"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    journal = json.loads((backup / "publication_receipt.json").read_text())
    assert journal["status"] == "rollback_incomplete"
    assert journal["rollback_errors"] == list(publication.ARTIFACT_NAMES[:3])


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupt_rolls_back_complete_generation(tmp_path, monkeypatch, interruption):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    make_generation(target, "old")
    prior = hashes(target)
    original = publication._replace_copy
    def interrupted(source, destination):
        if source.parent == stage and destination.name == publication.ARTIFACT_NAMES[3]:
            raise interruption()
        original(source, destination)
    monkeypatch.setattr(publication, "_replace_copy", interrupted)
    with pytest.raises(interruption):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert hashes(target) == prior
    assert json.loads((backup / "publication_receipt.json").read_text())["status"] == "restored"


def test_receipt_records_attempt_before_each_replacement(tmp_path, monkeypatch):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    original = publication._replace_copy
    def tracked(source, destination):
        receipt = json.loads((backup / "publication_receipt.json").read_text())
        assert receipt["status"] == "publishing"
        assert receipt["attempting"] == destination.name
        original(source, destination)
    monkeypatch.setattr(publication, "_replace_copy", tracked)
    publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])


def test_receipt_failure_after_rollback_preserves_error_class(tmp_path, monkeypatch):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    make_generation(target, "old")
    prior = hashes(target)
    original_copy, original_receipt = publication._replace_copy, publication._receipt
    def interrupted(source, destination):
        if source.parent == stage and destination.name == publication.ARTIFACT_NAMES[3]:
            raise PermissionError("locked")
        original_copy(source, destination)
    def locked_receipt(path, receipt):
        if receipt["status"] == "restored":
            raise OSError("receipt locked")
        original_receipt(path, receipt)
    monkeypatch.setattr(publication, "_replace_copy", interrupted)
    monkeypatch.setattr(publication, "_receipt", locked_receipt)
    with pytest.raises(publication.PublicationError, match="restored.*receipt"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert hashes(target) == prior


def test_broken_target_symlink_rejected(tmp_path):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    target.mkdir()
    link = target / publication.ARTIFACT_NAMES[0]
    try:
        link.symlink_to(tmp_path / "nonexistent")
    except OSError:
        pytest.skip("Symlink creation requires privileges on this Windows host")
    with pytest.raises(ValueError, match="Unsafe"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert link.is_symlink() and not backup.exists()


def test_atomic_receipt_failure_preserves_prior_json(tmp_path, monkeypatch):
    receipt = tmp_path / "receipt.json"
    publication._receipt(receipt, {"status": "old"})
    def locked(*args):
        raise PermissionError("locked receipt")
    monkeypatch.setattr(publication.os, "replace", locked)
    with pytest.raises(PermissionError):
        publication._receipt(receipt, {"status": "new"})
    assert json.loads(receipt.read_text()) == {"status": "old"}
    assert list(tmp_path.iterdir()) == [receipt]


@pytest.mark.parametrize("failure", ["changed_stage", "corrupt_copy", "final_receipt"])
def test_late_failures_restore_whole_generation(tmp_path, monkeypatch, failure):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    make_generation(stage, "new")
    make_generation(target, "old")
    prior = hashes(target)
    original_copy, original_receipt = publication._replace_copy, publication._receipt
    def disrupted_copy(source, destination):
        original_copy(source, destination)
        if source.parent == stage and destination.name == publication.ARTIFACT_NAMES[0]:
            if failure == "changed_stage":
                (stage / publication.ARTIFACT_NAMES[2]).write_bytes(b"changed")
            elif failure == "corrupt_copy":
                destination.write_bytes(b"corrupt")
    def interrupted_receipt(path, value):
        if failure == "final_receipt" and value["status"] == "published":
            raise OSError("late receipt failure")
        original_receipt(path, value)
    monkeypatch.setattr(publication, "_replace_copy", disrupted_copy)
    monkeypatch.setattr(publication, "_receipt", interrupted_receipt)
    with pytest.raises(publication.PublicationError, match="restored"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert hashes(target) == prior


# --- Nine-file generation: the limitations sidecar is protected and precedes the manifest ---

SIDECAR, MANIFEST = "producer_limitations.json", "producer_manifest.json"
NINE = (*publication.ARTIFACT_NAMES, SIDECAR, MANIFEST)  # Independent of the constant under test.


def hashes9(root):
    return {name: sha256_file(root / name) if (root / name).exists() else None for name in NINE}


def prior_generation(root, sidecar_present):
    names = tuple(n for n in NINE if sidecar_present or n != SIDECAR)
    root.mkdir(parents=True)
    for name in names:
        (root / name).write_bytes(("old" + name).encode())


def staged(root, names=NINE):
    root.mkdir(parents=True)
    for name in names:
        (root / name).write_bytes(("new" + name).encode())


def assert_restored(target, backup, prior):
    assert hashes9(target) == prior
    receipt = json.loads((backup / "publication_receipt.json").read_text())
    assert receipt["status"] == "restored" and receipt["rollback_errors"] == []
    # Backups equal the prior generation; files absent before have no backup.
    assert hashes9(backup) == prior


def test_publication_names_are_nine_with_sidecar_before_manifest():
    assert publication.PUBLICATION_NAMES == NINE
    assert publication.PUBLICATION_NAMES[-2:] == (SIDECAR, MANIFEST)


@pytest.mark.parametrize("sidecar_present", [True, False])
def test_stage_without_sidecar_rejected_before_any_target_mutation(tmp_path, sidecar_present):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    staged(stage, tuple(n for n in NINE if n != SIDECAR))
    prior_generation(target, sidecar_present)
    prior = hashes9(target)
    with pytest.raises(ValueError, match="Incomplete"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert hashes9(target) == prior and not backup.exists()


@pytest.mark.parametrize("sidecar_present", [True, False])
@pytest.mark.parametrize("mode", ["manifest_replace_fails", "manifest_corrupt_after_replace"])
def test_manifest_stage_failures_restore_whole_generation_including_sidecar(tmp_path, monkeypatch, sidecar_present, mode):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    staged(stage)
    prior_generation(target, sidecar_present)
    prior = hashes9(target)
    original, observed = publication._replace_copy, []

    def directed(source, destination):
        if source.parent == stage and destination.name == MANIFEST:
            # The new sidecar is already live when the manifest step runs.
            observed.append((target / SIDECAR).read_bytes() == (stage / SIDECAR).read_bytes())
            if mode == "manifest_replace_fails":
                raise PermissionError("locked manifest")
            original(source, destination)
            destination.write_bytes(b"corrupt")
            return
        original(source, destination)
    monkeypatch.setattr(publication, "_replace_copy", directed)
    with pytest.raises(publication.PublicationError, match="restored"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert observed == [True]
    assert_restored(target, backup, prior)


@pytest.mark.parametrize("sidecar_present", [True, False])
def test_final_published_receipt_failure_restores_sidecar_generation(tmp_path, monkeypatch, sidecar_present):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    staged(stage)
    prior_generation(target, sidecar_present)
    prior = hashes9(target)
    original = publication._receipt

    def late_failure(path, value):
        if value["status"] == "published":
            raise OSError("late receipt failure")
        original(path, value)
    monkeypatch.setattr(publication, "_receipt", late_failure)
    with pytest.raises(publication.PublicationError, match="restored"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert_restored(target, backup, prior)


def test_symlinked_sidecar_target_rejected_before_mutation(tmp_path):
    stage, target, backup = (tmp_path / name for name in ("stage", "target", "backup"))
    staged(stage)
    target.mkdir()
    link = target / SIDECAR
    try:
        link.symlink_to(tmp_path / "nonexistent")
    except OSError:
        pytest.skip("Symlink creation requires privileges on this Windows host")
    with pytest.raises(ValueError, match="Unsafe"):
        publication.promote_staged_outputs(stage, target, backup, validate=lambda path: [])
    assert link.is_symlink() and not backup.exists()
