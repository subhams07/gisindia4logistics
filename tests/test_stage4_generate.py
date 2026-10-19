"""S5 staged-generation orchestrator: synthetic roots under pytest basetemp only.

Real producers, a real (small) canonical graph, the real code closure and the real manifest/sidecar validators run
end to end on a synthetic national-shaped source. Nothing here is national data or a national-accuracy claim.
The orchestrator module is imported lazily so a missing API fails the test, not collection.
"""
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "analyze" / "stage4_generate.py"
ARTIFACTS = ("nh_district_travel_time_summary.csv", "nh_district_travel_time_summary.parquet", "nh_district_port_matrix.csv",
             "district_freight_modal_split.csv", "district_port_hinterland_catchment.csv", "major_ports_isochrones.geojson",
             "mmlp_isochrones.geojson")
NINE = (*ARTIFACTS, "producer_limitations.json", "producer_manifest.json")


def stage4():
    return importlib.import_module("scripts.analyze.stage4_generate")


def pm():
    return importlib.import_module("scripts.analyze.producer_manifest")


# ---- synthetic national-shaped source ---------------------------------------------------------

def write_source(root, *, geojson_highway=False):
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA
    for directory in ("roads", "administrative", "logistics_hubs", "rail", "demographic"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    center = gpd.GeoSeries([Point(73., 19.)], crs=4326).to_crs(7755).iloc[0]
    xy = [(center.x + x, center.y + y) for x, y in [(0., 0.), (10000., 0.), (10000., 10000.), (20000., 10000.)]]
    lines = [LineString([xy[0], xy[1], xy[2], xy[3]]), LineString([(center.x + 2000000, center.y), (center.x + 2010000, center.y)]), LineString([xy[0], xy[3]])]
    nh = gpd.GeoDataFrame(dict(highway=["motorway", "primary", "primary"], nh=["1", "2", "3"]), geometry=lines, crs=7755).to_crs(4326)
    if geojson_highway:
        nh.to_file(root / "roads/india_nh_network.geojson", driver="GeoJSON")
    else:
        nh.to_parquet(root / "roads/india_nh_network.parquet", index=False)
    points = gpd.GeoSeries([Point(*xy[0]), Point(*xy[3]), Point(center.x + 2000000, center.y), Point(center.x + 3000000, center.y), None], crs=7755).to_crs(4326)
    # Cross-state shared code 1 (Maharashtra/Delhi): a retained source condition, not corrected anywhere.
    districts = gpd.GeoDataFrame(dict(state=["Maharashtra", "Assam", "Lakshadweep", "Delhi", "Delhi"], district=["Pune", "NE", "Island", "Nazul", "Invalid"],
                                      district_code=[1., 2., 3., None, 1.]), geometry=points, crs=4326)
    districts.to_file(root / "administrative/india_districts_lgd.geojson", driver="GeoJSON")
    pd.DataFrame(dict(state=districts.state, district=districts.district, district_code=districts.district_code,
                      pop_2011=[1000. * (i + 1) for i in range(5)])).to_csv(root / "demographic/district_population_estimates.csv", index=False)
    hubs = gpd.GeoSeries([Point(*xy[3]), Point(*xy[1]), Point(center.x + 2000000, center.y), Point(center.x + 3000000, center.y)], crs=7755).to_crs(4326)
    keys = [column.replace("drive_hours_to_", "", 1) for column in PORT_MATRIX_DATA]
    names = ["Port of " + key.replace("_", " ") + " Port" for key in keys]
    ports = pd.DataFrame(dict(name=names, hub_type=["major_port"] * 12, longitude=[hubs.iloc[0].x] * 12, latitude=[hubs.iloc[0].y] * 12))
    ports.loc[10, ["longitude", "latitude"]] = [hubs.iloc[3].x, hubs.iloc[3].y]
    ports.loc[11, "latitude"] = np.nan
    ports.to_csv(root / "logistics_hubs/ports.csv", index=False)
    at = lambda i: dict(longitude=hubs.iloc[i].x, latitude=hubs.iloc[i].y)  # noqa: E731
    pd.DataFrame([dict(name="Terminal", **at(1)), dict(name="Tie", **at(1)), dict(name="Invalid", longitude=73., latitude=np.nan)]).to_csv(root / "rail/freight_terminals.csv", index=False)
    pd.DataFrame([dict(station_name="RailA", **at(0)), dict(station_name="RailB", **at(1))]).to_csv(root / "rail/railway_stations.csv", index=False)
    pd.DataFrame([dict(name="IcdA", **at(1))]).to_csv(root / "logistics_hubs/icds.csv", index=False)
    pd.DataFrame([dict(name="M1", **at(0)), dict(name="M2", **at(1)), dict(name="NoSnap", **at(3)), dict(name="Bad", longitude=73., latitude=np.nan)]).to_csv(root / "logistics_hubs/mmlps.csv", index=False)
    pd.DataFrame([dict(name="AirIsland", **at(2))]).to_csv(root / "logistics_hubs/air_cargo.csv", index=False)
    pd.DataFrame([dict(name="IcpA", **at(0))]).to_csv(root / "logistics_hubs/icps.csv", index=False)
    pd.DataFrame([dict(name="TollA", **at(0))]).to_csv(root / "roads/toll_plazas.csv", index=False)


def tree_hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(root).rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("s5template") / "source"
    write_source(root)
    return root


@pytest.fixture
def source(template, tmp_path):
    target = tmp_path / "source"
    shutil.copytree(template, target)
    return target


def roots(tmp_path):
    return dict(source_root=tmp_path / "source", stage_root=tmp_path / "stage", cache_root=tmp_path / "cache")


def run(tmp_path, **overrides):
    arguments = dict(roots(tmp_path), run_id="synthetic-s5-run")
    arguments.update(overrides)
    return stage4().generate_stage4(**arguments)


class Sentinel(Exception):
    pass


def stop_at_first_producer(monkeypatch):
    module = stage4()

    def stop(*args, **kwargs):
        raise Sentinel
    monkeypatch.setattr(module, "_run_matrix", stop)
    return module


def nothing_created(tmp_path):
    return not (tmp_path / "stage").exists() and not (tmp_path / "cache").exists()


# ---- API ---------------------------------------------------------------------------------------

def test_public_signature_and_main_exist():
    module = stage4()
    parameters = inspect.signature(module.generate_stage4).parameters
    assert list(parameters) == ["source_root", "stage_root", "cache_root", "run_id", "alpha", "beta"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())
    assert parameters["alpha"].default == 1.2 and parameters["beta"].default == 1.5
    assert module.REPO_ROOT == REPO and callable(module.main)
    assert module.ENTRY == pm().NATIONAL_CODE_ENTRY


# ---- preflight failures: nothing is created ----------------------------------------------------

@pytest.mark.parametrize("run_id", ["", "a b", "x" * 65, None, 5, "C:\\Users\\x", "run\n", "../up"])
def test_invalid_run_id_is_rejected_before_any_write(source, tmp_path, run_id):
    with pytest.raises(ValueError):
        run(tmp_path, run_id=run_id)
    assert nothing_created(tmp_path)


@pytest.mark.parametrize("field", ["alpha", "beta"])
@pytest.mark.parametrize("value", [-1., -1e-9, True, np.bool_(True), float("nan"), float("inf"), -float("inf"), 10 ** 400, "1", None, [1.]])
def test_invalid_alpha_beta_are_rejected_before_any_write(source, tmp_path, field, value):
    with pytest.raises(ValueError):
        run(tmp_path, **{field: value})
    assert nothing_created(tmp_path)


@pytest.mark.parametrize("alpha,beta", [(0., 0.), (0, 0), (0., 1.5), (1.2, 0.), (np.float64(.5), np.float64(2.))])
def test_zero_and_numpy_real_exponents_pass_preflight(source, tmp_path, monkeypatch, alpha, beta):
    stop_at_first_producer(monkeypatch)
    with pytest.raises(Sentinel):
        run(tmp_path, alpha=alpha, beta=beta)  # Reached the first producer: preflight accepted the exponents.


def required_inputs():
    return [*pm().NATIONAL_FIXED_INPUTS]


@pytest.mark.parametrize("logical", [*pm().NATIONAL_FIXED_INPUTS, "highway"])
def test_each_of_the_eleven_inputs_is_required_before_any_write(source, tmp_path, logical):
    if logical == "highway":
        (source / "roads/india_nh_network.parquet").unlink()
    else:
        (source / logical).unlink()
    with pytest.raises((FileNotFoundError, ValueError, OSError)):
        run(tmp_path)
    assert nothing_created(tmp_path)


def test_an_input_that_is_a_directory_is_rejected_before_any_write(source, tmp_path):
    (source / "rail/railway_stations.csv").unlink()
    (source / "rail/railway_stations.csv").mkdir()
    with pytest.raises((FileNotFoundError, ValueError, OSError)):
        run(tmp_path)
    assert nothing_created(tmp_path)


def make_alias(link, target):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
        return True
    except OSError:
        pass
    if sys.platform == "win32" and target.is_dir():
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return True
        except (OSError, ImportError, AttributeError):
            pass
    return False


def test_symlinked_input_resolving_outside_the_source_root_is_rejected_before_load(source, tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_text("name,latitude,longitude\n")
    victim = source / "rail/freight_terminals.csv"
    victim.unlink()
    if not make_alias(victim, outside):
        pytest.skip("Neither symlink nor junction creation is possible on this host")
    with pytest.raises((ValueError, OSError)):
        run(tmp_path)
    assert nothing_created(tmp_path)


def test_junctioned_input_directory_resolving_outside_the_source_root_is_rejected(source, tmp_path):
    outside = tmp_path / "outside-rail"
    shutil.copytree(source / "rail", outside)
    shutil.rmtree(source / "rail")
    if not make_alias(source / "rail", outside):
        pytest.skip("Neither symlink nor junction creation is possible on this host")
    with pytest.raises((ValueError, OSError)):
        run(tmp_path)
    assert nothing_created(tmp_path)


@pytest.mark.parametrize("stage,cache", [("source", "cache"), ("stage", "source"), ("source/inner-stage", "cache"), ("stage", "stage/cache"),
                                         ("stage/inner", "stage"), ("cache/stage", "cache"), ("same", "same")])
def test_overlapping_roots_are_rejected_in_both_containment_directions(source, tmp_path, stage, cache):
    if stage == "same":
        stage = cache = "shared"
    with pytest.raises(ValueError):
        stage4().generate_stage4(source_root=source, stage_root=tmp_path / stage if stage != "source" else source, cache_root=tmp_path / cache if cache != "source" else source,
                                 run_id="synthetic-s5-run")
    assert not (tmp_path / "cache").exists() and not (tmp_path / "shared").exists()


def test_source_inside_the_cache_and_cache_containing_the_source_are_rejected(source, tmp_path):
    with pytest.raises(ValueError):
        stage4().generate_stage4(source_root=source, stage_root=tmp_path / "stage", cache_root=tmp_path, run_id="synthetic-s5-run")
    assert not (tmp_path / "stage").exists()


def test_alias_of_the_source_root_is_rejected_for_stage_and_cache(source, tmp_path):
    alias = tmp_path / "alias"
    if not make_alias(alias, source):
        pytest.skip("Neither symlink nor junction creation is possible on this host")
    for stage, cache in ((alias / "stage", tmp_path / "cache"), (tmp_path / "stage", alias / "cache")):
        with pytest.raises(ValueError):
            stage4().generate_stage4(source_root=source, stage_root=stage, cache_root=cache, run_id="synthetic-s5-run")
    assert not (source / "stage").exists() and not (source / "cache").exists() and nothing_created(tmp_path)


def test_nonempty_stage_is_rejected_and_its_bytes_are_kept(source, tmp_path):
    stage = tmp_path / "stage"
    (stage / "analysis").mkdir(parents=True)
    (stage / "analysis" / "keep.txt").write_text("precious")
    before = tree_hashes(stage)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert tree_hashes(stage) == before and not (tmp_path / "cache").exists()


def test_stage_that_is_a_file_or_missing_source_is_rejected(source, tmp_path):
    (tmp_path / "stage").write_text("file")
    with pytest.raises(ValueError):
        run(tmp_path)
    (tmp_path / "stage").unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        stage4().generate_stage4(source_root=tmp_path / "no-such-source", stage_root=tmp_path / "stage", cache_root=tmp_path / "cache", run_id="synthetic-s5-run")
    assert nothing_created(tmp_path)


def test_an_existing_empty_stage_is_accepted(source, tmp_path, monkeypatch):
    (tmp_path / "stage").mkdir()
    stop_at_first_producer(monkeypatch)
    with pytest.raises(Sentinel):
        run(tmp_path)


# ---- order: code freeze before producer imports; config/constants before writes -------------------

def test_the_whole_code_closure_is_hashed_before_any_producer_module_is_imported(source, tmp_path, monkeypatch):
    module = stop_at_first_producer(monkeypatch)
    events, real_hash, real_load = [], module.sha256_file, module._load_producers
    monkeypatch.setattr(module, "sha256_file", lambda path: (events.append(("hash", Path(path))), real_hash(path))[1])
    monkeypatch.setattr(module, "_load_producers", lambda: (events.append(("import", None)), real_load())[1])
    with pytest.raises(Sentinel):
        run(tmp_path)
    closure = {REPO / logical for logical in pm().national_code_closure(REPO, module.ENTRY)}
    import_at = next(i for i, (kind, _) in enumerate(events) if kind == "import")
    hashed_before = {path for kind, path in events[:import_at] if kind == "hash"}
    assert closure <= hashed_before
    for producer in ("nh_travel_matrix", "intermodal_cost_engine", "port_hinterland_model", "generate_isochrones"):
        assert REPO / "scripts" / "analyze" / f"{producer}.py" in closure


def test_one_parameter_instance_and_one_capacity_dict_flow_to_producers_and_config(source, tmp_path, monkeypatch):
    module = stage4()
    seen = {}
    real_config = module._build_config
    real_freight = module._run_freight

    def config(params, capacities, alpha, beta):
        seen["config"] = (params, capacities)
        return real_config(params, capacities, alpha, beta)

    def freight(params, *args, **kwargs):
        seen["freight"] = params
        return real_freight(params, *args, **kwargs)

    def gravity(alpha, beta, capacities, *args, **kwargs):
        seen["gravity"] = capacities
        raise Sentinel
    monkeypatch.setattr(module, "_build_config", config)
    monkeypatch.setattr(module, "_run_freight", freight)
    monkeypatch.setattr(module, "_run_gravity", gravity)
    with pytest.raises(Sentinel):
        run(tmp_path)
    assert seen["config"][0] is seen["freight"] and seen["config"][1] is seen["gravity"]


@pytest.mark.parametrize("target,attribute,value", [
    ("scripts.analyze.nh_travel_matrix", "ACCESS_SPEED", 36.),
    ("scripts.analyze.highway_pair_kernel", "MAX_SOURCE_CHUNK", 16),
    ("scripts.analyze.highway_pair_kernel", "MAX_FEEDER_DISTANCE_M", 100000.),
    ("scripts.clean.graph_builder", "DEFAULT_SPEED", 51.),
])
def test_fixed_settings_that_disagree_with_source_constants_fail_before_any_write(source, tmp_path, monkeypatch, target, attribute, value):
    monkeypatch.setattr(importlib.import_module(target), attribute, value)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert nothing_created(tmp_path)


def test_a_changed_speed_table_fails_validate_config_before_any_write(source, tmp_path, monkeypatch):
    monkeypatch.setitem(importlib.import_module("scripts.clean.graph_builder").SPEEDS, "motorway", 91.)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert nothing_created(tmp_path)


def test_corrupt_preferred_parquet_is_an_error_with_no_geojson_fallback(source, tmp_path):
    write_geojson = source / "roads/india_nh_network.geojson"
    nh = gpd.read_parquet(source / "roads/india_nh_network.parquet")
    nh.to_file(write_geojson, driver="GeoJSON")  # A perfectly valid fallback exists...
    (source / "roads/india_nh_network.parquet").write_bytes(b"not parquet")
    with pytest.raises(Exception):
        run(tmp_path)
    stage = tmp_path / "stage" / "analysis"
    assert not (stage / "producer_manifest.json").exists() and not list(stage.glob("*.geojson")) if stage.exists() else True


# ---- consistency checks (pure) ----------------------------------------------------------------

def good_provenance():
    module = stage4()
    expected = {logical: "a" * 64 for logical in (*pm().NATIONAL_FIXED_INPUTS, "roads/india_nh_network.parquet")}
    config = {"thresholds_hours": {"major_port": [4., 8., 12., 24.], "mmlp": [4., 8., 12.]}, "buffer_m": 8000., "simplify_m": 500., "snap_limit_km": 150.,
              "feeder_speed_kmh": 35., "lmv_factor": 1., "chunk_size": 32}
    fingerprint = {"cache_version": 6}
    matrix = {"sources_sha256": dict(expected), "graph_fingerprint": dict(fingerprint), "lmv_factor": 1., "feeder_speed_kmh": 35., "candidate_radius_m": 150000., "chunk_size": 32}
    iso_sources = {"/s/roads/india_nh_network.parquet": "a" * 64, "/s/logistics_hubs/ports.csv": "a" * 64, "/s/logistics_hubs/mmlps.csv": "a" * 64}
    iso = {"sources": iso_sources, "graph_fingerprint": dict(fingerprint), "buffer_m": 8000., "simplify_m": 500., "max_snap_km": 150., "min_supported_component_nodes": module.kernel.MIN_SUPPORTED_COMPONENT_NODES, "feeder_speed_kmh": 35.,
           "vehicle_factor": 1., "thresholds_ports": [4., 8., 12., 24.], "thresholds_mmlps": [4., 8., 12.]}
    return module, matrix, iso, expected, config, fingerprint


def check(case):
    module, matrix, iso, expected, config, fingerprint = case
    return module._check_consistency(matrix, iso, expected=expected, config=config, fingerprint=fingerprint, source_root=Path("/s"),
                                     selected="roads/india_nh_network.parquet")


def test_consistent_provenance_passes_and_maps_absolute_iso_keys_in_memory_only():
    assert check(good_provenance()) is None


@pytest.mark.parametrize("mutate", [
    lambda m, i, e, c, f: m["sources_sha256"].pop("rail/railway_stations.csv"),
    lambda m, i, e, c, f: m["sources_sha256"].__setitem__("rail/railway_stations.csv", "b" * 64),
    lambda m, i, e, c, f: m["sources_sha256"].__setitem__("extra/other.csv", "a" * 64),
    lambda m, i, e, c, f: m["graph_fingerprint"].update(cache_version=5),
    lambda m, i, e, c, f: m.update(lmv_factor=.8),
    lambda m, i, e, c, f: m.update(feeder_speed_kmh=30.),
    lambda m, i, e, c, f: m.update(candidate_radius_m=100000.),
    lambda m, i, e, c, f: m.update(chunk_size=16),
    lambda m, i, e, c, f: i["graph_fingerprint"].update(cache_version=5),
    lambda m, i, e, c, f: i["sources"].pop("/s/logistics_hubs/mmlps.csv"),
    lambda m, i, e, c, f: i["sources"].__setitem__("/s/logistics_hubs/ports.csv", "b" * 64),
    lambda m, i, e, c, f: i["sources"].__setitem__("/s/roads/india_nh_network.geojson", "a" * 64),
    lambda m, i, e, c, f: i["sources"].__setitem__("/elsewhere/logistics_hubs/ports.csv", "a" * 64),
    lambda m, i, e, c, f: i.update(buffer_m=7000.),
    lambda m, i, e, c, f: i.update(simplify_m=100.),
    lambda m, i, e, c, f: i.update(max_snap_km=100.),
    lambda m, i, e, c, f: i.update(min_supported_component_nodes=2),
    lambda m, i, e, c, f: i.update(feeder_speed_kmh=30.),
    lambda m, i, e, c, f: i.update(vehicle_factor=.9),
    lambda m, i, e, c, f: i.update(thresholds_ports=[4., 8., 12.]),
    lambda m, i, e, c, f: i.update(thresholds_mmlps=[4., 8., 12., 24.]),
])
def test_any_provenance_mismatch_raises(mutate):
    case = good_provenance()
    module, matrix, iso, expected, config, fingerprint = case
    mutate(matrix, iso, expected, config, fingerprint)
    with pytest.raises(ValueError):
        check(case)


@pytest.mark.parametrize("value", [0, -1, True, False, float("nan"), float("inf"), 1.5, None, "1", 10 ** 400])
def test_unusable_memory_measurements_are_rejected(value):
    with pytest.raises((ValueError, RuntimeError, TypeError)):
        stage4()._validated_peak(value)


def test_actual_platform_memory_measurement_is_a_positive_integer_with_a_supported_method():
    module = stage4()
    peak, method = module._measure_peak_memory()
    assert type(peak) is int and peak > 0
    importlib.import_module("scripts.analyze.producer_limitations").RESOURCE_METHODS[method]
    assert method == ("windows_peak_working_set" if sys.platform == "win32" else "linux_ru_maxrss")


def test_unsupported_platform_is_a_typed_error(monkeypatch):
    module = stage4()
    monkeypatch.setattr(module.sys, "platform", "plan9")
    with pytest.raises(RuntimeError):
        module._measure_peak_memory()


# ---- real end-to-end success -------------------------------------------------------------------

@pytest.fixture(scope="module")
def success(tmp_path_factory, template):
    base = tmp_path_factory.mktemp("s5ok")
    source = base / "source"
    shutil.copytree(template, source)
    before = tree_hashes(source)
    module = stage4()
    events, patches = [], pytest.MonkeyPatch()
    for name in ("atomic_write_manifest", "validate_manifest", "validate_national_profile", "validate_limitations"):
        original = getattr(module, name)
        def spy(*args, _original=original, _name=name, **kwargs):
            events.append((_name, str(args[1]) if _name == "atomic_write_manifest" else ""))  # The destination path.
            return _original(*args, **kwargs)
        patches.setattr(module, name, spy)
    original_publish = module._publish
    patches.setattr(module, "_publish", lambda pending, final: (events.append(("publish", Path(final).name)), original_publish(pending, final))[1])
    try:
        result = module.generate_stage4(source_root=source, stage_root=base / "stage", cache_root=base / "cache", run_id="synthetic-s5-ok", alpha=1.1, beta=1.7)
    finally:
        patches.undo()
    return dict(base=base, source=source, before=before, result=result, events=events, analysis=base / "stage" / "analysis")


def test_success_publishes_exactly_nine_files_manifest_last_and_leaves_no_pending(success):
    analysis = success["analysis"]
    assert sorted(p.name for p in analysis.iterdir()) == sorted(NINE)
    assert sorted(p.name for p in (success["base"] / "stage").iterdir()) == ["analysis"]
    names = [(name, Path(target).name) for name, target in success["events"]]
    assert names.index(("atomic_write_manifest", "producer_limitations.json")) < names.index(("atomic_write_manifest", ".producer_manifest.json.pending"))
    last = names.index(("publish", "producer_manifest.json"))
    assert names.index(("atomic_write_manifest", ".producer_manifest.json.pending")) < last == len(names) - 1
    for validator in ("validate_manifest", "validate_national_profile", "validate_limitations"):
        assert any(name == validator for name, _ in names[:last])  # Self-checks ran before the final replace.


def test_success_documents_validate_with_the_real_closure_and_source_grounded_sidecar(success):
    module, analysis, source = pm(), success["analysis"], success["source"]
    limitations = importlib.import_module("scripts.analyze.producer_limitations")
    manifest = module.read_manifest(analysis / "producer_manifest.json")
    assert module.validate_manifest(analysis / "producer_manifest.json", analysis, source_root=source, code_root=REPO) == []
    assert module.validate_national_profile(manifest, code_root=REPO) == []
    assert limitations.validate_limitations(analysis / "producer_limitations.json", manifest, source_root=source) == []
    assert module.private_path_issues(manifest) == [] and module.private_path_issues(module.read_manifest(analysis / "producer_limitations.json")) == []
    assert module.private_path_issues(success["result"]) == []
    assert set(manifest["code_hashes"]) == set(module.national_code_closure(REPO, module.NATIONAL_CODE_ENTRY))


def test_success_manifest_records_exact_inputs_config_graph_and_real_resources(success):
    module, analysis, source = pm(), success["analysis"], success["source"]
    manifest = module.read_manifest(analysis / "producer_manifest.json")
    assert set(manifest["inputs"]) == {*module.NATIONAL_FIXED_INPUTS, "roads/india_nh_network.parquet"}
    assert manifest["graph"]["selected_source"] == "roads/india_nh_network.parquet"
    from scripts.analyze.freight_cost_core import FreightCostParameters
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA
    from scripts.clean import graph_builder as builder
    from dataclasses import asdict
    assert manifest["config"]["alpha"] == 1.1 and manifest["config"]["beta"] == 1.7
    assert manifest["config"]["capacities"] == {name: cap for name, cap in PORT_MATRIX_DATA.values()}
    assert manifest["config"]["freight_parameters"] == asdict(FreightCostParameters())
    nh, selected = builder.load_highway_source(source)
    assert manifest["graph"]["fingerprint"] == builder.compute_graph_fingerprint(nh) and manifest["graph"]["version"] == builder.CACHE_VERSION
    assert type(manifest["graph"]["node_count"]) is int and manifest["graph"]["node_count"] > 0 and manifest["graph"]["edge_count"] > 0
    assert type(manifest["resources"]["peak_memory_bytes"]) is int and manifest["resources"]["peak_memory_bytes"] > 0
    assert manifest["resources"]["elapsed_seconds"] > 0
    sidecar = module.read_manifest(analysis / "producer_limitations.json")
    assert sidecar["resource_measurement_method"] == importlib.import_module("scripts.analyze.producer_limitations").RESOURCE_METHODS["windows_peak_working_set" if sys.platform == "win32" else "linux_ru_maxrss"]


def test_success_preserves_source_conditions_and_route_only_nulls_without_sentinels(success):
    analysis = success["analysis"]
    sidecar = pm().read_manifest(analysis / "producer_limitations.json")
    cross = [e for e in sidecar["source_identity_disclosures"] if e["kind"] == "cross_state_shared_code"]
    assert [e["code"] for e in cross] == [1] and [(r["state"], r["district"]) for r in cross[0]["rows"]] == [("Maharashtra", "Pune"), ("Delhi", "Invalid")]
    summary = pd.read_csv(analysis / "nh_district_travel_time_summary.csv")
    assert summary.loc[2, "port_road_distance_km"] != summary.loc[2, "port_road_distance_km"]  # NaN: island/unreachable stays null.
    assert not (summary.select_dtypes("number") < 0).any().any()
    assert summary.district_code.tolist()[0] == 1 and summary.district_code.tolist()[4] == 1  # Ids are retained, not corrected.


def test_success_returns_logical_paths_and_never_modifies_the_source(success):
    result = success["result"]
    text = json.dumps(result, default=str)
    assert str(success["base"]) not in text and "\\" not in text.replace("\\\\", "")
    assert result["run_id"] == "synthetic-s5-ok" and sorted(result["artifacts"]) == sorted(ARTIFACTS)
    assert result["sidecar"] == "analysis/producer_limitations.json" and result["manifest"] == "analysis/producer_manifest.json"
    assert tree_hashes(success["source"]) == success["before"]
    assert (success["base"] / "cache" / "canonical_nh_graph.npz").exists()


def test_success_artifacts_come_from_the_staged_summary_not_source_analysis(success):
    assert not (success["source"] / "analysis").exists()
    summary = pd.read_csv(success["analysis"] / "nh_district_travel_time_summary.csv")
    freight = pd.read_csv(success["analysis"] / "district_freight_modal_split.csv")
    assert summary[["state", "district"]].equals(freight[["state", "district"]])


# ---- failures after generation: no final manifest, no pending, stage kept for inspection ----------

def assert_no_final_manifest(tmp_path):
    analysis = tmp_path / "stage" / "analysis"
    assert analysis.exists() and not (analysis / "producer_manifest.json").exists() and not list(analysis.glob(".producer_manifest*"))


def wrap_after(module, monkeypatch, name, action):
    original = getattr(module, name)

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        action()
        return result
    monkeypatch.setattr(module, name, wrapper)


@pytest.mark.parametrize("logical", ["roads/india_nh_network.parquet", "demographic/district_population_estimates.csv", "logistics_hubs/icds.csv", "administrative/india_districts_lgd.geojson"])
def test_an_input_mutated_mid_run_means_no_final_manifest(source, tmp_path, monkeypatch, logical):
    module = stage4()
    def mutate():
        if logical.endswith(".parquet"):
            frame = gpd.read_parquet(source / logical)
            frame.loc[0, "nh"] = "99"  # Different bytes, same schema: still loadable.
            frame.to_parquet(source / logical, index=False)
        else:
            with (source / logical).open("ab") as handle:
                handle.write(b"\n")
    wrap_after(module, monkeypatch, "_run_matrix", mutate)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)


def test_selected_highway_mutated_between_matrix_and_isochrones_is_detected(source, tmp_path, monkeypatch):
    module = stage4()
    def mutate():
        frame = gpd.read_parquet(source / "roads/india_nh_network.parquet")
        frame.loc[0, "nh"] = "98"
        frame.to_parquet(source / "roads/india_nh_network.parquet", index=False)
    wrap_after(module, monkeypatch, "_run_gravity", mutate)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)


def test_a_preferred_parquet_appearing_after_geojson_selection_is_detected(tmp_path, monkeypatch):
    source = tmp_path / "source"
    write_source(source, geojson_highway=True)
    module = stage4()
    def appear():
        gpd.read_file(source / "roads/india_nh_network.geojson").to_parquet(source / "roads/india_nh_network.parquet", index=False)
    wrap_after(module, monkeypatch, "_run_matrix", appear)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)


def test_geojson_selection_is_used_when_no_parquet_exists(tmp_path, monkeypatch):
    source = tmp_path / "source"
    write_source(source, geojson_highway=True)
    seen = {}
    module = stage4()
    def spy(*args, **kwargs):
        seen["selected_geojson"] = not (source / "roads/india_nh_network.parquet").exists()
        raise Sentinel
    monkeypatch.setattr(module, "_run_matrix", spy)
    with pytest.raises(Sentinel):
        run(tmp_path)
    assert seen == {"selected_geojson": True}


def test_a_code_hash_changed_after_generation_means_no_final_manifest(source, tmp_path, monkeypatch):
    module = stage4()
    real, calls = module.sha256_file, {}
    target = (REPO / "scripts" / "analyze" / "producer_io.py").resolve()
    def hashing(path):
        digest = real(path)
        if Path(path).resolve() == target:
            calls[target] = calls.get(target, 0) + 1
            if calls[target] > 1:
                return "0" * 64  # The file "changed" after the preflight freeze; no project file is written.
        return digest
    monkeypatch.setattr(module, "sha256_file", hashing)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)


def test_an_unsupported_memory_measurement_means_no_final_manifest(source, tmp_path, monkeypatch):
    module = stage4()
    def unsupported():
        raise RuntimeError("Unsupported memory measurement platform")
    monkeypatch.setattr(module, "_measure_peak_memory", unsupported)
    with pytest.raises(RuntimeError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)


def test_self_validation_issues_remove_pending_and_never_publish_a_manifest(source, tmp_path, monkeypatch):
    module = stage4()
    monkeypatch.setattr(module, "validate_limitations", lambda *args, **kwargs: ["limitations: synthetic issue"])
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)
    assert (tmp_path / "stage" / "analysis" / "producer_limitations.json").exists()  # Sidecar precedes the manifest.


def test_a_publish_failure_leaves_the_pending_manifest_removed(source, tmp_path, monkeypatch):
    module = stage4()
    def failing(pending, final):
        raise OSError("replace failed")
    monkeypatch.setattr(module, "_publish", failing)
    with pytest.raises(OSError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)


# ---- S5-F1: the ORIGINAL preflight input freeze holds through metadata and final validation -------

def wrap_before(module, monkeypatch, name, action):
    original = getattr(module, name)

    def wrapper(*args, **kwargs):
        action()
        return original(*args, **kwargs)
    monkeypatch.setattr(module, name, wrapper)


def record_written_documents(module, monkeypatch):
    written, real = [], module.atomic_write_manifest

    def recording(document, path):
        written.append(document)
        return real(document, path)
    monkeypatch.setattr(module, "atomic_write_manifest", recording)
    return written


def assert_no_foreign_input_hash(written, original):
    for document in written:
        for logical, entry in (document.get("inputs") or {}).items():
            assert entry["sha256"] == original[logical]


# ---- late source/code freeze (S5-F1): the ORIGINAL preflight facts are enforced through metadata and validation ----

def mutate_icds(source):
    with (source / "logistics_hubs/icds.csv").open("ab") as handle:
        handle.write(b"\n")


def mutate_population(source):
    path = source / "demographic/district_population_estimates.csv"
    frame = pd.read_csv(path)
    frame.loc[0, "pop_2011"] = 987654.
    frame.to_csv(path, index=False)


@pytest.mark.parametrize("boundary", ["before_limitations", "inside_build_manifest"])
@pytest.mark.parametrize("mutation", [mutate_icds, mutate_population], ids=["icds", "population"])
def test_a_source_changed_before_or_inside_manifest_preparation_never_becomes_the_frozen_baseline(source, tmp_path, monkeypatch, boundary, mutation):
    module = stage4()
    wrap_before(module, monkeypatch, "build_limitations" if boundary == "before_limitations" else "build_manifest", lambda: mutation(source))
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)


@pytest.mark.parametrize("validator", ["validate_manifest", "validate_national_profile", "validate_limitations"])
@pytest.mark.parametrize("mutation", [mutate_icds, mutate_population], ids=["icds", "population"])
def test_a_source_changed_after_each_real_validator_is_caught_by_the_final_freeze(source, tmp_path, monkeypatch, validator, mutation):
    module = stage4()
    real, calls = getattr(module, validator), []

    def validating(*args, **kwargs):
        issues = real(*args, **kwargs)
        assert issues == []  # The REAL validator accepted the state; the change comes after it.
        calls.append(validator)
        mutation(source)
        return issues
    monkeypatch.setattr(module, validator, validating)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert calls == [validator]
    assert_no_final_manifest(tmp_path)


def test_a_forged_prepared_input_hash_is_rejected_without_any_source_change(source, tmp_path, monkeypatch):
    module = stage4()
    real = module.build_manifest

    def forged(*args, **kwargs):
        document = real(*args, **kwargs)
        document["inputs"]["logistics_hubs/icds.csv"]["sha256"] = "0" * 64  # Adopted baseline would differ from the frozen one.
        return document
    monkeypatch.setattr(module, "build_manifest", forged)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)


def test_a_missing_or_extra_prepared_input_key_is_rejected(source, tmp_path, monkeypatch):
    module = stage4()
    real = module.build_manifest

    def short(*args, **kwargs):
        document = real(*args, **kwargs)
        document["inputs"].pop("rail/railway_stations.csv")
        return document
    monkeypatch.setattr(module, "build_manifest", short)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)


def test_a_preferred_parquet_appearing_after_the_last_validator_is_caught_before_publication(tmp_path, monkeypatch):
    source = tmp_path / "source"
    write_source(source, geojson_highway=True)
    module = stage4()
    real = module.validate_limitations

    def last(*args, **kwargs):
        issues = real(*args, **kwargs)
        gpd.read_file(source / "roads/india_nh_network.geojson").to_parquet(source / "roads/india_nh_network.parquet", index=False)
        return issues
    monkeypatch.setattr(module, "validate_limitations", last)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)


def test_a_code_hash_change_after_the_last_validator_is_caught_before_publication(source, tmp_path, monkeypatch):
    module = stage4()
    real_validate, real_hash, state = module.validate_limitations, module.sha256_file, {"last_validator_done": False}
    target = (REPO / "scripts" / "analyze" / "producer_io.py").resolve()

    def last(*args, **kwargs):
        issues = real_validate(*args, **kwargs)
        state["last_validator_done"] = True
        return issues

    def hashing(path):
        if state["last_validator_done"] and Path(path).resolve() == target:
            return "0" * 64  # A per-process hash patch; no project file is ever written.
        return real_hash(path)
    monkeypatch.setattr(module, "validate_limitations", last)
    monkeypatch.setattr(module, "sha256_file", hashing)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)


def test_success_records_exactly_the_original_frozen_input_hashes_and_all_three_validators_pass(success):
    manifest = pm().read_manifest(success["analysis"] / "producer_manifest.json")
    before = {key.replace("\\", "/"): value for key, value in success["before"].items()}
    assert {logical: entry["sha256"] for logical, entry in manifest["inputs"].items()} == {logical: before[logical] for logical in manifest["inputs"]}
    assert set(manifest["inputs"]) == {*pm().NATIONAL_FIXED_INPUTS, "roads/india_nh_network.parquet"}
    limitations = importlib.import_module("scripts.analyze.producer_limitations")
    analysis, source = success["analysis"], success["source"]
    assert pm().validate_manifest(analysis / "producer_manifest.json", analysis, source_root=source, code_root=REPO) == []
    assert pm().validate_national_profile(manifest, code_root=REPO) == []
    assert limitations.validate_limitations(analysis / "producer_limitations.json", manifest, source_root=source) == []
    assert sorted(p.name for p in analysis.iterdir()) == sorted(NINE)


def test_no_written_document_records_an_input_hash_that_differs_from_the_original_frozen_hashes(source, tmp_path, monkeypatch):
    module = stage4()
    original = {name: module.sha256_file(path) for name, path in module._input_paths(source)[0].items()}
    written = record_written_documents(module, monkeypatch)
    wrap_after(module, monkeypatch, "validate_manifest", lambda: mutate_icds(source))
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)
    recorded = [document for document in written if document.get("inputs")]
    assert recorded  # The pending manifest was written, so the check below is not vacuous.
    assert_no_foreign_input_hash(recorded, original)
    assert module.sha256_file(source / "logistics_hubs/icds.csv") != original["logistics_hubs/icds.csv"]


# ---- CLI ---------------------------------------------------------------------------------------

def cli(tmp_path, *arguments):
    environment = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    return subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, arguments)], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=900)


def test_cli_requires_explicit_roots_and_has_no_canonical_defaults(tmp_path):
    done = cli(tmp_path)
    assert done.returncode == 2 and "--source-root" in done.stderr
    help_text = cli(tmp_path, "--help").stdout
    for flag in ("--source-root", "--stage-root", "--cache-root", "--run-id", "--alpha", "--beta"):
        assert flag in help_text


def test_cli_rejects_invalid_arguments_from_an_external_cwd_without_creating_anything(source, tmp_path):
    done = cli(tmp_path, "--source-root", source, "--stage-root", tmp_path / "stage", "--cache-root", tmp_path / "cache", "--run-id", "bad run")
    assert done.returncode == 1 and "ValueError" in done.stderr and "ModuleNotFoundError" not in done.stderr
    assert nothing_created(tmp_path)
    done = cli(tmp_path, "--source-root", source, "--stage-root", source, "--cache-root", tmp_path / "cache", "--run-id", "ok-run")
    assert done.returncode == 1 and nothing_created(tmp_path)


def test_cli_full_generation_from_an_external_cwd_writes_nine_outputs_and_prints_logical_json(source, tmp_path):
    done = cli(tmp_path, "--source-root", source, "--stage-root", tmp_path / "stage", "--cache-root", tmp_path / "cache", "--run-id", "synthetic-cli-1", "--alpha", "1.2", "--beta", "1.5")
    assert done.returncode == 0, done.stderr[-2000:]
    assert sorted(p.name for p in (tmp_path / "stage" / "analysis").iterdir()) == sorted(NINE)
    summary = json.loads(done.stdout.strip().splitlines()[-1])
    assert summary["run_id"] == "synthetic-cli-1" and str(tmp_path) not in json.dumps(summary)
    manifest = pm().read_manifest(tmp_path / "stage" / "analysis" / "producer_manifest.json")
    assert pm().validate_manifest(tmp_path / "stage" / "analysis" / "producer_manifest.json", tmp_path / "stage" / "analysis", source_root=source, code_root=REPO) == []
    assert manifest["config"]["alpha"] == 1.2 and manifest["config"]["beta"] == 1.5


# ---- N1b: source-grounded isochrone coverage (sidecar schema 2) wired into the staged generator -----------------
# Every test below is a NEW requirement; none is a historical-bug claim. Real producers, validators and closure run.

N1B_PENDING = ".producer_manifest.json.pending"
N1B_FIELDS = ("nh_graph", "nh_distance_graph", "nh_node_xy", "nh_comp_labels", "nh_tree", "nh_bridge_mask")
N1B_THRESHOLDS = {"major_port": [4., 8., 12., 24.], "mmlp": [4., 8., 12.]}
N1B_ISO = ("major_ports_isochrones.geojson", "mmlp_isochrones.geojson")


def n1b_coverage_module():
    return importlib.import_module("scripts.analyze.isochrone_coverage")


def n1b_limitations_module():
    return importlib.import_module("scripts.analyze.producer_limitations")


def n1b_hub_rows(source):
    ports = pd.read_csv(source / "logistics_hubs/ports.csv")
    ports = ports[ports["hub_type"] == "major_port"]
    return ports.to_dict("records"), pd.read_csv(source / "logistics_hubs/mmlps.csv").to_dict("records")


def n1b_independent_facts(source, cache):
    """Hub facts from an independently loaded (warm) copy of the staged graph and the actual source CSV rows."""
    from types import SimpleNamespace
    from scripts.clean import graph_builder as builder
    nh, _selected = builder.load_highway_source(source)
    values = builder.load_or_build_cached_graph(nh, cache_dir=cache)
    builder.validate_graph_metadata(*values)
    graph = SimpleNamespace(**dict(zip(N1B_FIELDS, values[:6])))
    port_rows, mmlp_rows = n1b_hub_rows(source)
    coverage = n1b_coverage_module()
    return coverage.component_facts(graph, port_rows, "major_port"), coverage.component_facts(graph, mmlp_rows, "mmlp")


def limitations_keys():
    return set(n1b_limitations_module().TOP_LEVEL_KEYS_V2)


def test_n1b_success_emits_v2_source_grounded_coverage(success):
    analysis, source = success["analysis"], success["source"]
    sidecar = pm().read_manifest(analysis / "producer_limitations.json")
    manifest = pm().read_manifest(analysis / "producer_manifest.json")
    limitations = n1b_limitations_module()
    assert type(sidecar["schema_version"]) is int and sidecar["schema_version"] == 2
    # The file is written with sorted keys, so the key SET (not the in-memory order) is the on-disk contract.
    assert set(sidecar) == limitations_keys() and len(sidecar) == 9
    assert sidecar["limitations"] == limitations.LIMITATIONS_V2
    assert limitations.validate_limitations(analysis / "producer_limitations.json", manifest, require_coverage=True) == []
    assert limitations.validate_limitations(analysis / "producer_limitations.json", manifest, source_root=source, require_coverage=True) == []
    coverage = sidecar["isochrone_coverage"]
    port_rows, mmlp_rows = n1b_hub_rows(source)
    entries = coverage["entries"]
    ports, mmlps = [e for e in entries if e["hub_type"] == "major_port"], [e for e in entries if e["hub_type"] == "mmlp"]
    assert [e["hub_name"] for e in ports] == [row["name"] for row in port_rows] and len(ports) == 12  # Literal names, source order.
    assert [e["hub_name"] for e in mmlps] == [row["name"] for row in mmlp_rows] == ["M1", "M2", "NoSnap", "Bad"]
    assert entries == ports + mmlps and [e["hub_order"] for e in ports] == list(range(12)) and [e["hub_order"] for e in mmlps] == list(range(4))
    assert all(e["thresholds_hours"] == N1B_THRESHOLDS[e["hub_type"]] for e in entries)
    module = n1b_coverage_module()
    assert module.coverage_issues(coverage, N1B_THRESHOLDS) == []
    port_facts, mmlp_facts = n1b_independent_facts(source, success["base"] / "cache")
    for entry, fact in zip(entries, port_facts + mmlp_facts):
        assert (entry["hub_type"], entry["hub_name"], entry["hub_order"]) == (fact["hub_type"], fact["hub_name"], fact["hub_order"])
        assert entry["snap_component_node_count"] == fact["snap_component_node_count"]
        assert (entry["snap_feeder_km"] is None) == (fact["snap_feeder_km"] is None)
        if fact["snap_feeder_km"] is not None:
            assert abs(entry["snap_feeder_km"] - fact["snap_feeder_km"]) <= 1e-9
    documents = [pm().read_manifest(analysis / name) for name in N1B_ISO]
    assert coverage == module.coverage_entries(documents[0], documents[1], port_facts, mmlp_facts)  # Recomputed from the written bytes.


def test_n1b_invalid_and_no_snap_hubs_get_scalar_coverage(success):
    sidecar_path = success["analysis"] / "producer_limitations.json"
    text = sidecar_path.read_text(encoding="utf-8")
    coverage = json.loads(text, parse_constant=lambda name: pytest.fail(f"non-strict constant {name}"))["isochrone_coverage"]
    ports = [e for e in coverage["entries"] if e["hub_type"] == "major_port"]
    mmlps = [e for e in coverage["entries"] if e["hub_type"] == "mmlp"]
    unsnapped = {("major_port", 10): "no_highway_within_150km", ("major_port", 11): "invalid_coordinates",
                 ("mmlp", 2): "no_highway_within_150km", ("mmlp", 3): "invalid_coordinates"}
    for (hub_type, index), status in unsnapped.items():
        entry = (ports if hub_type == "major_port" else mmlps)[index]
        assert entry["statuses"] == [status] * len(entry["thresholds_hours"]) and entry["reachable_node_counts"] == [0] * len(entry["thresholds_hours"])
        assert entry["snap_feeder_km"] is None and entry["snap_component_node_count"] is None
        assert entry["ok_threshold_count"] == 0 and entry["saturated_across_ok_thresholds"] is None and entry["component_bounded_thresholds"] == []
    for entry in [*ports[:10], *mmlps[:2]]:  # Snapped toy hubs: plain scalar types only.
        assert type(entry["snap_feeder_km"]) is float and type(entry["snap_component_node_count"]) is int and entry["snap_component_node_count"] >= 1
        assert all(type(t) is float for t in entry["thresholds_hours"]) and all(type(c) is int for c in entry["reachable_node_counts"])
    assert not any(token in text for token in ("NaN", "Infinity", str(success["base"])))


def test_n1b_self_validation_requires_coverage(source, tmp_path, monkeypatch):
    module = stage4()
    real, calls = module.validate_limitations, []

    def spy(*args, **kwargs):
        calls.append((args, dict(kwargs)))
        return real(*args, **kwargs)
    monkeypatch.setattr(module, "validate_limitations", spy)
    result = run(tmp_path)
    assert result["manifest"] == "analysis/producer_manifest.json" and len(calls) == 1
    args, kwargs = calls[0]
    assert kwargs.get("require_coverage") is True and Path(kwargs.get("source_root")) == source


def test_n1b_component_facts_use_initial_validated_capture_without_extra_graph_load(source, tmp_path, monkeypatch):
    import gc
    import weakref
    module = stage4()
    builder = importlib.import_module("scripts.clean.graph_builder")
    matrix = importlib.import_module("scripts.analyze.nh_travel_matrix")
    coverage = n1b_coverage_module()
    real_loader, real_build, real_facts, real_matrix = (builder.load_or_build_cached_graph, builder.build_canonical_highway_graph,
                                                        coverage.component_facts, module._run_matrix)
    assert matrix.load_or_build_cached_graph is real_loader  # Imported by value: two bindings of one real function.
    events, loads, first = [], [], {}

    def loader(*args, **kwargs):
        result = real_loader(*args, **kwargs)
        events.append("load")
        loads.append(tuple(id(v) for v in result))
        first.setdefault("refs", [weakref.ref(v) for v in result])  # Weak references only: nothing retains the graph.
        return result

    def build(*args, **kwargs):
        events.append("build")
        return real_build(*args, **kwargs)

    def facts(graph, rows, hub_type):
        events.append(("facts", hub_type))
        first.setdefault("fields", sorted(vars(graph)))
        first.setdefault("ids", tuple(id(getattr(graph, name)) for name in N1B_FIELDS))
        return real_facts(graph, rows, hub_type)

    def matrix_entry(*args, **kwargs):
        gc.collect()
        first["released_at_matrix"] = all(ref() is None for ref in first["refs"])
        events.append("matrix")
        return real_matrix(*args, **kwargs)
    monkeypatch.setattr(builder, "load_or_build_cached_graph", loader)
    monkeypatch.setattr(matrix, "load_or_build_cached_graph", loader)
    monkeypatch.setattr(builder, "build_canonical_highway_graph", build)
    monkeypatch.setattr(coverage, "component_facts", facts)
    monkeypatch.setattr(module, "_run_matrix", matrix_entry)
    run(tmp_path)
    assert [e for e in events if isinstance(e, tuple)] == [("facts", "major_port"), ("facts", "mmlp")]
    assert events.count("load") == 3 and events.count("build") == 1  # Normal fresh writable cache: one cold build, two warm loads.
    assert events.index("load") < events.index(("facts", "major_port")) < events.index(("facts", "mmlp")) < events.index("matrix")
    assert events[events.index("matrix") + 1:].count("load") == 2
    assert first["fields"] == sorted(N1B_FIELDS) and first["ids"] == loads[0]  # Facts use the first validated capture.
    assert first["released_at_matrix"] is True


def n1b_install_atomic_recorder(module, monkeypatch):
    destinations, real = [], module.atomic_write_manifest

    def recording(document, path):
        destinations.append(Path(path).name)
        return real(document, path)
    monkeypatch.setattr(module, "atomic_write_manifest", recording)
    return destinations


@pytest.mark.parametrize("logical", ["logistics_hubs/ports.csv", "logistics_hubs/mmlps.csv"])
def test_n1b_hub_source_mutated_after_component_facts_has_no_final_manifest(source, tmp_path, monkeypatch, logical):
    module, coverage = stage4(), n1b_coverage_module()
    real, state = coverage.component_facts, {"facts": 0, "mutated_after_facts": None, "old": None, "new": None}

    def facts(*args, **kwargs):
        state["facts"] += 1
        return real(*args, **kwargs)

    def mutate():
        state["mutated_after_facts"], state["old"] = state["facts"], hashlib.sha256((source / logical).read_bytes()).hexdigest()
        with (source / logical).open("ab") as handle:
            handle.write(b"\n")  # Still a valid CSV with the same rows.
        state["new"] = hashlib.sha256((source / logical).read_bytes()).hexdigest()
    monkeypatch.setattr(coverage, "component_facts", facts)
    wrap_before(module, monkeypatch, "_run_matrix", mutate)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)  # Existing source freeze already rejects this: first-green sensitivity.
    assert state["old"] != state["new"] and state["mutated_after_facts"] == 2  # New wiring: both fact calls precede the change.


@pytest.mark.parametrize("case,pattern", [("wrong_feeder", "snap distance"), ("wrong_name", "does not bind"), ("wrong_order", "does not bind")])
def test_n1b_fact_or_iso_property_mismatch_has_no_final_manifest(source, tmp_path, monkeypatch, case, pattern):
    coverage, state = n1b_coverage_module(), {"tampered": False}
    real = coverage.component_facts

    def facts(graph, rows, hub_type):
        result = real(graph, rows, hub_type)  # The REAL helper on the validated capture, then one visible scalar tamper.
        if hub_type == "major_port":
            first_snapped = next(fact for fact in result if fact["snap_feeder_km"] is not None)
            if case == "wrong_feeder":
                first_snapped["snap_feeder_km"] += 1.0
            elif case == "wrong_name":
                first_snapped["hub_name"] = "Tampered Port"
            else:
                first_snapped["hub_order"] += 5
            state["tampered"] = True
        return result
    monkeypatch.setattr(coverage, "component_facts", facts)
    with pytest.raises(ValueError, match=pattern):
        run(tmp_path)
    assert state["tampered"] is True
    assert_no_final_manifest(tmp_path)
    assert not (tmp_path / "stage" / "analysis" / "producer_limitations.json").exists()  # Rejected before the sidecar.


@pytest.mark.parametrize("hook", ["component_facts", "coverage_entries"])
def test_n1b_component_or_coverage_failure_leaves_no_pending(source, tmp_path, monkeypatch, hook):
    coverage, state = n1b_coverage_module(), {"real_called": False}
    real = getattr(coverage, hook)

    def failing(*args, **kwargs):
        real(*args, **kwargs)  # The actual helper runs first; the controlled failure follows it.
        state["real_called"] = True
        raise Sentinel

    monkeypatch.setattr(coverage, hook, failing)
    with pytest.raises(Sentinel):
        run(tmp_path)
    assert state["real_called"] is True
    assert_no_final_manifest(tmp_path)  # The stage stays inspectable; no pending and no final manifest.
    analysis = tmp_path / "stage" / "analysis"
    assert not (analysis / "producer_limitations.json").exists()
    assert bool(list(analysis.glob("*.geojson"))) == (hook == "coverage_entries")  # Facts fail before producers; coverage after them.


def n1b_edit_iso(path, kind):
    if kind == "whitespace":
        with path.open("ab") as handle:
            handle.write(b"\n")
    else:
        document = json.loads(path.read_text(encoding="utf-8"))
        document["n1b_unused_property"] = 1  # Valid JSON; every coverage property is untouched.
        path.write_text(json.dumps(document), encoding="utf-8")


N1B_BEFORE_PENDING = ("before_limitations", "inside_build_manifest")
N1B_VALIDATORS = ("validate_manifest", "validate_national_profile", "validate_limitations")


def n1b_iso_edit_run(source, tmp_path, monkeypatch, iso, kind, window):
    module = stage4()
    target = tmp_path / "stage" / "analysis" / iso
    state = {"old": None, "new": None, "validated": []}
    writes = n1b_install_atomic_recorder(module, monkeypatch)

    def edit():
        state["old"] = sha256_hex(target)
        n1b_edit_iso(target, kind)
        state["new"] = sha256_hex(target)
    if window in N1B_BEFORE_PENDING:
        wrap_before(module, monkeypatch, "build_limitations" if window == "before_limitations" else "build_manifest", edit)
    else:
        real = getattr(module, window)

        def validating(*args, **kwargs):
            issues = real(*args, **kwargs)
            assert issues == []  # The REAL validator accepted the state; the persistent edit comes after it.
            state["validated"].append(window)
            edit()
            return issues
        monkeypatch.setattr(module, window, validating)
    with pytest.raises(ValueError):
        run(tmp_path)
    assert_no_final_manifest(tmp_path)
    assert state["old"] is not None and state["old"] != state["new"]
    return state, writes


def sha256_hex(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.mark.parametrize("window", [*N1B_BEFORE_PENDING, *N1B_VALIDATORS])
@pytest.mark.parametrize("iso", N1B_ISO)
def test_n1b_iso_bytes_edited_in_any_acceptance_window_have_no_final_manifest(source, tmp_path, monkeypatch, iso, window):
    state, writes = n1b_iso_edit_run(source, tmp_path, monkeypatch, iso, "whitespace", window)
    if window in N1B_BEFORE_PENDING:
        assert N1B_PENDING not in writes  # A difference from the post-producer baseline stops before any pending write.
    else:
        assert state["validated"] == [window] and N1B_PENDING in writes


@pytest.mark.parametrize("window", ["before_limitations", "validate_limitations"])
@pytest.mark.parametrize("iso", N1B_ISO)
def test_n1b_iso_unused_property_edit_is_also_rejected(source, tmp_path, monkeypatch, iso, window):
    n1b_iso_edit_run(source, tmp_path, monkeypatch, iso, "unused_property", window)


def test_n1b_unedited_run_records_the_post_producer_hashes_of_both_iso_files(success):
    manifest = pm().read_manifest(success["analysis"] / "producer_manifest.json")
    sidecar = pm().read_manifest(success["analysis"] / "producer_limitations.json")
    for name in N1B_ISO:
        digest = sha256_hex(success["analysis"] / name)
        assert manifest["artifacts"][name]["sha256"] == sidecar["artifact_sha256"][name] == digest


@pytest.fixture(autouse=True, scope="module")
def _tiny_synthetic_graphs_are_supported():
    """Synthetic fixtures are far below the production component-size floor, so this module runs with
    the floor at 1; supported-component snapping is covered in tests/test_isochrone_producer.py and
    tests/test_supported_component_floor.py."""
    from scripts.analyze import highway_pair_kernel as _kernel
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_kernel, "MIN_SUPPORTED_COMPONENT_NODES", 1)
        from scripts.audit import producer_graph_oracle as _oracle
        patch.setattr(_oracle, "ORACLE_MIN_SUPPORTED_COMPONENT_NODES", 1)
        yield
