"""S2-final: the national code closure of the REAL staged-generation entry on the real working tree.

No mocks, stubs, skips or synthetic entry. ``national_code_closure`` parses ``.py`` source text only: it never
imports or executes ``server`` (or any dependency), reads no ``.env``/JSON/TOML file and loads no data. The expected
facts below are derived from the actual code (AST/bytes), not from a guessed length or golden list. The synthetic
package-tree tests of the closure helper live in ``tests/test_producer_manifest.py`` and are unchanged.
"""
import ast
import builtins
import hashlib
from pathlib import Path
import re
import sys

import pytest

from scripts.analyze import producer_manifest as pm
from scripts.analyze.producer_io import sha256_file

ROOT = Path(__file__).resolve().parents[1]
assert Path(pm.__file__).resolve() == ROOT / "scripts" / "analyze" / "producer_manifest.py", "wrong worktree imported"
ENTRY = "scripts/analyze/stage4_generate.py"
REQUIRED = (
    ENTRY,
    "scripts/analyze/highway_pair_kernel.py",
    "scripts/analyze/nh_travel_matrix.py",
    "scripts/analyze/intermodal_cost_engine.py",
    "scripts/analyze/port_hinterland_model.py",
    "scripts/analyze/generate_isochrones.py",
    "scripts/analyze/isochrone_coverage.py",
    "scripts/clean/standardize.py",
    "scripts/clean/graph_builder.py",
    "server/__init__.py",
    "server/app.py",
    "server/services/__init__.py",
    "server/services/exceptions.py",
)


@pytest.fixture(scope="module")
def closure():
    return pm.national_code_closure(ROOT, ENTRY)


def imports(logical):
    tree = ast.parse((ROOT / logical).read_text(encoding="utf-8"))
    return [(node.module, tuple(alias.name for alias in node.names)) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]


def test_the_real_default_entry_is_the_staged_generator_and_the_default_call_is_equivalent(closure):
    assert pm.NATIONAL_CODE_ENTRY == ENTRY
    assert (ROOT / ENTRY).is_file()
    assert pm.national_code_closure(ROOT) == closure


def test_the_required_transitive_files_are_in_the_real_closure(closure):
    assert isinstance(closure, list)
    missing = [logical for logical in REQUIRED if logical not in closure]
    assert not missing, missing


def test_the_required_files_follow_from_actual_static_imports_not_from_a_guess():
    assert {"highway_pair_kernel", "nh_travel_matrix", "generate_isochrones", "intermodal_cost_engine", "port_hinterland_model"} <= {
        name for module, names in imports(ENTRY) if module == "scripts.analyze" for name in names}
    assert ("scripts.clean", ("graph_builder",)) in imports(ENTRY)
    # The kernel's error path is why the server package is reachable from a pure analytical pipeline.
    assert ("server.services.exceptions", ("DataIntegrityError",)) in imports("scripts/analyze/highway_pair_kernel.py")
    assert ("server.app", ("app",)) in imports("server/__init__.py")
    assert any(module == "server.services.exceptions" for module, _ in imports("server/services/__init__.py"))
    assert "from scripts.clean.standardize import" in (ROOT / "scripts/analyze/intermodal_cost_engine.py").read_text(encoding="utf-8")


def test_every_returned_path_is_a_unique_sorted_logical_posix_regular_py_file_inside_the_root(closure):
    assert closure == sorted(closure) and len(closure) == len(set(closure))
    resolved_root = ROOT.resolve()
    for logical in closure:
        assert isinstance(logical, str) and logical.endswith(".py")
        assert "\\" not in logical and ":" not in logical and not logical.startswith("/")
        assert not any(part in {"", ".", ".."} for part in logical.split("/"))
        assert not re.search(r"(?i)(/users/|/home/)", "/" + logical)
        path = ROOT / logical
        assert path.is_file()
        assert path.resolve().is_relative_to(resolved_root)
    assert pm.private_path_issues(closure) == []


def test_the_closure_is_deterministic_across_repeated_calls_and_root_spellings(closure):
    again = pm.national_code_closure(ROOT, ENTRY)
    spelled = pm.national_code_closure(str(ROOT / "scripts" / ".." ), ENTRY)
    assert again == closure and spelled == closure


def test_every_closure_file_parses_and_its_sha256_is_the_actual_byte_hash_including_the_entry(closure):
    hashes = {}
    for logical in closure:
        data = (ROOT / logical).read_bytes()
        ast.parse(data.decode("utf-8"))
        hashes[logical] = sha256_file(ROOT / logical)
        assert hashes[logical] == hashlib.sha256(data).hexdigest() and re.fullmatch(r"[0-9a-f]{64}", hashes[logical])
    assert ENTRY in hashes  # The real entry itself is part of the hashed code set.
    assert set(hashes) == set(closure)


def test_computing_the_closure_neither_imports_server_nor_opens_anything_but_py_files(monkeypatch):
    before = {name for name in sys.modules if name.split(".")[0] == "server"}
    opened, real_open, real_read = [], builtins.open, Path.read_text

    def spy_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def spy_read(self, *args, **kwargs):
        opened.append(str(self))
        return real_read(self, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(Path, "read_text", spy_read)
    pm.national_code_closure(ROOT, ENTRY)
    monkeypatch.undo()
    inside = [name for name in opened if str(ROOT) in name]
    assert inside and all(name.endswith(".py") for name in inside)
    assert not any(name.endswith((".env", ".json", ".toml", ".yaml", ".yml", ".csv", ".parquet", ".geojson")) for name in inside)
    assert {name for name in sys.modules if name.split(".")[0] == "server"} == before  # Parsed as text, never executed.


def test_a_missing_entry_still_raises_so_this_gate_can_never_pass_vacuously():
    with pytest.raises(FileNotFoundError):
        pm.national_code_closure(ROOT, "scripts/analyze/no_such_stage4_entry.py")


def test_n1b_the_generator_imports_the_coverage_helper_directly_and_it_is_in_the_real_closure(closure):
    assert ("scripts.analyze", ("isochrone_coverage",)) in imports(ENTRY)  # A direct static import, found by AST, not guessed.
    assert "scripts/analyze/isochrone_coverage.py" in closure
