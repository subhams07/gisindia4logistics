"""Stage 4 staged-generation orchestrator (no audit, no promotion).

Runs the four accepted producers into ``<stage_root>/analysis`` from explicit source/cache roots, then writes the
source-grounded limitations sidecar and, only after every self-check passes, the manifest LAST. Failures leave the
stage for inspection but never a final manifest. Everything published is a logical path: producer raw provenance
(absolute paths) is validated in memory and never persisted.

This is a per-file generation pipeline, not a dataset transaction. Peak memory is a whole-process high-water mark,
not an operation-incremental peak or headroom proof. No navigation, tariff, rail-routing, licensing or accuracy
claim is made.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import asdict
import json
import math
import numbers
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.analyze import highway_pair_kernel as kernel
from scripts.analyze import isochrone_coverage
from scripts.analyze.freight_cost_core import FreightCostParameters, resolve_parameters
from scripts.analyze.producer_io import sha256_file
from scripts.analyze.producer_limitations import SIDECAR_FILENAME, build_limitations, validate_limitations
from scripts.analyze.producer_manifest import (
    MANIFEST_FILENAME, NATIONAL_CODE_ENTRY, NATIONAL_FIXED_INPUTS, NATIONAL_HIGHWAY_SOURCES, REQUIRED_ARTIFACTS,
    RUN_ID_PATTERN, atomic_write_manifest, build_manifest, national_code_closure, private_path_issues, read_manifest,
    validate_config, validate_manifest, validate_national_profile)
from scripts.clean import graph_builder as builder

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY = NATIONAL_CODE_ENTRY
PENDING_NAME = ".producer_manifest.json.pending"
THRESHOLDS = {"major_port": [4., 8., 12., 24.], "mmlp": [4., 8., 12.]}
SUMMARY, PARQUET, MATRIX, FREIGHT, HINTERLAND, PORTS_ISO, MMLP_ISO = REQUIRED_ARTIFACTS


# ---- roots --------------------------------------------------------------------------------------

def _real(path):
    """Real path through the deepest existing ancestor (symlinks/junctions), remainder appended, case-normalized."""
    path = Path(os.path.abspath(path))
    missing = []
    while not os.path.lexists(path) and path != path.parent:
        missing.append(path.name)
        path = path.parent
    resolved = Path(os.path.realpath(path))
    for part in reversed(missing):
        resolved = resolved / part
    return Path(os.path.normcase(str(resolved)))


def _overlap(a, b):
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _validate_roots(source_root, stage_root, cache_root):
    source, stage, cache = Path(source_root), Path(stage_root), Path(cache_root)
    if not source.is_dir():
        raise ValueError("Source root must be an existing directory")
    real = [_real(source), _real(stage), _real(cache)]
    if any(_overlap(real[i], real[j]) for i in range(3) for j in range(i + 1, 3)):
        raise ValueError("Source, stage and cache roots must be pairwise disjoint (no equality or containment)")
    if os.path.lexists(stage) and (not stage.is_dir() or any(stage.iterdir())):
        raise ValueError("Stage root must be absent or an empty directory")
    if os.path.lexists(cache) and not cache.is_dir():
        raise ValueError("Cache root must be absent or a directory")
    return source, stage, cache


def _nonnegative_real(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Real):
        raise ValueError(f"{name} must be a finite non-negative real number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < 0:
        raise ValueError(f"{name} must be a finite non-negative real number")
    return float(value)


# ---- inputs -------------------------------------------------------------------------------------

def _select_highway(source):
    """The builder's policy: the parquet when it exists, else the GeoJSON; never a fallback after a parquet is chosen."""
    if (source / NATIONAL_HIGHWAY_SOURCES[0]).exists():
        return NATIONAL_HIGHWAY_SOURCES[0]
    if (source / NATIONAL_HIGHWAY_SOURCES[1]).exists():
        return NATIONAL_HIGHWAY_SOURCES[1]
    raise FileNotFoundError("No national highway source")


def _input_paths(source):
    """Exactly the ten fixed inputs plus the selected highway: regular files that resolve inside the source root."""
    selected = _select_highway(source)
    base = Path(os.path.normcase(os.path.realpath(source)))
    paths = {}
    for logical in (*NATIONAL_FIXED_INPUTS, selected):
        path = source / logical
        if not path.is_file():
            raise FileNotFoundError("A required national input is missing or not a regular file")
        if not Path(os.path.normcase(os.path.realpath(path))).is_relative_to(base):
            raise ValueError("A national input resolves outside the source root")
        paths[logical] = path
    return paths, selected


def _hash_inputs(paths):
    return {logical: sha256_file(path) for logical, path in paths.items()}


# ---- config -------------------------------------------------------------------------------------

def _build_config(params, capacities, alpha, beta):
    from scripts.analyze import nh_travel_matrix  # Imported only after the code freeze.
    return {"units": {"distance": "km", "time": "hours", "cost": "INR/tonne"},
            "objectives": {"hub_selection": "minimum_lmv_time", "snap": "closest_compatible", "distance": "time_predecessor_path"},
            "speeds": {**builder.SPEEDS, "default": builder.DEFAULT_SPEED},
            "feeder_speed_kmh": float(nh_travel_matrix.ACCESS_SPEED), "snap_limit_km": kernel.MAX_FEEDER_DISTANCE_M / 1000.0,
            "lmv_factor": 1.0, "chunk_size": kernel.MAX_SOURCE_CHUNK,
            "thresholds_hours": {key: list(values) for key, values in THRESHOLDS.items()},
            "buffer_m": 8000.0, "simplify_m": 500.0, "alpha": alpha, "beta": beta,
            "capacities": capacities, "freight_parameters": asdict(params)}


def _load_producers():
    from scripts.analyze import generate_isochrones, intermodal_cost_engine, nh_travel_matrix, port_hinterland_model
    return SimpleNamespace(matrix=nh_travel_matrix, freight=intermodal_cost_engine, gravity=port_hinterland_model, isochrones=generate_isochrones)


# ---- producer steps (explicit roots only) --------------------------------------------------------

def _run_matrix(producers, source, cache, analysis, chunk_size):
    summary, matrix, provenance = producers.matrix.build_travel_outputs(data_dir=source, cache_dir=cache, chunk_size=chunk_size)
    producers.matrix.write_travel_outputs(summary, matrix, summary_csv=analysis / SUMMARY, summary_parquet=analysis / PARQUET,
                                          port_matrix_csv=analysis / MATRIX)
    return provenance


def _run_freight(params, producers, stage, analysis):
    # Reads only the staged summary (data_dir is the stage root), never a source-tree analysis directory.
    producers.freight.compute_modal_costs(params, output_csv=analysis / FREIGHT, data_dir=stage)


def _run_gravity(alpha, beta, capacities, producers, source, analysis):
    producers.gravity.run_port_gravity_model(alpha=alpha, beta=beta, custom_capacities=capacities, output_csv=analysis / HINTERLAND,
                                             data_dir=source, matrix_csv=analysis / MATRIX,
                                             population_csv=source / "demographic" / "district_population_estimates.csv")


def _run_isochrones(producers, source, cache, analysis, config):
    thresholds = config["thresholds_hours"]
    _ports, _mmlps, provenance = producers.isochrones.generate_isochrones(
        data_dir=source, cache_dir=cache, output_dir=analysis, thresholds_ports=tuple(thresholds["major_port"]),
        thresholds_mmlps=tuple(thresholds["mmlp"]), chunk_size=config["chunk_size"])
    return provenance


# ---- consistency and freeze checks --------------------------------------------------------------

def _check_consistency(matrix, iso, *, expected, config, fingerprint, source_root, selected):
    """Producer provenance must match the preflight facts; absolute ISO source keys are mapped in memory only."""
    def require(condition):
        if not condition:
            raise ValueError("Producer provenance does not match the preflight facts")
    require(matrix.get("sources_sha256") == expected)
    require(matrix.get("graph_fingerprint") == fingerprint)
    require(matrix.get("lmv_factor") == config["lmv_factor"] and matrix.get("feeder_speed_kmh") == config["feeder_speed_kmh"])
    require(matrix.get("candidate_radius_m") == config["snap_limit_km"] * 1000.0 and matrix.get("chunk_size") == config["chunk_size"])
    mapped = {}
    for key, digest in iso.get("sources", {}).items():
        try:
            mapped[Path(key).relative_to(source_root).as_posix()] = digest
        except ValueError:
            raise ValueError("Producer provenance does not match the preflight facts") from None
    wanted = (selected, "logistics_hubs/ports.csv", "logistics_hubs/mmlps.csv")
    require(mapped == {logical: expected.get(logical) for logical in wanted})
    require(iso.get("graph_fingerprint") == fingerprint)
    require(iso.get("buffer_m") == config["buffer_m"] and iso.get("simplify_m") == config["simplify_m"])
    require(iso.get("max_snap_km") == config["snap_limit_km"] and iso.get("feeder_speed_kmh") == config["feeder_speed_kmh"])
    require(iso.get("vehicle_factor") == config["lmv_factor"])
    require(list(iso.get("thresholds_ports", [])) == list(config["thresholds_hours"]["major_port"]))
    require(list(iso.get("thresholds_mmlps", [])) == list(config["thresholds_hours"]["mmlp"]))


def _verify_unchanged(source, selected, input_hashes, closure, code_hashes):
    if _select_highway(source) != selected:
        raise ValueError("The preferred highway source changed during generation")
    paths, _ = _input_paths(source)
    if _hash_inputs(paths) != input_hashes:
        raise ValueError("A national input changed during generation")
    if set(national_code_closure(REPO_ROOT, ENTRY)) != set(closure):
        raise ValueError("The code closure changed during generation")
    if {logical: sha256_file(REPO_ROOT / logical) for logical in closure} != code_hashes:
        raise ValueError("A first-party code file changed during generation")


# ---- resources ----------------------------------------------------------------------------------

def _windows_peak():
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                                                 "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.PeakWorkingSetSize)


def _linux_peak():
    import resource
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _validated_peak(value):
    if type(value) is not int or not 0 < value < 2 ** 63:
        raise ValueError("Peak memory measurement is not a positive integer byte count")
    return value


def _measure_peak_memory():
    """(whole-process peak bytes, sidecar resource-method key); an unsupported platform is an error, never a guess."""
    if sys.platform == "win32":
        return _validated_peak(_windows_peak()), "windows_peak_working_set"
    if sys.platform.startswith("linux"):
        return _validated_peak(_linux_peak()), "linux_ru_maxrss"
    raise RuntimeError("Peak memory measurement is unsupported on this platform")


def _publish(pending, final):
    os.replace(pending, final)


# ---- orchestrator -------------------------------------------------------------------------------

def generate_stage4(*, source_root, stage_root, cache_root, run_id, alpha=1.2, beta=1.5) -> dict:
    """Generate the seven artifacts, the limitations sidecar and (last) the manifest under ``stage_root/analysis``."""
    started = time.perf_counter()
    # -- preflight: nothing below this line creates, writes or imports a producer before it completes ----
    if type(run_id) is not str or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id must match [A-Za-z0-9._-]{1,64}")
    alpha, beta = _nonnegative_real(alpha, "alpha"), _nonnegative_real(beta, "beta")
    source, stage, cache = _validate_roots(source_root, stage_root, cache_root)
    # Code freeze first: static closure of the real entry and each file's hash, before any producer module import.
    closure = national_code_closure(REPO_ROOT, ENTRY)
    code_hashes = {logical: sha256_file(REPO_ROOT / logical) for logical in closure}
    producers = _load_producers()
    params = FreightCostParameters()
    if asdict(resolve_parameters(params)) != asdict(params):
        raise ValueError("Freight parameters are not a complete valid set")
    capacities = {name: cap for name, cap in producers.gravity.PORT_MATRIX_DATA.values()}
    config = _build_config(params, capacities, alpha, beta)
    issues = validate_config(config)
    if issues:
        raise ValueError("Unsupported generation configuration: " + "; ".join(issues))
    input_paths, selected = _input_paths(source)
    input_hashes = _hash_inputs(input_paths)
    nh, selected_path = builder.load_highway_source(source)  # Read-only; a corrupt preferred parquet fails here, no fallback.
    if Path(os.path.normcase(os.path.realpath(selected_path))) != Path(os.path.normcase(os.path.realpath(source / selected))):
        raise ValueError("The builder selected a different highway source than the preflight")
    fingerprint = builder.compute_graph_fingerprint(nh)
    # -- writes begin ----------------------------------------------------------------------------------
    analysis = stage / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    values = builder.load_or_build_cached_graph(nh, cache_dir=cache)
    builder.validate_graph_metadata(*values)
    node_count, edge_count = int(values[0].shape[0]), int(values[0].nnz)
    # Coverage facts come from this first validated capture (no extra load or build); only scalar facts are kept and
    # every graph/frame reference is released before the producers run.
    graph = SimpleNamespace(nh_graph=values[0], nh_distance_graph=values[1], nh_node_xy=values[2], nh_comp_labels=values[3],
                            nh_tree=values[4], nh_bridge_mask=values[5])
    hubs = pd.read_csv(input_paths["logistics_hubs/ports.csv"])
    port_facts = isochrone_coverage.component_facts(graph, hubs[hubs["hub_type"] == "major_port"].to_dict("records"), "major_port")
    hubs = pd.read_csv(input_paths["logistics_hubs/mmlps.csv"])
    mmlp_facts = isochrone_coverage.component_facts(graph, hubs.to_dict("records"), "mmlp")
    del graph, hubs, values, nh
    matrix_provenance = _run_matrix(producers, source, cache, analysis, config["chunk_size"])
    _run_freight(params, producers, stage, analysis)
    _run_gravity(alpha, beta, capacities, producers, source, analysis)
    iso_provenance = _run_isochrones(producers, source, cache, analysis, config)
    # Post-producer baseline of the two staged isochrone files: later observations must equal it (observed-byte binding,
    # not a continuous-immutability or geometry claim; the two hashes are taken one after the other).
    iso_baseline = {name: sha256_file(analysis / name) for name in (PORTS_ISO, MMLP_ISO)}
    _check_consistency(matrix_provenance, iso_provenance, expected=input_hashes, config=config, fingerprint=fingerprint,
                       source_root=source, selected=selected)
    _verify_unchanged(source, selected, input_hashes, closure, code_hashes)
    # Strict reads of the written files; coverage binds selected semantics only and does not check bytes or geometry.
    coverage = isochrone_coverage.coverage_entries(read_manifest(analysis / PORTS_ISO), read_manifest(analysis / MMLP_ISO),
                                                   port_facts, mmlp_facts)
    # -- sidecar, then the pending manifest, then (only after every self-check) the final manifest -------
    artifacts = {name: analysis / name for name in REQUIRED_ARTIFACTS}
    peak, method = _measure_peak_memory()  # Whole-process high-water mark; failure or an unsupported platform raises.
    sidecar_path = analysis / SIDECAR_FILENAME
    atomic_write_manifest(build_limitations(source, artifacts, run_id, method, isochrone_coverage=coverage), sidecar_path)
    resources = {"elapsed_seconds": time.perf_counter() - started, "peak_memory_bytes": peak}
    graph = {"fingerprint": fingerprint, "version": builder.CACHE_VERSION, "node_count": node_count, "edge_count": edge_count, "selected_source": selected}
    document = build_manifest(run_id, artifacts, input_paths, graph, config, code_hashes, resources)
    # The ORIGINAL preflight facts stay the baseline: build_manifest re-hashes the inputs, and a fresh hash must never be
    # adopted (e.g. a source that changed while the sidecar or manifest was prepared). Compare before any pending file.
    if ({logical: entry["sha256"] for logical, entry in document["inputs"].items()} != input_hashes
            or document["code_hashes"] != code_hashes):
        raise ValueError("Prepared manifest provenance differs from the preflight freeze")
    if {name: document["artifacts"][name]["sha256"] for name in iso_baseline} != iso_baseline:
        raise ValueError("Prepared manifest isochrone artifacts differ from the post-producer baseline")
    pending, final = analysis / PENDING_NAME, analysis / MANIFEST_FILENAME
    try:
        atomic_write_manifest(document, pending)
        problems = validate_manifest(pending, analysis, source_root=source, code_root=REPO_ROOT)
        problems += validate_national_profile(document, code_root=REPO_ROOT)
        problems += validate_limitations(sidecar_path, document, source_root=source, require_coverage=True)
        if problems:
            raise ValueError("Staged manifest self-validation failed: " + "; ".join(problems))
        if {name: sha256_file(analysis / name) for name in iso_baseline} != iso_baseline:
            raise ValueError("An isochrone artifact changed after the post-producer baseline")
        # Last freeze check immediately before publication, covering the sidecar build and every validator above.
        # Not an atomic snapshot: a change after this line still needs the operational no-concurrent-edit freeze.
        _verify_unchanged(source, selected, input_hashes, closure, code_hashes)
        _publish(pending, final)
    except BaseException:
        pending.unlink(missing_ok=True)
        raise
    summary = {"run_id": run_id, "artifacts": list(REQUIRED_ARTIFACTS), "sidecar": f"analysis/{SIDECAR_FILENAME}",
               "manifest": f"analysis/{MANIFEST_FILENAME}", "selected_source": selected, "node_count": node_count,
               "edge_count": edge_count, "alpha": alpha, "beta": beta, "peak_memory_bytes": peak, "resource_method": method,
               "elapsed_seconds": resources["elapsed_seconds"]}
    if private_path_issues(summary):
        raise ValueError("The generation summary contains a path-like value")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--stage-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--alpha", type=float, default=1.2)
    parser.add_argument("--beta", type=float, default=1.5)
    args = parser.parse_args(argv)
    try:
        summary = generate_stage4(source_root=args.source_root, stage_root=args.stage_root, cache_root=args.cache_root,
                                  run_id=args.run_id, alpha=args.alpha, beta=args.beta)
    except Exception as exc:  # Type only: messages from producers may carry paths.
        print(f"stage4 generation failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
