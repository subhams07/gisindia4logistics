"""C5 root policy and CLI behaviour. Synthetic roots only; real audits run solely on EMPTY tmp_path trees."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.audit import audit_all as audit

SCRIPT = Path(audit.__file__).resolve()


def snapshot(root):
    return sorted(str(p) for p in Path(root).rglob("*"))


def layout(tmp_path):
    data = tmp_path / "data"
    (data / "analysis").mkdir(parents=True, exist_ok=True)
    return data, data / "analysis"


def runner(tmp_path, **kwargs):
    data, analysis = layout(tmp_path)
    options = dict(fast=True, source_root=data, artifact_root=analysis, cache_dir=tmp_path / "cache")
    options.update(kwargs)
    return audit.AuditRunner(**options)


def test_legacy_layout_with_nested_read_roots_is_accepted_and_creates_nothing(tmp_path):
    instance = runner(tmp_path, report_dir=tmp_path / "reports")
    assert instance.artifact_root == instance.source_root / "analysis"
    assert not (tmp_path / "cache").exists() and not (tmp_path / "reports").exists()


def test_default_cache_is_repo_outputs_cache_never_data_cache(tmp_path):
    data, _ = layout(tmp_path)
    before = (audit.REPO_ROOT / "outputs" / "cache").exists()
    instance = audit.AuditRunner(fast=True, source_root=data)
    assert instance.cache_dir == audit.REPO_ROOT / "outputs" / "cache" != data / "cache"
    assert (audit.REPO_ROOT / "outputs" / "cache").exists() is before


def violation_paths(tmp_path):
    data, analysis = layout(tmp_path)
    return {"equal_source": data, "inside_source": data / "cache", "inside_artifact": analysis / "deep" / "cache",
            "equal_artifact": analysis, "contains_source": tmp_path}


@pytest.mark.parametrize("case", ["equal_source", "inside_source", "inside_artifact", "equal_artifact", "contains_source"])
@pytest.mark.parametrize("kind", ["cache_dir", "report_dir"])
def test_write_root_overlapping_a_read_root_is_rejected_before_creating_anything(tmp_path, case, kind):
    data, analysis = layout(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        audit.AuditRunner(fast=True, source_root=data, artifact_root=analysis,
                          **{"cache_dir": tmp_path / "ok-cache", "report_dir": tmp_path / "ok-reports", kind: violation_paths(tmp_path)[case]})
    assert snapshot(tmp_path) == before


def test_separate_artifact_root_is_a_read_root_too(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    staged = tmp_path / "staged"
    staged.mkdir()
    for kind, bad in (("cache_dir", staged / "cache"), ("report_dir", staged), ("cache_dir", tmp_path)):
        with pytest.raises(ValueError):
            audit.AuditRunner(fast=True, source_root=data, artifact_root=staged, **{"cache_dir": tmp_path / "c", "report_dir": tmp_path / "r", kind: bad})
    audit.AuditRunner(fast=True, source_root=data, artifact_root=staged, cache_dir=tmp_path / "c", report_dir=tmp_path / "r")


@pytest.mark.parametrize("cache,report", [("same", "same"), ("both/inner", "both"), ("both", "both/inner")])
def test_cache_and_report_must_not_overlap_each_other(tmp_path, cache, report):
    data, analysis = layout(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        audit.AuditRunner(fast=True, source_root=data, artifact_root=analysis, cache_dir=tmp_path / cache, report_dir=tmp_path / report)
    assert snapshot(tmp_path) == before


def test_write_report_override_is_validated_before_mkdir(tmp_path):
    instance = runner(tmp_path)
    data = instance.source_root
    before = snapshot(tmp_path)
    for bad in (data / "reports", instance.artifact_root, data, tmp_path / "cache", tmp_path / "cache" / "inner"):
        with pytest.raises(ValueError):
            instance.write_report(bad)
    assert snapshot(tmp_path) == before
    report = instance.write_report(tmp_path / "reports")
    assert report == tmp_path / "reports" / "audit_report_fast.md" and report.exists()


def test_default_docs_only_for_the_legacy_layout_without_national_or_required_manifest(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    monkeypatch.setattr(audit, "REPO_ROOT", repo)
    data = tmp_path / "data"
    (data / "analysis").mkdir(parents=True)
    legacy = audit.AuditRunner(fast=True, source_root=data)
    assert legacy.cache_dir == repo / "outputs" / "cache"
    assert legacy.write_report() == repo / "docs" / "audit_report_fast.md"
    staged = tmp_path / "staged"
    staged.mkdir()
    for kwargs in ({"artifact_root": staged}, {"national": True}, {"require_manifest": True}):
        refused = audit.AuditRunner(fast=True, source_root=data, **kwargs)
        before = snapshot(tmp_path)
        with pytest.raises(ValueError, match="explicit report directory required"):
            refused.write_report()
        assert snapshot(tmp_path) == before
    explicit = audit.AuditRunner(fast=True, source_root=data, artifact_root=staged, report_dir=tmp_path / "chosen")
    assert explicit.write_report() == tmp_path / "chosen" / "audit_report_fast.md"
    assert explicit.write_report(tmp_path / "override") == tmp_path / "override" / "audit_report_fast.md"


def test_national_implies_required_manifest_and_names_full_and_fast_reports_apart(tmp_path):
    instance = runner(tmp_path, national=True, report_dir=tmp_path / "reports", fast=False)
    assert instance.national is True and instance.require_manifest is True
    assert instance.write_report().name == "audit_report.md"


def make_alias(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
        return "symlink"
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return "junction"
        except (OSError, ImportError, AttributeError):
            pass
    return None


@pytest.mark.parametrize("sub", ["", "inner/deeper"])
def test_alias_to_a_read_root_is_resolved_through_the_deepest_existing_ancestor(tmp_path, sub):
    data, analysis = layout(tmp_path)
    alias = tmp_path / "alias"
    if make_alias(alias, data) is None:
        pytest.skip("Neither symlink nor junction creation is possible on this host")
    before = snapshot(data)
    with pytest.raises(ValueError):
        audit.AuditRunner(fast=True, source_root=data, artifact_root=analysis, cache_dir=alias / sub if sub else alias)
    with pytest.raises(ValueError):
        audit.AuditRunner(fast=True, source_root=data, artifact_root=analysis, cache_dir=tmp_path / "ok", report_dir=alias / sub if sub else alias)
    assert snapshot(data) == before


def test_junction_alias_on_windows_when_creatable(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows junctions only")
    try:
        import _winapi
    except ImportError:
        pytest.skip("_winapi unavailable")
    data, analysis = layout(tmp_path)
    junction = tmp_path / "junction"
    try:
        _winapi.CreateJunction(str(data), str(junction))
    except OSError:
        pytest.skip("Junction creation unavailable on this host")
    with pytest.raises(ValueError):
        audit.AuditRunner(fast=True, source_root=data, artifact_root=analysis, cache_dir=junction / "cache")


def test_cli_passes_national_and_report_dir_to_the_constructor_then_writes_without_arguments(tmp_path, monkeypatch):
    seen = {}

    class FakeRunner:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self):
            return [{"status": "PASS"}]

        def write_report(self):
            return tmp_path / "reports" / "x.md"
    monkeypatch.setattr(audit, "AuditRunner", FakeRunner)
    monkeypatch.setattr(audit.sys, "argv", ["audit_all.py", "--national", "--data-dir", str(tmp_path / "s"), "--analysis-dir", str(tmp_path / "a"),
                                            "--cache-dir", str(tmp_path / "c"), "--report-dir", str(tmp_path / "r")])
    audit.main()
    assert seen == {"fast": False, "require_manifest": True, "source_root": tmp_path / "s", "artifact_root": tmp_path / "a",
                    "cache_dir": tmp_path / "c", "national": True, "report_dir": tmp_path / "r"}


def run_cli(tmp_path, *arguments):
    environment = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    return subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, arguments)], cwd=tmp_path, env=environment,
                          capture_output=True, text=True, timeout=600)


def canonical_report_fingerprint():
    path = audit.REPO_ROOT / "docs" / "audit_report_fast.md"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def test_real_cli_subprocess_on_empty_trees_bootstraps_and_writes_only_the_chosen_report(tmp_path):
    source, artifacts = tmp_path / "empty-source", tmp_path / "empty-analysis"
    source.mkdir()
    artifacts.mkdir()
    before = canonical_report_fingerprint()
    done = run_cli(tmp_path, "--fast", "--data-dir", source, "--analysis-dir", artifacts, "--cache-dir", tmp_path / "cache", "--report-dir", tmp_path / "r")
    output = done.stdout + done.stderr
    assert done.returncode == 1, output[-2000:]
    assert "ModuleNotFoundError" not in output and "Traceback" not in done.stderr
    report = tmp_path / "r" / "audit_report_fast.md"
    assert report.exists() and "Data Audit Report" in report.read_text(encoding="utf-8") and "ModuleNotFoundError" not in report.read_text(encoding="utf-8")
    assert not (tmp_path / "cache").exists()
    assert canonical_report_fingerprint() == before


def test_real_cli_rejects_overlapping_roots_with_exit_2_and_creates_nothing(tmp_path):
    source, artifacts = tmp_path / "empty-source", tmp_path / "empty-analysis"
    source.mkdir()
    artifacts.mkdir()
    before = snapshot(tmp_path)
    done = run_cli(tmp_path, "--fast", "--data-dir", source, "--analysis-dir", artifacts, "--cache-dir", source / "cache", "--report-dir", tmp_path / "r")
    assert done.returncode == 2, done.stdout[-500:] + done.stderr[-500:]
    assert snapshot(tmp_path) == before and "== LGD" not in done.stdout


def test_real_cli_staged_run_without_report_dir_exits_2_and_writes_no_report(tmp_path):
    source, artifacts = tmp_path / "empty-source", tmp_path / "staged"
    source.mkdir()
    artifacts.mkdir()
    before = snapshot(tmp_path)
    canonical = canonical_report_fingerprint()
    done = run_cli(tmp_path, "--fast", "--data-dir", source, "--analysis-dir", artifacts, "--cache-dir", tmp_path / "cache")
    assert done.returncode == 2 and "explicit report directory required" in done.stderr
    assert snapshot(tmp_path) == before and canonical_report_fingerprint() == canonical


def graph_backed_bundle(tmp_path):
    """Canonical synthetic producer bundle (real graph builder) from the S1 oracle test module."""
    import contextlib
    import importlib.util
    import io
    spec = importlib.util.spec_from_file_location("s3_roots_oracle_fixture", Path(__file__).with_name("test_producer_graph_oracle.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with contextlib.redirect_stdout(io.StringIO()):
        source, artifacts, _unused_cache, _unused = module.build_bundle(tmp_path)
    return source, artifacts


@pytest.mark.parametrize("flags", [[], ["--require-manifest"], ["--national"]])
def test_real_full_cli_rejects_a_missing_report_dir_before_any_graph_cache_write(tmp_path, flags):
    source, artifacts = graph_backed_bundle(tmp_path)
    fresh_cache = tmp_path / "fresh-cache"
    before = snapshot(tmp_path)
    canonical = canonical_report_fingerprint()
    done = run_cli(tmp_path, *flags, "--data-dir", source, "--analysis-dir", artifacts, "--cache-dir", fresh_cache)
    assert done.returncode == 2 and "explicit report directory required" in done.stderr
    assert not fresh_cache.exists(), "rejected CLI created: " + str(list(fresh_cache.rglob("*")))
    assert "== LGD" not in done.stdout and "AUDIT:" not in done.stdout  # No audit work ran at all.
    assert snapshot(tmp_path) == before and canonical_report_fingerprint() == canonical


def test_real_full_cli_with_a_report_dir_still_runs_and_uses_the_cache_and_report_roots(tmp_path):
    source, artifacts = graph_backed_bundle(tmp_path)
    fresh_cache, reports = tmp_path / "fresh-cache", tmp_path / "reports"
    done = run_cli(tmp_path, "--data-dir", source, "--analysis-dir", artifacts, "--cache-dir", fresh_cache, "--report-dir", reports)
    assert done.returncode in (0, 1), done.stdout[-1500:] + done.stderr[-1500:]
    assert (reports / "audit_report.md").exists() and (fresh_cache / "canonical_nh_graph.npz").exists()


def test_real_cli_national_full_on_empty_trees_is_blocked_without_creating_a_cache(tmp_path):
    source, artifacts = tmp_path / "empty-source", tmp_path / "staged"
    source.mkdir()
    artifacts.mkdir()
    done = run_cli(tmp_path, "--national", "--data-dir", source, "--analysis-dir", artifacts, "--cache-dir", tmp_path / "cache", "--report-dir", tmp_path / "r")
    assert done.returncode == 1 and "ModuleNotFoundError" not in done.stdout + done.stderr
    assert not (tmp_path / "cache").exists()
    text = (tmp_path / "r" / "audit_report.md").read_text(encoding="utf-8")
    assert "blocked: national prerequisites" in text


@pytest.fixture(autouse=True, scope="module")
def _tiny_synthetic_graphs_are_supported():
    """Synthetic fixtures are far below the production component-size floor."""
    from scripts.analyze import highway_pair_kernel as _kernel
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_kernel, "MIN_SUPPORTED_COMPONENT_NODES", 1)
        from scripts.audit import producer_graph_oracle as _oracle
        patch.setattr(_oracle, "ORACLE_MIN_SUPPORTED_COMPONENT_NODES", 1)
        yield
