"""Only synthetic tmp_path artifacts; never national producers."""
import builtins
import copy
from dataclasses import asdict
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.analyze import producer_manifest as manifest
from scripts.analyze.freight_cost_core import FreightCostParameters


def configuration():
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA
    return {"units": {"distance": "km", "time": "hours", "cost": "INR/tonne"},
            "objectives": {"hub_selection": "minimum_lmv_time", "snap": "closest_compatible", "distance": "time_predecessor_path"},
            "speeds": {"motorway": 90., "trunk": 70., "primary": 55., "default": 50.},
            "feeder_speed_kmh": 35., "snap_limit_km": 150., "lmv_factor": 1.,
            "chunk_size": 32, "thresholds_hours": {"major_port": [4., 8., 12., 24.], "mmlp": [4., 8., 12.]}, "buffer_m": 8000., "simplify_m": 500.,
            "alpha": .85, "beta": 1.65, "capacities": {name: cap for name, cap in PORT_MATRIX_DATA.values()},
            "freight_parameters": asdict(FreightCostParameters())}


@pytest.fixture
def bundle(tmp_path):
    artifacts = {}
    for name in manifest.REQUIRED_ARTIFACTS:
        path = tmp_path / name
        if path.suffix == ".geojson":
            path.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
        elif path.suffix == ".parquet":
            pd.DataFrame({"value": [1., None]}).to_parquet(path, index=False)
        else:
            pd.DataFrame({"value": [1., None]}).to_csv(path, index=False)
        artifacts[name] = path
    value = manifest.build_manifest("provided-run", artifacts, {},
        {"fingerprint": "synthetic", "version": 1, "node_count": 2, "edge_count": 2, "selected_source": "roads/test.parquet"},
        configuration(), {}, {"elapsed_seconds": None, "peak_memory_bytes": None})
    path = tmp_path / manifest.MANIFEST_FILENAME
    manifest.atomic_write_manifest(value, path)
    return path, value, artifacts


def test_manifest_roundtrip_portable_deterministic(bundle, tmp_path):
    path, value, _ = bundle
    assert manifest.validate_manifest(path, tmp_path) == []
    text = path.read_text()
    assert str(tmp_path) not in text and "NaN" not in text
    assert value["resources"]["peak_memory_bytes"] is None
    assert value["artifacts"][manifest.REQUIRED_ARTIFACTS[0]]["rows"] == 2
    manifest.atomic_write_manifest(value, path)
    assert path.read_text() == text


@pytest.mark.parametrize("alpha,beta", [(0., 1.5), (1.2, 0.), (0., 0.)])
def test_zero_gravity_exponents_are_valid_explicit_configuration(alpha, beta):
    config = configuration()
    config.update(alpha=alpha, beta=beta)
    assert manifest.validate_config(config) == []


@pytest.mark.parametrize("field,value", [("path", "../outside.csv"), ("path", "C:/private.csv"), ("bytes", True), ("rows", 9), ("columns", ["other"]), ("type", "geojson"), ("sha256", "0" * 64)])
def test_corrupt_artifact_metadata(bundle, tmp_path, field, value):
    path, document, _ = bundle
    document["artifacts"][manifest.REQUIRED_ARTIFACTS[0]][field] = value
    manifest.atomic_write_manifest(document, path)
    assert manifest.validate_manifest(path, tmp_path)


def test_hash_corruption(bundle, tmp_path):
    path, _, artifacts = bundle
    artifacts[manifest.REQUIRED_ARTIFACTS[0]].write_text("value\n22\n")
    assert any("mismatch" in v for v in manifest.validate_manifest(path, tmp_path))


@pytest.mark.parametrize("mutation", ["unknown", "missing", "config", "graph", "resource", "code", "input"])
def test_schema_corruption(bundle, tmp_path, mutation):
    path, value, _ = bundle
    if mutation == "unknown":
        value["artifacts"]["other.csv"] = {}
    elif mutation == "missing":
        value["artifacts"].pop(manifest.REQUIRED_ARTIFACTS[0])
    elif mutation == "config":
        value["config"]["lmv_factor"] = .8
    elif mutation == "graph":
        value["graph"]["selected_source"] = "../outside"
    elif mutation == "resource":
        value["resources"]["elapsed_seconds"] = -1
    elif mutation == "code":
        value["code_hashes"] = {"../outside.py": "0" * 64}
    else:
        value["inputs"] = {"/private": {"bytes": 0, "sha256": "0" * 64}}
    manifest.atomic_write_manifest(value, path)
    assert manifest.validate_manifest(path, tmp_path)


@pytest.mark.parametrize("text", ['{"schema_version":1,"schema_version":1}', '{"x":NaN}', '[]'])
def test_duplicate_nonfinite_nonobject(tmp_path, text):
    path = tmp_path / "manifest.json"
    path.write_text(text)
    assert manifest.validate_manifest(path, tmp_path)


def test_writer_failure_preserves_target_and_cleans(bundle, monkeypatch):
    path, value, _ = bundle
    before = path.read_bytes()
    def fail(*args):
        raise OSError("synthetic replace failure")
    monkeypatch.setattr(manifest.os, "replace", fail)
    with pytest.raises(OSError):
        manifest.atomic_write_manifest(value, path)
    assert path.read_bytes() == before
    assert not list(path.parent.glob(".*.tmp"))
    value["resources"]["elapsed_seconds"] = float("nan")
    with pytest.raises(ValueError):
        manifest.atomic_write_manifest(value, path)
    assert path.read_bytes() == before


def test_optional_provenance_roots(bundle, tmp_path):
    path, value, _ = bundle
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.csv").write_text("x\n1\n")
    value["inputs"] = {"input.csv": {"sha256": manifest.sha256_file(source / "input.csv"), "bytes": (source / "input.csv").stat().st_size}}
    value["code_hashes"] = {"input.csv": manifest.sha256_file(source / "input.csv")}
    manifest.atomic_write_manifest(value, path)
    assert manifest.validate_manifest(path, tmp_path, source_root=source, code_root=source) == []
    (source / "input.csv").write_text("x\n2\n")
    assert len(manifest.validate_manifest(path, tmp_path, source_root=source, code_root=source)) == 2


def test_traversal_never_reads_outside(bundle, tmp_path, monkeypatch):
    path, value, _ = bundle
    value["artifacts"][manifest.REQUIRED_ARTIFACTS[0]]["path"] = "../outside.csv"
    value["inputs"] = {"../outside.csv": {"sha256": "0" * 64, "bytes": 0}}
    manifest.atomic_write_manifest(value, path)
    original = manifest._metadata
    def guarded(target):
        assert target.resolve().is_relative_to(tmp_path.resolve())
        assert target.name != manifest.REQUIRED_ARTIFACTS[0]
        return original(target)
    monkeypatch.setattr(manifest, "_metadata", guarded)
    assert manifest.validate_manifest(path, tmp_path, source_root=tmp_path)


def test_staging_paths_and_caller_mutation(bundle, tmp_path):
    _, original, artifacts = bundle
    staging = tmp_path / "nested-staging"
    staging.mkdir()
    new_artifacts = {}
    for name, path in artifacts.items():
        target = staging / name
        target.write_bytes(path.read_bytes())
        new_artifacts[name] = target
    config = configuration()
    value = manifest.build_manifest("provided-run", new_artifacts, {}, original["graph"], config, {}, original["resources"])
    config["lmv_factor"] = .8
    assert value["config"]["lmv_factor"] == 1.
    assert value == original


@pytest.mark.parametrize("field", ["alpha", "beta"])
@pytest.mark.parametrize("value", [-1., True, False, float("nan"), float("inf"), -float("inf"), "0", 10**400])
def test_invalid_gravity_exponents_return_config_issues(field, value):
    config = configuration()
    config[field] = value
    assert manifest.validate_config(config) == [f"config: invalid {field}"]


@pytest.mark.parametrize("section,field,issue", [
    ("config", "alpha", "config: invalid alpha"),
    ("resources", "elapsed_seconds", "manifest: invalid measured resources"),
])
def test_oversized_integer_bundle_returns_issue_and_builder_valueerror(bundle, tmp_path, section, field, issue):
    path, document, artifacts = bundle
    document[section][field] = 10**400
    manifest.atomic_write_manifest(document, path)
    assert manifest.validate_manifest(path, tmp_path) == [issue]
    with pytest.raises(ValueError, match=issue):
        manifest.build_manifest("provided-run", artifacts, {}, document["graph"], document["config"], {}, document["resources"])


@pytest.mark.parametrize("name", ["major_ports_isochrones.geojson", "mmlp_isochrones.geojson"])
@pytest.mark.parametrize("root", [[], "invalid", None, {}])
def test_invalid_geojson_root_returns_fixed_issue_and_builder_valueerror(bundle, tmp_path, name, root):
    path, document, artifacts = bundle
    artifacts[name].write_text(json.dumps(root))
    assert manifest.validate_manifest(path, tmp_path) == [
        f"artifact {name}: invalid metadata or unavailable artifact"]
    with pytest.raises(ValueError, match="Expected GeoJSON FeatureCollection"):
        manifest.build_manifest("provided-run", artifacts, {}, document["graph"], document["config"], {}, document["resources"])


@pytest.mark.parametrize("mutation", ["partial", "unknown", "internal_columns"])
def test_capacity_names_must_exactly_match_all_display_names(bundle, tmp_path, mutation):
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA
    path, document, artifacts = bundle
    config = document["config"]
    if mutation == "partial":
        config["capacities"].pop("Mumbai Port")
    elif mutation == "unknown":
        config["capacities"]["Unknown Port"] = config["capacities"].pop("Mumbai Port")
    else:
        config["capacities"] = {column: cap for column, (_, cap) in PORT_MATRIX_DATA.items()}
    assert manifest.validate_config(config) == ["config: invalid capacities"]
    manifest.atomic_write_manifest(document, path)
    assert manifest.validate_manifest(path, tmp_path) == ["config: invalid capacities"]
    with pytest.raises(ValueError, match="config: invalid capacities"):
        manifest.build_manifest("provided-run", artifacts, {}, document["graph"], config, {}, document["resources"])


@pytest.mark.parametrize("value", [0., -1., True, False, float("nan"), float("inf"), -float("inf"), "65", 10**400])
def test_capacities_require_finite_positive_scalars(value):
    config = configuration()
    config["capacities"]["Mumbai Port"] = value
    assert manifest.validate_config(config) == ["config: invalid capacities"]


# --- Opt-in national profile and code closure (synthetic only; additive to the generic API) ---

SHA = "a" * 64
HIGHWAY = "roads/india_nh_network.parquet"


def national_document(bundle):
    from scripts.clean import graph_builder as builder
    import hashlib
    _, document, _ = bundle
    doc = copy.deepcopy(document)
    doc["run_id"] = "synthetic-national-1"
    doc["inputs"] = {name: {"sha256": SHA, "bytes": 1} for name in (*manifest.NATIONAL_FIXED_INPUTS, HIGHWAY)}
    doc["graph"] = {"fingerprint": {"cache_version": builder.CACHE_VERSION, "row_count": 3, "crs": "EPSG:7755",
        "snap_grid_meters": builder.SNAP_GRID_METERS, "bridge_max_meters": builder.BRIDGE_MAX_METERS,
        "bridge_speed": builder.BRIDGE_SPEED, "default_speed": builder.DEFAULT_SPEED,
        "speed_hash": hashlib.sha256(json.dumps(builder.SPEEDS, sort_keys=True).encode()).hexdigest(),
        "source_content_sha256": SHA}, "version": builder.CACHE_VERSION, "node_count": 10, "edge_count": 20, "selected_source": HIGHWAY}
    doc["code_hashes"] = {"scripts/analyze/stage4_generate.py": SHA}
    doc["resources"] = {"elapsed_seconds": 12.5, "peak_memory_bytes": 123456789}  # Synthetic fixture values.
    return doc


def test_complete_synthetic_national_manifest_passes_both_levels(bundle, tmp_path):
    doc = national_document(bundle)
    before = copy.deepcopy(doc)
    assert manifest.validate_national_profile(doc) == []
    assert doc == before
    path = tmp_path / "national" / manifest.MANIFEST_FILENAME
    path.parent.mkdir()
    manifest.atomic_write_manifest(doc, path)
    for name in manifest.REQUIRED_ARTIFACTS:
        (path.parent / name).write_bytes((tmp_path / name).read_bytes())
    assert manifest.validate_manifest(path, path.parent) == []


def test_geojson_selected_highway_is_also_valid(bundle):
    doc = national_document(bundle)
    doc["inputs"]["roads/india_nh_network.geojson"] = doc["inputs"].pop(HIGHWAY)
    doc["graph"]["selected_source"] = "roads/india_nh_network.geojson"
    assert manifest.validate_national_profile(doc) == []


def test_generic_manifest_stays_valid_but_fails_national_profile(bundle, tmp_path):
    path, document, _ = bundle
    assert manifest.validate_manifest(path, tmp_path) == []
    issues = "; ".join(manifest.validate_national_profile(document))
    for fragment in ("inputs must be exactly", "graph fingerprint must have exactly the nine", "code_hashes must be a nonempty",
                     "elapsed_seconds", "peak_memory_bytes", "graph version"):
        assert fragment in issues
    assert manifest.validate_national_profile(None) == ["national: manifest unavailable"]
    assert manifest.validate_national_profile([]) == ["national: manifest unavailable"]


def drop(section, key):
    return lambda d: d[section].pop(key)


def put(section, key, value):
    return lambda d: d[section].__setitem__(key, value)


def put_fp(key, value):
    return lambda d: d["graph"]["fingerprint"].__setitem__(key, value)


@pytest.mark.parametrize("mutate,fragment", [
    (drop("inputs", "demographic/district_population_estimates.csv"), "inputs must be exactly"),
    (drop("inputs", "roads/toll_plazas.csv"), "inputs must be exactly"),
    (put("inputs", "roads/india_nh_network.geojson", {"sha256": SHA, "bytes": 1}), "inputs must be exactly"),
    (put("inputs", "extra/padding.csv", {"sha256": SHA, "bytes": 1}), "inputs must be exactly"),
    (put("graph", "selected_source", "roads/india_nh_network.geojson"), "inputs must be exactly"),
    (put("graph", "selected_source", "roads/other.parquet"), "unsupported selected highway"),
    (put("graph", "selected_source", None), "unsupported selected highway"),
    (put_fp("cache_version", True), "fingerprint cache_version"),
    (put_fp("cache_version", 5), "fingerprint cache_version"),
    (put_fp("row_count", 0), "fingerprint row_count"),
    (put_fp("row_count", True), "fingerprint row_count"),
    (put_fp("crs", "EPSG:4326"), "fingerprint crs"),
    (put_fp("snap_grid_meters", 10.), "fingerprint snap_grid_meters"),
    (put_fp("snap_grid_meters", True), "fingerprint snap_grid_meters"),
    (put_fp("bridge_max_meters", 1.), "fingerprint bridge_max_meters"),
    (put_fp("bridge_speed", float("nan")), "fingerprint bridge_speed"),
    (put_fp("default_speed", "50"), "fingerprint default_speed"),
    (put_fp("speed_hash", "0" * 64), "fingerprint speed_hash"),
    (put_fp("source_content_sha256", "xyz"), "fingerprint source_content_sha256"),
    (put_fp("source_content_sha256", "A" * 64), "fingerprint source_content_sha256"),
    (put_fp("cache_policy", "x"), "nine supported fields"),
    (lambda d: d["graph"]["fingerprint"].pop("crs"), "nine supported fields"),
    (put("graph", "fingerprint", "synthetic"), "nine supported fields"),
    (put("graph", "version", 5), "graph version"),
    (put("graph", "version", "6"), "graph version"),
    (put("graph", "node_count", 0), "node and edge counts"),
    (put("graph", "edge_count", 0), "node and edge counts"),
    (put("graph", "node_count", True), "node and edge counts"),
    (put("resources", "elapsed_seconds", None), "elapsed_seconds"),
    (put("resources", "elapsed_seconds", 0), "elapsed_seconds"),
    (put("resources", "elapsed_seconds", -1.), "elapsed_seconds"),
    (put("resources", "elapsed_seconds", True), "elapsed_seconds"),
    (put("resources", "elapsed_seconds", float("inf")), "elapsed_seconds"),
    (put("resources", "elapsed_seconds", 10**400), "elapsed_seconds"),
    (put("resources", "peak_memory_bytes", None), "peak_memory_bytes"),
    (put("resources", "peak_memory_bytes", 0), "peak_memory_bytes"),
    (put("resources", "peak_memory_bytes", 1.5), "peak_memory_bytes"),
    (put("resources", "peak_memory_bytes", 2.0), "peak_memory_bytes"),
    (put("resources", "peak_memory_bytes", True), "peak_memory_bytes"),
    (lambda d: d.__setitem__("code_hashes", {}), "code_hashes must be a nonempty"),
    (lambda d: d.__setitem__("code_hashes", []), "code_hashes must be a nonempty"),
    (lambda d: d.__setitem__("run_id", "a b"), "invalid run_id"),
    (lambda d: d.__setitem__("run_id", "x" * 65), "invalid run_id"),
    (lambda d: d.__setitem__("run_id", ""), "invalid run_id"),
    (lambda d: d.__setitem__("run_id", None), "invalid run_id"),
    (lambda d: d.__setitem__("run_id", "run\n"), "invalid run_id"),
    (lambda d: d.__setitem__("run_id", "C:\\Users\\x"), "invalid run_id"),
])
def test_national_profile_rejects_each_tamper(bundle, mutate, fragment):
    doc = national_document(bundle)
    mutate(doc)
    assert fragment in "; ".join(manifest.validate_national_profile(doc))


@pytest.mark.parametrize("mutate", [
    lambda d: d["inputs"].__setitem__("C:/Users/x/a.csv", {"sha256": SHA, "bytes": 1}),
    lambda d: d["code_hashes"].__setitem__("/home/x/a.py", SHA),
    lambda d: d["code_hashes"].__setitem__("scripts\\Users\\a.py", SHA),
    lambda d: d["library_versions"].__setitem__("GEOS", "C:\\tools\\geos"),
    lambda d: d["config"].__setitem__("note", ["ok", {"deep": "/Users/x/y"}]),
    lambda d: d["config"]["units"].__setitem__("distance", "/etc/passwd"),
])
def test_national_profile_privacy_scan_is_recursive_and_does_not_echo(bundle, mutate):
    doc = national_document(bundle)
    mutate(doc)
    issues = manifest.validate_national_profile(doc)
    assert "national: absolute or private path-like value present" in issues
    assert not any("Users" in issue or "/home" in issue or "passwd" in issue for issue in issues)


def test_logical_paths_and_versions_pass_privacy_scan(bundle):
    doc = national_document(bundle)
    doc["inputs"]["roads/india_nh_network.parquet"]["note"] = "ignored"  # Unknown inner field is not a path.
    assert not [i for i in manifest.validate_national_profile(doc) if "private" in i]


def tree(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


ENTRY = "scripts/analyze/stage4_generate.py"


def test_closure_follows_packages_init_side_effects_relative_and_nested_imports(tmp_path):
    root = tree(tmp_path, {
        ENTRY: "import numpy\nfrom scripts.analyze import helper\n\ndef late():\n    from server.services.exceptions import Boom\n\ntry:\n    import scripts.clean.graph_builder\nexcept ImportError:\n    pass\nif True:\n    import mcp_server.tools\n",
        "scripts/analyze/helper.py": "from . import sibling\nfrom ..clean import extra\nraise SystemExit(7)\n",
        "scripts/analyze/sibling.py": "ATTR = 1\n",
        "scripts/clean/extra.py": "import os\n",
        "scripts/clean/graph_builder.py": "from scripts.analyze import ATTR\n",
        "server/__init__.py": "from server import app\n",
        "server/app.py": "",
        "server/services/__init__.py": "from .alpha import A\n",
        "server/services/alpha.py": "",
        "server/services/exceptions.py": "",
        "mcp_server/__init__.py": "",
        "mcp_server/tools.py": "",
        "unrelated/skipped.py": "",
    })
    closure = manifest.national_code_closure(root)
    assert closure == sorted(closure) == manifest.national_code_closure(root)
    assert closure == sorted([ENTRY, "scripts/analyze/helper.py", "scripts/analyze/sibling.py", "scripts/clean/extra.py",
        "scripts/clean/graph_builder.py", "server/__init__.py", "server/app.py", "server/services/__init__.py",
        "server/services/alpha.py", "server/services/exceptions.py", "mcp_server/__init__.py", "mcp_server/tools.py"])


def test_closure_namespace_packages_cycles_and_submodule_vs_attribute(tmp_path):
    root = tree(tmp_path, {
        ENTRY: "import scripts.analyze.ns.leaf\nfrom scripts.analyze import cyc_a, CONSTANT\n",
        "scripts/analyze/ns/leaf.py": "",
        "scripts/analyze/cyc_a.py": "from scripts.analyze import cyc_b\n",
        "scripts/analyze/cyc_b.py": "from scripts.analyze import cyc_a\n",
    })
    assert manifest.national_code_closure(root) == sorted([ENTRY, "scripts/analyze/ns/leaf.py",
        "scripts/analyze/cyc_a.py", "scripts/analyze/cyc_b.py"])


@pytest.mark.parametrize("text,error,match", [
    ("import scripts.analyze.nothere\n", ImportError, "Unresolvable"),
    ("import mcp_server.tools\n", ImportError, "Unresolvable"),
    ("from scripts.analyze.stage4_generate.x import y\n", ImportError, "Unresolvable"),
    ("import standardize\n", ImportError, "Ambiguous"),
    ("def f():\n    from standardize import norm\n", ImportError, "Ambiguous"),
    ("from .... import y\n", ImportError, "escapes"),
])
def test_closure_raises_on_missing_ambiguous_or_escaping_imports(tmp_path, text, error, match):
    root = tree(tmp_path, {ENTRY: text, "scripts/clean/standardize.py": ""})
    with pytest.raises(error, match=match):
        manifest.national_code_closure(root)


def test_closure_missing_default_or_custom_entry_raises_without_stub(tmp_path):
    root = tree(tmp_path, {"scripts/analyze/other.py": ""})
    with pytest.raises(FileNotFoundError):
        manifest.national_code_closure(root)
    with pytest.raises(FileNotFoundError):
        manifest.national_code_closure(root, "scripts/analyze/missing.py")
    assert manifest.national_code_closure(root, "scripts/analyze/other.py") == ["scripts/analyze/other.py"]
    with pytest.raises(ValueError):
        manifest.national_code_closure(root, "../escape.py")


def test_closure_reads_only_py_text_and_never_executes(tmp_path, monkeypatch):
    import sys
    root = tree(tmp_path, {ENTRY: "from scripts.analyze import marker\n", "scripts/analyze/marker.py": "raise SystemExit(9)\nimport marker_executed\n",
        ".env": "SECRET=1\n", "config.json": "{}", "pyproject.toml": "[x]\n", "scripts/analyze/data.json": "{}"})
    opened, before = [], set(sys.modules)
    real_open, real_read = builtins.open, Path.read_text

    def spy_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def spy_read(self, *args, **kwargs):
        opened.append(str(self))
        return real_read(self, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(Path, "read_text", spy_read)
    assert manifest.national_code_closure(root) == sorted([ENTRY, "scripts/analyze/marker.py"])
    monkeypatch.undo()
    mine = [name for name in opened if str(tmp_path) in name]
    assert mine and all(name.endswith(".py") for name in mine)
    assert "marker_executed" not in sys.modules and not {m for m in set(sys.modules) - before if m.startswith("scripts.analyze.marker")}


def test_national_profile_code_hashes_must_equal_closure_when_root_given(bundle, tmp_path):
    root = tree(tmp_path / "repo", {ENTRY: "from scripts.analyze import helper\n", "scripts/analyze/helper.py": ""})
    doc = national_document(bundle)
    doc["code_hashes"] = {path: SHA for path in manifest.national_code_closure(root)}
    assert manifest.validate_national_profile(doc, code_root=root) == []
    extra = copy.deepcopy(doc)
    extra["code_hashes"]["scripts/analyze/extra.py"] = SHA
    missing = copy.deepcopy(doc)
    missing["code_hashes"].pop("scripts/analyze/helper.py")
    for bad in (extra, missing):
        assert "national: code_hashes must equal the computed code closure" in manifest.validate_national_profile(bad, code_root=root)
    # Without code_root only well-formedness is checked; closure equality is deferred.
    assert manifest.validate_national_profile(extra) == [] and "deferred" in manifest.CODE_CLOSURE_DEFERRED
    # No default entry: unavailable, never a silent pass.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert manifest.validate_national_profile(doc, code_root=empty) == ["national: code closure unavailable"]


@pytest.mark.parametrize("field", ["snap_grid_meters", "bridge_max_meters", "bridge_speed", "default_speed"])
@pytest.mark.parametrize("value", [pytest.param(10**400, id="oversized"), pytest.param(-10**400, id="negative-oversized")])
def test_oversized_numeric_fingerprint_returns_issue_without_raising(bundle, tmp_path, field, value):
    doc = national_document(bundle)
    assert manifest.validate_national_profile(doc) == []  # Finite valid control.
    doc["graph"]["fingerprint"][field] = value
    # The generic layer accepts the fingerprint shape; only the national profile can reject it.
    path = tmp_path / "oversized" / manifest.MANIFEST_FILENAME
    path.parent.mkdir()
    manifest.atomic_write_manifest(doc, path)
    assert not [i for i in manifest._provenance(doc) if "graph" in i]
    assert manifest.validate_national_profile(doc) == [f"national: invalid graph fingerprint {field}"]


def test_generic_validation_unchanged_by_national_additions(bundle, tmp_path):
    path, document, artifacts = bundle
    document["resources"]["elapsed_seconds"] = 10**400
    manifest.atomic_write_manifest(document, path)
    assert manifest.validate_manifest(path, tmp_path) == ["manifest: invalid measured resources"]
    assert manifest.validate_national_profile(document)  # Returns issues; never raises on overflow.
