"""Synthetic fixtures and registration spies only, never real audit execution."""
from concurrent.futures import ThreadPoolExecutor
import json

import pandas as pd
import pytest

from scripts.audit import audit_all as audit


@pytest.mark.parametrize("d,t,ok", [(0., 0., True), (.1, 0., True), (0., .01, False), (70., 1., True), (35., 1., True), (90., 1., True), (120., 1., False), (10., 1., False), (-1., 0., False), (float("inf"), 1., False), (None, 1., False), (None, None, True)])
def test_rounding_envelope(d, t, ok):
    assert audit.rounded_route_valid(d, t) is ok


def test_semantic_coercion_order_nullmask():
    a = pd.DataFrame({"district_code": [1., None], "value": [2., None], "name": ["X", "Y"]})
    b = pd.DataFrame({"district_code": ["1", None], "value": ["2.0000000001", None], "name": ["X", "Y"]})
    assert audit.frames_semantically_equal(a, b)
    assert not audit.frames_semantically_equal(a, b.iloc[::-1].reset_index(drop=True))
    b.loc[1, "value"] = "0"
    assert not audit.frames_semantically_equal(a, b)


@pytest.mark.parametrize("fast", [True, False])
def test_registration_roots_and_report_separation(tmp_path, monkeypatch, fast):
    seen = []
    names = ["audit_boundaries", "audit_rail", "audit_hubs", "audit_freight", "audit_demographics", "audit_geoparquet", "audit_catalog", "check_baseline", "audit_villages", "audit_analysis", "audit_roads", "audit_producers_fast", "audit_producers_full"]
    for name in names:
        def spy(*args, _name=name, **kwargs):
            seen.append((_name, args, kwargs))
            audit.check(_name, "synthetic", "FAIL", True)
        monkeypatch.setattr(audit, name, spy)
    source, artifacts, cache = (tmp_path / n for n in ["source", "artifacts", "cache"])
    original_root = audit.DATA_DIR
    runner = audit.AuditRunner(fast=fast, source_root=source, artifact_root=artifacts, cache_dir=cache)
    assert not any(p.exists() for p in [source, artifacts, cache])
    results = runner.run()
    assert len(results) == len(seen)
    assert audit.DATA_DIR == original_root
    calls = {n: (args, kw) for n, args, kw in seen}
    assert calls["audit_roads"] == ((source,), {"fast": fast})
    assert calls["audit_producers_fast"] == ((artifacts,), {})
    assert ("audit_producers_full" in calls) is not fast
    if not fast:
        assert calls["audit_producers_full"] == ((source, artifacts, cache), {})
    report = runner.write_report(tmp_path / "reports")
    assert report.name == ("audit_report_fast.md" if fast else "audit_report.md")
    assert not (report.parent / ("audit_report.md" if fast else "audit_report_fast.md")).exists()
    assert not cache.exists()


def test_parallel_result_sinks(tmp_path, monkeypatch):
    names = ["audit_boundaries", "audit_rail", "audit_hubs", "audit_freight", "audit_demographics", "audit_geoparquet", "audit_catalog", "check_baseline", "audit_roads", "audit_producers_fast"]
    for name in names:
        monkeypatch.setattr(audit, name, lambda *a, **k: audit.check("synthetic", "one", "FAIL", True))
    runners = [audit.AuditRunner(fast=True, source_root=tmp_path / str(i)) for i in range(2)]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda r: r.run(), runners))
    assert all(len(r) == len(names) for r in results)
    assert results[0] is not results[1]


def captured(function, *args, **kwargs):
    results = []
    token = audit._RESULT_SINK.set(results)
    try:
        function(*args, **kwargs)
    finally:
        audit._RESULT_SINK.reset(token)
    return results


@pytest.fixture
def semantic_bundle(tmp_path):
    from scripts.analyze.freight_cost_core import calculate_unit_costs
    root = tmp_path / "staged"
    root.mkdir()
    identities = [("Maharashtra", "Pune", 1., 123.), ("Delhi", "Nazul", None, None), ("Lakshadweep", "Lakshadweep", 3., 42.)]
    summary, matrix, costs, hinterland = [], [], [], []
    for index, (state, district, code, population) in enumerate(identities):
        island = index == 2
        routed = index == 0
        s = dict.fromkeys(audit.SUMMARY_COLUMNS)
        s.update(state=state, district=district, district_code=code, pop_2011=population, is_island=island, highway_access_dist_km=0., highway_access_time_min=0.)
        for kind in audit.KINDS:
            if routed:
                s.update({f"nearest_{kind}_name": "Mumbai", f"{kind}_road_distance_km": 70., f"{kind}_straight_km": 60., f"{kind}_drive_time_hours": 1., f"{kind}_drive_time_min": 60.})
        m = dict.fromkeys(audit.MATRIX_COLUMNS)
        m.update(state=state, district=district, district_code=code)
        if routed:
            for key in audit.PORT_KEYS:
                m[f"drive_hours_to_{key}"] = 1.
                m[f"road_km_to_{key}"] = 70.
        c = dict.fromkeys(audit.COST_COLUMNS)
        c.update(state=state, district=district, district_code=code, pop_2011=population, is_island=island, target_port=s["nearest_port_name"], optimal_mode="N/A (Island / Isolated)")
        if routed:
            unit = calculate_unit_costs(70., 1., 70., state)
            c.update(road_distance_km=70., road_drive_time_hours=1., road_cost_per_ton_inr=round(unit.road.cost_per_ton_inr, 1), rail_cost_per_ton_inr=round(unit.conventional_rail.cost_per_ton_inr, 1), dfc_cost_per_ton_inr=round(unit.dfc_rail.cost_per_ton_inr, 1), optimal_mode=unit.optimal_mode, modal_shift_savings_inr=round(unit.modal_shift_savings_per_ton_inr, 1), modal_shift_savings_pct=round(unit.modal_shift_savings_pct, 1), break_even_distance_km=unit.break_even_distance_km)
        h = dict.fromkeys(audit.HINTERLAND_COLUMNS)
        h.update(state=state, district=district, district_code=code, pop_2011=population, is_island=island, primary_port="N/A", secondary_port="N/A", hinterland_category="Island / Isolated" if island else "Isolated")
        if routed:
            h.update(primary_port="Mumbai", secondary_port="Cochin", primary_port_probability=60., secondary_port_probability=20., primary_port_drive_hours=1., secondary_port_drive_hours=1., hinterland_category="Dominant / Mildly Contested", contestability_index=.6)
        summary.append(s)
        matrix.append(m)
        costs.append(c)
        hinterland.append(h)
    frame = pd.DataFrame(summary, columns=audit.SUMMARY_COLUMNS)
    frame.to_csv(root / "nh_district_travel_time_summary.csv", index=False)
    frame.to_parquet(root / "nh_district_travel_time_summary.parquet", index=False)
    for name, rows, cols in [("nh_district_port_matrix.csv", matrix, audit.MATRIX_COLUMNS), ("district_freight_modal_split.csv", costs, audit.COST_COLUMNS), ("district_port_hinterland_catchment.csv", hinterland, audit.HINTERLAND_COLUMNS)]:
        pd.DataFrame(rows, columns=cols).to_csv(root / name, index=False)
    for name, kind in [("major_ports_isochrones.geojson", "major_port"), ("mmlp_isochrones.geojson", "mmlp")]:
        features = []
        for threshold in ([4., 8., 12., 24.] if kind == "major_port" else [4., 8., 12.]):
            x = 72. + threshold / 100
            geometry = {"type": "Polygon", "coordinates": [[[72., 18.], [x, 18.], [x, 18. + threshold / 100], [72., 18. + threshold / 100], [72., 18.]]]}
            features.append({"type": "Feature", "properties": {"hub_type": kind, "hub_name": "Synthetic", "status": "ok", "time_threshold_hours": threshold, "reachable_node_count": int(threshold) + 1, "snap_feeder_km": 0., "feeder_time_hours": 0., "coverage_method": "approximate_highway_catchment"}, "geometry": geometry})
        (root / name).write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return root


def test_fast_complete_fixture(semantic_bundle):
    results = captured(audit.audit_producers_fast, semantic_bundle)
    assert not [r for r in results if r["status"] == "FAIL"]
    assert [r for r in results if r["status"] == "WARN"] == [r for r in results if r["check"] == "manifest present (legacy pre-regeneration debt)"]


@pytest.mark.parametrize("column,value,check", [("port_drive_time_min", 1., "minutes == 60 hours"), ("port_straight_km", 90., "straight <= road"), ("port_drive_time_hours", None, "rounding-aware"), ("port_road_distance_km", -1., "rounding-aware"), ("port_road_distance_km", float("inf"), "rounding-aware")])
def test_route_corruption_always_fails(semantic_bundle, column, value, check):
    path = semantic_bundle / "nh_district_travel_time_summary.csv"
    frame = pd.read_csv(path)
    frame.loc[0, column] = value
    frame.to_csv(path, index=False)
    results = captured(audit.audit_producers_fast, semantic_bundle)
    assert any(r["status"] == "FAIL" and check in r["check"] for r in results)


@pytest.mark.parametrize("mutation,fragment", [("csv", "semantic values"), ("identity", "ordered identities"), ("island", "geographic island"), ("population", "population matches"), ("columns", "ordered schema"), ("shares", "shares bounds")])
def test_contract_corruptions_legacy_warn_then_manifest_fail(semantic_bundle, mutation, fragment):
    filename = "nh_district_travel_time_summary.csv" if mutation in {"csv", "columns"} else "district_port_hinterland_catchment.csv"
    path = semantic_bundle / filename
    frame = pd.read_csv(path)
    if mutation == "csv":
        frame.loc[0, "highway_access_dist_km"] = 4.
    elif mutation == "identity":
        frame.loc[1, "district"] = "Changed uncoded"
    elif mutation == "island":
        frame.loc[1, "is_island"] = True
    elif mutation == "population":
        frame.loc[1, "pop_2011"] = 0.
    elif mutation == "columns":
        frame = frame[list(reversed(frame.columns))]
    else:
        frame.loc[0, "secondary_port_probability"] = 90.
    frame.to_csv(path, index=False)
    legacy = captured(audit.audit_producers_fast, semantic_bundle)
    assert any(r["status"] == ("FAIL" if mutation == "shares" else "WARN") and fragment in r["check"] for r in legacy)
    # Intentionally malformed manifest still upgrades semantics and fails provenance.
    (semantic_bundle / "producer_manifest.json").write_text("{}")
    strict = captured(audit.audit_producers_fast, semantic_bundle)
    assert any(r["status"] == "FAIL" and fragment in r["check"] for r in strict)


@pytest.mark.parametrize("mutation,fragment", [("status", "explicit status"), ("threshold", "exact threshold"), ("count", "node count/snap"), ("snap", "node count/snap"), ("feeder", "node count/snap"), ("label", "approximate coverage"), ("null", "status geometry"), ("nodes", "monotonic nodes"), ("fake_circle", "status geometry")])
def test_iso_contract_corruption(semantic_bundle, mutation, fragment):
    path = semantic_bundle / "mmlp_isochrones.geojson"
    document = json.loads(path.read_text())
    feature = document["features"][1]
    p = feature["properties"]
    if mutation == "status":
        p["status"] = "invented"
    elif mutation == "threshold":
        p["time_threshold_hours"] = 4.
    elif mutation == "count":
        p["reachable_node_count"] = 1.5
    elif mutation == "snap":
        p["snap_feeder_km"] = 151.
    elif mutation == "feeder":
        p["feeder_time_hours"] = 100.
    elif mutation == "label":
        p["coverage_method"] = "official_verified"
    elif mutation == "null":
        feature["geometry"] = None
    elif mutation == "nodes":
        p["reachable_node_count"] = 100
    else:
        p["status"] = "no_highway_within_150km"
    path.write_text(json.dumps(document))
    results = captured(audit.audit_producers_fast, semantic_bundle)
    assert any(fragment in r["check"] and r["status"] == "WARN" and r["dataset"] == path.name for r in results)


def test_full_freight_fixture_keeps_gate_blocked(semantic_bundle, tmp_path):
    results = captured(audit.audit_producers_full, tmp_path / "source", semantic_bundle, tmp_path / "separate-cache")
    # Approved supersession: explicit named prerequisites replace silent skips (reconciliation E, S3 row 1).
    assert {(r["dataset"], r["check"], r["status"]) for r in results if r["status"] != "PASS"} == {
        ("population_source", "W0 population source available", "WARN"),
        ("district_source", "declared district GeoJSON available", "WARN"),
        ("hinterland_recompute", "gravity recomputation prerequisites", "WARN"),
        ("port_source", "source ports available for selected-port classification", "WARN"),
        ("source_disclosures", "source identity disclosure prerequisites", "WARN"),
        ("major_ports_isochrones.geojson", "isochrone source available", "WARN"),
        ("mmlp_isochrones.geojson", "isochrone source available", "WARN"),
        ("graph_oracle", "independent graph oracles unavailable/corrupt", "FAIL")}
    assert not (tmp_path / "separate-cache").exists()


@pytest.mark.parametrize("column,value", [("road_cost_per_ton_inr", 123.), ("modal_shift_savings_pct", 9.), ("road_drive_time_hours", 60.), ("optimal_mode", "Other"), ("dfc_cost_per_ton_inr", None)])
def test_full_freight_corruption(semantic_bundle, tmp_path, column, value):
    path = semantic_bundle / "district_freight_modal_split.csv"
    frame = pd.read_csv(path)
    frame.loc[0, column] = value
    frame.to_csv(path, index=False)
    results = captured(audit.audit_producers_full, tmp_path / "source", semantic_bundle, tmp_path / "cache")
    assert any(r["dataset"] == "freight_recompute" and "pure core" in r["check"] and r["status"] == "WARN" for r in results)


def test_roads_fast_preferred_parquet_metadata(tmp_path, monkeypatch):
    roads = tmp_path / "roads"
    roads.mkdir()
    pd.DataFrame({"nh": ["1", "2A"]}).to_parquet(roads / "india_nh_network.parquet", index=False)
    def forbidden(*a, **k):
        pytest.fail("Fast road audit must not read geometry or fallback")
    monkeypatch.setattr(audit.gpd, "read_file", forbidden)
    monkeypatch.setattr(audit.gpd, "read_parquet", forbidden)
    results = captured(audit.audit_roads, tmp_path, fast=True)
    assert any(r["dataset"] == "nh_network" and r["status"] == "PASS" for r in results)


def test_required_manifest_registration(tmp_path, monkeypatch):
    for name in ["audit_boundaries", "audit_rail", "audit_hubs", "audit_freight", "audit_demographics", "audit_geoparquet", "audit_catalog", "check_baseline", "audit_roads", "audit_producers_fast"]:
        monkeypatch.setattr(audit, name, lambda *a, **k: None)
    runner = audit.AuditRunner(fast=True, source_root=tmp_path, require_manifest=True)
    assert runner.run()[0]["status"] == "FAIL"
    assert runner.artifact_root == tmp_path / "analysis"
    assert runner.cache_dir == audit.REPO_ROOT / "outputs" / "cache"  # Approved supersession: never data/cache.


def test_actual_separate_roots_counts(semantic_bundle, tmp_path):
    source = tmp_path / "source"
    (source / "rail").mkdir(parents=True)
    pd.DataFrame({"value": [1, 2]}).to_csv(source / "rail" / "railway_stations.csv", index=False)
    counts = audit.collect_counts(source, semantic_bundle)
    assert counts["rail:stations"] == 2
    assert counts["analysis:nh_summary"] == 3
    assert not (source / "analysis").exists()


def test_w0_population_source_and_counts(semantic_bundle, tmp_path):
    import geopandas as gpd
    from shapely.geometry import Point
    source = tmp_path / "source"
    (source / "demographic").mkdir(parents=True)
    (source / "administrative").mkdir()
    summary = pd.read_csv(semantic_bundle / "nh_district_travel_time_summary.csv")
    population = summary[audit.IDENTITY + ["pop_2011"]].copy()
    # Coded row must ignore misleading names, uncoded casefold pair must join.
    population.loc[0, "district"] = "Old Census Name"
    population.loc[1, ["state", "district"]] = ["DELHI", "NAZUL"]
    population.to_csv(source / "demographic" / "district_population_estimates.csv", index=False)
    boundaries = gpd.GeoDataFrame(summary[audit.IDENTITY], geometry=[Point(72., 18.)] * 3, crs=4326)
    boundaries.to_file(source / "administrative" / "india_districts_lgd.geojson", driver="GeoJSON")  # Declared source only.
    results = captured(audit.audit_producers_full, source, semantic_bundle, tmp_path / "cache")
    assert all(r["status"] == "PASS" for r in results if "W0" in r["check"] or "source-derived district" in r["check"])
    assert sum("source-derived district" in r["check"] for r in results) == 5
    population.loc[1, "pop_2011"] = 0.
    population.to_csv(source / "demographic" / "district_population_estimates.csv", index=False)
    results = captured(audit.audit_producers_full, source, semantic_bundle, tmp_path / "cache")
    assert any("W0" in r["check"] and r["status"] == "WARN" for r in results)


def test_iso_source_invalid_coordinates_retained(semantic_bundle, tmp_path):
    source = tmp_path / "source"
    (source / "logistics_hubs").mkdir(parents=True)
    pd.DataFrame({"name": ["Synthetic"], "hub_type": ["mmlp"], "latitude": [None], "longitude": [72.]}).to_csv(source / "logistics_hubs" / "mmlps.csv", index=False)
    path = semantic_bundle / "mmlp_isochrones.geojson"
    document = json.loads(path.read_text())
    for feature in document["features"]:
        feature["properties"]["status"] = "invalid_coordinates"
        feature["geometry"] = None
    path.write_text(json.dumps(document))
    # Approved supersession: the isochrone helper, not the all-sections wrapper.
    results = captured(audit._source_isochrone_checks, source, semantic_bundle, "FAIL")
    assert [(r["dataset"], r["check"], r["status"]) for r in results if r["status"] != "PASS"] == [
        ("major_ports_isochrones.geojson", "isochrone source available", "FAIL")]
    mmlp = [r for r in results if r["dataset"] == "mmlp_isochrones.geojson"]
    assert mmlp and all(r["status"] == "PASS" for r in mmlp)
    document["features"].pop()
    path.write_text(json.dumps(document))
    results = captured(audit._source_isochrone_checks, source, semantic_bundle, "FAIL")
    assert any(r["dataset"] == "mmlp_isochrones.geojson" and r["status"] == "FAIL" for r in results)


def test_geometry_degree_corruption_fails(semantic_bundle):
    path = semantic_bundle / "major_ports_isochrones.geojson"
    document = json.loads(path.read_text())
    document["features"][0]["geometry"]["coordinates"][0][1][0] = 7200.
    path.write_text(json.dumps(document))
    results = captured(audit.audit_producers_fast, semantic_bundle)
    assert any(r["status"] == "FAIL" and "valid polygon" in r["check"] for r in results)


def test_nondefault_manifest_freight_parameters_explicit(semantic_bundle, tmp_path):
    from dataclasses import asdict
    from scripts.analyze.freight_cost_core import FreightCostParameters
    from scripts.analyze import producer_manifest as pm
    # Approved supersession: a complete synthetic source fixture (declared GeoJSON, population, ports, MMLP and a
    # hinterland regenerated by the accepted pure builder at .85/1.65) exists BEFORE the manifest is built.
    make_world(tmp_path, semantic_bundle)
    parameters = asdict(FreightCostParameters())
    parameters["road_handling_cost"] = 141.
    path = semantic_bundle / "district_freight_modal_split.csv"
    frame = pd.read_csv(path)
    frame.loc[0, "road_cost_per_ton_inr"] += 1.
    frame.to_csv(path, index=False)
    config = {"units": {"distance": "km", "time": "hours", "cost": "INR/tonne"},
              "objectives": {"hub_selection": "minimum_lmv_time", "snap": "closest_compatible", "distance": "time_predecessor_path"},
              "speeds": {"motorway": 90., "trunk": 70., "primary": 55., "default": 50.},
              "feeder_speed_kmh": 35., "snap_limit_km": 150., "lmv_factor": 1., "chunk_size": 32,
              "thresholds_hours": {"major_port": [4., 8., 12., 24.], "mmlp": [4., 8., 12.]},
              "buffer_m": 8000., "simplify_m": 500., "alpha": .85, "beta": 1.65,
              "capacities": {"Paradip Port": 145.38, "Deendayal Port (Kandla)": 132.50,
                             "Jawaharlal Nehru Port (JNPT)": 86.00, "Visakhapatnam Port": 81.00,
                             "Syama Prasad Mookerjee Port (Kolkata/Haldia)": 66.00, "Mumbai Port": 65.00,
                             "Chennai Port": 54.50, "Kamarajar Port (Ennore)": 48.00,
                             "New Mangalore Port": 46.00, "V.O. Chidambaranar Port (Tuticorin)": 41.50,
                             "Cochin Port": 36.50, "Mormugao Port": 20.50}, "freight_parameters": parameters}
    value = pm.build_manifest("synthetic-nondefault", {n: semantic_bundle / n for n in pm.REQUIRED_ARTIFACTS}, {},
        {"fingerprint": "synthetic", "version": 6, "node_count": 3, "edge_count": 4, "selected_source": "roads/synthetic.parquet"},
        config, {}, {"elapsed_seconds": None, "peak_memory_bytes": None})
    pm.atomic_write_manifest(value, semantic_bundle / pm.MANIFEST_FILENAME)
    results = captured(audit.audit_producers_full, tmp_path / "source", semantic_bundle, tmp_path / "cache")
    assert all(r["status"] == "PASS" for r in results if r["dataset"] != "graph_oracle")
    assert [(r["dataset"], r["status"]) for r in results if r["dataset"] == "graph_oracle"] == [("graph_oracle", "FAIL")]
    assert any("validated run/source" in r["check"] for r in results)
    assert not (tmp_path / "cache").exists()


def test_cli_root_flags_without_real_audit(tmp_path, monkeypatch):
    seen = {}
    class FakeRunner:
        def __init__(self, **kwargs):
            seen.update(kwargs)
        def run(self):
            return [{"status": "PASS"}]
        def write_report(self):
            return tmp_path / "synthetic-report.md"
    monkeypatch.setattr(audit, "AuditRunner", FakeRunner)
    monkeypatch.setattr(audit.sys, "argv", ["audit_all.py", "--fast", "--require-manifest", "--data-dir", str(tmp_path / "source"), "--analysis-dir", str(tmp_path / "artifacts"), "--cache-dir", str(tmp_path / "separate-cache")])
    audit.main()
    assert seen == {"fast": True, "require_manifest": True, "source_root": tmp_path / "source", "artifact_root": tmp_path / "artifacts", "cache_dir": tmp_path / "separate-cache",
                    "national": False, "report_dir": None}  # Approved supersession: two new constructor keys.
    assert not (tmp_path / "synthetic-report.md").exists()


def test_hinterland_category_dominance_corruption(semantic_bundle):
    path = semantic_bundle / "district_port_hinterland_catchment.csv"
    frame = pd.read_csv(path)
    frame.loc[0, "hinterland_category"] = "Captive Hinterland"
    frame.to_csv(path, index=False)
    results = captured(audit.audit_producers_fast, semantic_bundle)
    assert any("category agrees" in r["check"] and r["status"] == "WARN" for r in results)


# ---------------------------------------------------------------------------------------------
# S3 additions. Everything below is synthetic (tmp_path) and runs the actual audit functions.
# ---------------------------------------------------------------------------------------------
import hashlib  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
from shapely.geometry import Point  # noqa: E402

SUMMARY = "nh_district_travel_time_summary.csv"
SUMMARY_PQ = "nh_district_travel_time_summary.parquet"
MATRIX = "nh_district_port_matrix.csv"
COSTS = "district_freight_modal_split.csv"
HINTERLAND = "district_port_hinterland_catchment.csv"
TABLES = (SUMMARY, SUMMARY_PQ, MATRIX, COSTS, HINTERLAND)
QUALIFIED = "qualified identity: state+code / uncoded state+district"
CROSS = "cross-state shared code (disclosed)"
SAME = "same-state label with multiple codes (disclosed)"


def valid_config(alpha=.85, beta=1.65, capacities=None):
    from dataclasses import asdict
    from scripts.analyze.freight_cost_core import FreightCostParameters
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA
    return {"units": {"distance": "km", "time": "hours", "cost": "INR/tonne"},
            "objectives": {"hub_selection": "minimum_lmv_time", "snap": "closest_compatible", "distance": "time_predecessor_path"},
            "speeds": {"motorway": 90., "trunk": 70., "primary": 55., "default": 50.},
            "feeder_speed_kmh": 35., "snap_limit_km": 150., "lmv_factor": 1., "chunk_size": 32,
            "thresholds_hours": {"major_port": [4., 8., 12., 24.], "mmlp": [4., 8., 12.]},
            "buffer_m": 8000., "simplify_m": 500., "alpha": alpha, "beta": beta,
            "capacities": capacities or {name: cap for name, cap in PORT_MATRIX_DATA.values()},
            "freight_parameters": asdict(FreightCostParameters())}


def write_manifest(root, config=None, inputs=None, graph=None, code_hashes=None, resources=None, run_id="synthetic-s3"):
    from scripts.analyze import producer_manifest as pm
    graph = graph or {"fingerprint": "synthetic", "version": 6, "node_count": 3, "edge_count": 4, "selected_source": "roads/synthetic.parquet"}
    value = pm.build_manifest(run_id, {n: root / n for n in pm.REQUIRED_ARTIFACTS}, inputs or {}, graph, config or valid_config(),
                              code_hashes or {}, resources or {"elapsed_seconds": None, "peak_memory_bytes": None})
    pm.atomic_write_manifest(value, root / pm.MANIFEST_FILENAME)
    return value


def edit_table(root, name, change):
    path = Path(root) / name
    frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    result = change(frame)
    frame = frame if result is None else result
    if path.suffix == ".parquet":
        frame.to_parquet(path, index=False)
    else:
        frame.to_csv(path, index=False)


def set_everywhere(root, row, **values):
    """Apply a field change to every table whose schema has the field (the port matrix has no is_island)."""
    for name in TABLES:
        def change(frame):
            for column, value in values.items():
                if column in frame.columns:
                    frame[column] = frame[column].astype(object)
                    frame.loc[row, column] = value
            return frame
        edit_table(root, name, change)


def fast_records(root, **kwargs):
    return captured(audit.audit_producers_fast, root, **kwargs)


def non_pass(results):
    return [(r["dataset"], r["check"], r["status"]) for r in results if r["status"] != "PASS"]


def make_world(tmp_path, bundle, *, shared=False, population=True, ports=True, alpha=.85, beta=1.65, capacities=None):
    """Complete synthetic sources plus a hinterland regenerated by the accepted pure builder."""
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA, build_port_hinterland
    source = tmp_path / "source"
    for sub in ("administrative", "demographic", "logistics_hubs"):
        (source / sub).mkdir(parents=True, exist_ok=True)
    if shared:  # Row 0 (Maharashtra) now shares code 3 with row 2 (Lakshadweep): cross-state.
        set_everywhere(bundle, 0, district_code=3.)
    summary = pd.read_csv(bundle / SUMMARY)
    gpd.GeoDataFrame(summary[audit.IDENTITY], geometry=[Point(72., 18.)] * len(summary), crs=4326).to_file(
        source / "administrative" / "india_districts_lgd.geojson", driver="GeoJSON")
    pop = summary[audit.IDENTITY + ["pop_2011"]]
    if population:
        pop.to_csv(source / "demographic" / "district_population_estimates.csv", index=False)
    if ports:
        pd.DataFrame({"name": ["Synthetic", "Mumbai"], "hub_type": ["major_port", "minor_port"], "latitude": [18., 19.], "longitude": [72., 73.]}).to_csv(source / "logistics_hubs" / "ports.csv", index=False)
        pd.DataFrame({"name": ["Synthetic"], "hub_type": ["mmlp"], "latitude": [18.], "longitude": [72.]}).to_csv(source / "logistics_hubs" / "mmlps.csv", index=False)
    capacity = capacities or {name: cap for name, cap in PORT_MATRIX_DATA.values()}
    port_data = {column: capacity[name] for column, (name, _) in PORT_MATRIX_DATA.items()}
    build_port_hinterland(pd.read_csv(bundle / MATRIX), pop, port_data=port_data, alpha=alpha, beta=beta).to_csv(bundle / HINTERLAND, index=False)
    return SimpleNamespace(source=source, root=bundle, cache=tmp_path / "cache")


def full_records(world, **kwargs):
    return captured(audit.audit_producers_full, world.source, world.root, world.cache, **kwargs)


@pytest.fixture
def spy_oracle(monkeypatch):
    calls = []
    monkeypatch.setattr(audit, "audit_producer_graph_oracles", lambda *args: calls.append(args))
    return calls


# ---- P1: actual fast-path typed identity regressions (frozen S1 helpers, no coercion) ----------

def test_fast_cross_state_shared_code_is_valid_with_named_disclosure_warn(semantic_bundle):
    set_everywhere(semantic_bundle, 0, district_code=3.)
    write_manifest(semantic_bundle)
    results = fast_records(semantic_bundle)
    assert not [r for r in results if r["status"] == "FAIL"]
    assert [(r["dataset"], r["check"]) for r in results if r["status"] == "WARN"] == [("source_disclosures", CROSS)]
    detail = next(r["detail"] for r in results if r["check"] == CROSS)
    assert "Maharashtra" in detail and "Lakshadweep" in detail and "3" in detail
    assert not any("official" in r["detail"].lower() or "correct" in r["detail"].lower() for r in results if r["check"] == CROSS)


def test_fast_same_label_multiple_codes_is_a_named_warn_not_a_failure(semantic_bundle):
    set_everywhere(semantic_bundle, 1, state="Maharashtra", district="Pune", district_code=2.)
    set_everywhere(semantic_bundle, 1, is_island=False)
    write_manifest(semantic_bundle)
    results = fast_records(semantic_bundle)
    assert not [r for r in results if r["status"] == "FAIL" and "identity" in r["check"]]
    assert ("source_disclosures", SAME) in [(r["dataset"], r["check"]) for r in results if r["status"] == "WARN"]


@pytest.mark.parametrize("table", [SUMMARY, MATRIX, COSTS, HINTERLAND])
def test_fast_same_state_duplicate_code_fails_every_csv_table(semantic_bundle, table):
    write_manifest(semantic_bundle)
    def duplicate(frame):  # Row 1 becomes Maharashtra/1, repeating row 0.
        frame["district_code"] = frame["district_code"].astype(object)
        frame.loc[1, ["state", "district_code"]] = ["Maharashtra", 1.]
    edit_table(semantic_bundle, table, duplicate)
    write_manifest(semantic_bundle)
    results = fast_records(semantic_bundle)
    assert any(r["dataset"] == table and r["check"] == QUALIFIED and r["status"] == "FAIL" for r in results)


@pytest.mark.parametrize("case", ["bool", "np_bool", "string", "fraction", "inf", "oversized", "blank_district", "blank_state"])
def test_fast_summary_parquet_typed_identity_fails_through_actual_function(semantic_bundle, monkeypatch, case):
    if case == "string":
        # Every other artifact carries the numeric 598, so only the injected string "598" can discriminate
        # strict typing from numeric coercion (the old global check coerces "598" to 598 and accepts it).
        set_everywhere(semantic_bundle, 0, district_code=598.)
    write_manifest(semantic_bundle)
    real = audit.pd.read_parquet
    value = {"bool": True, "np_bool": np.bool_(True), "string": "598", "fraction": 598.5, "inf": float("inf"), "oversized": 10 ** 400}.get(case, 1.)

    def typed(path, *args, **kwargs):  # Narrow reader fixture: preserves exactly the object types under test.
        frame = real(path, *args, **kwargs)
        if Path(path).name == SUMMARY_PQ:
            frame = frame.copy()
            frame["district_code"] = pd.Series([value, None, 3.], index=frame.index, dtype=object)
            frame["district"] = frame["district"].astype(object)
            if case == "blank_district":
                frame.loc[0, "district"] = "   "
            if case == "blank_state":
                frame["state"] = frame["state"].astype(object)
                frame.loc[0, "state"] = ""
        return frame
    monkeypatch.setattr(audit.pd, "read_parquet", typed)
    results = fast_records(semantic_bundle)
    assert any(r["dataset"] == SUMMARY_PQ and r["check"] == QUALIFIED and r["status"] == "FAIL" for r in results), case
    assert not any(r["dataset"] == SUMMARY and r["check"] == QUALIFIED and r["status"] != "PASS" for r in results)


@pytest.mark.parametrize("case", ["bool", "fraction", "inf", "blank_district"])
def test_fast_csv_typed_identity_fails_where_csv_preserves_the_type(semantic_bundle, case):
    def typed(frame):
        frame["district_code"] = frame["district_code"].astype(object)
        frame["district"] = frame["district"].astype(object)
        if case == "blank_district":
            frame.loc[0, "district"] = None  # An empty CSV cell reads back as a missing label.
        else:
            frame.loc[0, "district_code"] = {"bool": True, "fraction": 1.5, "inf": float("inf")}[case]
    edit_table(semantic_bundle, MATRIX, typed)
    write_manifest(semantic_bundle)
    results = fast_records(semantic_bundle)
    assert any(r["dataset"] == MATRIX and r["check"] == QUALIFIED and r["status"] == "FAIL" for r in results), case


def test_fast_typed_failure_is_warn_in_legacy_and_fail_with_manifest(semantic_bundle):
    edit_table(semantic_bundle, MATRIX, lambda f: f.__setitem__("district_code", f["district_code"].astype(object)) or f.loc.__setitem__((0, "district_code"), 1.5))
    legacy = fast_records(semantic_bundle)
    assert any(r["check"] == QUALIFIED and r["dataset"] == MATRIX and r["status"] == "WARN" for r in legacy)
    (semantic_bundle / "producer_manifest.json").write_text("{}")
    strict = fast_records(semantic_bundle)
    assert any(r["check"] == QUALIFIED and r["dataset"] == MATRIX and r["status"] == "FAIL" for r in strict)


def shared_code_bundle(root):
    set_everywhere(root, 0, district_code=3.)  # Shared code across states, consistently in every table.
    write_manifest(root)


def test_fast_shared_code_control_has_no_failures(semantic_bundle):
    shared_code_bundle(semantic_bundle)
    assert not [r for r in fast_records(semantic_bundle) if r["status"] == "FAIL"]


def test_fast_partial_state_collision_fails_independently(semantic_bundle):
    shared_code_bundle(semantic_bundle)
    # One shared-code row's state is changed to duplicate the other's state in one artifact.
    edit_table(semantic_bundle, HINTERLAND, lambda f: f.__setitem__("state", ["Lakshadweep", "Delhi", "Lakshadweep"]))
    write_manifest(semantic_bundle)
    results = fast_records(semantic_bundle)
    assert any(r["dataset"] == HINTERLAND and r["check"] == QUALIFIED and r["status"] == "FAIL" for r in results)


def test_fast_swapped_shared_code_rows_fail_independently(semantic_bundle):
    shared_code_bundle(semantic_bundle)
    # The two shared-code rows (0 and 2) are swapped in one artifact; the control above has no failure.
    edit_table(semantic_bundle, COSTS, lambda f: f.iloc[[2, 1, 0]].reset_index(drop=True))
    write_manifest(semantic_bundle)
    results = fast_records(semantic_bundle)
    assert any(r["dataset"] == COSTS and "ordered identities" in r["check"] and r["status"] == "FAIL" for r in results)


@pytest.mark.parametrize("table,column,value", [(MATRIX, "district", "PUNE"), (COSTS, "state", " Maharashtra"), (HINTERLAND, "district", "pune")])
def test_fast_label_case_or_spacing_change_fails_exact_ordered_identity(semantic_bundle, table, column, value):
    edit_table(semantic_bundle, table, lambda f: f.loc.__setitem__((0, column), value))
    write_manifest(semantic_bundle)
    results = fast_records(semantic_bundle)
    assert any(r["dataset"] == table and "ordered identities" in r["check"] and r["status"] == "FAIL" for r in results)


# ---- selected-port coverage (fast) -------------------------------------------------------------

def test_fast_always_emits_selected_port_coverage_counts(semantic_bundle):
    results = fast_records(semantic_bundle)
    record = next(r for r in results if r["check"] == "selected-port coverage")
    assert record["status"] == "PASS" and record["dataset"] == "summary"
    assert "parity_checked=1" in record["detail"] and "minor_or_no_column=0" in record["detail"]
    assert "non_string=2" in record["detail"] and "total=3" in record["detail"]
    for table in (SUMMARY, SUMMARY_PQ):
        edit_table(semantic_bundle, table, lambda f: f.loc.__setitem__((0, "nearest_port_name"), "Some Minor Port"))
    record = next(r for r in fast_records(semantic_bundle) if r["check"] == "selected-port coverage")
    assert "parity_checked=0" in record["detail"] and "minor_or_no_column=1" in record["detail"] and record["status"] == "PASS"


# ---- ISO approximate nesting (EPSG:7755; 2*simplify_m + 1 m) -----------------------------------

def rectangle(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def set_iso_geometry(root, index, geometry):
    path = root / "mmlp_isochrones.geojson"
    document = json.loads(path.read_text())
    document["features"][index]["geometry"] = geometry
    path.write_text(json.dumps(document))


def nesting(results):
    return [r for r in results if r["check"] == "approximate nesting"]


def test_iso_nested_bundle_passes_nesting(semantic_bundle):
    records = nesting(fast_records(semantic_bundle))
    assert records and all(r["status"] == "PASS" for r in records)


@pytest.mark.parametrize("geometry,expected", [
    (rectangle(71.9973, 18., 72.04, 18.04), "PASS"),     # ~290 m protrusion: inside the 1001 m tolerance
    (rectangle(72.0027, 18.0027, 72.08, 18.08), "PASS"),  # shallow corner under the tolerance
    (rectangle(71.9858, 18., 72.04, 18.04), "FAIL"),     # ~1500 m protrusion beyond 2*500+1 m
])
def test_iso_nesting_tolerance_boundary(semantic_bundle, geometry, expected):
    set_iso_geometry(semantic_bundle, 0, geometry)
    write_manifest(semantic_bundle)
    statuses = {r["status"] for r in nesting(fast_records(semantic_bundle))}
    assert expected in statuses and (expected == "FAIL" or statuses == {"PASS"})


def test_iso_long_thin_nested_pair_passes(semantic_bundle):
    set_iso_geometry(semantic_bundle, 0, rectangle(72., 18., 72.04, 18.0004))
    set_iso_geometry(semantic_bundle, 1, rectangle(72., 18., 72.08, 18.0008))
    assert {r["status"] for r in nesting(fast_records(semantic_bundle))} == {"PASS"}


def test_iso_shifted_larger_polygon_fails_nesting_even_if_area_is_monotonic(semantic_bundle):
    set_iso_geometry(semantic_bundle, 1, rectangle(72.05, 18., 72.20, 18.12))  # ~5.3 km shift, larger area
    write_manifest(semantic_bundle)
    results = fast_records(semantic_bundle)
    assert any(r["dataset"] == "mmlp_isochrones.geojson" and r["check"] == "approximate nesting" and r["status"] == "FAIL" for r in results)


def test_iso_nesting_legacy_default_simplification_is_500(semantic_bundle):
    set_iso_geometry(semantic_bundle, 0, rectangle(71.9858, 18., 72.04, 18.04))
    results = fast_records(semantic_bundle)  # No manifest: legacy default, contract severity WARN.
    assert any(r["check"] == "approximate nesting" and r["status"] == "WARN" for r in results)


# ---- FULL: independent sections, named prerequisites, accepted pure builders --------------------

def test_full_complete_world_has_only_the_expected_graph_failure(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    write_manifest(semantic_bundle)
    results = full_records(world)
    assert [(r["dataset"], r["status"]) for r in results if r["status"] != "PASS"] == [("graph_oracle", "FAIL")]
    assert not world.cache.exists()


def test_full_freight_schema_corruption_does_not_stop_other_sections(semantic_bundle, tmp_path, spy_oracle):
    world = make_world(tmp_path, semantic_bundle)
    edit_table(semantic_bundle, COSTS, lambda f: f.drop(columns=["optimal_mode"]))
    results = full_records(world)
    assert ("freight_recompute", "required recomputation schema", "WARN") in non_pass(results)
    assert spy_oracle  # The graph oracle section still runs.
    assert sum("source-derived district" in r["check"] for r in results) == 5


def test_full_invalid_manifest_blocks_freight_and_gravity_but_not_other_sections(semantic_bundle, tmp_path, spy_oracle):
    world = make_world(tmp_path, semantic_bundle)
    (semantic_bundle / "producer_manifest.json").write_text("{}")
    results = full_records(world)
    blocked = {(r["dataset"], r["check"]): (r["status"], r["detail"]) for r in results if "blocked" in r["detail"]}
    assert blocked == {("freight_recompute", "required recomputation prerequisites"): ("FAIL", "blocked: invalid manifest"),
                       ("hinterland_recompute", "gravity recomputation prerequisites"): ("FAIL", "blocked: invalid manifest")}
    assert ("provenance", "source/code hashes and model config", "FAIL") in non_pass(results)
    assert sum("source-derived district" in r["check"] for r in results) == 5 and spy_oracle


def test_full_bad_state_fails_that_row_and_other_rows_are_still_checked(semantic_bundle, tmp_path, spy_oracle):
    world = make_world(tmp_path, semantic_bundle)
    edit_table(semantic_bundle, SUMMARY, lambda f: f.loc.__setitem__((0, "state"), "Atlantis"))
    results = full_records(world)
    # C6: an invalid state is an unconditional FAIL, even in legacy (no-manifest) mode; rows continue.
    assert ("freight_recompute", "row 0: canonical state", "FAIL") in non_pass(results)
    assert ("freight_recompute", "row 0: canonical state", "WARN") not in non_pass(results)
    assert any(r["check"] == "row 1: target port identity" for r in results) and any(r["check"] == "row 2: target port identity" for r in results)
    assert spy_oracle


def test_full_section_crash_is_sanitized_isolated_and_later_sections_run(semantic_bundle, tmp_path, monkeypatch, spy_oracle):
    world = make_world(tmp_path, semantic_bundle)
    def boom(*args, **kwargs):
        raise RuntimeError("C:\\private\\path\\secret")
    monkeypatch.setattr(audit, "_source_port_checks", boom)
    results = full_records(world)
    crashed = [r for r in results if r["check"] == "section crashed"]
    assert [(r["dataset"], r["status"], r["detail"]) for r in crashed] == [("port_source", "FAIL", "RuntimeError")]
    assert not any("secret" in r["detail"] or "private" in r["detail"] for r in results)
    assert any(r["dataset"] == "mmlp_isochrones.geojson" for r in results) and spy_oracle


@pytest.mark.parametrize("corruption", ["manifest", "major_iso"])
def test_corrupt_manifest_or_one_iso_artifact_keeps_healthy_iso_source_diagnostics(semantic_bundle, tmp_path, corruption):
    world = make_world(tmp_path, semantic_bundle)
    target = semantic_bundle / ("producer_manifest.json" if corruption == "manifest" else "major_ports_isochrones.geojson")
    target.write_text("{", encoding="utf-8")
    results = full_records(world)
    mmlp = [r for r in results if r["dataset"] == "mmlp_isochrones.geojson"]
    assert mmlp and all(r["status"] == "PASS" for r in mmlp)
    major = [r for r in results if r["dataset"] == "major_ports_isochrones.geojson"]
    if corruption == "manifest":
        assert major and all(r["status"] == "PASS" for r in major)
        assert ("provenance", "source/code hashes and model config", "FAIL") in non_pass(results)
    else:
        crashed = [r for r in major if r["check"] == "section crashed"]
        assert [(r["status"], r["detail"]) for r in crashed][0][0] == "FAIL" and crashed[0]["detail"].isidentifier()
        assert "{" not in crashed[0]["detail"] and str(tmp_path) not in crashed[0]["detail"]


def test_corrupt_mmlp_iso_artifact_keeps_healthy_major_port_source_diagnostics(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    (semantic_bundle / "mmlp_isochrones.geojson").write_text("{", encoding="utf-8")
    results = full_records(world)
    major = [r for r in results if r["dataset"] == "major_ports_isochrones.geojson"]
    assert major and all(r["status"] == "PASS" for r in major)
    assert any(r["dataset"] == "mmlp_isochrones.geojson" and r["check"] == "section crashed" and r["status"] == "FAIL" for r in results)


def test_fast_corrupt_iso_artifact_does_not_erase_the_sibling_artifact_records(semantic_bundle):
    (semantic_bundle / "major_ports_isochrones.geojson").write_text("{", encoding="utf-8")
    results = fast_records(semantic_bundle)
    assert any(r["dataset"] == "major_ports_isochrones.geojson" and r["check"] == "section crashed" and r["status"] == "FAIL" for r in results)
    assert any(r["dataset"] == "mmlp_isochrones.geojson" and r["check"] == "approximate nesting" for r in results)
    assert any(r["check"] == "selected-port coverage" for r in results)  # Earlier records are retained.


# F4: national mode selects strict contract severity independently of manifest presence.
@pytest.mark.parametrize("table", TABLES)
def test_national_fast_without_manifest_fails_invalid_identity_in_every_table(semantic_bundle, table):
    edit_table(semantic_bundle, table, lambda f: f.loc.__setitem__((0, "district_code"), 1.5))
    national = fast_records(semantic_bundle, national=True)
    assert [r["status"] for r in national if r["dataset"] == table and r["check"] == QUALIFIED] == ["FAIL"]
    legacy = fast_records(semantic_bundle)
    assert [r["status"] for r in legacy if r["dataset"] == table and r["check"] == QUALIFIED] == ["WARN"]


def test_national_fast_without_manifest_keeps_disclosure_warns_as_warns(semantic_bundle):
    set_everywhere(semantic_bundle, 0, district_code=3.)
    records = fast_records(semantic_bundle, national=True)
    assert [(r["check"], r["status"]) for r in records if r["dataset"] == "source_disclosures" and r["check"] == CROSS] == [(CROSS, "WARN")]
    assert not [r for r in records if r["check"] == QUALIFIED and r["status"] != "PASS"]


def test_national_full_without_manifest_keeps_source_prerequisites_strict_but_legacy_warns(semantic_bundle, tmp_path):
    missing, cache = tmp_path / "missing-source", tmp_path / "cache"
    national = captured(audit.audit_producers_full, missing, semantic_bundle, cache, national=True)
    legacy = captured(audit.audit_producers_full, missing, semantic_bundle, tmp_path / "legacy-cache")
    names = [("population_source", "W0 population source available"), ("district_source", "declared district GeoJSON available"),
             ("port_source", "source ports available for selected-port classification"),
             ("source_disclosures", "source identity disclosure prerequisites"), ("hinterland_recompute", "gravity recomputation prerequisites")]
    for dataset, name in names:
        assert [r["status"] for r in national if (r["dataset"], r["check"]) == (dataset, name)] == ["FAIL"], name
        assert [r["status"] for r in legacy if (r["dataset"], r["check"]) == (dataset, name)] == ["WARN"], name
    assert not cache.exists()


def test_national_full_without_manifest_does_not_promote_named_disclosure_warns_to_failures(semantic_bundle, tmp_path, spy_oracle):
    world = make_world(tmp_path, semantic_bundle, shared=True)
    results = full_records(world, national=True, code_root=tmp_path / "missing-repo")
    assert [(r["check"], r["status"]) for r in results if r["check"] == CROSS] == [(CROSS, "WARN")]
    assert not spy_oracle


def test_source_producer_checks_wrapper_composes_the_three_helpers(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    wrapped = captured(audit._source_producer_checks, world.source, semantic_bundle, "FAIL")
    parts = []
    for helper in (audit._source_identity_checks, audit._source_port_checks, audit._source_isochrone_checks):
        parts += captured(helper, world.source, semantic_bundle, "FAIL")
    assert [(r["dataset"], r["check"], r["status"]) for r in wrapped] == [(r["dataset"], r["check"], r["status"]) for r in parts]
    assert wrapped and all(r["status"] == "PASS" for r in wrapped)


def test_declared_geojson_only_parquet_is_never_consulted(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    declared = world.source / "administrative" / "india_districts_lgd.geojson"
    frame = gpd.read_file(declared)
    declared.unlink()
    frame.to_parquet(world.source / "administrative" / "india_districts_lgd.parquet", index=False)
    results = full_records(world)
    assert ("district_source", "declared district GeoJSON available", "WARN") in non_pass(results)
    assert not any("source-derived district" in r["check"] for r in results)


def test_missing_district_geojson_is_fail_when_manifest_contract_applies(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    (world.source / "administrative" / "india_districts_lgd.geojson").unlink()
    write_manifest(semantic_bundle)
    assert ("district_source", "declared district GeoJSON available", "FAIL") in non_pass(full_records(world))


def test_source_identity_is_exact_literal_ordered_not_semantic(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    edit_table(semantic_bundle, COSTS, lambda f: f.loc.__setitem__((0, "district"), "PUNE"))
    results = full_records(world)
    assert any(r["dataset"] == COSTS and "source-derived district" in r["check"] and r["status"] == "WARN" for r in results)


# gravity (accepted pure builder, manifest alpha/beta/capacities, zero exponents valid)
def test_full_gravity_recomputation_matches_manifest_parameters(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    write_manifest(semantic_bundle)
    results = full_records(world)
    assert ("hinterland_recompute", "pure builder stored-output consistency", "PASS") in [(r["dataset"], r["check"], r["status"]) for r in results]
    assert non_pass(results) == [("graph_oracle", "independent graph oracles unavailable/corrupt", "FAIL")]


@pytest.mark.parametrize("alpha,beta", [(0., 0.), (0., 1.5), (1.2, 0.)])
def test_full_gravity_zero_exponents_are_valid(semantic_bundle, tmp_path, alpha, beta):
    world = make_world(tmp_path, semantic_bundle, alpha=alpha, beta=beta)
    write_manifest(semantic_bundle, valid_config(alpha, beta))
    results = full_records(world)
    assert ("hinterland_recompute", "pure builder stored-output consistency", "PASS") in [(r["dataset"], r["check"], r["status"]) for r in results]


@pytest.mark.parametrize("mutation", ["beta", "alpha", "capacity"])
def test_full_gravity_manifest_parameter_change_fails_even_when_rehashed(semantic_bundle, tmp_path, mutation):
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA
    # Unequal drive times, so beta (which cancels when every time is equal) changes the shares.
    edit_table(semantic_bundle, MATRIX, lambda f: f.loc.__setitem__((0, "drive_hours_to_mumbai"), 3.))
    world = make_world(tmp_path, semantic_bundle)  # Outputs were produced with .85/1.65 and default capacities.
    capacities = {name: cap for name, cap in PORT_MATRIX_DATA.values()}
    if mutation == "capacity":
        capacities["Mumbai Port"] = 10.
    config = valid_config(alpha=1.2 if mutation == "alpha" else .85, beta=2.5 if mutation == "beta" else 1.65, capacities=capacities)
    write_manifest(semantic_bundle, config)
    results = full_records(world)
    assert ("hinterland_recompute", "pure builder stored-output consistency", "FAIL") in non_pass(results)


def gravity_status(results):
    return [r["status"] for r in results if r["check"] == "pure builder stored-output consistency"]


def test_full_gravity_literal_na_placeholders_round_trip_through_the_csv_reader(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    write_manifest(semantic_bundle)
    stored_text = (semantic_bundle / HINTERLAND).read_text()
    assert "N/A" in stored_text  # The builder's literal placeholder for unreachable rows is persisted.
    assert pd.read_csv(semantic_bundle / HINTERLAND)["primary_port"].isna().any()  # ...and the default reader sees it as missing.
    assert gravity_status(full_records(world)) == ["PASS"]


@pytest.mark.parametrize("row,column,value", [
    (0, "primary_port", "Cochin Port"),              # a reachable row's winner is altered
    (1, "primary_port", "Mumbai Port"),              # a placeholder row is given an invented port
    (1, "secondary_port", "Mumbai Port"),
    (2, "hinterland_category", "Isolated"),           # the island category is altered
    (0, "primary_port_probability", 99.9),
])
def test_full_gravity_altered_stored_values_still_fail_after_normalization(semantic_bundle, tmp_path, row, column, value):
    world = make_world(tmp_path, semantic_bundle)
    edit_table(semantic_bundle, HINTERLAND, lambda f: f.loc.__setitem__((row, column), value))
    write_manifest(semantic_bundle)  # Rehashed, so provenance passes and only the recomputation can object.
    results = full_records(world)
    assert gravity_status(results) == ["FAIL"]
    assert ("provenance", "source/code hashes and model config", "PASS") in [(r["dataset"], r["check"], r["status"]) for r in results]


def test_full_gravity_without_manifest_is_a_named_warn_not_a_recompute(semantic_bundle, tmp_path):
    results = full_records(make_world(tmp_path, semantic_bundle))
    assert not any(r["check"] == "pure builder stored-output consistency" for r in results)
    assert ("hinterland_recompute", "gravity recomputation prerequisites", "WARN") in non_pass(results)


# population (W0 authority + exact-tuple corroboration)
def test_full_missing_population_fails_with_manifest_and_warns_in_legacy(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle, population=False)
    assert ("population_source", "W0 population source available", "WARN") in non_pass(full_records(world))
    write_manifest(semantic_bundle)
    results = full_records(world)
    assert ("population_source", "W0 population source available", "FAIL") in non_pass(results)
    assert not any("W0 code-first" in r["check"] for r in results)


def test_full_exact_tuple_corroboration_applicable_and_not_applicable(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    results = full_records(world)
    record = next(r for r in results if r["check"] == "exact-tuple corroboration")
    assert record["status"] == "PASS" and "not applicable" not in record["detail"]
    # Not uniquely exact: a population label differs (W0 still resolves by code).
    path = world.source / "demographic" / "district_population_estimates.csv"
    frame = pd.read_csv(path)
    frame.loc[0, "district"] = "Old Census Name"
    frame.to_csv(path, index=False)
    legacy = next(r for r in full_records(world) if r["check"] == "exact-tuple corroboration")
    assert legacy["status"] == "PASS" and legacy["detail"].startswith("not applicable")


def test_full_exact_tuple_not_applicable_is_fail_in_national_mode(semantic_bundle, tmp_path, spy_oracle):
    world = make_world(tmp_path, semantic_bundle)
    path = world.source / "demographic" / "district_population_estimates.csv"
    frame = pd.read_csv(path)
    frame.loc[0, "district"] = "Old Census Name"
    frame.to_csv(path, index=False)
    results = full_records(world, national=True, code_root=tmp_path / "missing-repo")
    assert ("population_source", "exact-tuple corroboration", "FAIL") in non_pass(results)


# source port classification
def set_nearest_port(root, name, row=0):
    for table in (SUMMARY, SUMMARY_PQ):
        edit_table(root, table, lambda f: f.loc.__setitem__((row, "nearest_port_name"), name))


def port_record(results):
    return next(r for r in results if r["check"] == "selected-port classification")


def test_full_port_classification_minor_selected_is_valid(semantic_bundle, tmp_path):
    record = port_record(full_records(make_world(tmp_path, semantic_bundle)))
    assert record["status"] == "PASS" and "minor_or_no_column=1" in record["detail"] and "non_string=2" in record["detail"]


def test_full_port_classification_unknown_name_fails(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    set_nearest_port(semantic_bundle, "Atlantis Port")
    record = port_record(full_records(world))
    assert record["status"] == "WARN" and "unknown=1" in record["detail"]
    write_manifest(semantic_bundle)
    assert port_record(full_records(world))["status"] == "FAIL"


def test_full_port_classification_selected_major_must_map_to_a_matrix_port(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    set_nearest_port(semantic_bundle, "Synthetic")  # A source major port with no one of the 12 matrix columns.
    record = port_record(full_records(world))
    assert record["status"] == "WARN" and "unmapped_major=1" in record["detail"]


def test_full_port_classification_selected_major_that_maps_is_parity_checked(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    pd.DataFrame({"name": ["Synthetic", "Mumbai"], "hub_type": ["major_port", "major_port"], "latitude": [18., 19.], "longitude": [72., 73.]}).to_csv(world.source / "logistics_hubs" / "ports.csv", index=False)
    record = port_record(full_records(world))
    assert record["status"] == "PASS" and "parity_checked=1" in record["detail"] and "major_rows=1" in record["detail"]


# source-condition disclosures
def test_full_source_disclosure_warns_without_correcting_anything(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle, shared=True)
    before = (world.source / "administrative" / "india_districts_lgd.geojson").read_bytes()
    results = full_records(world)
    warns = [(r["dataset"], r["check"]) for r in results if r["status"] == "WARN"]
    assert ("source_disclosures", CROSS) in warns
    detail = next(r["detail"] for r in results if r["check"] == CROSS)
    assert "Maharashtra" in detail and "Lakshadweep" in detail and "cause" not in detail.lower()
    assert before == (world.source / "administrative" / "india_districts_lgd.geojson").read_bytes()
    assert ("source_disclosures", "source identity validation", "FAIL") not in non_pass(results)


def test_full_source_with_same_state_duplicate_fails_identity_validation(semantic_bundle, tmp_path):
    world = make_world(tmp_path, semantic_bundle)
    declared = world.source / "administrative" / "india_districts_lgd.geojson"
    frame = gpd.read_file(declared)
    frame.loc[1, ["state", "district_code"]] = ["Maharashtra", 1.]
    frame.to_file(declared, driver="GeoJSON")
    assert ("source_disclosures", "source identity validation", "FAIL") in non_pass(full_records(world))


# ---- national mode (synthetic code tree and sources; the real S5 entry is never faked) -----------

def national_world(tmp_path, bundle):
    """Truthful version-2 national fixture: a real small NH capture, real ISO outputs, real coverage; only the non-ISO tables stay structural."""
    from scripts.analyze import producer_limitations as limitations
    from scripts.analyze import producer_manifest as pm
    from scripts.analyze.producer_io import sha256_file
    world = make_world(tmp_path, bundle, shared=True)  # A cross-state shared code gives the sidecar a disclosure.
    repo = tmp_path / "repo"
    (repo / "scripts" / "analyze").mkdir(parents=True)
    (repo / pm.NATIONAL_CODE_ENTRY).write_text("from scripts.analyze import helper\n")
    (repo / "scripts" / "analyze" / "helper.py").write_text("VALUE = 1\n")
    code_hashes = {logical: sha256_file(repo / logical) for logical in pm.national_code_closure(repo)}
    n1d_write_small_nh(world.source)  # The real small highway source exists before any input is hashed.
    inputs = {}
    for logical in (*pm.NATIONAL_FIXED_INPUTS, pm.NATIONAL_HIGHWAY_SOURCES[0]):
        path = world.source / logical
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(("synthetic " + logical).encode())
        inputs[logical] = path
    graph, coverage = n1d_real_graph_isochrones_and_coverage(world, bundle, tmp_path)  # Separate setup cache; never world.cache.
    sidecar = limitations.build_limitations(world.source, {n: bundle / n for n in pm.REQUIRED_ARTIFACTS}, "synthetic-national", "linux_ru_maxrss",
                                            isochrone_coverage=coverage)
    pm.atomic_write_manifest(sidecar, bundle / limitations.SIDECAR_FILENAME)
    manifest = write_manifest(bundle, inputs=inputs, graph=graph, code_hashes=code_hashes, run_id="synthetic-national",
                              resources={"elapsed_seconds": 12.5, "peak_memory_bytes": 123456789})  # Synthetic fixture values.
    assert pm.validate_manifest(bundle / pm.MANIFEST_FILENAME, bundle, source_root=world.source, code_root=repo) == []
    assert pm.validate_national_profile(manifest, code_root=repo) == []
    assert limitations.validate_limitations(bundle / limitations.SIDECAR_FILENAME, manifest, source_root=world.source, require_coverage=True) == []
    world.repo, world.inputs = repo, inputs
    return world


def test_national_fast_reports_closure_deferred_and_validates_profile_and_sidecar_format(semantic_bundle, tmp_path):
    world = national_world(tmp_path, semantic_bundle)
    results = fast_records(world.root, national=True)
    assert not [r for r in results if r["status"] == "FAIL"]
    from scripts.analyze.producer_manifest import CODE_CLOSURE_DEFERRED
    deferred = [r for r in results if r["status"] == "WARN" and CODE_CLOSURE_DEFERRED in r["detail"]]
    assert len(deferred) == 1 and "certified" not in deferred[0]["detail"].lower()
    assert any(r["check"] == "national profile" and r["status"] == "PASS" for r in results)
    assert any(r["dataset"] == "source_disclosures" and r["check"] == "sidecar format validation" and r["status"] == "PASS" for r in results)


def test_national_fast_rejects_generic_manifest_and_missing_or_obsolete_sidecar(semantic_bundle, tmp_path):
    world = national_world(tmp_path, semantic_bundle)
    sidecar = world.root / "producer_limitations.json"
    document = json.loads(sidecar.read_text())
    document["route_only_outputs"] = []  # Obsolete key from the superseded schema.
    sidecar.write_text(json.dumps(document))
    assert any(r["dataset"] == "source_disclosures" and r["status"] == "FAIL" for r in fast_records(world.root, national=True))
    sidecar.unlink()
    assert any(r["dataset"] == "source_disclosures" and r["status"] == "FAIL" for r in fast_records(world.root, national=True))
    write_manifest(world.root)  # Generic (non-national) manifest.
    assert any(r["check"] == "national profile" and r["status"] == "FAIL" for r in fast_records(world.root, national=True))
    assert not any(r["check"] == "national profile" for r in fast_records(world.root))


def test_national_full_runs_oracles_only_after_actual_closure_and_inputs_are_verified(semantic_bundle, tmp_path, spy_oracle):
    world = national_world(tmp_path, semantic_bundle)
    results = full_records(world, national=True, code_root=world.repo)
    assert not [r for r in results if r["status"] == "FAIL"], non_pass(results)
    assert any(r["check"] == "sidecar source-grounded validation" and r["status"] == "PASS" for r in results)
    assert any(r["check"] == CROSS and r["status"] == "WARN" for r in results)
    assert len(spy_oracle) == 1 and not world.cache.exists()


@pytest.mark.parametrize("breakage", ["input_missing", "generic_manifest", "closure_mismatch", "no_entry", "input_outside_root"])
def test_national_full_blocks_before_any_cache_when_prerequisites_fail(semantic_bundle, tmp_path, spy_oracle, breakage):
    world = national_world(tmp_path, semantic_bundle)
    code_root = world.repo
    if breakage == "input_missing":
        next(iter(world.inputs.values())).unlink()
    elif breakage == "generic_manifest":
        write_manifest(world.root)
    elif breakage == "closure_mismatch":
        (world.repo / "scripts" / "analyze" / "extra.py").write_text("")
        (world.repo / "scripts" / "analyze" / "helper.py").write_text("from scripts.analyze import extra\n")
    elif breakage == "no_entry":
        (world.repo / "scripts" / "analyze" / "stage4_generate.py").unlink()
    else:
        outside = tmp_path / "outside.csv"
        outside.write_text("x")
        link = world.source / "rail" / "railway_stations.csv"
        link.unlink()
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("Symlink creation requires privileges on this Windows host")
    results = full_records(world, national=True, code_root=code_root)
    assert ("graph_oracle", "blocked: national prerequisites", "FAIL") in non_pass(results)
    assert not spy_oracle and not world.cache.exists()
    assert not any("C:\\" in r["detail"] or str(tmp_path) in r["detail"] for r in results)


def test_national_full_missing_real_s5_entry_is_blocked_not_stubbed(semantic_bundle, tmp_path, spy_oracle):
    world = national_world(tmp_path, semantic_bundle)
    results = full_records(world, national=True)  # code_root defaults to the real repository root.
    entry = audit.REPO_ROOT / "scripts" / "analyze" / "stage4_generate.py"
    if not entry.exists():
        assert ("graph_oracle", "blocked: national prerequisites", "FAIL") in non_pass(results) and not spy_oracle


def test_national_full_tampered_sidecar_disclosure_fails_source_grounding(semantic_bundle, tmp_path, spy_oracle):
    world = national_world(tmp_path, semantic_bundle)
    sidecar = world.root / "producer_limitations.json"
    document = json.loads(sidecar.read_text())
    document["source_identity_disclosures"][0]["rows"][0]["pop_2011"] = 999.  # Shape-valid but ungrounded.
    sidecar.write_text(json.dumps(document))
    assert not [r for r in fast_records(world.root, national=True) if r["status"] == "FAIL"]
    results = full_records(world, national=True, code_root=world.repo)
    assert ("source_disclosures", "sidecar source-grounded validation", "FAIL") in non_pass(results)


QUIET = ["audit_boundaries", "audit_rail", "audit_hubs", "audit_freight", "audit_demographics", "audit_geoparquet", "audit_catalog",
         "check_baseline", "audit_villages", "audit_analysis", "audit_roads"]


def quiet_other_audits(monkeypatch):
    for name in QUIET:
        monkeypatch.setattr(audit, name, lambda *a, **k: None)


def test_staged_validator_accepts_untampered_and_rejects_tampered_sidecar_before_publication(semantic_bundle, tmp_path, monkeypatch, spy_oracle):
    from scripts.analyze import stage4_publication as publication
    quiet_other_audits(monkeypatch)
    world = national_world(tmp_path, semantic_bundle)
    monkeypatch.setattr(audit, "REPO_ROOT", world.repo)  # The sanctioned synthetic code tree.
    reports, cache = tmp_path / "reports", tmp_path / "audit-cache"
    validator = audit.staged_validator(world.source, cache, reports)
    sidecar = world.root / "producer_limitations.json"
    good = sidecar.read_bytes()
    document = json.loads(good)
    document["source_identity_disclosures"][0]["rows"][0]["pop_2011"] = 999.
    sidecar.write_text(json.dumps(document))
    target, backup = tmp_path / "target", tmp_path / "backup"
    issues = validator(world.root)
    assert issues and any("sidecar source-grounded validation" in i for i in issues)
    with pytest.raises(ValueError):
        publication.promote_staged_outputs(world.root, target, backup, validate=validator)
    assert not target.exists() and not backup.exists()
    sidecar.write_bytes(good)
    assert validator(world.root) == []
    result = publication.promote_staged_outputs(world.root, target, backup, validate=validator)
    assert result["status"] == "published" and result["atomic_dataset_transaction"] is False
    assert sorted(p.name for p in target.iterdir()) == sorted(publication.PUBLICATION_NAMES)
    assert {p.name for p in reports.iterdir()} == {"audit_report.md", "audit_report_fast.md"}
    assert not cache.exists()


def test_staged_validator_reports_forbidden_roots_as_issues_without_creating_anything(semantic_bundle, tmp_path):
    validator = audit.staged_validator(tmp_path / "source", semantic_bundle / "cache-inside-stage", tmp_path / "reports")
    issues = validator(semantic_bundle)
    assert issues and issues[0].startswith("roots:")
    assert not (semantic_bundle / "cache-inside-stage").exists() and not (tmp_path / "reports").exists()


# ---------------------------------------------------------------------------------------------
# S3 final integration. Label: producer-contract integration with source-inventory audits isolated;
# not raw-inventory or national validation. The real generate_stage4, producers, oracles, validators, manifest,
# closure, limitations, identity/cost/gravity/population code, kernel and graph builder all run UNMOCKED on a small
# synthetic source. Only the eleven legacy source-inventory audits are replaced, by spies that emit NO record.
# ---------------------------------------------------------------------------------------------
from collections import Counter  # noqa: E402

INTEGRATION_LABEL = "producer-contract integration with source-inventory audits isolated; not raw-inventory or national validation"
INVENTORY_AUDITS = ("audit_boundaries", "audit_rail", "audit_hubs", "audit_freight", "audit_demographics", "audit_geoparquet", "audit_catalog",
                    "check_baseline", "audit_roads", "audit_villages", "audit_analysis")
CLOSURE_WARN = ("provenance", "code closure validation deferred to full mode")
CROSS_WARN, SAME_WARN = ("source_disclosures", CROSS), ("source_disclosures", SAME)


def write_integration_source(root):
    """Eleven national-shaped inputs, truthfully small; synthetic source conditions only, no real-data claim."""
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA
    from shapely.geometry import LineString, Polygon
    for directory in ("roads", "administrative", "logistics_hubs", "rail", "demographic"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    center = gpd.GeoSeries([Point(73., 19.)], crs=4326).to_crs(7755).iloc[0]

    def at(x, y):
        return (center.x + x, center.y + y)

    def box7755(x0, y0, x1, y1):
        return Polygon([at(x0, y0), at(x1, y0), at(x1, y1), at(x0, y1)])
    path = [at(0., 0.), at(10000., 0.), at(10000., 10000.), at(20000., 10000.)]
    lines = [LineString(path), LineString([at(2000000., 0.), at(2010000., 0.)]), LineString([path[0], path[3]])]
    nh = gpd.GeoDataFrame(dict(highway=["motorway", "primary", "primary"], nh=["1", "2", "3"]), geometry=lines, crs=7755).to_crs(4326)
    nh.to_parquet(root / "roads/india_nh_network.parquet", index=False)
    # Two Pune rows (one label, two codes) are valid overlapping polygons; code 1 is also used under Delhi.
    polygons = [box7755(-3000., -3000., 3000., 3000.), box7755(1000., -3000., 7000., 3000.), box7755(17000., 7000., 23000., 13000.),
                box7755(8000., -3000., 12000., 3000.), box7755(1999000., -3000., 2003000., 3000.), box7755(2999000., -3000., 3003000., 3000.)]
    districts = gpd.GeoDataFrame(dict(state=["Maharashtra", "Maharashtra", "Delhi", "Assam", "Lakshadweep", "Delhi"],
                                      district=["Pune", "Pune", "Dwarka", "NE", "Island", "Nazul"], district_code=[1., 2., 1., 3., 4., None]),
                                 geometry=gpd.GeoSeries(polygons, crs=7755).to_crs(4326), crs=4326)
    districts.to_file(root / "administrative/india_districts_lgd.geojson", driver="GeoJSON")
    pd.DataFrame(dict(state=districts.state, district=districts.district, district_code=districts.district_code,
                      pop_2011=[1000. * (i + 1) for i in range(6)], method="synthetic")).to_csv(root / "demographic/district_population_estimates.csv", index=False)
    hubs = gpd.GeoSeries([Point(*path[3]), Point(*path[1]), Point(*at(2000000., 0.)), Point(*at(3000000., 0.))], crs=7755).to_crs(4326)

    def hub(i):
        return dict(longitude=hubs.iloc[i].x, latitude=hubs.iloc[i].y)
    # The audited matrix schema lists the twelve major ports in PORT_KEYS order; ports.csv must carry them in that order.
    assert {f"drive_hours_to_{key}" for key in audit.PORT_KEYS} == set(PORT_MATRIX_DATA)
    names = ["Port of " + key.replace("_", " ") + " Port" for key in audit.PORT_KEYS]
    ports = pd.DataFrame(dict(name=names, hub_type=["major_port"] * 12, longitude=[hubs.iloc[0].x] * 12, latitude=[hubs.iloc[0].y] * 12))
    ports.loc[10, ["longitude", "latitude"]] = [hubs.iloc[3].x, hubs.iloc[3].y]
    ports.loc[11, "latitude"] = np.nan
    ports.to_csv(root / "logistics_hubs/ports.csv", index=False)
    pd.DataFrame([dict(name="Terminal", **hub(1)), dict(name="Tie", **hub(1)), dict(name="Invalid", longitude=73., latitude=np.nan)]).to_csv(root / "rail/freight_terminals.csv", index=False)
    pd.DataFrame([dict(station_name="RailA", **hub(0)), dict(station_name="RailB", **hub(1))]).to_csv(root / "rail/railway_stations.csv", index=False)
    pd.DataFrame([dict(name="IcdA", **hub(1))]).to_csv(root / "logistics_hubs/icds.csv", index=False)
    pd.DataFrame([dict(name="M1", **hub(0)), dict(name="M2", **hub(1)), dict(name="NoSnap", **hub(3)), dict(name="Bad", longitude=73., latitude=np.nan)]).to_csv(root / "logistics_hubs/mmlps.csv", index=False)
    pd.DataFrame([dict(name="AirIsland", **hub(2))]).to_csv(root / "logistics_hubs/air_cargo.csv", index=False)
    pd.DataFrame([dict(name="IcpA", **hub(0))]).to_csv(root / "logistics_hubs/icps.csv", index=False)
    pd.DataFrame([dict(name="TollA", **hub(0))]).to_csv(root / "roads/toll_plazas.csv", index=False)


def integration_world(tmp_path, run_id="synthetic-s3-integration"):
    from scripts.analyze import stage4_generate
    source, stage, cache = tmp_path / "source", tmp_path / "stage", tmp_path / "generation-cache"
    write_integration_source(source)
    summary = stage4_generate.generate_stage4(source_root=source, stage_root=stage, cache_root=cache, run_id=run_id)
    return SimpleNamespace(source=source, analysis=stage / "analysis", cache=tmp_path / "audit-cache", reports=tmp_path / "audit-reports",
                           run_id=run_id, summary=summary, tmp=tmp_path)


def isolate_inventory_audits(monkeypatch):
    """Recording spies only: they append their call and emit no PASS, WARN or FAIL record."""
    calls = []
    for name in INVENTORY_AUDITS:
        monkeypatch.setattr(audit, name, lambda *args, _name=name, **kwargs: calls.append((_name, args, kwargs)))
    return calls


def national_audit(world, *, fast):
    runner = audit.AuditRunner(fast=fast, source_root=world.source, artifact_root=world.analysis, cache_dir=world.cache,
                               require_manifest=True, national=True, report_dir=world.reports)
    results = runner.run()
    runner.write_report()
    return results


def warn_multiset(results):
    return Counter((r["dataset"], r["check"]) for r in results if r["status"] == "WARN")


def assert_registrations(calls, world, *, fast):
    names = [name for name, _, _ in calls]
    assert names == [*INVENTORY_AUDITS[:9], *([] if fast else INVENTORY_AUDITS[9:])]
    for name, args, kwargs in calls:
        if name == "check_baseline":
            assert args == (world.source, world.analysis) and kwargs == {}
        elif name == "audit_analysis":
            assert args == (world.source, world.analysis) and kwargs == {}
        elif name == "audit_roads":
            assert args == (world.source,) and kwargs == {"fast": fast}
        else:
            assert args == (world.source,) and kwargs == {}


def test_integration_clean_generation_passes_fast_full_and_staged_validator_with_exact_warn_multisets(tmp_path, monkeypatch):
    """producer-contract integration with source-inventory audits isolated; not raw-inventory or national validation"""
    world = integration_world(tmp_path)
    assert world.summary["peak_memory_bytes"] > 0 and world.summary["resource_method"] in ("windows_peak_working_set", "linux_ru_maxrss")  # Observation only.
    calls = isolate_inventory_audits(monkeypatch)
    fast = national_audit(world, fast=True)
    assert_registrations(calls, world, fast=True)
    calls.clear()
    full = national_audit(world, fast=False)
    assert_registrations(calls, world, fast=False)
    for results in (fast, full):
        assert [r for r in results if r["status"] == "FAIL"] == []
    # Fast: the closure-deferred WARN plus the two summary-derived disclosures.
    bounded = n1d_expected_bounded_hubs(world)  # Statically asserted identities first; the Counter is then derived from them.
    assert warn_multiset(fast) == Counter({CLOSURE_WARN: 1, CROSS_WARN: 1, SAME_WARN: 1, N1D_WARN_KEY: len(bounded)})
    # Full also runs the fast producer pass (same three WARNs) and adds the source-grounded disclosures: duplicates are counted, not removed.
    assert warn_multiset(full) == Counter({CLOSURE_WARN: 1, CROSS_WARN: 2, SAME_WARN: 2, N1D_WARN_KEY: len(bounded)})  # The same single coverage contribution.
    assert any(r["check"] == "national profile" and r["status"] == "PASS" for r in full)  # Real closure, code_root = REPO_ROOT.
    assert any(r["check"] == "sidecar source-grounded validation" and r["status"] == "PASS" for r in full)
    assert any(r["check"] == "national profile" and r["status"] == "PASS" for r in fast) and any(r["check"] == "sidecar format validation" for r in fast)
    assert [r["status"] for r in fast if (r["dataset"], r["check"]) == N1D_COHERENCE_KEY] == ["PASS"]  # New: exactly one coherence PASS (fast only).
    assert [r["status"] for r in full if (r["dataset"], r["check"]) == N1D_COHERENCE_KEY] == ["PASS"] and [r for r in full if r["dataset"] == "coverage_oracle"]
    assert all(r["status"] == "PASS" for r in full if r["dataset"] == "coverage_oracle")  # The N1c oracle is registered and passes.
    oracles = [r for r in full if r["dataset"] in ("graph_oracle", "route_oracle", "category_oracle", "iso_oracle")]
    assert {r["dataset"] for r in oracles} == {"graph_oracle", "route_oracle", "category_oracle", "iso_oracle"} and all(r["status"] == "PASS" for r in oracles)
    identity = [r for r in full if r["check"].endswith("exact source identities including null codes")]
    assert {r["check"] for r in identity} == {f"{prefix}{label}: exact source identities including null codes" for prefix in ("", "category sample: ")
                                              for label in ("summary CSV", "summary parquet")} | {"matrix: exact source identities including null codes"}
    assert all(r["status"] == "PASS" for r in identity)
    assert any(r["dataset"] == "category_oracle" and r["check"].startswith("summary CSV row") for r in full)
    assert any(r["dataset"] == "category_oracle" and r["check"].startswith("summary parquet row") for r in full)
    assert sorted(path.name for path in world.reports.iterdir()) == ["audit_report.md", "audit_report_fast.md"]
    assert not (world.source / "analysis").exists() and not any(p.name.startswith("audit_report") for p in world.analysis.iterdir())
    assert not (world.analysis / "cache").exists() and world.cache.exists() and not str(world.cache).startswith(str(world.source))
    assert audit.staged_validator(world.source, world.cache, world.reports)(world.analysis) == []


def rebuild_provenance(world):
    """The REAL helpers, original run id, resource method, graph, config, code hashes and resources, real input paths."""
    from scripts.analyze import producer_limitations as limitations
    from scripts.analyze import producer_manifest as manifest
    original = manifest.read_manifest(world.analysis / manifest.MANIFEST_FILENAME)
    sidecar = manifest.read_manifest(world.analysis / limitations.SIDECAR_FILENAME)
    method = next(key for key, value in limitations.RESOURCE_METHODS.items() if value == sidecar["resource_measurement_method"])
    artifacts = {name: world.analysis / name for name in manifest.REQUIRED_ARTIFACTS}
    # The ORIGINAL coverage is retained only while its surfaces are unchanged: ISO bytes, every input and the code closure.
    from scripts.analyze.producer_io import sha256_file
    try:
        try:
            discovered = manifest.national_code_closure(audit.REPO_ROOT)  # Only this INVOCATION may normalize parse/import errors.
        except (ImportError, SyntaxError, UnicodeDecodeError):
            raise ValueError("retained coverage surfaces changed") from None
        closure = {logical: sha256_file(audit.REPO_ROOT / logical) for logical in discovered}
        changed = (any(sha256_file(artifacts[name]) != original["artifacts"][name]["sha256"] for name in manifest.REQUIRED_ARTIFACTS[5:])
                   or any(sha256_file(world.source / logical) != entry["sha256"] for logical, entry in original["inputs"].items())
                   or closure != original["code_hashes"])
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, PermissionError):  # A retained surface is absent or unreadable.
        raise ValueError("retained coverage surfaces changed") from None
    if changed:
        raise ValueError("retained coverage surfaces changed")
    manifest.atomic_write_manifest(limitations.build_limitations(world.source, artifacts, original["run_id"], method,
                                                                 isochrone_coverage=sidecar["isochrone_coverage"]), world.analysis / limitations.SIDECAR_FILENAME)
    document = manifest.build_manifest(original["run_id"], artifacts, {logical: world.source / logical for logical in original["inputs"]},
                                       original["graph"], original["config"], original["code_hashes"], original["resources"])
    manifest.atomic_write_manifest(document, world.analysis / manifest.MANIFEST_FILENAME)
    assert manifest.validate_manifest(world.analysis / manifest.MANIFEST_FILENAME, world.analysis, source_root=world.source, code_root=audit.REPO_ROOT) == []
    assert limitations.validate_limitations(world.analysis / limitations.SIDECAR_FILENAME, document, source_root=world.source, require_coverage=True) == []


def test_integration_category_sample_misalignment_fails_blocks_the_validator_and_refuses_promotion(tmp_path, monkeypatch):
    """producer-contract integration with source-inventory audits isolated; not raw-inventory or national validation"""
    from scripts.analyze import stage4_publication as publication
    world = integration_world(tmp_path)
    parquet = world.analysis / "nh_district_travel_time_summary.parquet"
    frame = pd.read_parquet(parquet)
    frame.loc[0, "district"] = "PUNE"  # A literal-case change in the staged summary parquet only.
    frame.to_parquet(parquet, index=False)
    rebuild_provenance(world)
    n1d_assert_strict_v2_and_corroborated(world.source, world.analysis)  # New: the retained schema-2 sidecar is valid BEFORE the audit.
    isolate_inventory_audits(monkeypatch)
    full = national_audit(world, fast=False)
    assert warn_multiset(full)[N1D_WARN_KEY] == len(n1d_expected_bounded_hubs(world))  # New: the disclosure Counter, once.
    prefix = "category sample: summary parquet: exact source identities including null codes"
    assert ("route_oracle", prefix, "FAIL") in [(r["dataset"], r["check"], r["status"]) for r in full]
    # No category coverage is reported for the misaligned frame; the aligned CSV frame keeps its category cells.
    assert not any(r["dataset"] == "category_oracle" and r["check"].startswith("summary parquet row") for r in full)
    assert any(r["dataset"] == "category_oracle" and r["check"].startswith("summary CSV row") and r["status"] == "PASS" for r in full)
    # Other legitimate FAILs caused by the same change are present, not suppressed.
    assert any(r["status"] == "FAIL" and r["check"] == "summary parquet: exact source identities including null codes" for r in full)
    validator = audit.staged_validator(world.source, world.cache, world.reports)
    issues = validator(world.analysis)
    assert f"route_oracle: {prefix}" in issues
    target, backup = tmp_path / "target", tmp_path / "backup"
    target.mkdir()
    for name in publication.PUBLICATION_NAMES:
        (target / name).write_bytes(b"previous generation " + name.encode())
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    with pytest.raises(ValueError):
        publication.promote_staged_outputs(world.analysis, target, backup, validate=validator)
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before and not backup.exists()


# =============================================================================================
# N1d: sidecar coverage coherence (graph-free) and the component-bounded disclosure. Appended region; every test below is
# a NEW requirement. The coherence check certifies stored-output consistency against DECLARED facts only (declared
# component sizes are not independent graph truth) and makes no catchment-accuracy claim.
# =============================================================================================
N1D_DATASET = "isochrone_coverage"
N1D_COHERENCE = "sidecar coverage equals ISO artifact properties and geometry/status coherence"
N1D_WARN = "component-bounded catchment (disclosed)"
N1D_COHERENCE_KEY, N1D_WARN_KEY = (N1D_DATASET, N1D_COHERENCE), (N1D_DATASET, N1D_WARN)
N1D_FIELDS = ("nh_graph", "nh_distance_graph", "nh_node_xy", "nh_comp_labels", "nh_tree", "nh_bridge_mask")
N1D_ISO = ("major_ports_isochrones.geojson", "mmlp_isochrones.geojson")


def n1d_pm():
    from scripts.analyze import producer_manifest
    return producer_manifest


def n1d_limitations():
    from scripts.analyze import producer_limitations
    return producer_limitations


def n1d_write_small_nh(source):
    """A real small highway source (four vertices, connected) at the synthetic hubs' location."""
    from shapely.geometry import LineString
    center = gpd.GeoSeries([Point(72., 18.)], crs=4326).to_crs(7755).iloc[0]

    def at(x, y):
        return (center.x + x, center.y + y)
    path = [at(0., 0.), at(10000., 0.), at(10000., 10000.), at(20000., 10000.)]
    nh = gpd.GeoDataFrame(dict(highway=["motorway", "primary"], nh=["1", "2"]), geometry=[LineString(path), LineString([path[0], path[3]])], crs=7755).to_crs(4326)
    (source / "roads").mkdir(parents=True, exist_ok=True)
    nh.to_parquet(source / "roads/india_nh_network.parquet", index=False)


def n1d_real_graph_isochrones_and_coverage(world, bundle, tmp_path):
    """SETUP ONLY: canonical capture (validated), actual fingerprint/counts, real ISO build+write, N1a facts and coverage from the written files."""
    from scripts.analyze import isochrone_coverage as coverage_module
    from scripts.analyze.generate_isochrones import build_isochrones, write_isochrones
    from scripts.clean import graph_builder as builder
    pm = n1d_pm()
    cache = tmp_path / "n1d-setup-cache"  # Never world.cache and never an audit cache.
    nh, _selected = builder.load_highway_source(world.source)
    values = builder.load_or_build_cached_graph(nh, cache_dir=cache)
    builder.validate_graph_metadata(*values)
    graph = SimpleNamespace(**dict(zip(N1D_FIELDS, values)))
    record = {"fingerprint": builder.compute_graph_fingerprint(nh), "version": builder.CACHE_VERSION, "node_count": int(values[0].shape[0]),
              "edge_count": int(values[0].nnz), "selected_source": pm.NATIONAL_HIGHWAY_SOURCES[0]}
    ports, mmlps, _ = build_isochrones(data_dir=world.source, cache_dir=cache)
    write_isochrones(ports, mmlps, ports_path=bundle / pm.REQUIRED_ARTIFACTS[5], mmlps_path=bundle / pm.REQUIRED_ARTIFACTS[6])
    rows = pd.read_csv(world.source / "logistics_hubs/ports.csv")
    port_facts = coverage_module.component_facts(graph, rows[rows.hub_type == "major_port"].to_dict("records"), "major_port")
    mmlp_facts = coverage_module.component_facts(graph, pd.read_csv(world.source / "logistics_hubs/mmlps.csv").to_dict("records"), "mmlp")
    coverage = coverage_module.coverage_entries(pm.read_manifest(bundle / pm.REQUIRED_ARTIFACTS[5]), pm.read_manifest(bundle / pm.REQUIRED_ARTIFACTS[6]),
                                                port_facts, mmlp_facts)
    return record, coverage


def n1d_expected_bounded_hubs(world):
    """Statically known bounded hubs of the integration source: every PORT_KEYS major port except the last two, then M1 and M2."""
    expected = [("major_port", i, "Port of " + key.replace("_", " ") + " Port") for i, key in enumerate(audit.PORT_KEYS[:10])] + [("mmlp", 0, "M1"), ("mmlp", 1, "M2")]
    assert len(expected) == 12 and len(set(expected)) == 12  # Each hub once, in order.
    entries = n1d_pm().read_manifest(world.analysis / "producer_limitations.json")["isochrone_coverage"]["entries"]
    assert [(e["hub_type"], e["hub_order"], e["hub_name"]) for e in entries if e["component_bounded_thresholds"]] == expected
    return expected


def n1d_assert_strict_v2_and_corroborated(source, analysis):
    pm, limitations = n1d_pm(), n1d_limitations()
    manifest = pm.read_manifest(analysis / pm.MANIFEST_FILENAME)
    assert limitations.validate_limitations(analysis / limitations.SIDECAR_FILENAME, manifest, source_root=source, require_coverage=True) == []
    records = captured(audit.audit_producers_fast, analysis, national=True)
    assert [r["status"] for r in records if (r["dataset"], r["check"]) == N1D_COHERENCE_KEY] == ["PASS"]


def n1d_rewrite_sidecar(world, change):
    path = world.root / "producer_limitations.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    result = change(document)
    n1d_pm().atomic_write_manifest(document if result is None else result, path)


def n1d_refresh(world):
    """Rebuild the sidecar (retaining its coverage) and the manifest after an ISO edit, so the manifest stays usable."""
    pm, limitations = n1d_pm(), n1d_limitations()
    original = pm.read_manifest(world.root / pm.MANIFEST_FILENAME)
    sidecar = pm.read_manifest(world.root / limitations.SIDECAR_FILENAME)
    method = next(key for key, value in limitations.RESOURCE_METHODS.items() if value == sidecar["resource_measurement_method"])
    artifacts = {name: world.root / name for name in pm.REQUIRED_ARTIFACTS}
    pm.atomic_write_manifest(limitations.build_limitations(world.source, artifacts, original["run_id"], method, isochrone_coverage=sidecar["isochrone_coverage"]),
                             world.root / limitations.SIDECAR_FILENAME)
    document = pm.build_manifest(original["run_id"], artifacts, world.inputs, original["graph"], original["config"], original["code_hashes"], original["resources"])
    pm.atomic_write_manifest(document, world.root / pm.MANIFEST_FILENAME)


def n1d_edit_iso(world, name, change):
    path = world.root / name
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def n1d_coherence(results):
    return [r for r in results if (r["dataset"], r["check"]) == N1D_COHERENCE_KEY]


def n1d_warns(results):
    return [r for r in results if (r["dataset"], r["check"]) == N1D_WARN_KEY]


def n1d_quiet_staged(world, monkeypatch):
    quiet_other_audits(monkeypatch)
    monkeypatch.setattr(audit, "REPO_ROOT", world.repo)


# --- T03: the truthful setup calls the REAL validators (witness installed BEFORE explicit construction) -----------------
def test_n1d_national_world_setup_calls_real_validators_on_truthful_facts(semantic_bundle, tmp_path, monkeypatch):
    from scripts.clean import graph_builder as builder
    pm, limitations = n1d_pm(), n1d_limitations()
    calls = {"manifest": [], "profile": [], "limitations": []}
    real = {"manifest": pm.validate_manifest, "profile": pm.validate_national_profile, "limitations": limitations.validate_limitations}

    def recorder(key):
        def record(*args, **kwargs):
            issues = real[key](*args, **kwargs)  # The real validator; its result is returned unaltered.
            calls[key].append((args, dict(kwargs), issues))
            return issues
        return record
    monkeypatch.setattr(pm, "validate_manifest", recorder("manifest"))
    monkeypatch.setattr(pm, "validate_national_profile", recorder("profile"))
    monkeypatch.setattr(limitations, "validate_limitations", recorder("limitations"))
    world = national_world(tmp_path, semantic_bundle)  # Explicit construction AFTER the witnesses.
    assert [len(v) for v in calls.values()] == [1, 1, 1] and all(c[0][2] == [] for c in calls.values())
    (m_args, m_kwargs, _), (p_args, p_kwargs, _), (l_args, l_kwargs, _) = calls["manifest"][0], calls["profile"][0], calls["limitations"][0]
    assert Path(m_args[0]) == world.root / pm.MANIFEST_FILENAME and Path(m_args[1]) == world.root
    assert Path(m_kwargs["source_root"]) == world.source and Path(m_kwargs["code_root"]) == world.repo
    assert p_args[0] == pm.read_manifest(world.root / pm.MANIFEST_FILENAME) and Path(p_kwargs["code_root"]) == world.repo
    assert Path(l_args[0]) == world.root / limitations.SIDECAR_FILENAME and l_args[1] == p_args[0]
    assert Path(l_kwargs["source_root"]) == world.source and l_kwargs["require_coverage"] is True
    # Truthful facts: the manifest graph record equals an independent canonical capture of the actual source.
    nh, _ = builder.load_highway_source(world.source)
    values = builder.load_or_build_cached_graph(nh, cache_dir=tmp_path / "independent-cache")
    builder.validate_graph_metadata(*values)
    assert p_args[0]["graph"] == {"fingerprint": builder.compute_graph_fingerprint(nh), "version": builder.CACHE_VERSION, "node_count": int(values[0].shape[0]),
                                  "edge_count": int(values[0].nnz), "selected_source": pm.NATIONAL_HIGHWAY_SOURCES[0]}
    assert not world.cache.exists()  # The setup cache is separate; no audit cache was touched.
    sidecar = pm.read_manifest(world.root / limitations.SIDECAR_FILENAME)
    assert sidecar["schema_version"] == 2 and [e["hub_name"] for e in sidecar["isochrone_coverage"]["entries"]] == ["Synthetic", "Synthetic"]
    assert all(e["snap_component_node_count"] == 4 for e in sidecar["isochrone_coverage"]["entries"])  # The four-vertex NH, counted by the builders.
    iso = pm.read_manifest(world.root / N1D_ISO[0])["features"][0]["properties"]
    assert iso["coverage_method"] == "approximate_highway_catchment" and iso["hub_name"] == "Synthetic"  # Real producer output, not handmade.


# --- T01: applicability table -------------------------------------------------------------------------------------------
def n1d_case_absent(world):
    (world.root / "producer_limitations.json").unlink()


def n1d_case_garbage(world):
    (world.root / "producer_limitations.json").write_bytes(b"\x00not json")


def n1d_case_nonobject(world):
    (world.root / "producer_limitations.json").write_text("[1]")


def n1d_set_version(value):
    return lambda world: n1d_rewrite_sidecar(world, lambda d: d.__setitem__("schema_version", value))


def n1d_case_v1_extra_key(world):
    n1d_rewrite_sidecar(world, lambda d: (d.pop("isochrone_coverage"), d.__setitem__("schema_version", 1), d.__setitem__("route_only_outputs", [])))


N1D_APPLICABILITY = {
    "absent": n1d_case_absent, "garbage": n1d_case_garbage, "nonobject": n1d_case_nonobject, "schema_bool": n1d_set_version(True),
    "schema_float": n1d_set_version(2.0), "schema_string": n1d_set_version("2"), "unknown_version": n1d_set_version(3), "v1_wrong_keys": n1d_case_v1_extra_key,
}


@pytest.mark.parametrize("name", sorted(N1D_APPLICABILITY))
def test_n1d_applicability_generic_and_national_for_absent_and_unsupported_sidecars(semantic_bundle, tmp_path, name):
    world = national_world(tmp_path, semantic_bundle)
    N1D_APPLICABILITY[name](world)
    generic, national = n1d_coherence(fast_records(world.root)), n1d_coherence(fast_records(world.root, national=True))
    if name == "absent":
        assert generic == []  # Row A: no new record and no WARN in generic mode.
        assert [(r["status"], r["detail"]) for r in national] == [("FAIL", "coverage: schema-2 sidecar required")]
    else:
        assert [(r["status"], r["detail"]) for r in generic] == [("FAIL", "coverage: sidecar unreadable or unsupported")]
        assert [(r["status"], r["detail"]) for r in national] == [("FAIL", "coverage: sidecar unreadable or unsupported")]
    assert n1d_warns(fast_records(world.root)) == [] and n1d_warns(fast_records(world.root, national=True)) == []


def test_n1d_genuinely_valid_v1_is_legacy_in_generic_mode_but_rejected_by_national_fast_full_and_staged(semantic_bundle, tmp_path, monkeypatch, spy_oracle):
    pm, limitations = n1d_pm(), n1d_limitations()
    world = national_world(tmp_path, semantic_bundle)
    v1 = limitations.build_limitations(world.source, {n: world.root / n for n in pm.REQUIRED_ARTIFACTS}, "synthetic-national", "linux_ru_maxrss")
    pm.atomic_write_manifest(v1, world.root / limitations.SIDECAR_FILENAME)
    manifest = pm.read_manifest(world.root / pm.MANIFEST_FILENAME)
    assert v1["schema_version"] == 1 and limitations.validate_limitations(world.root / limitations.SIDECAR_FILENAME, manifest, source_root=world.source) == []
    generic = fast_records(world.root)
    assert n1d_coherence(generic) == [] and n1d_warns(generic) == []  # Row B: structural legacy, not a certificate.
    national = fast_records(world.root, national=True)
    assert [(r["status"], r["detail"]) for r in n1d_coherence(national)] == [("FAIL", "coverage: schema-2 sidecar required")] and n1d_warns(national) == []
    full = full_records(world, national=True, code_root=world.repo)
    blocked = [r for r in full if r["check"] == "blocked: national prerequisites"]
    assert len(blocked) == 1 and blocked[0]["status"] == "FAIL"
    assert blocked[0]["detail"].index("national: schema-2 sidecar source validation failed") < blocked[0]["detail"].index("national: isochrone coverage artifact corroboration failed")
    assert not spy_oracle and not world.cache.exists() and not [r for r in full if (r["dataset"], r["check"]) == N1D_COHERENCE_KEY]
    n1d_quiet_staged(world, monkeypatch)
    issues = audit.staged_validator(world.source, tmp_path / "audit-cache", tmp_path / "reports")(world.root)
    assert f"{N1D_DATASET}: {N1D_COHERENCE}" in issues and "graph_oracle: blocked: national prerequisites" in issues and not (tmp_path / "audit-cache").exists()


def test_n1d_national_coherence_record_survives_a_missing_manifest(semantic_bundle):
    results = fast_records(semantic_bundle, national=True)  # No manifest: _national_fast_checks returns early.
    assert [(r["status"], r["detail"]) for r in n1d_coherence(results)] == [("FAIL", "coverage: schema-2 sidecar required")]
    assert any(r["dataset"] == "major_ports_isochrones.geojson" for r in results) and any(r["dataset"] == "mmlp_isochrones.geojson" for r in results)


def test_n1d_recognizable_v2_without_a_usable_manifest_needs_a_valid_manifest(semantic_bundle, tmp_path):
    world = national_world(tmp_path, semantic_bundle)
    (world.root / "producer_manifest.json").unlink()
    for national in (False, True):
        results = fast_records(world.root, national=national)
        assert [r["detail"] for r in n1d_coherence(results)] == ["coverage: valid manifest required"] and n1d_warns(results) == []
    manifest_path = world.root / "producer_manifest.json"
    n1d_pm().atomic_write_manifest(json.loads(json.dumps({"schema_version": 1})), manifest_path)  # Present but unusable.
    assert [r["detail"] for r in n1d_coherence(fast_records(world.root))] == ["coverage: valid manifest required"]


def test_n1d_valid_national_world_gets_one_pass_then_one_warn_per_bounded_hub_not_per_threshold(semantic_bundle, tmp_path):
    world = national_world(tmp_path, semantic_bundle)
    for national in (False, True):
        results = fast_records(world.root, national=national)
        assert [r["status"] for r in n1d_coherence(results)] == ["PASS"] and not [r for r in results if r["status"] == "FAIL"]
        warns = n1d_warns(results)
        assert [r["status"] for r in warns] == ["WARN", "WARN"]  # One per hub (port, mmlp), not four thresholds each.
        assert "major_port 0 Synthetic" in warns[0]["detail"] and "mmlp 0 Synthetic" in warns[1]["detail"]
        assert results.index(warns[0]) > results.index(n1d_coherence(results)[0])  # Declaration order, after the coherence record.
        assert str(tmp_path) not in " ".join(r["detail"] for r in warns)


def test_n1d_saturation_alone_never_warns_and_bounded_hubs_in_declaration_order(semantic_bundle, tmp_path):
    world = national_world(tmp_path, semantic_bundle)

    def bigger_port_component(document):
        entry = document["isochrone_coverage"]["entries"][0]
        entry["snap_component_node_count"] += 1  # Counts stay saturated (all equal) but no longer fill the component.
        entry["component_bounded_thresholds"] = []
    n1d_rewrite_sidecar(world, bigger_port_component)
    sidecar = n1d_pm().read_manifest(world.root / "producer_limitations.json")["isochrone_coverage"]["entries"][0]
    assert sidecar["saturated_across_ok_thresholds"] is True
    results = fast_records(world.root, national=True)
    assert [r["status"] for r in n1d_coherence(results)] == ["PASS"] and len(n1d_warns(results)) == 1 and "mmlp 0 Synthetic" in n1d_warns(results)[0]["detail"]


# --- T02: the three existing national bindings plus the independent prerequisite use require_coverage=True -------------
def n1d_recording_validator(monkeypatch):
    limitations = n1d_limitations()
    real, calls = limitations.validate_limitations, []

    def record(*args, **kwargs):
        issues = real(*args, **kwargs)
        calls.append((args, dict(kwargs), issues))
        return issues
    monkeypatch.setattr(limitations, "validate_limitations", record)
    return calls


def test_n1d_national_fast_calls_require_coverage_without_inventing_a_source_root(semantic_bundle, tmp_path, monkeypatch):
    world = national_world(tmp_path, semantic_bundle)
    calls = n1d_recording_validator(monkeypatch)
    fast_records(world.root, national=True)
    assert calls and all(kwargs.get("require_coverage") is True and "source_root" not in kwargs for _, kwargs, _ in calls)


def test_n1d_direct_full_calls_require_coverage_with_the_source_root_and_the_prerequisite_is_independent(semantic_bundle, tmp_path, monkeypatch, spy_oracle):
    world = national_world(tmp_path, semantic_bundle)
    calls = n1d_recording_validator(monkeypatch)
    monkeypatch.setattr(audit, "_full_disclosures", lambda *args, **kwargs: None)  # The disclosure call never runs here.
    results = full_records(world, national=True, code_root=world.repo)
    assert calls and all(kwargs.get("require_coverage") is True and Path(kwargs["source_root"]) == world.source for _, kwargs, _ in calls)
    assert not [r for r in results if r["status"] == "FAIL"] and len(spy_oracle) == 1


def test_n1d_prerequisite_enforces_schema_two_even_when_the_disclosure_section_never_validates(semantic_bundle, tmp_path, monkeypatch, spy_oracle):
    world = national_world(tmp_path, semantic_bundle)
    monkeypatch.setattr(audit, "_full_disclosures", lambda *args, **kwargs: None)  # As if it had returned early, before its own validator call.
    sidecar = world.root / "producer_limitations.json"
    document = json.loads(sidecar.read_text())
    document["source_identity_disclosures"][0]["rows"][0]["pop_2011"] = 999.  # Shape-valid but ungrounded in the source.
    sidecar.write_text(json.dumps(document))
    results = full_records(world, national=True, code_root=world.repo)
    blocked = [r for r in results if r["check"] == "blocked: national prerequisites"]
    assert len(blocked) == 1 and "national: schema-2 sidecar source validation failed" in blocked[0]["detail"] and not spy_oracle and not world.cache.exists()
    assert not [r for r in results if r["check"] == "sidecar source-grounded validation"]  # The disclosure section never ran its call.


def test_n1d_staged_validator_final_call_requires_coverage(semantic_bundle, tmp_path, monkeypatch, spy_oracle):
    world = national_world(tmp_path, semantic_bundle)
    n1d_quiet_staged(world, monkeypatch)
    calls = n1d_recording_validator(monkeypatch)
    assert audit.staged_validator(world.source, tmp_path / "audit-cache", tmp_path / "reports")(world.root) == []
    assert calls and all(kwargs.get("require_coverage") is True for _, kwargs, _ in calls)
    assert any("source_root" in kwargs for _, kwargs, _ in calls)  # The final nested validation is source-grounded.


# --- T04: ISO artifact vs declared sidecar fields, through the public fast path with a refreshed (usable) manifest ----------
def n1d_prop(document, feature, key, value):
    document["features"][feature]["properties"][key] = value


def n1d_drop_feature_keys(document):
    del document["features"][0]["geometry"]


N1D_TAMPER = {
    "threshold_int_alias": lambda d: n1d_prop(d, 0, "time_threshold_hours", 4),
    "threshold_value": lambda d: n1d_prop(d, 0, "time_threshold_hours", 5.0),
    "threshold_swapped": lambda d: d["features"].insert(0, d["features"].pop(1)),
    "threshold_string": lambda d: n1d_prop(d, 0, "time_threshold_hours", "4.0"),
    "status_changed": lambda d: n1d_prop(d, 1, "status", "unsupported_threshold"),
    "status_non_string": lambda d: n1d_prop(d, 1, "status", ["ok"]),
    "count_bool": lambda d: n1d_prop(d, 0, "reachable_node_count", True),
    "count_float": lambda d: n1d_prop(d, 0, "reachable_node_count", 4.0),
    "count_changed": lambda d: n1d_prop(d, 2, "reachable_node_count", 5),
    "feeder_first_off": lambda d: n1d_prop(d, 0, "snap_feeder_km", d["features"][0]["properties"]["snap_feeder_km"] + 1e-6),
    "feeder_later_off": lambda d: n1d_prop(d, 3, "snap_feeder_km", d["features"][3]["properties"]["snap_feeder_km"] + 1e-6),
    "feeder_later_null": lambda d: n1d_prop(d, 3, "snap_feeder_km", None),
    "feeder_later_string": lambda d: n1d_prop(d, 3, "snap_feeder_km", "0.0"),
    "feeder_later_bool": lambda d: n1d_prop(d, 3, "snap_feeder_km", False),
    "feeder_out_of_range": lambda d: n1d_prop(d, 2, "snap_feeder_km", 151.0),
    "name_changed": lambda d: [n1d_prop(d, i, "hub_name", "Other") for i in range(len(d["features"]))],
    "type_changed": lambda d: n1d_prop(d, 0, "hub_type", "mmlp"),
    "run_dropped": lambda d: d["features"].clear(),
    "run_repeated": lambda d: d["features"].extend([dict(f) for f in d["features"]]),
    "feature_dropped": lambda d: d["features"].pop(),
    "extra_run_other_name": lambda d: d["features"].extend([{**f, "properties": {**f["properties"], "hub_name": "Extra"}} for f in d["features"]]),
    "geometry_key_missing": n1d_drop_feature_keys,
    "geometry_null_for_ok": lambda d: d["features"][0].__setitem__("geometry", None),
    "geometry_point": lambda d: d["features"][0].__setitem__("geometry", {"type": "Point", "coordinates": [72., 18.]}),
    "geometry_out_of_bounds": lambda d: d["features"][0].__setitem__("geometry", {"type": "Polygon", "coordinates": [[[200., 18.], [201., 18.], [201., 19.], [200., 18.]]]}),
    "geometry_bowtie": lambda d: d["features"][0].__setitem__("geometry", {"type": "Polygon", "coordinates": [[[72., 18.], [73., 19.], [73., 18.], [72., 19.], [72., 18.]]]}),
    "feature_type_wrong": lambda d: d["features"][0].__setitem__("type", "Thing"),
    "properties_key_missing": lambda d: d["features"][0]["properties"].pop("snap_feeder_km"),
}


@pytest.mark.parametrize("name", sorted(N1D_TAMPER))
def test_n1d_artifact_field_tamper_fails_the_coherence_record_and_withholds_warns(semantic_bundle, tmp_path, name):
    world = national_world(tmp_path, semantic_bundle)
    n1d_edit_iso(world, N1D_ISO[0], N1D_TAMPER[name])
    n1d_refresh(world)  # The manifest and sidecar hashes follow the edit, so only the semantic comparison can reject it.
    results = fast_records(world.root, national=True)
    assert [(r["status"], r["detail"]) for r in n1d_coherence(results)] == [("FAIL", "coverage: artifact corroboration failed")]
    assert n1d_warns(results) == [] and any(r["dataset"] == N1D_ISO[1] for r in results)  # Sibling records stay.
    assert str(tmp_path) not in " ".join(r["detail"] for r in results if r["dataset"] == N1D_DATASET)


def test_n1d_later_feeder_within_the_uniform_1e9_tolerance_passes_and_first_feeder_is_not_special(semantic_bundle, tmp_path):
    world = national_world(tmp_path, semantic_bundle)
    n1d_edit_iso(world, N1D_ISO[0], lambda d: [n1d_prop(d, i, "snap_feeder_km", d["features"][i]["properties"]["snap_feeder_km"] + 5e-10) for i in range(1, 4)])
    n1d_refresh(world)
    assert [r["status"] for r in n1d_coherence(fast_records(world.root, national=True))] == ["PASS"]


def test_n1d_mmlp_artifact_tamper_is_also_rejected_and_the_failure_is_not_a_crash(semantic_bundle, tmp_path):
    world = national_world(tmp_path, semantic_bundle)
    n1d_edit_iso(world, N1D_ISO[1], lambda d: n1d_prop(d, 2, "reachable_node_count", 1))
    n1d_refresh(world)
    results = fast_records(world.root, national=True)
    assert [r["detail"] for r in n1d_coherence(results)] == ["coverage: artifact corroboration failed"]


def test_n1d_strict_format_failure_is_reported_as_format_invalid_and_withholds_warns(semantic_bundle, tmp_path):
    world = national_world(tmp_path, semantic_bundle)
    n1d_rewrite_sidecar(world, lambda d: d["isochrone_coverage"]["entries"][0].__setitem__("ok_threshold_count", 1))
    results = fast_records(world.root, national=True)
    assert [(r["status"], r["detail"]) for r in n1d_coherence(results)] == [("FAIL", "coverage: sidecar format invalid")] and n1d_warns(results) == []


def test_n1d_unexpected_exception_becomes_the_single_type_only_record(semantic_bundle, tmp_path, monkeypatch):
    world = national_world(tmp_path, semantic_bundle)

    def boom(*args, **kwargs):
        raise KeyError("C:\\private\\raw message")
    monkeypatch.setattr(audit, "_coverage_corroborates", boom)
    results = fast_records(world.root, national=True)
    assert [(r["status"], r["detail"]) for r in n1d_coherence(results)] == [("FAIL", "coverage: corroboration crashed (KeyError)")]
    assert n1d_warns(results) == [] and any(r["dataset"] == N1D_ISO[0] for r in results)


# --- T08: direct national full blocks before any loader/cache when corroboration fails -----------------------------------
def test_n1d_direct_full_corroboration_failure_blocks_before_the_oracle_and_the_cache(semantic_bundle, tmp_path, spy_oracle):
    world = national_world(tmp_path, semantic_bundle)
    n1d_edit_iso(world, N1D_ISO[0], lambda d: n1d_prop(d, 0, "reachable_node_count", 1))
    n1d_refresh(world)
    results = full_records(world, national=True, code_root=world.repo)
    blocked = [r for r in results if r["check"] == "blocked: national prerequisites"]
    assert len(blocked) == 1 and blocked[0]["detail"].endswith("national: isochrone coverage artifact corroboration failed")
    assert "schema-2 sidecar source validation failed" not in blocked[0]["detail"] and not spy_oracle and not world.cache.exists()
    assert not n1d_coherence(results) and not n1d_warns(results)  # No coherence record or WARN in direct full mode.
    assert str(tmp_path) not in blocked[0]["detail"]


# --- T05: direct, graph-free corroboration of every supported status against geometry and envelope cases -----------------
N1D_GEOMETRY = {
    "none": ("present", None), "valid": ("present", {"type": "Polygon", "coordinates": [[[72., 18.], [72.1, 18.], [72.1, 18.1], [72., 18.1], [72., 18.]]]}),
    "missing": ("missing", None), "point": ("present", {"type": "Point", "coordinates": [72., 18.]}),
    "empty": ("present", {"type": "Polygon", "coordinates": []}),
    "bowtie": ("present", {"type": "Polygon", "coordinates": [[[72., 18.], [73., 19.], [73., 18.], [72., 19.], [72., 18.]]]}),
    "out_of_bounds": ("present", {"type": "Polygon", "coordinates": [[[181., 18.], [182., 18.], [182., 19.], [181., 18.]]]}),
}
N1D_STATUS_FACTS = {"ok": (1.0, 5), "unsupported_threshold": (1.0, 2), "feeder_exceeds_threshold": (1.0, 0),
                    "invalid_coordinates": (None, 0), "no_highway_within_150km": (None, 0)}


def n1d_iso_directory(tmp_path, port_features, mmlp_features=()):
    for name, features in zip(N1D_ISO, (port_features, list(mmlp_features))):
        (tmp_path / name).write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return tmp_path


def n1d_feature(hub_type="major_port", name="P", threshold=4.0, status="ok", count=5, feeder=1.0, geometry=("present", None)):
    feature = {"type": "Feature", "properties": {"hub_type": hub_type, "hub_name": name, "time_threshold_hours": threshold, "status": status,
                                                 "reachable_node_count": count, "snap_feeder_km": feeder}}
    if geometry[0] == "present":
        feature["geometry"] = geometry[1]
    return feature


def n1d_declared(status="ok", count=5, feeder=1.0, extra_types=()):
    entry = {"hub_type": "major_port", "hub_order": 0, "hub_name": "P", "thresholds_hours": [4.0], "statuses": [status],
             "reachable_node_counts": [count], "snap_feeder_km": feeder}
    return {"isochrone_coverage": {"entries": [entry, *extra_types]}}


@pytest.mark.parametrize("geometry", sorted(N1D_GEOMETRY))
@pytest.mark.parametrize("status", sorted(N1D_STATUS_FACTS))
def test_n1d_status_geometry_matrix_for_every_supported_status(tmp_path, status, geometry):
    feeder, count = N1D_STATUS_FACTS[status]
    root = n1d_iso_directory(tmp_path, [n1d_feature(status=status, count=count, feeder=feeder, geometry=N1D_GEOMETRY[geometry])])
    expected = (status == "ok" and geometry == "valid") or (status != "ok" and geometry == "none")
    assert audit._coverage_corroborates(root, n1d_declared(status, count, feeder), False) is expected


def test_n1d_corroboration_nonempty_per_type_in_national_mode_and_generic_empty_only_when_both_empty(tmp_path):
    good = [n1d_feature(geometry=N1D_GEOMETRY["valid"])]
    root = n1d_iso_directory(tmp_path, good)
    assert audit._coverage_corroborates(root, n1d_declared(), False) is True  # Generic: the mmlp declaration and artifact are both empty.
    assert audit._coverage_corroborates(root, n1d_declared(), True) is False  # National: both types must be non-empty.
    assert audit._coverage_corroborates(n1d_iso_directory(tmp_path, good, [n1d_feature("mmlp", geometry=N1D_GEOMETRY["valid"])]), n1d_declared(), False) is False  # Artifact has an undeclared run.


def test_n1d_corroboration_returns_false_for_unreadable_or_malformed_iso_files_and_never_raises(tmp_path):
    root = n1d_iso_directory(tmp_path, [n1d_feature(geometry=N1D_GEOMETRY["valid"])])
    assert audit._coverage_corroborates(root, n1d_declared(), False) is True
    (root / N1D_ISO[1]).write_bytes(b"\x00 garbage")
    assert audit._coverage_corroborates(root, n1d_declared(), False) is False
    (root / N1D_ISO[1]).write_text(json.dumps([1, 2]))
    assert audit._coverage_corroborates(root, n1d_declared(), False) is False
    (root / N1D_ISO[1]).write_text(json.dumps({"type": "Collection", "features": []}))  # Not a FeatureCollection (cannot be refreshed into a manifest).
    assert audit._coverage_corroborates(root, n1d_declared(), False) is False
    (root / N1D_ISO[1]).unlink()
    assert audit._coverage_corroborates(root, n1d_declared(), False) is False


# --- T10: rebuild_provenance retains the ORIGINAL coverage and refuses to reuse it when a surface changed ------------------
def test_n1d_rebuild_provenance_retains_the_exact_original_coverage(tmp_path):
    pm, limitations = n1d_pm(), n1d_limitations()
    world = integration_world(tmp_path)
    before = pm.read_manifest(world.analysis / limitations.SIDECAR_FILENAME)
    assert before["schema_version"] == 2
    frame = pd.read_parquet(world.analysis / "nh_district_travel_time_summary.parquet")
    frame.loc[0, "district"] = "PUNE"
    frame.to_parquet(world.analysis / "nh_district_travel_time_summary.parquet", index=False)
    rebuild_provenance(world)  # Its own real validators return [] before it returns.
    after = pm.read_manifest(world.analysis / limitations.SIDECAR_FILENAME)
    assert after["schema_version"] == 2 and after["isochrone_coverage"] == before["isochrone_coverage"]
    assert after["limitations"] == limitations.LIMITATIONS_V2


@pytest.mark.parametrize("surface", ["iso_bytes", "input_bytes", "code_closure"])
def test_n1d_rebuild_provenance_refuses_changed_retained_surfaces(tmp_path, monkeypatch, surface):
    pm, limitations = n1d_pm(), n1d_limitations()
    world = integration_world(tmp_path)
    sidecar_path = world.analysis / limitations.SIDECAR_FILENAME
    before = hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
    if surface == "iso_bytes":
        with (world.analysis / N1D_ISO[0]).open("ab") as handle:
            handle.write(b"\n")
    elif surface == "input_bytes":
        with (world.source / "logistics_hubs/icds.csv").open("ab") as handle:
            handle.write(b"\n")
    else:
        repo = tmp_path / "other-repo"
        (repo / "scripts" / "analyze").mkdir(parents=True)
        (repo / pm.NATIONAL_CODE_ENTRY).write_text("VALUE = 2\n")
        monkeypatch.setattr(audit, "REPO_ROOT", repo)
    with pytest.raises(ValueError, match="retained coverage surfaces changed"):
        rebuild_provenance(world)
    assert hashlib.sha256(sidecar_path.read_bytes()).hexdigest() == before  # Nothing was rebuilt from stale facts.


# =============================================================================================
# N1d repair, phase A (closed list F1, F2, A1, T02, T04, T07). Appended region; F2's missing-surface cases were a genuine
# RED against the unchanged helper, everything else is FIRST_GREEN against unchanged production. Names are n1d_repair_*.
# =============================================================================================
@pytest.fixture(scope="module")
def n1d_repair_template(tmp_path_factory):
    """One REAL integration world (real generator, producers, validators), built before any fault patch and copied per case."""
    return integration_world(tmp_path_factory.mktemp("n1d-repair-template"))


def n1d_repair_world(template, tmp_path):
    import shutil
    shutil.copytree(template.source, tmp_path / "source")
    shutil.copytree(template.analysis, tmp_path / "stage" / "analysis")
    return SimpleNamespace(source=tmp_path / "source", analysis=tmp_path / "stage" / "analysis", run_id=template.run_id, tmp=tmp_path)


def n1d_repair_documents(world):
    pm = n1d_pm()
    paths = (world.analysis / pm.MANIFEST_FILENAME, world.analysis / "producer_limitations.json")
    return tuple(path.read_bytes() if path.exists() else None for path in paths)  # None records a deliberately absent document.


def n1d_repair_raiser(exception):
    def raiser(*args, **kwargs):
        raise exception
    return raiser


# --- F1: a valid, bounded, non-empty polygon whose REAL EPSG:7755 projection is not finite is rejected ------------------
def test_n1d_repair_f1_real_projection_nonfinite_area_is_rejected_and_the_india_control_passes():
    import warnings
    from pyproj import Transformer
    from shapely.geometry import Polygon, mapping
    from shapely.ops import transform
    project = Transformer.from_crs(4326, 7755, always_xy=True).transform  # The same construction _coverage_geometry_ok uses.
    south = Polygon([(72., -90.), (73., -90.), (73., -89.), (72., -89.), (72., -90.)])
    assert south.is_valid and not south.is_empty and south.geom_type == "Polygon"
    assert -180 <= south.bounds[0] <= south.bounds[2] <= 180 and -90 <= south.bounds[1] <= south.bounds[3] <= 90
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        area = transform(project, south).area
        assert not np.isfinite(area)  # The actual intermediate, not an assumption.
        assert audit._coverage_geometry_ok(mapping(south), project) is False
        india = Polygon([(72., 18.), (73., 18.), (73., 19.), (72., 19.), (72., 18.)])
        india_area = transform(project, india).area
        assert np.isfinite(india_area) and india_area > 0
        assert audit._coverage_geometry_ok(mapping(india), project) is True


# --- F2: retained-surface errors (genuine RED before the helper change for the missing cases) -------------------------------
N1D_REPAIR_FIXED = "retained coverage surfaces changed"


@pytest.mark.parametrize("case", ["iso_missing", "input_missing", "iso_directory", "closure_import_error", "closure_syntax_error", "closure_unicode_error"])
def test_n1d_repair_f2_listed_retained_surface_errors_normalize_to_the_fixed_mismatch(n1d_repair_template, tmp_path, monkeypatch, case):
    pm = n1d_pm()
    world = n1d_repair_world(n1d_repair_template, tmp_path)
    before = n1d_repair_documents(world)  # Captured after the real build and before any fault.
    iso = world.analysis / N1D_ISO[0]
    if case == "iso_missing":
        iso.unlink()
    elif case == "input_missing":
        (world.source / "logistics_hubs/icds.csv").unlink()
    elif case == "iso_directory":
        iso.unlink()
        iso.mkdir()
    else:
        injected = {"closure_import_error": ImportError("synthetic"), "closure_syntax_error": SyntaxError("synthetic"),
                    "closure_unicode_error": UnicodeDecodeError("utf-8", b"\xff", 0, 1, "synthetic")}[case]
        monkeypatch.setattr(pm, "national_code_closure", n1d_repair_raiser(injected))  # The closure INVOCATION only.
    with pytest.raises(ValueError) as caught:
        rebuild_provenance(world)
    assert type(caught.value) is ValueError and str(caught.value) == N1D_REPAIR_FIXED
    assert caught.value.__cause__ is None and caught.value.__suppress_context__ is True  # raised "from None"
    assert n1d_repair_documents(world) == before  # Neither provenance document was written.


@pytest.mark.parametrize("case", ["propagate_alloc", "propagate_io", "propagate_hash_import", "propagate_hash_type", "propagate_hash_attribute",
                                  "propagate_key", "propagate_initial_read", "propagate_postguard_builder"])
def test_n1d_repair_f2_unlisted_errors_propagate_unchanged(n1d_repair_template, tmp_path, monkeypatch, case):
    from scripts.analyze import producer_io
    pm, limitations = n1d_pm(), n1d_limitations()
    world = n1d_repair_world(n1d_repair_template, tmp_path)
    injected = {"propagate_alloc": MemoryError("synthetic injected propagation control"), "propagate_io": OSError("synthetic injected propagation control"),
                "propagate_hash_import": ImportError("synthetic injected propagation control"), "propagate_hash_type": TypeError("synthetic injected propagation control"),
                "propagate_hash_attribute": AttributeError("synthetic injected propagation control"),
                "propagate_postguard_builder": FileNotFoundError("synthetic injected propagation control")}.get(case)
    if case == "propagate_key":
        manifest_path = world.analysis / pm.MANIFEST_FILENAME
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        del document["artifacts"][N1D_ISO[0]]  # The original manifest lacks a retained surface entry.
        manifest_path.write_text(json.dumps(document), encoding="utf-8")
        expected = KeyError
    elif case == "propagate_initial_read":
        (world.analysis / "producer_limitations.json").unlink()  # An initial sidecar read, outside the retained-surface guard.
        try:
            pm.read_manifest(world.analysis / "producer_limitations.json")
        except Exception as exc:
            expected = type(exc)
    elif case == "propagate_postguard_builder":
        monkeypatch.setattr(limitations, "build_limitations", n1d_repair_raiser(injected))  # After the guard: must not be normalized.
        expected = FileNotFoundError
    else:
        real, target = producer_io.sha256_file, world.analysis / N1D_ISO[0]

        def faulting(path, *args, **kwargs):
            if Path(path) == target:  # Only the retained read; setup and later validation are never the asserted failure.
                raise injected
            return real(path, *args, **kwargs)
        monkeypatch.setattr(producer_io, "sha256_file", faulting)  # The binding the helper imports from inside its body.
        expected = type(injected)
    before = n1d_repair_documents(world)
    with pytest.raises(expected) as caught:
        rebuild_provenance(world)
    if injected is not None:
        assert caught.value is injected  # The very same instance, unchanged.
    assert str(caught.value) != N1D_REPAIR_FIXED and n1d_repair_documents(world) == before


# --- A1: exact plain-int discriminator PLUS exact key set; anything else is row C, in generic and national fast ----------
def n1d_repair_drop(key):
    def drop(document):
        del document[key]  # Returns None, so the whole mutated schema-2 document is what gets written.
    return drop


def n1d_repair_extra(document):
    document["extra"] = 1


N1D_REPAIR_A1 = {"missing_coverage": n1d_repair_drop("isochrone_coverage"), "missing_run_id": n1d_repair_drop("run_id"), "extra_key": n1d_repair_extra}


@pytest.mark.parametrize("name", sorted(N1D_REPAIR_A1))
def test_n1d_repair_a1_schema_two_with_wrong_top_level_keys_is_row_c_in_generic_and_national_fast(semantic_bundle, tmp_path, name):
    world = national_world(tmp_path, semantic_bundle)
    assert [r["status"] for r in n1d_coherence(fast_records(world.root, national=True))] == ["PASS"]  # The untampered control.
    n1d_rewrite_sidecar(world, N1D_REPAIR_A1[name])
    keys = set(n1d_limitations().TOP_LEVEL_KEYS_V2)
    expected = {"missing_coverage": keys - {"isochrone_coverage"}, "missing_run_id": keys - {"run_id"}, "extra_key": keys | {"extra"}}[name]
    persisted = json.loads((world.root / "producer_limitations.json").read_text(encoding="utf-8"))  # The input actually audited.
    assert isinstance(persisted, dict) and type(persisted["schema_version"]) is int and persisted["schema_version"] == 2 and set(persisted) == expected
    for national in (False, True):
        results = fast_records(world.root, national=national)
        assert [(r["status"], r["detail"]) for r in n1d_coherence(results)] == [("FAIL", "coverage: sidecar unreadable or unsupported")]
        assert n1d_warns(results) == []


# --- T02: a REAL malformed source: _full_disclosures returns early, the independent prerequisite still blocks -----------------
def test_n1d_repair_t02_real_malformed_source_early_disclosure_return_still_reaches_the_independent_prerequisite(semantic_bundle, tmp_path, monkeypatch):
    import inspect
    pm, limitations = n1d_pm(), n1d_limitations()
    world = national_world(tmp_path, semantic_bundle)
    path = world.source / "administrative" / "india_districts_lgd.geojson"
    document = json.loads(path.read_text(encoding="utf-8"))
    for feature in document["features"]:  # EVERY feature, so the column itself disappears.
        feature["properties"].pop("district", None)
    path.write_text(json.dumps(document), encoding="utf-8")
    old = pm.read_manifest(world.root / pm.MANIFEST_FILENAME)
    rebuilt = pm.build_manifest(old["run_id"], {n: world.root / n for n in pm.REQUIRED_ARTIFACTS}, world.inputs, old["graph"], old["config"], old["code_hashes"], old["resources"])
    pm.atomic_write_manifest(rebuilt, world.root / pm.MANIFEST_FILENAME)  # Fresh synthetic source hashes; run, graph, config, code and resources retained.
    assert pm.validate_manifest(world.root / pm.MANIFEST_FILENAME, world.root, source_root=world.source, code_root=world.repo) == []  # Manifest integrity, not source validity.
    real, callers = limitations.validate_limitations, []

    def witness(*args, **kwargs):
        issues = real(*args, **kwargs)
        callers.append((inspect.currentframe().f_back.f_code.co_name, issues))
        return issues

    def forbidden(*args, **kwargs):
        raise AssertionError("the graph oracle must never be admitted")
    monkeypatch.setattr(limitations, "validate_limitations", witness)  # Installed only after setup; real validator, never replaced.
    monkeypatch.setattr(audit, "audit_producer_graph_oracles", forbidden)
    results = full_records(world, national=True, code_root=world.repo)
    assert any(r["dataset"] == "source_disclosures" and r["check"] == "source identity validation" and r["status"] == "FAIL" for r in results)
    assert not any(caller == "_full_disclosures" for caller, _ in callers)  # The disclosure section really returned before its validator call.
    assert any(caller == "_coverage_prerequisite_issues" and issues for caller, issues in callers)  # The independent prerequisite called the real validator and it failed.
    blocked = [r for r in results if r["check"] == "blocked: national prerequisites"]
    assert len(blocked) == 1 and "national: schema-2 sidecar source validation failed" in blocked[0]["detail"]
    assert not world.cache.exists() and not n1d_warns(results) and not n1d_coherence(results)


# --- T04: the FIRST feature's feeder has the same positive uniform tolerance as later features -----------------------------
def test_n1d_repair_t04_first_feature_feeder_within_the_absolute_1e9_tolerance_passes(semantic_bundle, tmp_path):
    world = national_world(tmp_path, semantic_bundle)
    n1d_edit_iso(world, N1D_ISO[0], lambda d: n1d_prop(d, 0, "snap_feeder_km", d["features"][0]["properties"]["snap_feeder_km"] + 5e-10))
    n1d_refresh(world)
    assert [r["status"] for r in n1d_coherence(fast_records(world.root, national=True))] == ["PASS"]


# --- T07: strict SOURCE-valid declared facts: bounded unsupported entries WARN exactly twice, unsnapped never ---------------
@pytest.mark.parametrize("mode", ["unsupported_bounded", "unsnapped"])
def test_n1d_repair_t07_declared_nonok_facts_warn_only_when_bounded(semantic_bundle, tmp_path, mode):
    pm, limitations = n1d_pm(), n1d_limitations()
    world = national_world(tmp_path, semantic_bundle)
    path = world.root / limitations.SIDECAR_FILENAME
    document = pm.read_manifest(path)
    bounded = mode == "unsupported_bounded"
    for entry in document["isochrone_coverage"]["entries"]:  # Declared consistency only: these are not independent graph facts.
        n = len(entry["thresholds_hours"])
        entry.update(statuses=["unsupported_threshold" if bounded else "no_highway_within_150km"] * n, reachable_node_counts=[2 if bounded else 0] * n,
                     snap_component_node_count=2 if bounded else None, snap_feeder_km=entry["snap_feeder_km"] if bounded else None,
                     ok_threshold_count=0, saturated_across_ok_thresholds=None,
                     component_bounded_thresholds=list(entry["thresholds_hours"]) if bounded else [])
    pm.atomic_write_manifest(document, path)
    for name, entry in zip(N1D_ISO, document["isochrone_coverage"]["entries"]):
        collection = pm.read_manifest(world.root / name)
        for index, feature in enumerate(collection["features"]):
            feature["geometry"] = None
            feature["properties"].update(status=entry["statuses"][index], reachable_node_count=entry["reachable_node_counts"][index], snap_feeder_km=entry["snap_feeder_km"])
        pm.atomic_write_manifest(collection, world.root / name)
    n1d_refresh(world)
    manifest = pm.read_manifest(world.root / pm.MANIFEST_FILENAME)
    assert limitations.validate_limitations(path, manifest, source_root=world.source, require_coverage=True) == []  # Real strict validation BEFORE the audit.
    results = fast_records(world.root, national=True)
    assert [r["status"] for r in n1d_coherence(results)] == ["PASS"]
    warns = n1d_warns(results)
    assert len(warns) == (2 if bounded else 0) and all(r["status"] == "WARN" for r in warns)
    if bounded:
        assert "major_port 0 Synthetic" in warns[0]["detail"] and "mmlp 0 Synthetic" in warns[1]["detail"]  # Declaration order.
