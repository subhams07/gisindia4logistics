"""Strict, portable provenance for the seven Stage 4 derivatives.

Schema 1 configuration is explicit; no timestamps or resource measurements are
invented. Single-file atomic publication is not a seven-artifact transaction.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile

import pandas as pd

from scripts.analyze.producer_io import sha256_file

REQUIRED_ARTIFACTS = (
    "nh_district_travel_time_summary.csv",
    "nh_district_travel_time_summary.parquet",
    "nh_district_port_matrix.csv",
    "district_freight_modal_split.csv",
    "district_port_hinterland_catchment.csv",
    "major_ports_isochrones.geojson",
    "mmlp_isochrones.geojson",
)
MANIFEST_FILENAME = "producer_manifest.json"
VINTAGE_STATEMENT = "Recomputation does not upgrade source vintage, copyright or licensing claims."
SHA = re.compile(r"[0-9a-f]{64}\Z")


def _logical(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("Unsafe logical path")
    if PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute():
        raise ValueError("Absolute logical path")
    if any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError("Unsafe logical path")
    return name


def _strict(value):
    # Also rejects numpy scalars, Path objects and other accidental content.
    json.dumps(value, allow_nan=False)
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def read_manifest(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=_pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def _metadata(path):
    path = Path(path)
    result = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    if path.suffix == ".geojson":
        document = read_manifest(path)
        if (not isinstance(document, dict) or document.get("type") != "FeatureCollection"
                or not isinstance(document.get("features"), list)):
            raise ValueError("Expected GeoJSON FeatureCollection")
        result.update(type="geojson", features=len(document["features"]))
    else:
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        result.update(type="parquet" if path.suffix == ".parquet" else "csv",
                      rows=len(frame), columns=list(frame.columns))
    return result


def _number(value, *, positive=False):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        return False


def validate_config(config):
    """Return schema-1 config issues; unsupported/missing settings never default.

    Keys: units, objectives, speeds, feeder_speed_kmh, snap_limit_km,
    lmv_factor, chunk_size, thresholds_hours, buffer_m, simplify_m, alpha,
    beta, capacities, freight_parameters. thresholds_hours maps major_port and
    mmlp to their explicit positive ordered hours. Freight parameters are complete.
    """
    from dataclasses import fields
    from scripts.analyze.freight_cost_core import FreightCostParameters, resolve_parameters
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA

    expected = {"units", "objectives", "speeds", "feeder_speed_kmh", "snap_limit_km",
                "lmv_factor", "chunk_size", "thresholds_hours", "buffer_m", "simplify_m",
                "alpha", "beta", "capacities", "freight_parameters"}
    if not isinstance(config, dict) or set(config) != expected:
        return ["config: missing or unknown settings"]
    issues = []
    fixed = {"units": {"distance": "km", "time": "hours", "cost": "INR/tonne"},
             "objectives": {"hub_selection": "minimum_lmv_time", "snap": "closest_compatible",
                            "distance": "time_predecessor_path"},
             "speeds": {"motorway": 90.0, "trunk": 70.0, "primary": 55.0, "default": 50.0},
             "feeder_speed_kmh": 35.0, "snap_limit_km": 150.0, "lmv_factor": 1.0,
             "chunk_size": 32, "buffer_m": 8000.0, "simplify_m": 500.0}
    for key, value in fixed.items():
        if config[key] != value or (isinstance(value, (int, float)) and type(config[key]) not in (int, float)):
            issues.append(f"config: unsupported {key}")
    thresholds = config["thresholds_hours"]
    if (not isinstance(thresholds, dict) or set(thresholds) != {"major_port", "mmlp"}
            or any(not isinstance(values, list) or not values
                   or any(not _number(v, positive=True) for v in values)
                   or values != sorted(set(values)) for values in thresholds.values())):
        issues.append("config: invalid thresholds_hours")
    for key in ("alpha", "beta"):
        if not _number(config[key]):
            issues.append(f"config: invalid {key}")
    caps = config["capacities"]
    if (not isinstance(caps, dict) or set(caps) != {name for name, _ in PORT_MATRIX_DATA.values()}
            or any(not _number(v, positive=True) for v in caps.values())):
        issues.append("config: invalid capacities")
    parameters = config["freight_parameters"]
    try:
        if not isinstance(parameters, dict) or set(parameters) != {f.name for f in fields(FreightCostParameters)} or any(v is None for v in parameters.values()):
            raise ValueError("Incomplete parameters")
        resolve_parameters(parameters)
    except ValueError:
        issues.append("config: invalid freight_parameters")
    return issues


def _provenance(manifest):
    issues = validate_config(manifest.get("config"))
    expected_fields = {"schema_version", "run_id", "artifacts", "inputs", "graph", "config", "code_hashes", "resources", "library_versions", "vintage_statement"}
    if set(manifest) != expected_fields:
        issues.append("manifest: missing or unknown fields")
    if manifest.get("schema_version") != 1 or type(manifest.get("schema_version")) is not int:
        issues.append("manifest: unsupported schema")
    if not isinstance(manifest.get("run_id"), str) or not manifest["run_id"].strip():
        issues.append("manifest: invalid run_id")
    if manifest.get("vintage_statement") != VINTAGE_STATEMENT:
        issues.append("manifest: missing vintage statement")
    for section in ("inputs", "code_hashes"):
        mapping = manifest.get(section)
        if not isinstance(mapping, dict):
            issues.append(f"manifest: invalid {section}")
            continue
        for key, value in mapping.items():
            try:
                _logical(key)
                digest = value.get("sha256") if section == "inputs" and isinstance(value, dict) else value
                if not isinstance(digest, str) or not SHA.fullmatch(digest):
                    raise ValueError("Invalid hash")
                if section == "inputs" and (set(value) != {"sha256", "bytes"} or type(value["bytes"]) is not int or value["bytes"] < 0):
                    raise ValueError("Invalid input metadata")
            except (ValueError, TypeError):
                issues.append(f"manifest: invalid {section} entry")
    graph = manifest.get("graph")
    if (not isinstance(graph, dict) or set(graph) != {"fingerprint", "version", "node_count", "edge_count", "selected_source"}
            or not isinstance(graph.get("fingerprint"), (str, dict)) or not graph.get("fingerprint")
            or type(graph.get("version")) not in (int, str)
            or any(type(graph.get(k)) is not int or graph[k] < 0 for k in ("node_count", "edge_count"))):
        issues.append("manifest: invalid graph metadata")
    else:
        try:
            _logical(graph["selected_source"])
            if isinstance(graph["fingerprint"], dict):
                supported = {"cache_version", "row_count", "crs", "snap_grid_meters", "bridge_max_meters", "bridge_speed", "default_speed", "speed_hash", "source_content_sha256"}
                if set(graph["fingerprint"]) != supported:
                    raise ValueError("Unsupported graph fingerprint fields")
        except (ValueError, TypeError):
            issues.append("manifest: unsafe graph source")
    resources = manifest.get("resources")
    if not isinstance(resources, dict) or set(resources) != {"elapsed_seconds", "peak_memory_bytes"} or any(v is not None and not _number(v) for v in resources.values()):
        issues.append("manifest: invalid measured resources")
    versions = manifest.get("library_versions")
    if not isinstance(versions, dict) or set(versions) != {"pandas", "numpy", "scipy", "geopandas", "pyproj", "shapely", "GEOS"} or any(not isinstance(v, str) or not v for v in versions.values()):
        issues.append("manifest: invalid library versions")
    return issues


def build_manifest(run_id, artifacts, inputs, graph, config, code_hashes, resources) -> dict:
    if set(artifacts) != set(REQUIRED_ARTIFACTS):
        raise ValueError("Exactly seven required artifacts must be supplied")
    versions = {name: importlib.metadata.version(name) for name in ("pandas", "numpy", "scipy", "geopandas", "pyproj", "shapely")}
    import shapely
    versions["GEOS"] = shapely.geos_version_string
    manifest = {"schema_version": 1, "run_id": run_id,
                "artifacts": {name: {"path": name, **_metadata(path)} for name, path in sorted(artifacts.items())},
                "inputs": {_logical(name): {"sha256": sha256_file(path), "bytes": Path(path).stat().st_size} for name, path in sorted(inputs.items())},
                "graph": graph, "config": config, "code_hashes": code_hashes,
                "resources": resources, "library_versions": versions,
                "vintage_statement": VINTAGE_STATEMENT}
    _strict(manifest)
    issues = _provenance(manifest)
    if issues:
        raise ValueError("; ".join(issues))
    # Return an isolated plain-JSON copy, never references to caller dictionaries.
    return json.loads(json.dumps(manifest, allow_nan=False))


def atomic_write_manifest(manifest, path):
    payload = json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _contained(root, logical):
    target = (Path(root).resolve() / _logical(logical)).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        raise ValueError("Path escapes declared root")
    return target


def validate_manifest(path, artifact_root, *, source_root=None, code_root=None) -> list[str]:
    """Read only named artifacts; optional source/code roots verify provenance.

    Never follow manifest artifact paths outside artifact_root. Optional logical
    inputs/code paths are constrained to their explicit roots, including symlinks.
    """
    try:
        manifest = read_manifest(path)
        _strict(manifest)
        if not isinstance(manifest, dict):
            raise ValueError("Manifest must be an object")
    except (OSError, ValueError, TypeError):
        return ["manifest: unreadable or invalid strict JSON"]
    issues = _provenance(manifest)
    entries = manifest.get("artifacts")
    if not isinstance(entries, dict) or set(entries) != set(REQUIRED_ARTIFACTS):
        return issues + ["artifacts: missing or unknown filenames"]
    for name in REQUIRED_ARTIFACTS:
        entry = entries[name]
        try:
            if not isinstance(entry, dict) or entry.get("path") != name:
                raise ValueError("Noncanonical artifact path")
            target = _contained(artifact_root, name)
            actual = {"path": name, **_metadata(target)}
            if set(entry) != set(actual):
                raise ValueError("Invalid metadata fields")
            for key in ("bytes", "rows", "features"):
                if key in entry and (type(entry[key]) is not int or entry[key] < 0):
                    raise ValueError("Invalid count type")
            if "columns" in entry and (not isinstance(entry["columns"], list) or any(not isinstance(v, str) for v in entry["columns"]) or len(set(entry["columns"])) != len(entry["columns"])):
                raise ValueError("Invalid columns")
            if entry != actual:
                issues.append(f"artifact {name}: hash/bytes/counts/columns/type mismatch")
        except (OSError, ValueError, TypeError, KeyError):
            issues.append(f"artifact {name}: invalid metadata or unavailable artifact")
    for section, root in (("inputs", source_root), ("code_hashes", code_root)):
        if root is None or not isinstance(manifest.get(section), dict):
            continue
        for name, expected in manifest[section].items():
            try:
                target = _contained(root, name)
                digest = expected["sha256"] if section == "inputs" else expected
                if sha256_file(target) != digest or (section == "inputs" and target.stat().st_size != expected["bytes"]):
                    issues.append(f"{section}: provenance mismatch for {name}")
            except (OSError, ValueError, TypeError, KeyError):
                issues.append(f"{section}: unavailable or unsafe logical source")
    return issues


# --- Opt-in national profile (additive; the generic manifest API above is unchanged) ---

# Traced from the producers' actual reads; the selected highway source is added per manifest.
NATIONAL_FIXED_INPUTS = (
    "administrative/india_districts_lgd.geojson",
    "demographic/district_population_estimates.csv",
    "rail/railway_stations.csv",
    "rail/freight_terminals.csv",
    "logistics_hubs/ports.csv",
    "logistics_hubs/icds.csv",
    "logistics_hubs/mmlps.csv",
    "logistics_hubs/air_cargo.csv",
    "logistics_hubs/icps.csv",
    "roads/toll_plazas.csv",
)
NATIONAL_HIGHWAY_SOURCES = ("roads/india_nh_network.parquet", "roads/india_nh_network.geojson")
NATIONAL_CODE_ENTRY = "scripts/analyze/stage4_generate.py"
FIRST_PARTY_ROOTS = ("scripts", "server", "gisindia4logistics", "gis4logistics", "mcp_server")
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
CODE_CLOSURE_DEFERRED = "national: code closure validation deferred to full mode (no code_root supplied)"
_FINGERPRINT_FIELDS = {"cache_version", "row_count", "crs", "snap_grid_meters", "bridge_max_meters",
                       "bridge_speed", "default_speed", "speed_hash", "source_content_sha256"}
_PRIVATE_PATH = re.compile(r"[A-Za-z]:[\\/]|\\Users\\|/Users/|/home/|^[\\/]")


def private_path_issues(value, label="document"):
    """Fixed-message issue when any key or string looks like an absolute/user-home path.

    The offending text is never echoed. Logical POSIX paths pass.
    """
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if _PRIVATE_PATH.search(item):
                return [f"{label}: absolute or private path-like value present"]
        elif isinstance(item, dict):
            for key, child in item.items():
                stack.extend((key, child))
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return []


def _stem_names(root):
    names = set()
    for top in FIRST_PARTY_ROOTS:
        if (root / top).is_dir():
            names.update(p.stem for p in (root / top).rglob("*.py") if p.stem != "__init__")
    return names


def _resolve_module(root, parts):
    """Logical .py paths for a first-party dotted module, including package ancestors.

    Mirrors Python lookup: a package directory (or namespace directory) wins over
    nothing; a regular module wins over a namespace directory. Unresolvable raises.
    """
    found = []
    for depth in range(1, len(parts) + 1):
        prefix = parts[:depth]
        directory = root.joinpath(*prefix)
        module = root.joinpath(*prefix[:-1], prefix[-1] + ".py")
        init = directory / "__init__.py"
        if directory.is_dir() and (init.is_file() or not module.is_file()):
            if init.is_file():
                found.append("/".join((*prefix, "__init__.py")))
        elif module.is_file() and depth == len(parts):
            found.append("/".join(prefix) + ".py")
        else:
            raise ImportError("Unresolvable first-party module")
    return found


def national_code_closure(repo_root, entry=NATIONAL_CODE_ENTRY):
    """Sorted logical POSIX paths of first-party .py files reachable from `entry`.

    Parses source text only (never imports/executes modules, never opens non-.py
    files). Follows every Import/ImportFrom at any nesting (functions, try/except,
    conditionals), package __init__ ancestors and their imports, relative imports,
    namespace packages and ``from pkg import submodule``. A missing entry, an
    unresolvable first-party module or a bare import whose name matches a
    first-party .py stem (ambiguous through sys.path) raises. Dynamic imports
    (importlib, __import__, exec) are not detected; completeness is not claimed.
    """
    root = Path(repo_root).resolve()
    entry = _logical(entry)
    if not entry.endswith(".py") or not (root / entry).is_file():
        raise FileNotFoundError("Missing national code closure entry")
    stems = _stem_names(root)
    included, pending = set(), [entry]
    while pending:
        logical = pending.pop()
        if logical in included:
            continue
        included.add(logical)
        origin = PurePosixPath(logical).parts
        for depth in range(1, len(origin)):
            init = "/".join((*origin[:depth], "__init__.py"))
            if (root / init).is_file():
                pending.append(init)
        tree = ast.parse((root / logical).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                requests = [(tuple(alias.name.split(".")), ()) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = tuple(alias.name for alias in node.names if alias.name != "*")
                if node.level:
                    base = origin[:-1]
                    if node.level - 1 > len(base):
                        raise ImportError("Relative import escapes the repository")
                    base = base[:len(base) - (node.level - 1)]
                    module = base + (tuple(node.module.split(".")) if node.module else ())
                else:
                    module = tuple(node.module.split("."))
                requests = [(module, names)]
            else:
                continue
            for module, names in requests:
                if not module or module[0] not in FIRST_PARTY_ROOTS:
                    if module and module[0] in stems and not (isinstance(node, ast.ImportFrom) and node.level):
                        raise ImportError("Ambiguous bare import matches a first-party module name")
                    if not module or not (isinstance(node, ast.ImportFrom) and node.level):
                        continue
                    raise ImportError("Relative import leaves first-party roots")
                pending.extend(_resolve_module(root, module))
                for name in names:
                    try:
                        pending.extend(_resolve_module(root, (*module, name)))
                    except ImportError:
                        pass  # An attribute, not a submodule.
    return sorted(included)


def _is_int(value):
    return type(value) is int


def _close(value, expected):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value == expected
    except OverflowError:  # Oversized integers are invalid values, not crashes.
        return False


def validate_national_profile(manifest, *, code_root=None) -> list[str]:
    """Additional opt-in checks for a national run; apply after validate_manifest.

    Generic manifests (empty inputs/code_hashes, null resources, string fingerprint)
    stay valid for validate_manifest and fail only here. Without ``code_root`` the
    code_hashes keys are checked for well-formed nonemptiness only; exact closure
    equality is deferred to full mode (see CODE_CLOSURE_DEFERRED). The graph
    fingerprint's source_content_sha256 is the builder's projected-content hash,
    never the raw file SHA, and is not compared with the input hash.
    """
    if not isinstance(manifest, dict):
        return ["national: manifest unavailable"]
    from scripts.clean import graph_builder as builder
    issues = []
    if not isinstance(manifest.get("run_id"), str) or not RUN_ID_PATTERN.fullmatch(manifest["run_id"]):
        issues.append("national: invalid run_id")
    graph = manifest.get("graph") if isinstance(manifest.get("graph"), dict) else {}
    selected = graph.get("selected_source")
    supported = isinstance(selected, str) and selected in NATIONAL_HIGHWAY_SOURCES
    if not supported:
        issues.append("national: unsupported selected highway source")
    expected = set(NATIONAL_FIXED_INPUTS) | ({selected} if supported else set())
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != expected:
        issues.append("national: inputs must be exactly the fixed inputs plus the selected highway source")
    fingerprint = graph.get("fingerprint")
    if not isinstance(fingerprint, dict) or set(fingerprint) != _FINGERPRINT_FIELDS:
        issues.append("national: graph fingerprint must have exactly the nine supported fields")
    else:
        speed_hash = hashlib.sha256(json.dumps(builder.SPEEDS, sort_keys=True).encode()).hexdigest()
        checks = {
            "cache_version": _is_int(fingerprint["cache_version"]) and fingerprint["cache_version"] == builder.CACHE_VERSION,
            "row_count": _is_int(fingerprint["row_count"]) and fingerprint["row_count"] > 0,
            "crs": fingerprint["crs"] == f"EPSG:{builder.PROJ_EPSG}" and type(fingerprint["crs"]) is str,
            "snap_grid_meters": _close(fingerprint["snap_grid_meters"], builder.SNAP_GRID_METERS),
            "bridge_max_meters": _close(fingerprint["bridge_max_meters"], builder.BRIDGE_MAX_METERS),
            "bridge_speed": _close(fingerprint["bridge_speed"], builder.BRIDGE_SPEED),
            "default_speed": _close(fingerprint["default_speed"], builder.DEFAULT_SPEED),
            "speed_hash": type(fingerprint["speed_hash"]) is str and fingerprint["speed_hash"] == speed_hash,
            "source_content_sha256": isinstance(fingerprint["source_content_sha256"], str) and bool(SHA.fullmatch(fingerprint["source_content_sha256"])),
        }
        issues.extend(f"national: invalid graph fingerprint {field}" for field, ok in checks.items() if not ok)
    if not _is_int(graph.get("version")) or graph.get("version") != builder.CACHE_VERSION:
        issues.append("national: graph version must equal the builder cache version")
    if any(not _is_int(graph.get(key)) or graph[key] <= 0 for key in ("node_count", "edge_count")):
        issues.append("national: graph node and edge counts must be positive integers")
    resources = manifest.get("resources") if isinstance(manifest.get("resources"), dict) else {}
    if not _number(resources.get("elapsed_seconds"), positive=True):
        issues.append("national: elapsed_seconds must be a finite positive number")
    peak = resources.get("peak_memory_bytes")
    if not _is_int(peak) or peak <= 0:
        issues.append("national: peak_memory_bytes must be a positive integer")
    hashes = manifest.get("code_hashes")
    if not isinstance(hashes, dict) or not hashes:
        issues.append("national: code_hashes must be a nonempty mapping")
    elif code_root is not None:
        try:
            closure = national_code_closure(code_root)
        except (OSError, ImportError, SyntaxError, ValueError):
            issues.append("national: code closure unavailable")
        else:
            if set(hashes) != set(closure):
                issues.append("national: code_hashes must equal the computed code closure")
    issues.extend(private_path_issues(manifest, "national"))
    return issues
