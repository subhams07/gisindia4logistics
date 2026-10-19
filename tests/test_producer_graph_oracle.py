"""Independent oracle regressions: only synthetic roots and cache directories."""
import ast
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix
from scipy.spatial import KDTree
from shapely.geometry import LineString, Point, mapping

from scripts.audit import audit_all as audit
from scripts.analyze import producer_manifest as pm
from scripts.analyze.freight_cost_core import FreightCostParameters, calculate_unit_costs


def capture(fn, *args):
    results = []
    token = audit._RESULT_SINK.set(results)
    try:
        fn(*args)
    finally:
        audit._RESULT_SINK.reset(token)
    return results


def config():
    from scripts.analyze.port_hinterland_model import PORT_MATRIX_DATA
    return dict(units=dict(distance="km", time="hours", cost="INR/tonne"),
        objectives=dict(hub_selection="minimum_lmv_time", snap="closest_compatible", distance="time_predecessor_path"),
        speeds=dict(motorway=90., trunk=70., primary=55., default=50.), feeder_speed_kmh=35.,
        snap_limit_km=150., lmv_factor=1., chunk_size=32, thresholds_hours=dict(major_port=[4., 8., 12., 24.], mmlp=[4., 8., 12.]),
        buffer_m=8000., simplify_m=500., alpha=.85, beta=1.65, capacities={name: cap for name, cap in PORT_MATRIX_DATA.values()},
        freight_parameters=asdict(FreightCostParameters()))


def refresh(bundle):
    source, root, cache, provenance = bundle
    value = pm.build_manifest("synthetic-oracle", {n: root / n for n in pm.REQUIRED_ARTIFACTS}, {},
        dict(fingerprint=provenance["graph_fingerprint"], version=6, node_count=provenance["nodes"],
             edge_count=provenance["edges"], selected_source="roads/india_nh_network.parquet"), config(), {},
        dict(elapsed_seconds=None, peak_memory_bytes=None))
    pm.atomic_write_manifest(value, root / pm.MANIFEST_FILENAME)
    assert pm.validate_manifest(root / pm.MANIFEST_FILENAME, root, source_root=source) == []


@pytest.fixture
def bundle(tmp_path):
    return build_bundle(tmp_path)


@pytest.fixture
def shared_bundle(tmp_path):
    # Rows 0 (Maharashtra) and 4 (Delhi) share code 1: a cross-state source condition.
    return build_bundle(tmp_path, codes=[1., 2., 3., None, 1.])


def build_bundle(tmp_path, codes=(1., 2., 3., None, 5.), categories=False):
    # Real canonical builder and real producer builds, never their runtime CLI.
    from scripts.analyze.nh_travel_matrix import build_travel_outputs, write_travel_outputs
    from scripts.analyze.generate_isochrones import build_isochrones, write_isochrones
    from scripts.clean import graph_builder as builder
    source, root, cache = (tmp_path / p for p in ("source", "artifacts", "cache"))
    for directory in (source / "roads", source / "administrative", source / "logistics_hubs", source / "rail", source / "demographic", root):
        directory.mkdir(parents=True)
    xy = [(0., 0.), (10000., 0.), (10000., 10000.), (20000., 10000.)]
    # India-projected coordinates; distant island component is genuinely unreachable.
    center = gpd.GeoSeries([Point(73., 19.)], crs=4326).to_crs(7755).iloc[0]
    xy = [(center.x + x, center.y + y) for x, y in xy]
    lines = [LineString([xy[0], xy[1], xy[2], xy[3]]),
             LineString([(center.x + 2000000, center.y), (center.x + 2010000, center.y)]),
             LineString([xy[0], xy[3]])]
    # Fast motorway detour beats the geometrically shorter primary road.
    nh = gpd.GeoDataFrame(dict(highway=["motorway", "primary", "primary"], nh=["1", "2", "3"]), geometry=lines, crs=7755)
    nh.to_crs(4326).to_parquet(source / "roads/india_nh_network.parquet", index=False)
    points = gpd.GeoSeries([Point(*xy[0]), Point(*xy[3]), Point(center.x + 2000000, center.y), Point(center.x + 3000000, center.y), None], crs=7755).to_crs(4326)
    districts = gpd.GeoDataFrame(dict(state=["Maharashtra", "Assam", "Lakshadweep", "Delhi", "Delhi"],
        district=["Pune", "NE", "Island", "Nazul", "Invalid"], district_code=list(codes)), geometry=points, crs=4326)
    districts.to_file(source / "administrative/india_districts_lgd.geojson", driver="GeoJSON")
    # Same exact primary identities with finite populations, before any producer build.
    pd.DataFrame(dict(state=districts.state, district=districts.district, district_code=districts.district_code,
        pop_2011=[1000. * (i + 1) for i in range(len(districts))])).to_csv(source / "demographic/district_population_estimates.csv", index=False)
    hub_points = gpd.GeoSeries([Point(*xy[3]), Point(*xy[1]), Point(center.x + 3000000, center.y)], crs=7755).to_crs(4326)
    names = ["Port of " + key.replace("_", " ") + " Port" for key in audit.PORT_KEYS]
    ports = pd.DataFrame(dict(name=names, hub_type=["major_port"] * 12,
        longitude=[hub_points.iloc[0].x] * 12, latitude=[hub_points.iloc[0].y] * 12))
    # One no-snap and one invalid source, retained in both artifacts.
    ports.loc[10, ["longitude", "latitude"]] = [hub_points.iloc[2].x, hub_points.iloc[2].y]
    ports.loc[11, "latitude"] = np.nan
    ports.to_csv(source / "logistics_hubs/ports.csv", index=False)
    pd.DataFrame(dict(name=["Terminal", "Tie", "Invalid"], longitude=[hub_points.iloc[1].x] * 3,
        latitude=[hub_points.iloc[1].y, hub_points.iloc[1].y, np.nan])).to_csv(source / "rail/freight_terminals.csv", index=False)
    pd.DataFrame(dict(name=["M1", "M2", "NoSnap", "Bad"], longitude=[hub_points.iloc[0].x, hub_points.iloc[1].x, hub_points.iloc[2].x, 73.],
        latitude=[hub_points.iloc[0].y, hub_points.iloc[1].y, hub_points.iloc[2].y, np.nan])).to_csv(source / "logistics_hubs/mmlps.csv", index=False)
    if categories:  # Additive: synthetic sources for the other six hub categories (S4), before the producer build.
        (source / "roads").mkdir(exist_ok=True)
        at = lambda i: dict(longitude=hub_points.iloc[i].x, latitude=hub_points.iloc[i].y)  # noqa: E731
        pd.DataFrame([dict(station_name="RailA", **at(0)), dict(station_name="RailB", **at(1)), dict(station_name="RailBad", longitude=73., latitude=np.nan)]).to_csv(source / "rail/railway_stations.csv", index=False)
        pd.DataFrame([dict(name="IcdA", **at(1)), dict(name="IcdTie", **at(1))]).to_csv(source / "logistics_hubs/icds.csv", index=False)
        # On the island component, exactly at the island district's point (a same-point, valid island route).
        pd.DataFrame([dict(name="AirIsland", longitude=points.iloc[2].x, latitude=points.iloc[2].y)]).to_csv(source / "logistics_hubs/air_cargo.csv", index=False)
        pd.DataFrame([dict(name="IcpA", **at(0))]).to_csv(source / "logistics_hubs/icps.csv", index=False)
        pd.DataFrame([dict(name="TollA", **at(0)), dict(name="TollB", **at(1))]).to_csv(source / "roads/toll_plazas.csv", index=False)
    summary, matrix, provenance = build_travel_outputs(data_dir=source, cache_dir=cache)
    write_travel_outputs(summary, matrix, summary_csv=root / pm.REQUIRED_ARTIFACTS[0], summary_parquet=root / pm.REQUIRED_ARTIFACTS[1], port_matrix_csv=root / pm.REQUIRED_ARTIFACTS[2])
    costs = []
    for row in summary.to_dict("records"):
        c = dict.fromkeys(audit.COST_COLUMNS)
        c.update({k: row[k] for k in audit.IDENTITY + ["pop_2011", "is_island"]})
        c.update(target_port=row["nearest_port_name"], optimal_mode="N/A (Island / Isolated)")
        if pd.notna(row["port_road_distance_km"]) and not row["is_island"]:
            unit = calculate_unit_costs(row["port_road_distance_km"], row["port_drive_time_hours"], row["freight_terminal_road_distance_km"], row["state"])
            c.update(road_distance_km=round(unit.road.distance_km, 1), road_drive_time_hours=round(unit.road.transit_time_hours, 2),
                road_cost_per_ton_inr=round(unit.road.cost_per_ton_inr, 1), rail_cost_per_ton_inr=round(unit.conventional_rail.cost_per_ton_inr, 1),
                dfc_cost_per_ton_inr=round(unit.dfc_rail.cost_per_ton_inr, 1) if unit.dfc_rail else None, optimal_mode=unit.optimal_mode,
                modal_shift_savings_inr=round(unit.modal_shift_savings_per_ton_inr, 1), modal_shift_savings_pct=round(unit.modal_shift_savings_pct, 1), break_even_distance_km=unit.break_even_distance_km)
        costs.append(c)
    pd.DataFrame(costs).to_csv(root / pm.REQUIRED_ARTIFACTS[3], index=False)
    from scripts.analyze.port_hinterland_model import build_port_hinterland
    populations = pd.read_csv(source / "demographic/district_population_estimates.csv")
    hinterland = build_port_hinterland(matrix, populations, alpha=.85, beta=1.65)
    hinterland.to_csv(root / pm.REQUIRED_ARTIFACTS[4], index=False)
    p, m, _ = build_isochrones(data_dir=source, cache_dir=cache)
    write_isochrones(p, m, ports_path=root / pm.REQUIRED_ARTIFACTS[5], mmlps_path=root / pm.REQUIRED_ARTIFACTS[6])
    values = builder.load_or_build_cached_graph(builder.load_highway_source(source)[0], cache_dir=cache)
    provenance.update(nodes=values[0].shape[0], edges=values[0].nnz)
    result = source, root, cache, provenance
    refresh(result)
    return result


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob("*") if p.is_file()}


def test_correct_seven_artifacts_full_oracles_pass_readonly(bundle):
    source, root, cache, _ = bundle
    before = [hashes(p) for p in (source, root, cache)]
    results = capture(audit.audit_producers_full, source, root, cache)
    assert not [r for r in results if r["status"] != "PASS"]
    assert {r["dataset"] for r in results} >= {"route_oracle", "iso_oracle"}
    assert before == [hashes(p) for p in (source, root, cache)]
    assert not [r for r in capture(audit.audit_producers_fast, root) if r["status"] != "PASS"]


def test_rehashed_distance_only_shortcut_fails_time_path_oracle(bundle):
    source, root, cache, _ = bundle
    summary = pd.read_csv(root / pm.REQUIRED_ARTIFACTS[0])
    matrix = pd.read_csv(root / pm.REQUIRED_ARTIFACTS[2])
    # Actual source motorway path is ~30 km / .33h; direct primary ~22.4 km is slower.
    assert summary.loc[0, "port_road_distance_km"] == 30.
    assert summary.loc[0, "port_drive_time_hours"] == .33
    summary.loc[0, "port_road_distance_km"] = summary.loc[0, "port_straight_km"]
    for column in matrix:
        if column.startswith("road_km_to_") and pd.notna(matrix.loc[0, column]):
            matrix.loc[0, column] = summary.loc[0, "port_straight_km"]
    summary.to_csv(root / pm.REQUIRED_ARTIFACTS[0], index=False)
    summary.to_parquet(root / pm.REQUIRED_ARTIFACTS[1], index=False)
    matrix.to_csv(root / pm.REQUIRED_ARTIFACTS[2], index=False)
    refresh(bundle)
    # Cosmetic speed envelope, straight bound and selected-port parity all survive.
    assert not [r for r in capture(audit.audit_producers_fast, root) if r["status"] == "FAIL"]
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert any(r["status"] == "FAIL" and "summary CSV row 0 port" in r["check"] for r in results)
    assert any(r["status"] == "FAIL" and "matrix row 0" in r["check"] for r in results)


@pytest.mark.parametrize("mutation", ["winner", "time", "km", "straight", "unreachable", "nullcode", "geometry", "nodecount", "status", "feeder", "lostsourceid"])
def test_rehashed_corruption_fails_concrete_oracle(bundle, mutation):
    source, root, cache, _ = bundle
    if mutation in {"winner", "time", "km", "straight", "unreachable", "nullcode"}:
        path = root / pm.REQUIRED_ARTIFACTS[0]
        frame = pd.read_csv(path)
        column, value = {"winner": ("nearest_freight_terminal_name", "Tie"), "time": ("port_drive_time_hours", 2.),
            "km": ("port_road_distance_km", 99.), "straight": ("port_straight_km", 99.),
            "unreachable": ("port_drive_time_hours", 0.), "nullcode": ("district_code", 777.)}[mutation]
        frame.loc[3 if mutation in {"nullcode", "unreachable"} else 0, column] = value
        frame.to_csv(path, index=False)
        frame.to_parquet(root / pm.REQUIRED_ARTIFACTS[1], index=False)
    else:
        path = root / pm.REQUIRED_ARTIFACTS[6]
        doc = json.loads(path.read_text())
        feature = doc["features"][0]
        if mutation == "geometry":
            feature["geometry"] = mapping(Point(73., 19.).buffer(3.))
        elif mutation == "lostsourceid":
            doc["features"].pop(0)
        else:
            column, value = {"nodecount": ("reachable_node_count", 999), "status": ("status", "unsupported_threshold"), "feeder": ("feeder_time_hours", .5)}[mutation]
            feature["properties"][column] = value
        path.write_text(json.dumps(doc))
    refresh(bundle)  # Fresh valid hashes cannot bless fabricated semantics.
    results = capture(audit.audit_producers_full, source, root, cache)
    dataset = "route_oracle" if mutation in {"winner", "time", "km", "straight", "unreachable", "nullcode"} else "iso_oracle"
    assert any(r["dataset"] == dataset and r["status"] == "FAIL" for r in results)


def test_oracle_module_no_producer_imports():
    path = Path(audit.__file__).with_name("producer_graph_oracle.py")
    tree = ast.parse(path.read_text())
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any("nh_travel_matrix" in name or "generate_isochrones" in name for name in imports)


def test_sample_derived_stable_null_and_specials():
    from scripts.audit.producer_graph_oracle import sample_district_indices
    frame = pd.DataFrame(dict(state=["Delhi"] * 20 + ["Assam", "Lakshadweep", "Maharashtra"],
        district=[str(i) for i in range(20)] + ["NE", "Island", "Pune"], district_code=list(range(20)) + [100, 101, None]))
    indices = sample_district_indices(frame)
    assert len(indices) <= 12 and {20, 21, 22} <= set(indices)
    assert indices == sample_district_indices(frame.copy())


def graph_fixture():
    xy = np.array([[0., 0.], [5000., 0.], [0., 5000.], [400000., 0.]])
    # Direct shorter edge is deliberately slower than the two-edge detour.
    km = np.array([[0, 5, 5, 0], [5, 0, np.sqrt(50), 0], [5, np.sqrt(50), 0, 0], [0, 0, 0, 0]])
    time = np.array([[0, 2, .1, 0], [2, 0, .1, 0], [.1, .1, 0, 0], [0, 0, 0, 0]])
    return SimpleNamespace(nh_graph=csr_matrix(time), nh_distance_graph=csr_matrix(km), nh_node_xy=xy,
        nh_comp_labels=np.array([1000000, 1000000, 1000000, 2000000]), nh_tree=KDTree(xy), nh_bridge_mask=csr_matrix((4, 4)))


def test_scalar_time_path_not_distance_path_and_sparse_unreachable(monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    graph = graph_fixture()
    seen = []
    original = oracle.kernel.pair_network_metrics
    def bounded(graph, pairs, *, chunk_size):
        seen.append(chunk_size)
        return original(graph, pairs, chunk_size=chunk_size)
    monkeypatch.setattr(oracle.kernel, "pair_network_metrics", bounded)
    points = np.array([[0., 0.], [400000., 0.], [np.nan, np.nan]])
    hubs = [dict(kind="port", name="First", source_order=0, point=np.array([5000., 0.]), major=True),
            dict(kind="port", name="Tie", source_order=1, point=np.array([5000., 0.]), major=True)]
    expected = oracle.route_expectations(graph, points, hubs, chunk_size=1)
    assert expected[0]["port"]["nearest_port_name"] == "First"
    assert expected[0]["port"]["port_road_distance_km"] == 12.1
    assert expected[0]["port"]["port_drive_time_hours"] == .2
    assert expected[1]["port"]["port_drive_time_hours"] is None
    assert expected[2]["port"]["port_drive_time_hours"] is None
    assert seen == [1]


def test_iso_physical_snap_threshold_feeder_and_unsupported():
    from scripts.audit import producer_graph_oracle as oracle
    graph = graph_fixture()
    values = oracle.iso_expectations(graph, np.array([0., -35000.]), [1., 1.05, 1.3])
    assert values[0]["status"] == "unsupported_threshold" and values[0]["reachable_node_count"] == 1
    assert values[-1]["status"] == "ok" and values[-1]["reachable_node_count"] == 3
    assert values[0]["feeder_time_hours"] == 1.
    assert oracle.iso_expectations(graph, np.array([0., -150000.]), [1.])[0]["status"] == "feeder_exceeds_threshold"
    assert oracle.iso_expectations(graph, np.array([0., -150001.]), [10.])[0]["status"] == "no_highway_within_150km"
    assert oracle.iso_expectations(graph, None, [1.])[0]["status"] == "invalid_coordinates"


def test_matrix_all_twelve_cells_rehashed_corruption(bundle):
    source, root, cache, _ = bundle
    path = root / pm.REQUIRED_ARTIFACTS[2]
    frame = pd.read_csv(path)
    frame.loc[0, "road_km_to_" + audit.PORT_KEYS[9]] = 444.
    frame.loc[2, "drive_hours_to_" + audit.PORT_KEYS[0]] = 1.
    frame.to_csv(path, index=False)
    refresh(bundle)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert any(r["status"] == "FAIL" and "matrix row 0" in r["check"] for r in results)
    assert any(r["status"] == "FAIL" and "matrix row 2" in r["check"] for r in results)


@pytest.mark.parametrize("corruption", ["metadata", "domain"])
def test_graph_corruption_is_fail_never_warn(bundle, monkeypatch, corruption):
    from scripts.audit import producer_graph_oracle as oracle
    source, root, cache, _ = bundle
    if corruption == "metadata":
        original = oracle.builder.load_or_build_cached_graph
        def corrupt(*args, **kwargs):
            values = list(original(*args, **kwargs))
            values[3] = np.zeros(len(values[3]), dtype=int)  # Lose the island component.
            return tuple(values)
        monkeypatch.setattr(oracle.builder, "load_or_build_cached_graph", corrupt)
    else:
        from server.services.exceptions import DataIntegrityError
        def corrupt(*args, **kwargs):
            raise DataIntegrityError("Synthetic corrupt time-predecessor metadata")
        monkeypatch.setattr(oracle.kernel, "pair_network_metrics", corrupt)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    if corruption == "metadata":
        assert len(results) == 1 and results[0]["status"] == "FAIL"
        assert results[0]["detail"] == "ValueError"
        return
    # A usable graph isolates the route crash; ISO sections still report.
    outcomes = [(r["dataset"], r["check"], r["status"]) for r in results]
    # Authorized S4 supersession: route AND category sections each crash with the same domain error.
    assert outcomes[:4] == [("graph_oracle", "validated six-field graph capture", "PASS"),
                            ("graph_oracle", "manifest matches actual canonical graph", "PASS"),
                            ("route_oracle", "section crashed", "FAIL"),
                            ("category_oracle", "section crashed", "FAIL")]
    assert results[2]["detail"] == results[3]["detail"] == "DataIntegrityError"
    assert len(outcomes) > 5 and all(o[0] == "iso_oracle" and o[2] == "PASS" for o in outcomes[4:-1]) and (outcomes[-1], results[-1]["detail"]) == (("coverage_oracle", "coverage sidecar availability", "PASS"), "not applicable: legacy coverage unavailable")


def test_actual_custom_source_cache_arguments_and_no_producer_calls(bundle, monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    from scripts.analyze import nh_travel_matrix, generate_isochrones
    source, root, cache, _ = bundle
    seen = []
    original_load, original_build = oracle.builder.load_highway_source, oracle.builder.load_or_build_cached_graph
    def load(actual_root):
        seen.append(("load", actual_root))
        return original_load(actual_root)
    def build(nh, *, cache_dir):
        seen.append(("cache", cache_dir))
        return original_build(nh, cache_dir=cache_dir)
    def forbidden(*args, **kwargs):
        pytest.fail("Oracle must not invoke producer/private helper")
    monkeypatch.setattr(oracle.builder, "load_highway_source", load)
    monkeypatch.setattr(oracle.builder, "load_or_build_cached_graph", build)
    for name in ["build_travel_outputs", "_point_candidates", "_compatible_hubs", "_port_key"]:
        monkeypatch.setattr(nh_travel_matrix, name, forbidden)
    for name in ["build_isochrones", "_hub_features", "_nearest_source"]:
        monkeypatch.setattr(generate_isochrones, name, forbidden)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert all(r["status"] == "PASS" for r in results)
    assert seen == [("load", source), ("cache", cache)]


def test_unsupported_config_fails_before_builder(bundle, monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    source, root, cache, _ = bundle
    path = root / pm.MANIFEST_FILENAME
    doc = json.loads(path.read_text())
    doc["config"]["lmv_factor"] = .8
    path.write_text(json.dumps(doc))
    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported settings must fail before graph loading")
    monkeypatch.setattr(oracle.builder, "load_highway_source", forbidden)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert len(results) == 1 and results[0]["status"] == "FAIL"
    assert results[0]["detail"] == "config: unsupported lmv_factor"


def test_manifest_thresholds_not_blind_defaults(bundle):
    from scripts.analyze.generate_isochrones import build_isochrones, write_isochrones
    source, root, cache, _ = bundle
    ports, mmlps, _ = build_isochrones(data_dir=source, cache_dir=cache, thresholds_ports=(.00001, .25, .5), thresholds_mmlps=(.00001, .25, .5))
    write_isochrones(ports, mmlps, ports_path=root / pm.REQUIRED_ARTIFACTS[5], mmlps_path=root / pm.REQUIRED_ARTIFACTS[6])
    refresh(bundle)
    path = root / pm.MANIFEST_FILENAME
    doc = json.loads(path.read_text())
    doc["config"]["thresholds_hours"] = dict(major_port=[.00001, .25, .5], mmlp=[.00001, .25, .5])
    pm.atomic_write_manifest(doc, path)
    assert pm.validate_manifest(path, root) == []
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert all(r["status"] == "PASS" for r in results)
    assert any("feeder_exceeds_threshold" in r["detail"] for r in results)


@pytest.mark.parametrize("fast", [True, False])
def test_actual_graph_wrapper_registration_only_full(bundle, monkeypatch, fast):
    source, root, cache, _ = bundle
    names = ["audit_boundaries", "audit_rail", "audit_hubs", "audit_freight", "audit_demographics", "audit_geoparquet", "audit_catalog", "check_baseline", "audit_villages", "audit_analysis", "audit_roads", "audit_producers_fast"]
    for name in names:
        monkeypatch.setattr(audit, name, lambda *args, **kwargs: None)
    results = audit.AuditRunner(fast=fast, source_root=source, artifact_root=root, cache_dir=cache).run()
    assert any(r["dataset"] == "route_oracle" for r in results) is not fast
    assert any(r["dataset"] == "iso_oracle" for r in results) is not fast
    assert all(r["status"] == "PASS" for r in results)


def test_exact_same_projected_point_zero_and_time_winner():
    from scripts.audit import producer_graph_oracle as oracle
    graph = graph_fixture()
    hubs = [dict(kind="port", name="SlowerShorter", source_order=0, point=np.array([5000., 0.]), major=True),
            dict(kind="port", name="Faster", source_order=1, point=np.array([0., 5000.]), major=True)]
    results = oracle.route_expectations(graph, np.array([[0., 0.], [0., 5000.]]), hubs)
    assert results[0]["port"]["nearest_port_name"] == "Faster"
    assert results[1]["port"]["port_road_distance_km"] == 0.
    assert results[1]["port"]["port_drive_time_hours"] == 0.
    # A cosmetic plausible shortest-distance answer is not the fastest-time path.
    expected = results[0]["matrix"]
    tampered = dict(expected, road_km_to_slowershorter=5.)
    assert not oracle._values_equal(tampered, expected)


def test_many_origin_requests_are_grouped_chunk_bounded(monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    n = 40
    xy = np.column_stack((np.arange(n) * 1000., np.zeros(n)))
    rows, cols = [], []
    for i in range(n - 1):
        rows.extend([i, i + 1])
        cols.extend([i + 1, i])
    km = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
    graph = SimpleNamespace(nh_graph=km / 70., nh_distance_graph=km, nh_node_xy=xy,
        nh_comp_labels=np.full(n, 999999), nh_tree=KDTree(xy), nh_bridge_mask=csr_matrix((n, n)))
    seen = []
    original = oracle.kernel.dijkstra
    def bounded(matrix, **kwargs):
        seen.append(len(kwargs["indices"]))
        assert kwargs["return_predecessors"] is True
        return original(matrix, **kwargs)
    monkeypatch.setattr(oracle.kernel, "dijkstra", bounded)
    hubs = [dict(kind="port", name="Last", source_order=0, point=xy[-1], major=True)]
    result = oracle.route_expectations(graph, xy, hubs)
    assert seen == [32, 7]
    assert result[0]["port"]["port_road_distance_km"] == 39.


def test_iso_nearest_ties_small_components_and_no_mainland_preference():
    from scripts.audit import producer_graph_oracle as oracle
    graph = graph_fixture()
    # Island nearest physical node is retained, even though its catchment has <3 nodes.
    isolated = oracle.iso_expectations(graph, np.array([400000., 0.]), [24.])[0]
    assert isolated["status"] == "unsupported_threshold" and isolated["reachable_node_count"] == 1
    # Equal-distance source candidates prefer ID0 (time to ID1 takes detour .2).
    tied = oracle.iso_expectations(graph, np.array([2500., 0.]), [.18])[0]
    assert tied["reachable_node_count"] == 2


def outcomes(results):
    return [(r["dataset"], r["check"], r["status"]) for r in results]


def failures(results):
    return [(r["dataset"], r["check"]) for r in results if r["status"] != "PASS"]


def rewrite_districts(source, change):
    path = source / "administrative/india_districts_lgd.geojson"
    frame = gpd.read_file(path)
    change(frame)
    frame.to_file(path, driver="GeoJSON")


def test_cross_state_shared_code_passes_full_oracles(shared_bundle):
    source, root, cache, _ = shared_bundle
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert not failures(results)
    assert {r["dataset"] for r in results} >= {"route_oracle", "iso_oracle"}
    sample = next(r for r in results if r["check"] == "fixed source-derived sample/objective")
    assert "('coded', 'maharashtra', 1)" in sample["detail"] and "('coded', 'delhi', 1)" in sample["detail"]


def test_same_state_duplicate_code_fails_route_section_while_iso_still_runs(bundle):
    source, root, cache, _ = bundle

    def duplicate(frame):
        frame.loc[3, "district_code"] = 5.  # Delhi/Nazul now repeats Delhi/Invalid code 5.
    rewrite_districts(source, duplicate)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    crashed = [r for r in results if r["check"] == "section crashed"]
    assert [(r["dataset"], r["status"], r["detail"]) for r in crashed] == [("route_oracle", "FAIL", "IdentityError")]
    iso = [r for r in results if r["dataset"] == "iso_oracle"]
    assert iso and all(r["status"] == "PASS" for r in iso)
    assert not [r for r in results if r["dataset"] == "route_oracle" and r["check"] != "section crashed"]


def test_swapped_shared_code_rows_in_summary_fail_exact_identity(shared_bundle):
    source, root, cache, _ = shared_bundle
    path = root / pm.REQUIRED_ARTIFACTS[0]
    frame = pd.read_csv(path)
    frame.iloc[[0, 4]] = frame.iloc[[4, 0]].to_numpy()
    frame.to_csv(path, index=False)
    refresh(shared_bundle)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert ("route_oracle", "summary CSV: exact source identities including null codes") in failures(results)
    assert ("route_oracle", "summary parquet: exact source identities including null codes", "PASS") in outcomes(results)
    assert ("route_oracle", "matrix: exact source identities including null codes", "PASS") in outcomes(results)
    assert not [f for f in failures(results) if f[0] != "route_oracle"]


def test_summary_label_case_change_fails_exact_identity(bundle):
    source, root, cache, _ = bundle
    path = root / pm.REQUIRED_ARTIFACTS[0]
    frame = pd.read_csv(path)
    frame.loc[0, "district"] = "PUNE"
    frame.to_csv(path, index=False)
    refresh(bundle)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert ("route_oracle", "summary CSV: exact source identities including null codes") in failures(results)


@pytest.mark.parametrize("case", ["npbool", "string", "fraction", "oversized", "blank"])
def test_malformed_source_identity_fails_route_section_and_iso_still_runs(bundle, monkeypatch, case):
    from scripts.audit import producer_graph_oracle as oracle
    source, root, cache, _ = bundle
    real = oracle.gpd.read_file
    bad = {"npbool": np.bool_(True), "string": "598", "fraction": 1.5, "oversized": 10 ** 400}

    def malformed(path, *args, **kwargs):
        frame = real(path, *args, **kwargs)
        if Path(path).name == "india_districts_lgd.geojson":
            frame["district_code"] = pd.Series([1, 2, 3, None, 5], index=frame.index, dtype=object)
            frame["district"] = frame["district"].astype(object)
            if case == "blank":
                frame.loc[frame.index[0], "district"] = "   "
            else:
                frame.loc[frame.index[0], "district_code"] = bad[case]
        return frame
    monkeypatch.setattr(oracle.gpd, "read_file", malformed)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    crashed = [r for r in results if r["check"] == "section crashed"]
    assert [(r["dataset"], r["status"], r["detail"]) for r in crashed] == [("route_oracle", "FAIL", "IdentityError")]
    iso = [r for r in results if r["dataset"] == "iso_oracle"]
    assert iso and all(r["status"] == "PASS" for r in iso)


def collision_frame(count):
    """count codes, each shared by two states: 2*count collision rows."""
    return pd.DataFrame(dict(state=[f"S{i // count}" for i in range(2 * count)],
        district=[f"D{i % count}" for i in range(2 * count)], district_code=[float(i % count + 1) for i in range(2 * count)]))


def test_sampler_includes_collision_rows_deterministically_within_bound():
    from scripts.audit.producer_graph_oracle import sample_district_indices
    codes = list(range(1, 41))
    codes[33] = codes[4]  # Code 5 repeats across states; stride sampling alone would miss it.
    frame = pd.DataFrame(dict(state=["Delhi"] * 33 + ["Goa"] + ["Delhi"] * 6, district=[f"D{i}" for i in range(40)], district_code=codes))
    indices = sample_district_indices(frame)
    assert len(indices) == len(set(indices)) <= 12 and {4, 33} <= set(indices)
    assert indices == sample_district_indices(frame.copy())
    assert indices.index(4) < indices.index(33)


def test_sampler_collision_stratum_order_and_truncation_are_deterministic():
    from scripts.audit.producer_graph_oracle import sample_district_indices
    frame = collision_frame(7)  # 14 mandatory collision rows exceed the 12-row bound.
    expected = [0, 7, 1, 8, 2, 9, 3, 10, 4, 11, 5, 12]  # (code, row) order, first 12
    assert sample_district_indices(frame) == expected
    assert sample_district_indices(frame.copy()) == expected


def test_mandatory_collision_overflow_fails_and_continues_with_bounded_sample(bundle, monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    source, root, cache, _ = bundle
    real = oracle.gpd.read_file

    def many(path, *args, **kwargs):
        frame = real(path, *args, **kwargs)
        if Path(path).name != "india_districts_lgd.geojson":
            return frame
        wide = frame.iloc[[0, 1, 2, 3, 4] * 3].iloc[:14].reset_index(drop=True)
        extra = collision_frame(7)
        for column in extra:
            wide[column] = extra[column].to_numpy()
        return wide
    monkeypatch.setattr(oracle.gpd, "read_file", many)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    bound = [r for r in results if r["check"] == "mandatory collision coverage within 12-row bound"]
    assert [(r["dataset"], r["status"]) for r in bound] == [("route_oracle", "FAIL")]
    sample = next(r for r in results if r["check"] == "fixed source-derived sample/objective")
    assert "rows=[0, 7, 1, 8, 2, 9, 3, 10, 4, 11, 5, 12]" in sample["detail"]
    assert [r for r in results if r["dataset"] == "iso_oracle"]


def install_many(monkeypatch, oracle):
    """Declared source of 14 valid mandatory collision rows (7 codes shared by two states)."""
    real = oracle.gpd.read_file

    def many(path, *args, **kwargs):
        frame = real(path, *args, **kwargs)
        if Path(path).name != "india_districts_lgd.geojson":
            return frame
        wide = frame.iloc[[0, 1, 2, 3, 4] * 3].iloc[:14].reset_index(drop=True)
        extra = collision_frame(7)
        for column in extra:
            wide[column] = extra[column].to_numpy()
        return wide
    monkeypatch.setattr(oracle.gpd, "read_file", many)


COVERAGE = "mandatory collision coverage within 12-row bound"


def assert_coverage_and_crash(results, detail):
    assert [(r["dataset"], r["status"]) for r in results if r["check"] == COVERAGE] == [("route_oracle", "FAIL")]
    assert [(r["dataset"], r["status"], r["detail"]) for r in results if r["check"] == "section crashed"] == [("route_oracle", "FAIL", detail)]
    iso = [r for r in results if r["dataset"] == "iso_oracle"]
    assert iso and all(r["status"] == "PASS" for r in iso)


def test_coverage_fail_survives_later_route_metric_crash(bundle, monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    from server.services.exceptions import DataIntegrityError
    source, root, cache, _ = bundle
    install_many(monkeypatch, oracle)

    def corrupt(*args, **kwargs):
        raise DataIntegrityError("Synthetic corrupt metrics")
    monkeypatch.setattr(oracle.kernel, "pair_network_metrics", corrupt)
    assert_coverage_and_crash(capture(audit.audit_producer_graph_oracles, source, root, cache), "DataIntegrityError")


def test_coverage_fail_survives_later_summary_csv_read_crash(bundle, monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    source, root, cache, _ = bundle
    install_many(monkeypatch, oracle)
    real = oracle.pd.read_csv

    def broken(path, *args, **kwargs):
        if Path(path).name == "nh_district_travel_time_summary.csv":
            raise OSError("private injected detail")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(oracle.pd, "read_csv", broken)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert_coverage_and_crash(results, "OSError")
    assert not any("private" in r["detail"] for r in results)


def test_earlier_route_checks_survive_later_artifact_read_crash(bundle, monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    source, root, cache, _ = bundle
    real = oracle.pd.read_csv

    def broken(path, *args, **kwargs):
        if Path(path).name == "nh_district_port_matrix.csv":
            raise ValueError("injected")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(oracle.pd, "read_csv", broken)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    route = [r for r in results if r["dataset"] == "route_oracle"]
    assert [(r["status"], r["detail"]) for r in route if r["check"] == "section crashed"] == [("FAIL", "ValueError")]
    assert any(r["check"].startswith("summary CSV row") for r in route) and any(r["check"].startswith("summary parquet row") for r in route)
    assert all(r["status"] == "PASS" for r in route if r["check"] != "section crashed")
    iso = [r for r in results if r["dataset"] == "iso_oracle"]
    assert iso and all(r["status"] == "PASS" for r in iso)


def test_earlier_iso_checks_survive_later_iso_failure_without_contaminating_route(bundle, monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    source, root, cache, _ = bundle
    real = oracle.read_manifest

    def broken(path, *args, **kwargs):
        if Path(path).name == "mmlp_isochrones.geojson":
            raise ValueError("injected")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(oracle, "read_manifest", broken)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    iso = [r for r in results if r["dataset"] == "iso_oracle"]
    assert [(r["status"], r["detail"]) for r in iso if r["check"] == "section crashed"] == [("FAIL", "ValueError")]
    assert any(r["check"].startswith("major_port") for r in iso)
    assert all(r["status"] == "PASS" for r in iso if r["check"] != "section crashed")
    route = [r for r in results if r["dataset"] == "route_oracle"]
    assert route and all(r["status"] == "PASS" for r in route)


def test_adjacent_large_integer_codes_are_sampled_as_mandatory_collision():
    from scripts.audit.producer_graph_oracle import mandatory_sample_indices, sample_district_indices
    codes = list(range(1, 41))
    codes[4], codes[5] = 2 ** 53, 2 ** 53 + 1
    districts = [f"D{i}" for i in range(40)]
    districts[4] = districts[5] = "collision"
    frame = pd.DataFrame(dict(state=["A"] * 40, district=districts, district_code=np.array(codes, dtype="int64")))
    assert frame.district_code.dtype == np.int64
    assert mandatory_sample_indices(frame) == [4, 5]
    assert sample_district_indices(frame)[:2] == [4, 5]


def linear_graph(network_hours, network_km):
    xy = np.array([[0., 0.], [1000., 0.]])
    return SimpleNamespace(nh_graph=csr_matrix(np.array([[0, network_hours], [network_hours, 0]])),
        nh_distance_graph=csr_matrix(np.array([[0, network_km], [network_km, 0]])), nh_node_xy=xy,
        nh_comp_labels=np.array([1000000, 1000000]), nh_tree=KDTree(xy), nh_bridge_mask=csr_matrix((2, 2)))


def find_rounding_boundary(scale, target, digits):
    """Deterministic (od, hd, network) where sequential and pooled feeder sums round differently."""
    # Feeder distances in metres within the 150 km snap limit; finite positive network value.
    for od in range(1000, 60001, 13):
        for hd in range(1000, 60001, 997):
            a, b = od / 1000. / scale, hd / 1000. / scale
            network = target - a - b
            sequential, pooled = network + a + b, network + (od + hd) / 1000. / scale
            if network > 0 and np.isfinite([network, sequential, pooled]).all() and round(sequential, digits) != round(pooled, digits):
                return od, hd, network
    raise AssertionError("no rounding boundary found")


@pytest.mark.parametrize("quantity", ["hours", "km"])
def test_sequential_feeder_arithmetic_matches_producer_rounding_boundary(quantity):
    from scripts.audit import producer_graph_oracle as oracle
    if quantity == "hours":
        od, hd, network = find_rounding_boundary(35., 1.005, 2)
        graph = linear_graph(network, 5.)
        produced = round(network + od / 1000. / 35. + hd / 1000. / 35., 2)
        pooled = round(network + (od + hd) / 1000. / 35., 2)
    else:
        od, hd, network = find_rounding_boundary(1., 10.05, 1)
        graph = linear_graph(1., network)
        produced = round(network + od / 1000. + hd / 1000., 1)
        pooled = round(network + (od + hd) / 1000., 1)
    assert produced != pooled
    hubs = [dict(kind="port", name="Hub", source_order=0, point=np.array([1000., -float(hd)]), major=True)]
    result = oracle.route_expectations(graph, np.array([[0., -float(od)]]), hubs)[0]
    if quantity == "hours":
        assert result["port"]["port_drive_time_hours"] == produced != pooled
        assert result["matrix"]["drive_hours_to_hub"] == produced
    else:
        assert result["port"]["port_road_distance_km"] == produced != pooled
        assert result["matrix"]["road_km_to_hub"] == produced


# ---------------------------------------------------------------------------------------------
# S4: category (rail station, ICD, MMLP, air cargo, ICP, toll plaza, expressway) and highway access.
# Synthetic only. Producer-independent, but shares the accepted kernel/graph builder; no rail routing.
# ---------------------------------------------------------------------------------------------
import dataclasses  # noqa: E402
import shutil  # noqa: E402
import weakref  # noqa: E402

CATEGORY_KINDS = ("rail_station", "icd", "mmlp", "air_cargo", "icp", "toll_plaza", "expressway")
SUMMARY_CSV = "nh_district_travel_time_summary.csv"
SUMMARY_PARQUET = "nh_district_travel_time_summary.parquet"


def category_columns(kind):
    return [f"nearest_{kind}_name", f"{kind}_straight_km", f"{kind}_road_distance_km", f"{kind}_drive_time_hours", f"{kind}_drive_time_min"]


def category_records(results):
    return [r for r in results if r["dataset"] == "category_oracle"]


@pytest.fixture(scope="module")
def category_base(tmp_path_factory):
    return build_bundle(tmp_path_factory.mktemp("s4cat"), categories=True)


def clone_bundle(base, tmp_path):
    source, root, cache, provenance = base
    new = [tmp_path / name for name in ("source", "artifacts", "cache")]
    for old, target in zip((source, root, cache), new):
        shutil.copytree(old, target)
    return (*new, provenance)


def hub(kind, name, point, order=0):
    return dict(kind=kind, name=name, source_order=order, point=None if point is None else np.array(point, dtype=float))


def line_graph(n=40):
    xy = np.column_stack((np.arange(n) * 1000., np.zeros(n)))
    rows, cols = [], []
    for i in range(n - 1):
        rows.extend([i, i + 1])
        cols.extend([i + 1, i])
    km = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
    return SimpleNamespace(nh_graph=km / 70., nh_distance_graph=km, nh_node_xy=xy, nh_comp_labels=np.full(n, 999999),
                           nh_tree=KDTree(xy), nh_bridge_mask=csr_matrix((n, n))), xy


def test_public_clean_category_bundle_passes_all_seven_categories_and_access(category_base, tmp_path):
    source, root, cache, _ = clone_bundle(category_base, tmp_path)
    summary = pd.read_csv(root / SUMMARY_CSV)
    for kind in CATEGORY_KINDS:  # The fixture genuinely exercises every category.
        assert summary[f"nearest_{kind}_name"].notna().any(), kind
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert all(r["status"] == "PASS" for r in results), [r for r in results if r["status"] != "PASS"]
    records = category_records(results)
    for label in ("summary CSV", "summary parquet"):
        for kind in (*CATEGORY_KINDS, "access"):
            assert any(r["check"].startswith(f"{label} row ") and f" {kind}:" in r["check"] for r in records), (label, kind)
        assert any(r["check"] == f"category sample: {label}: exact source identities including null codes" and r["status"] == "PASS" for r in results)


def test_public_minimal_bundle_has_absent_categories_as_null_expectations(bundle):
    source, root, cache, _ = bundle
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert all(r["status"] == "PASS" for r in results)
    records = category_records(results)
    absent = [r for r in records if "source absent" in r["detail"]]
    assert {k for k in ("icd", "air_cargo", "icp", "toll_plaza", "rail_station") if any(f" {k}:" in r["check"] for r in absent)} == {"icd", "air_cargo", "icp", "toll_plaza", "rail_station"}
    assert not any("expressway" in r["check"] and "source absent" in r["detail"] for r in records)  # Derived from the highway frame.


@pytest.mark.parametrize("artifact", ["csv", "parquet"])
def test_public_wrong_value_for_an_absent_category_source_fails(bundle, artifact):
    source, root, cache, _ = bundle
    def corrupt(frame):
        frame["nearest_icd_name"] = frame["nearest_icd_name"].astype(object)
        frame.loc[0, "nearest_icd_name"] = "Ghost ICD"
        frame.loc[0, "icd_road_distance_km"] = 12.3
    if artifact == "csv":
        frame = pd.read_csv(root / SUMMARY_CSV)
        corrupt(frame)
        frame.to_csv(root / SUMMARY_CSV, index=False)
    else:
        frame = pd.read_parquet(root / SUMMARY_PARQUET)
        corrupt(frame)
        frame.to_parquet(root / SUMMARY_PARQUET, index=False)
    refresh(bundle)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    label = "summary CSV" if artifact == "csv" else "summary parquet"
    assert any(r["dataset"] == "category_oracle" and r["status"] == "FAIL" and r["check"].startswith(f"{label} row 0 icd:") for r in results)


CORRUPTIONS = {"name": 0, "straight": 1, "road": 2, "hours": 3, "minutes": 4}


@pytest.mark.parametrize("kind", CATEGORY_KINDS)
@pytest.mark.parametrize("field", [*CORRUPTIONS, "null"])
def test_public_rehashed_category_corruption_fails_in_csv(category_base, tmp_path, kind, field):
    bundle_tuple = clone_bundle(category_base, tmp_path)
    source, root, cache, _ = bundle_tuple
    frame = pd.read_csv(root / SUMMARY_CSV)
    row = int(frame[f"nearest_{kind}_name"].first_valid_index())
    columns = category_columns(kind)
    if field == "null":
        frame.loc[row, columns[2]] = np.nan
    elif field == "name":
        frame.loc[row, columns[0]] = "Wrong Winner"
    else:
        frame.loc[row, columns[CORRUPTIONS[field]]] += 7.
    frame.to_csv(root / SUMMARY_CSV, index=False)
    refresh(bundle_tuple)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert any(r["dataset"] == "category_oracle" and r["status"] == "FAIL" and r["check"].startswith(f"summary CSV row {row} {kind}:") for r in results)
    assert not any(r["status"] == "FAIL" and r["dataset"] != "category_oracle" for r in results)


@pytest.mark.parametrize("kind", CATEGORY_KINDS)
def test_public_rehashed_category_corruption_fails_in_parquet(category_base, tmp_path, kind):
    bundle_tuple = clone_bundle(category_base, tmp_path)
    source, root, cache, _ = bundle_tuple
    frame = pd.read_parquet(root / SUMMARY_PARQUET)
    row = int(frame[f"nearest_{kind}_name"].first_valid_index())
    frame.loc[row, category_columns(kind)[2]] += 9.
    frame.to_parquet(root / SUMMARY_PARQUET, index=False)
    refresh(bundle_tuple)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert any(r["dataset"] == "category_oracle" and r["status"] == "FAIL" and r["check"].startswith(f"summary parquet row {row} {kind}:") for r in results)


@pytest.mark.parametrize("column", ["highway_access_dist_km", "highway_access_time_min"])
@pytest.mark.parametrize("artifact", ["csv", "parquet"])
def test_public_rehashed_access_corruption_fails(category_base, tmp_path, column, artifact):
    bundle_tuple = clone_bundle(category_base, tmp_path)
    source, root, cache, _ = bundle_tuple
    path = root / (SUMMARY_CSV if artifact == "csv" else SUMMARY_PARQUET)
    frame = pd.read_csv(path) if artifact == "csv" else pd.read_parquet(path)
    frame.loc[0, column] += 3.
    frame.to_csv(path, index=False) if artifact == "csv" else frame.to_parquet(path, index=False)
    refresh(bundle_tuple)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert any(r["dataset"] == "category_oracle" and r["status"] == "FAIL" and " access:" in r["check"] for r in results)


def test_public_flow_calls_route_then_category_metrics_with_only_category_winners(category_base, tmp_path, monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    source, root, cache, _ = clone_bundle(category_base, tmp_path)
    calls, original = [], oracle.kernel.pair_network_metrics

    def spy(graph, pairs, *, chunk_size):
        pairs = list(pairs)
        calls.append(len(pairs))
        return original(graph, pairs, chunk_size=chunk_size)
    monkeypatch.setattr(oracle.kernel, "pair_network_metrics", spy)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert all(r["status"] == "PASS" for r in results)
    summary = pd.read_csv(root / SUMMARY_CSV)
    winners = int(sum(summary[f"nearest_{kind}_name"].notna().sum() for kind in CATEGORY_KINDS))
    assert len(calls) == 2 and calls[1] == winners and winners > 0  # Route pairs first, category winners only second.


def test_category_crash_does_not_suppress_route_or_iso_and_route_crash_does_not_suppress_category(bundle, monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    source, root, cache, _ = bundle
    def boom(*args, **kwargs):
        raise RuntimeError("C:\\private\\secret")
    monkeypatch.setattr(oracle, "category_expectations", boom)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert [(r["dataset"], r["status"], r["detail"]) for r in results if r["check"] == "section crashed"] == [("category_oracle", "FAIL", "RuntimeError")]
    assert any(r["dataset"] == "route_oracle" and r["status"] == "PASS" for r in results)
    assert any(r["dataset"] == "iso_oracle" and r["status"] == "PASS" for r in results)
    assert not any("secret" in r["detail"] for r in results)
    monkeypatch.undo()
    monkeypatch.setattr(oracle, "route_expectations", boom)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert [(r["dataset"], r["detail"]) for r in results if r["check"] == "section crashed"] == [("route_oracle", "RuntimeError")]
    assert category_records(results) and all(r["status"] == "PASS" for r in category_records(results))
    assert any(r["dataset"] == "iso_oracle" for r in results)


def test_category_sibling_read_failure_keeps_earlier_records_and_iso(bundle, monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    source, root, cache, _ = bundle
    real = oracle.pd.read_parquet

    def broken(path, *args, **kwargs):
        if Path(path).name == SUMMARY_PARQUET:
            raise OSError("private injected detail")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(oracle.pd, "read_parquet", broken)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    category = category_records(results)
    assert any(r["check"].startswith("summary CSV row") and r["status"] == "PASS" for r in category)
    assert [(r["status"], r["detail"]) for r in category if r["check"] == "section crashed"] == [("FAIL", "OSError")]
    assert any(r["dataset"] == "iso_oracle" and r["status"] == "PASS" for r in results)


def test_category_section_stops_with_its_own_record_on_mandatory_collision_overflow(bundle, monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    source, root, cache, _ = bundle
    install_many(monkeypatch, oracle)
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert [(r["dataset"], r["status"]) for r in category_records(results)] == [("category_oracle", "FAIL")]
    assert category_records(results)[0]["check"] == "category sample: mandatory collision coverage within 12-row bound"
    assert not [r for r in results if r["check"] == "section crashed" and r["dataset"] == "category_oracle"]
    assert [(r["dataset"], r["status"]) for r in results if r["check"] == COVERAGE] == [("route_oracle", "FAIL")]  # S1 record untouched.
    assert any(r["dataset"] == "iso_oracle" for r in results)


def test_category_section_with_invalid_source_identity_is_blocked_not_crashed(bundle):
    source, root, cache, _ = bundle
    path = source / "administrative/india_districts_lgd.geojson"
    frame = gpd.read_file(path)
    frame.loc[3, "district_code"] = 5.
    frame.to_file(path, driver="GeoJSON")
    results = capture(audit.audit_producer_graph_oracles, source, root, cache)
    assert [(r["dataset"], r["check"]) for r in results if r["check"] == "section crashed"] == [("route_oracle", "section crashed")]
    assert [(r["status"], r["detail"]) for r in category_records(results)] == [("FAIL", "IdentityError")]
    assert category_records(results)[0]["check"] == "source identity invalid: category oracle blocked"
    assert any(r["dataset"] == "iso_oracle" and r["status"] == "PASS" for r in results)


# ---- pure expectations: objective, arithmetic, nulls ----------------------------------------

def test_category_time_winner_beats_shorter_distance_and_source_order_breaks_exact_ties():
    from scripts.audit import producer_graph_oracle as oracle
    graph = graph_fixture()  # Direct 0-1 edge is shorter in km but slower: 0.2 h via node 2 beats 2 h.
    hubs = {"rail_station": [hub("rail_station", "SlowerShorter", [5000., 0.], 0), hub("rail_station", "FasterNode2", [0., 5000.], 1)],
            "icd": [hub("icd", "First", [5000., 0.], 0), hub("icd", "TieSecond", [5000., 0.], 1)]}
    result = oracle.category_expectations(graph, np.array([[0., 0.]]), hubs)[0]
    assert dict(zip(category_columns("rail_station"), [result["rail_station"][c] for c in category_columns("rail_station")]))["nearest_rail_station_name"] == "FasterNode2"
    assert result["rail_station"]["rail_station_road_distance_km"] == 5.0 and result["rail_station"]["rail_station_drive_time_hours"] == .1
    assert result["rail_station"]["rail_station_drive_time_min"] == 6.0 and result["rail_station"]["rail_station_straight_km"] == 5.0
    assert result["icd"]["nearest_icd_name"] == "First" and result["icd"]["icd_drive_time_hours"] == .2
    for kind in ("mmlp", "air_cargo", "icp", "toll_plaza", "expressway"):
        assert set(result[kind]) == set(category_columns(kind)) and all(v is None for v in result[kind].values())
    assert set(result["access"]) == {"highway_access_dist_km", "highway_access_time_min"}


@pytest.mark.parametrize("quantity", ["hours", "km"])
def test_category_sequential_feeder_arithmetic_matches_producer_rounding_boundary(quantity):
    from scripts.audit import producer_graph_oracle as oracle
    if quantity == "hours":
        od, hd, network = find_rounding_boundary(35., 1.005, 2)
        graph = linear_graph_pair(network, 5.)
        produced, pooled = round(network + od / 1000. / 35. + hd / 1000. / 35., 2), round(network + (od + hd) / 1000. / 35., 2)
    else:
        od, hd, network = find_rounding_boundary(1., 10.05, 1)
        graph = linear_graph_pair(1., network)
        produced, pooled = round(network + od / 1000. + hd / 1000., 1), round(network + (od + hd) / 1000., 1)
    assert produced != pooled
    hubs = {"icd": [hub("icd", "Hub", [1000., -float(hd)])]}
    result = oracle.category_expectations(graph, np.array([[0., -float(od)]]), hubs)[0]["icd"]
    assert result["icd_drive_time_hours" if quantity == "hours" else "icd_road_distance_km"] == produced != pooled


def linear_graph_pair(network_hours, network_km):
    return linear_graph(network_hours, network_km)


def test_category_nulls_invalid_coordinates_disconnected_island_and_same_point_rules():
    from scripts.audit import producer_graph_oracle as oracle
    graph = graph_fixture()  # Mainland nodes 0-2 and a distant island node 3 in its own component.
    far = np.array([1000000., 1000000.])
    points = np.array([[0., 0.], [400000., 0.], [np.nan, np.nan], far])
    hubs = {"rail_station": [hub("rail_station", "Mainland", [5000., 0.], 0), hub("rail_station", "InvalidCoords", None, 1),
                             hub("rail_station", "IslandHub", [400000., 0.], 2), hub("rail_station", "FarSame", far, 3)]}
    values = oracle.category_expectations(graph, points, hubs)
    assert values[0]["rail_station"]["nearest_rail_station_name"] == "Mainland" and values[0]["rail_station"]["rail_station_road_distance_km"] == 12.1
    island = values[1]["rail_station"]  # Valid island routing: same node and same point, so exactly zero.
    assert island["nearest_rail_station_name"] == "IslandHub" and island["rail_station_road_distance_km"] == 0. and island["rail_station_drive_time_hours"] == 0.
    assert all(v is None for v in values[2]["rail_station"].values()) and all(v is None for v in values[2]["access"].values())
    assert all(v is None for v in values[3]["rail_station"].values())  # Same point beyond every node: no compatible pair, never zero.
    raw = float(np.min(np.linalg.norm(graph.nh_node_xy - far, axis=1))) / 1000.
    assert raw > 150 and values[3]["access"] == {"highway_access_dist_km": round(raw, 1), "highway_access_time_min": round(raw / 35.0 * 60, 1)}


def test_access_uses_the_oracles_own_tree_and_rounds_distance_and_minutes_independently():
    from scripts.audit import producer_graph_oracle as oracle
    graph = linear_graph(1., 1.)

    class NoQuery(KDTree):
        def query(self, *args, **kwargs):
            raise AssertionError("graph.nh_tree.query must not be used for access")
    graph.nh_tree = NoQuery(graph.nh_node_xy)
    meters = next(m for m in range(1000, 60000) if round(round(m / 1000., 1) / 35. * 60., 1) != round(m / 1000. / 35. * 60., 1))
    raw = meters / 1000.
    assert round(round(raw, 1) / 35. * 60., 1) != round(raw / 35. * 60., 1)  # Distance-then-minutes would differ here.
    access = oracle.category_expectations(graph, np.array([[0., -float(meters)]]), {})[0]["access"]
    assert access == {"highway_access_dist_km": round(raw, 1), "highway_access_time_min": round(raw / 35. * 60., 1)}
    far = oracle.category_expectations(graph, np.array([[0., -200000.]]), {})[0]["access"]  # Beyond 150 km: uncapped.
    assert far == {"highway_access_dist_km": 200.0, "highway_access_time_min": round(200. / 35. * 60., 1)}


def test_category_metric_mismatch_and_nonfinite_time_are_corruption_not_values(monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    graph = graph_fixture()
    hubs = {"icd": [hub("icd", "A", [5000., 0.])]}
    original = oracle.kernel.pair_network_metrics

    def skewed(graph_arg, pairs, *, chunk_size):
        return [dataclasses.replace(m, network_time_hours=m.network_time_hours + .5) for m in original(graph_arg, pairs, chunk_size=chunk_size)]
    monkeypatch.setattr(oracle.kernel, "pair_network_metrics", skewed)
    with pytest.raises(ValueError):
        oracle.category_expectations(graph, np.array([[0., 0.]]), hubs)
    monkeypatch.undo()
    real = oracle.dijkstra
    monkeypatch.setattr(oracle, "dijkstra", lambda *a, **k: np.full_like(real(*a, **k), np.inf))
    with pytest.raises(ValueError):
        oracle.category_expectations(graph, np.array([[0., 0.]]), hubs)


# ---- instrumentation: time-only bounded Dijkstra, retained arrays, winner-only path metrics ------

def many_origin_case():
    graph, xy = line_graph(40)
    hubs = {"rail_station": [hub("rail_station", f"R{i}", xy[i], i) for i in (3, 9, 15, 21, 27, 33)],
            "icd": [hub("icd", "I0", xy[39], 0)]}
    return graph, xy, hubs


def test_category_dijkstra_is_own_time_only_unique_and_bounded_to_32_origins(monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    graph, xy, hubs = many_origin_case()
    seen, original = [], oracle.dijkstra

    def spy(matrix, **kwargs):
        seen.append((list(kwargs["indices"]), kwargs.get("directed"), kwargs.get("return_predecessors")))
        return original(matrix, **kwargs)
    monkeypatch.setattr(oracle, "dijkstra", spy)
    values = oracle.category_expectations(graph, xy, hubs)
    assert [len(indices) for indices, _, _ in seen] == [32, 8]  # 40 unique origins across both kinds, once each.
    flat = [i for indices, _, _ in seen for i in indices]
    assert len(flat) == len(set(flat)) == 40
    assert all(directed is False and not predecessors for _, directed, predecessors in seen)
    assert values[0]["rail_station"]["nearest_rail_station_name"] == "R3" and values[39]["icd"]["icd_drive_time_hours"] == 0.
    seen.clear()
    oracle.category_expectations(graph, xy, hubs, chunk_size=1)
    assert [len(indices) for indices, _, _ in seen] == [1] * 40
    seen.clear()
    oracle.category_expectations(graph, xy, hubs, chunk_size=32)
    assert [len(indices) for indices, _, _ in seen] == [32, 8]


@pytest.mark.parametrize("bad", [0, 33, -1, True, "32", 1.5, None, np.int64(8)])
def test_category_rejects_unsupported_chunk_sizes_like_the_route_contract(bad):
    from scripts.audit import producer_graph_oracle as oracle
    graph, xy, hubs = many_origin_case()
    with pytest.raises(ValueError):
        oracle.category_expectations(graph, xy[:2], hubs, chunk_size=bad)


class Tracked(np.ndarray):
    registry = []

    def __array_finalize__(self, obj):
        if obj is not None:
            Tracked.registry.append(weakref.ref(self))


def tracked_dijkstra(original, probe):
    def wrapper(matrix, **kwargs):
        probe.append(all(ref() is None for ref in Tracked.registry))  # Every earlier node-wide array (and view) is gone.
        Tracked.registry.clear()
        base = original(matrix, **kwargs)
        Tracked.registry.append(weakref.ref(base))
        return base.view(Tracked)
    return wrapper


def test_no_node_wide_array_survives_to_the_next_dijkstra_call(monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    graph, xy, hubs = many_origin_case()
    Tracked.registry.clear()
    probe = []
    monkeypatch.setattr(oracle, "dijkstra", tracked_dijkstra(oracle.dijkstra, probe))
    oracle.category_expectations(graph, xy, hubs, chunk_size=8)
    assert len(probe) == 5 and all(probe)
    assert all(ref() is None for ref in Tracked.registry)  # ...including after the function returns.


def test_the_retention_probe_detects_a_retaining_caller(monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    graph, xy, _ = many_origin_case()
    Tracked.registry.clear()
    probe, kept = [], []
    wrapper = tracked_dijkstra(oracle.dijkstra, probe)
    kept.append(wrapper(graph.nh_graph, directed=False, indices=[0, 1])[0])  # A retained row view
    wrapper(graph.nh_graph, directed=False, indices=[2])
    assert probe == [True, False]


def test_category_path_metrics_are_requested_for_winners_only_with_no_nonwinner_paths(monkeypatch):
    from scripts.audit import producer_graph_oracle as oracle
    graph, xy, hubs = many_origin_case()
    requests, paths = [], []
    original, original_path = oracle.kernel.pair_network_metrics, oracle.kernel.reconstruct_path

    def spy(graph_arg, pairs, *, chunk_size):
        pairs = list(pairs)
        requests.append(pairs)
        return original(graph_arg, pairs, chunk_size=chunk_size)

    def path_spy(u, v, predecessors):
        paths.append((u, v))
        return original_path(u, v, predecessors)
    monkeypatch.setattr(oracle.kernel, "pair_network_metrics", spy)
    monkeypatch.setattr(oracle.kernel, "reconstruct_path", path_spy)
    oracle.category_expectations(graph, xy, hubs)
    assert len(requests) == 1 and len(requests[0]) == 80  # 40 origins x 2 categories; 5 rail non-winners per origin never traced.
    assert len(paths) <= 80 and len(paths) == sum(1 for u, v in requests[0] if u != v)


# ---- expressway hubs, derived independently from the selected projected highway frame ------------

def test_expressway_hubs_are_motorway_midpoints_with_name_fallbacks_and_skipped_invalid_geometry():
    from scripts.audit import producer_graph_oracle as oracle
    offset = np.array([2000000., 1000000.])  # A projected offset, so no coincidental origin handling.
    def line(*coords):
        return LineString([tuple(offset + c) for c in coords])
    nh = gpd.GeoDataFrame(dict(highway=["motorway", "primary", "motorway", "motorway", "motorway", "motorway", "motorway"],
        nh=["1", "2", "7", None, "9", "10", "11"], name=["Eastern", "Ignored", None, None, "NoGeom", "Empty", None],
        geometry=[line((0, 0), (4000, 0)), line((0, 0), (1, 1)), line((0, 1000), (0, 5000)), line((0, 0), (0, 2000)), None, LineString(),
                  line((0, 0), (10000, 0), (10000, 10000), (20000, 10000))]), crs=7755)
    hubs = oracle._expressway_hubs(nh)
    assert [h["name"] for h in hubs] == ["Eastern", "NE 7", "Expressway", "NE 11"]
    assert [h["source_order"] for h in hubs] == [0, 1, 2, 5]  # Enumeration over motorway rows, skipped rows keep their index.
    expected = [(2000, 0), (0, 3000), (0, 1000), (10000, 5000)]  # Raw-coordinate midpoints at normalized distance 0.5.
    assert all(np.allclose(h["point"], offset + e) for h, e in zip(hubs, expected))
    assert oracle._expressway_hubs(gpd.GeoDataFrame(dict(nh=["1"], geometry=[line((0, 0), (1, 1))]), crs=7755)) == []


def test_category_source_adapter_maps_files_validates_schema_and_marks_absence(tmp_path):
    from scripts.audit import producer_graph_oracle as oracle
    (tmp_path / "rail").mkdir()
    (tmp_path / "logistics_hubs").mkdir()
    pd.DataFrame(dict(station_name=["S1", np.nan, "S3"], latitude=[19., 19., 95.], longitude=[73., 73., 73.])).to_csv(tmp_path / "rail/railway_stations.csv", index=False)
    pd.DataFrame(dict(wrong=["x"], latitude=[19.], longitude=[73.])).to_csv(tmp_path / "logistics_hubs/icds.csv", index=False)
    nh = gpd.GeoDataFrame(dict(highway=["primary"], geometry=[LineString([(0, 0), (1, 1)])]), crs=7755)
    with pytest.raises(ValueError):
        oracle._category_hubs(tmp_path, nh)  # icds.csv lacks the actual name column.
    (tmp_path / "logistics_hubs/icds.csv").unlink()
    hubs = oracle._category_hubs(tmp_path, nh)
    assert hubs["icd"] is None and hubs["air_cargo"] is None and hubs["mmlp"] is None and hubs["expressway"] == []
    rail = hubs["rail_station"]
    assert [h["source_order"] for h in rail] == [0, 1, 2] and rail[0]["name"] == "S1" and pd.isna(rail[1]["name"])
    assert rail[0]["point"] is not None and rail[2]["point"] is None  # Out-of-range latitude is an invalid candidate, retained.


@pytest.mark.parametrize("kind", CATEGORY_KINDS)
@pytest.mark.parametrize("order", ["declared_later_first", "declared_earlier_first"])
def test_exact_raw_time_ties_are_broken_by_declared_source_order_not_input_slot(kind, order):
    from scripts.audit import producer_graph_oracle as oracle
    graph = graph_fixture()  # Both hubs sit on node 1: identical raw time and feeders.
    later, earlier = hub(kind, "LaterSourceRow", [5000., 0.], 19), hub(kind, "EarlierSourceRow", [5000., 0.], 3)
    hubs = [later, earlier] if order == "declared_later_first" else [earlier, later]
    result = oracle.category_expectations(graph, np.array([[0., 0.]]), {kind: hubs})[0][kind]
    assert result[f"nearest_{kind}_name"] == "EarlierSourceRow"
    assert result[f"{kind}_drive_time_hours"] == .2 and result[f"{kind}_road_distance_km"] == 12.1


@pytest.mark.parametrize("kind", CATEGORY_KINDS)
def test_equal_declared_source_order_falls_back_to_the_stable_input_slot(kind):
    from scripts.audit import producer_graph_oracle as oracle
    hubs = [hub(kind, "FirstSlot", [5000., 0.], 7), hub(kind, "SecondSlot", [5000., 0.], 7)]
    assert oracle.category_expectations(graph_fixture(), np.array([[0., 0.]]), {kind: hubs})[0][kind][f"nearest_{kind}_name"] == "FirstSlot"


def three_node_graph(time_one, time_two):
    xy = np.array([[0., 0.], [1000., 0.], [0., 1000.]])
    times = np.array([[0, time_one, time_two], [time_one, 0, 0], [time_two, 0, 0]])
    km = np.array([[0, 1., 1.], [1., 0, 0], [1., 0, 0]])
    return SimpleNamespace(nh_graph=csr_matrix(times), nh_distance_graph=csr_matrix(km), nh_node_xy=xy,
                           nh_comp_labels=np.array([1000000] * 3), nh_tree=KDTree(xy), nh_bridge_mask=csr_matrix((3, 3)))


@pytest.mark.parametrize("kind", CATEGORY_KINDS)
@pytest.mark.parametrize("order", ["declared_later_first", "declared_earlier_first"])
def test_a_raw_faster_route_beats_an_earlier_source_order_even_when_the_published_hour_is_the_same(kind, order):
    from scripts.audit import producer_graph_oracle as oracle
    graph = three_node_graph(.101, .104)  # Both publish as 0.10 h; the first is raw-faster.
    faster_later = hub(kind, "RawFasterLaterRow", [1000., 0.], 5)
    slower_earlier = hub(kind, "RawSlowerEarlierRow", [0., 1000.], 1)
    hubs = [faster_later, slower_earlier] if order == "declared_later_first" else [slower_earlier, faster_later]
    result = oracle.category_expectations(graph, np.array([[0., 0.]]), {kind: hubs})[0][kind]
    assert result[f"nearest_{kind}_name"] == "RawFasterLaterRow" and result[f"{kind}_drive_time_hours"] == .1
    assert round(.101, 2) == round(.104, 2)  # The control: rounding alone could not have discriminated.


def test_the_adapter_order_still_selects_ties_in_csv_order_for_every_source_kind(tmp_path):
    from scripts.audit import producer_graph_oracle as oracle
    for relative, column in (v for v in oracle.CATEGORY_SOURCES.values()):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({column: ["Row0", "Row1"], "latitude": [19., 19.], "longitude": [73., 73.]}).to_csv(tmp_path / relative, index=False)
    nh = gpd.GeoDataFrame(dict(highway=["primary"], geometry=[LineString([(0, 0), (1, 1)])]), crs=7755)
    hubs = oracle._category_hubs(tmp_path, nh)
    assert all([h["source_order"] for h in hubs[kind]] == [0, 1] for kind in oracle.CATEGORY_SOURCES)


def test_new_category_code_has_no_producer_imports_or_private_producer_constants():
    from scripts.audit import producer_graph_oracle as oracle
    tree = ast.parse(Path(oracle.__file__).read_text())
    names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any("nh_travel_matrix" in name or "generate_isochrones" in name for name in names)
    assert "HUB_FILES" not in Path(oracle.__file__).read_text().replace("# HUB_FILES", "")


# ---- N1c: independent source-grounded isochrone coverage oracle (dataset coverage_oracle) --------------------------
# Every test below is a NEW requirement (appended; no old binding is shadowed). Builders, N1a facts, coverage and
# limitations helpers are SETUP ONLY; they are forbidden while the oracle runs and never supply its expectations.
import os  # noqa: E402  (new import in the appended region)

N1C_SIDECAR = "producer_limitations.json"
N1C_AVAILABILITY = "coverage sidecar availability"
N1C_LEGACY = "not applicable: legacy coverage unavailable"
N1C_FINAL = "coverage hub set equals source order"
N1C_COORDINATE_AST_SHA256 = "48c85ff3845929f61b94170eee611a57531842fcaac3fa4bad87162618edc302"
N1C_FIELDS = ("nh_graph", "nh_distance_graph", "nh_node_xy", "nh_comp_labels", "nh_tree", "nh_bridge_mask")


def n1c_oracle():
    from scripts.audit import producer_graph_oracle as oracle
    return oracle


def n1c_run(bundle):
    source, root, cache, _ = bundle
    return capture(audit.audit_producer_graph_oracles, source, root, cache)


def n1c_block(results):
    return [r for r in results if r["dataset"] == "coverage_oracle"]


def n1c_triples(block):
    return [(r["check"], r["status"], r["detail"]) for r in block]


def n1c_pairs(block):
    return [(r["check"], r["status"]) for r in block]


def n1c_only(monkeypatch):
    """Coverage-only runs: the three older sections are replaced by no-ops (public full path covered separately)."""
    oracle = n1c_oracle()
    for name in ("_route_checks", "_category_checks", "_iso_checks"):
        monkeypatch.setattr(oracle, name, lambda *args, **kwargs: None)


def n1c_label(entry):
    return f"{entry['hub_type']} {entry['hub_order']} {entry['hub_name']}: independent snap feeder and component cardinality"


def n1c_expected_block(entries):
    return [(N1C_AVAILABILITY, "PASS"), *[(n1c_label(e), "PASS") for e in entries], (N1C_FINAL, "PASS")]


def n1c_write_sidecar(bundle, *, v2=True):
    """SETUP ONLY: real validated capture, real N1a facts/coverage/limitations builders and the written ISO documents."""
    from scripts.analyze import isochrone_coverage as coverage_module
    from scripts.analyze import producer_limitations as limitations
    from scripts.clean import graph_builder as builder
    source, root, cache, _ = bundle
    nh, _selected = builder.load_highway_source(source)
    values = builder.load_or_build_cached_graph(nh, cache_dir=cache)
    builder.validate_graph_metadata(*values)
    graph = SimpleNamespace(**dict(zip(N1C_FIELDS, values)))
    ports = pd.read_csv(source / "logistics_hubs/ports.csv")
    port_facts = coverage_module.component_facts(graph, ports[ports.hub_type == "major_port"].to_dict("records"), "major_port")
    mmlp_facts = coverage_module.component_facts(graph, pd.read_csv(source / "logistics_hubs/mmlps.csv").to_dict("records"), "mmlp")
    coverage = coverage_module.coverage_entries(pm.read_manifest(root / pm.REQUIRED_ARTIFACTS[5]), pm.read_manifest(root / pm.REQUIRED_ARTIFACTS[6]),
                                                port_facts, mmlp_facts)
    document = limitations.build_limitations(source, {n: root / n for n in pm.REQUIRED_ARTIFACTS}, "synthetic-oracle", "windows_peak_working_set",
                                             isochrone_coverage=coverage if v2 else None)
    pm.atomic_write_manifest(document, root / N1C_SIDECAR)
    manifest = pm.read_manifest(root / pm.MANIFEST_FILENAME)
    assert limitations.validate_limitations(root / N1C_SIDECAR, manifest, source_root=source, require_coverage=v2) == []
    return document


def n1c_rewrite(bundle, change):
    path = bundle[1] / N1C_SIDECAR
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    pm.atomic_write_manifest(document, path)
    return document


@pytest.fixture
def n1c_v2(bundle):
    return bundle, n1c_write_sidecar(bundle)


def n1c_read_type(path):
    try:
        pm.read_manifest(path)
    except Exception as exc:
        return type(exc).__name__
    return None


# T01: absent sidecar and a genuine version-1 sidecar are legacy non-applicability, after the ISO block.
def test_n1c_legacy_availability_for_an_absent_sidecar(bundle):
    results = n1c_run(bundle)
    assert n1c_triples(n1c_block(results)) == [(N1C_AVAILABILITY, "PASS", N1C_LEGACY)]
    assert results[-1]["dataset"] == "coverage_oracle"
    assert max(i for i, r in enumerate(results) if r["dataset"] == "iso_oracle") < len(results) - 1


def test_n1c_legacy_availability_for_a_real_version_one_sidecar(bundle):
    assert n1c_write_sidecar(bundle, v2=False)["schema_version"] == 1
    results = n1c_run(bundle)
    assert n1c_triples(n1c_block(results)) == [(N1C_AVAILABILITY, "PASS", N1C_LEGACY)] and results[-1]["dataset"] == "coverage_oracle"


# T02: the public full-only registration and the block order.
@pytest.mark.parametrize("fast", [True, False])
def test_n1c_public_wrapper_registers_coverage_only_in_the_full_run_and_last(bundle, monkeypatch, fast):
    source, root, cache, _ = bundle
    for name in ["audit_boundaries", "audit_rail", "audit_hubs", "audit_freight", "audit_demographics", "audit_geoparquet", "audit_catalog", "check_baseline", "audit_villages", "audit_analysis", "audit_roads", "audit_producers_fast"]:
        monkeypatch.setattr(audit, name, lambda *args, **kwargs: None)
    results = audit.AuditRunner(fast=fast, source_root=source, artifact_root=root, cache_dir=cache).run()
    datasets = [r["dataset"] for r in results]
    assert ("coverage_oracle" in datasets) is not fast
    if not fast:
        last = lambda name: max(i for i, d in enumerate(datasets) if d == name)  # noqa: E731
        assert datasets.index("graph_oracle") < datasets.index("route_oracle") < datasets.index("coverage_oracle")
        assert last("category_oracle") < datasets.index("coverage_oracle") and last("iso_oracle") < datasets.index("coverage_oracle")
        assert all(r["status"] == "PASS" for r in results)


# T03: a genuine version-2 sidecar, every source hub, read-only, with expectations known from the toy graph.
def test_n1c_v2_all_source_pairs_pass_readonly_with_independent_toy_expectations(n1c_v2):
    bundle, document = n1c_v2
    source, root, cache, _ = bundle
    before = [hashes(source), hashes(root), hashes(cache)]
    results = n1c_run(bundle)
    entries = document["isochrone_coverage"]["entries"]
    assert n1c_pairs(n1c_block(results)) == n1c_expected_block(entries) and all(r["status"] == "PASS" for r in results)
    assert [hashes(source), hashes(root), hashes(cache)] == before
    ports, mmlps = pd.read_csv(source / "logistics_hubs/ports.csv"), pd.read_csv(source / "logistics_hubs/mmlps.csv")
    assert [(e["hub_type"], e["hub_order"], e["hub_name"]) for e in entries] == (
        [("major_port", i, n) for i, n in enumerate(ports.name)] + [("mmlp", i, n) for i, n in enumerate(mmlps.name)])
    for position, entry in enumerate(entries):
        if position in {10, 11, 14, 15}:  # No-snap and invalid-coordinate toy hubs.
            assert entry["snap_feeder_km"] is None and entry["snap_component_node_count"] is None
        else:  # The hubs sit on the 4-node mainland vertices (grid snapping moves them by metres only); the island has 2 nodes.
            assert entry["snap_feeder_km"] < .02 and entry["snap_component_node_count"] == 4


# T04: schema discriminator and availability boundary (no legacy fallback for anything unreadable or unsupported).
N1C_DISCRIMINATOR = {
    "missing_key": "{}", "bool_true": '{"schema_version": true}', "bool_false": '{"schema_version": false}',
    "float_one": '{"schema_version": 1.0}', "float_two": '{"schema_version": 2.0}', "string_one": '{"schema_version": "1"}',
    "string_two": '{"schema_version": "2"}', "int_three": '{"schema_version": 3}', "int_zero": '{"schema_version": 0}',
    "null_version": '{"schema_version": null}', "list_document": "[1]", "null_document": "null", "string_document": '"2"',
    "number_document": "1",
}


@pytest.mark.parametrize("name", sorted(N1C_DISCRIMINATOR))
def test_n1c_unsupported_discriminator_raises_value_error_before_availability(bundle, monkeypatch, name):
    n1c_only(monkeypatch)
    (bundle[1] / N1C_SIDECAR).write_text(N1C_DISCRIMINATOR[name], encoding="utf-8")
    assert n1c_triples(n1c_block(n1c_run(bundle))) == [("section crashed", "FAIL", "ValueError")]


def test_n1c_exact_schema_one_is_discriminator_only_legacy_not_a_full_v1_validation(bundle, monkeypatch):
    n1c_only(monkeypatch)
    (bundle[1] / N1C_SIDECAR).write_text('{"schema_version": 1, "unvalidated": [1, 2]}', encoding="utf-8")
    assert n1c_triples(n1c_block(n1c_run(bundle))) == [(N1C_AVAILABILITY, "PASS", N1C_LEGACY)]


N1C_READ_FAILURES = {"duplicate_key": '{"schema_version": 1, "schema_version": 1}', "nan_token": '{"schema_version": NaN}',
                     "infinity_token": '{"schema_version": Infinity}', "invalid_json": "not json", "empty_file": ""}


@pytest.mark.parametrize("name", sorted(N1C_READ_FAILURES))
def test_n1c_present_but_unreadable_sidecar_keeps_its_native_exception_type_never_legacy(bundle, monkeypatch, name):
    n1c_only(monkeypatch)
    path = bundle[1] / N1C_SIDECAR
    path.write_text(N1C_READ_FAILURES[name], encoding="utf-8")
    expected = n1c_read_type(path)
    assert expected is not None
    assert n1c_triples(n1c_block(n1c_run(bundle))) == [("section crashed", "FAIL", expected)]


def test_n1c_a_directory_at_the_sidecar_path_is_present_not_absent(bundle, monkeypatch):
    n1c_only(monkeypatch)
    path = bundle[1] / N1C_SIDECAR
    path.mkdir()
    expected = n1c_read_type(path)
    assert expected is not None
    assert n1c_triples(n1c_block(n1c_run(bundle))) == [("section crashed", "FAIL", expected)]


def test_n1c_a_real_broken_link_is_present_not_absent(bundle, monkeypatch):
    n1c_only(monkeypatch)
    path = bundle[1] / N1C_SIDECAR
    try:
        os.symlink(bundle[1] / "no-such-target.json", path)
    except (OSError, NotImplementedError):
        pytest.skip("NEW SKIP (not executed coverage): link creation needs privileges on this host")
    assert os.path.lexists(path) and not path.exists()
    expected = n1c_read_type(path)
    assert expected is not None
    assert n1c_triples(n1c_block(n1c_run(bundle))) == [("section crashed", "FAIL", expected)]


def test_n1c_controlled_present_but_missing_target_probe_is_not_legacy(bundle, monkeypatch):
    """Branch logic only: lexists says present while the target is missing. NOT a real Windows link certification."""
    n1c_only(monkeypatch)
    path = bundle[1] / N1C_SIDECAR
    real = os.path.lexists
    monkeypatch.setattr(os.path, "lexists", lambda candidate: True if Path(candidate) == path else real(candidate))
    assert not path.exists()
    assert n1c_triples(n1c_block(n1c_run(bundle))) == [("section crashed", "FAIL", "FileNotFoundError")]


# T05: version-2 container and entry-envelope handling.
N1C_CONTAINERS = {
    "coverage_missing": {"schema_version": 2}, "coverage_null": {"schema_version": 2, "isochrone_coverage": None},
    "coverage_list": {"schema_version": 2, "isochrone_coverage": []}, "coverage_string": {"schema_version": 2, "isochrone_coverage": "x"},
    "entries_missing": {"schema_version": 2, "isochrone_coverage": {}},
    "entries_null": {"schema_version": 2, "isochrone_coverage": {"entries": None}},
    "entries_dict": {"schema_version": 2, "isochrone_coverage": {"entries": {}}},
    "entries_string": {"schema_version": 2, "isochrone_coverage": {"entries": "abc"}},
}


@pytest.mark.parametrize("name", sorted(N1C_CONTAINERS))
def test_n1c_version_two_declaration_then_bad_container_keeps_availability_and_crashes(bundle, monkeypatch, name):
    n1c_only(monkeypatch)
    (bundle[1] / N1C_SIDECAR).write_text(json.dumps(N1C_CONTAINERS[name]), encoding="utf-8")
    assert n1c_pairs(n1c_block(n1c_run(bundle))) == [(N1C_AVAILABILITY, "PASS"), ("section crashed", "FAIL")]
    assert n1c_block(n1c_run(bundle))[-1]["detail"] == "ValueError"


def test_n1c_empty_entries_with_nonempty_sources_is_availability_then_final_fail(bundle, monkeypatch):
    n1c_only(monkeypatch)
    (bundle[1] / N1C_SIDECAR).write_text(json.dumps({"schema_version": 2, "isochrone_coverage": {"entries": []}}), encoding="utf-8")
    assert n1c_pairs(n1c_block(n1c_run(bundle))) == [(N1C_AVAILABILITY, "PASS"), (N1C_FINAL, "FAIL")]


def n1c_drop(key):
    return lambda entry: {k: v for k, v in entry.items() if k != key}


N1C_ENVELOPES = {
    "non_object": lambda e: 5, "list_entry": lambda e: [], "null_entry": lambda e: None,
    "missing_type": n1c_drop("hub_type"), "missing_order": n1c_drop("hub_order"), "missing_name": n1c_drop("hub_name"),
    "missing_feeder": n1c_drop("snap_feeder_km"), "missing_component": n1c_drop("snap_component_node_count"),
    "bool_order": lambda e: {**e, "hub_order": True}, "string_order": lambda e: {**e, "hub_order": "2"},
    "float_order": lambda e: {**e, "hub_order": 2.0}, "null_order": lambda e: {**e, "hub_order": None},
    "blank_name": lambda e: {**e, "hub_name": "  "}, "empty_name": lambda e: {**e, "hub_name": ""},
    "int_name": lambda e: {**e, "hub_name": 7}, "null_name": lambda e: {**e, "hub_name": None},
    "int_type": lambda e: {**e, "hub_type": 3}, "null_type": lambda e: {**e, "hub_type": None},
}


@pytest.mark.parametrize("name", sorted(N1C_ENVELOPES))
def test_n1c_unformattable_entry_crashes_after_retaining_earlier_records_and_no_final(n1c_v2, monkeypatch, name):
    bundle, document = n1c_v2
    n1c_only(monkeypatch)
    entries = document["isochrone_coverage"]["entries"]
    n1c_rewrite(bundle, lambda d: d["isochrone_coverage"]["entries"].__setitem__(2, N1C_ENVELOPES[name](entries[2])))
    block = n1c_block(n1c_run(bundle))
    assert n1c_pairs(block) == [*n1c_expected_block(entries[:2])[:-1], ("section crashed", "FAIL")]
    assert block[-1]["detail"] == "ValueError"  # Type only: no value, label or path is echoed.


# T06: pair types, nulls and numeric tamper fail and continue; later entries and the final identity record survive.
N1C_PAIRS = {
    "feeder_off_tolerance": (0, "snap_feeder_km", lambda v: v + 1e-6, False), "feeder_within_tolerance": (0, "snap_feeder_km", lambda v: v + 5e-10, True),
    "feeder_bool": (0, "snap_feeder_km", lambda v: True, False), "feeder_int": (0, "snap_feeder_km", lambda v: 0, False),
    "feeder_string": (0, "snap_feeder_km", lambda v: str(v), False), "feeder_null": (0, "snap_feeder_km", lambda v: None, False),
    "feeder_negative": (0, "snap_feeder_km", lambda v: -1.0, False),
    "component_plus_one": (0, "snap_component_node_count", lambda v: v + 1, False), "component_bool": (0, "snap_component_node_count", lambda v: True, False),
    "component_float": (0, "snap_component_node_count", lambda v: float(v), False), "component_string": (0, "snap_component_node_count", lambda v: str(v), False),
    "component_zero": (0, "snap_component_node_count", lambda v: 0, False), "component_negative": (0, "snap_component_node_count", lambda v: -4, False),
    "component_null": (0, "snap_component_node_count", lambda v: None, False),
    "nosnap_feeder_value": (10, "snap_feeder_km", lambda v: 0.0, False), "nosnap_component_value": (10, "snap_component_node_count", lambda v: 4, False),
    "invalid_feeder_value": (11, "snap_feeder_km", lambda v: 0.0, False), "invalid_component_value": (11, "snap_component_node_count", lambda v: 4, False),
    "mmlp_nosnap_feeder_value": (14, "snap_feeder_km", lambda v: 1.0, False),
}


@pytest.mark.parametrize("name", sorted(N1C_PAIRS))
def test_n1c_pair_types_nulls_and_numeric_tamper_fail_and_continue(n1c_v2, monkeypatch, name):
    bundle, document = n1c_v2
    n1c_only(monkeypatch)
    position, field, change, passes = N1C_PAIRS[name]
    n1c_rewrite(bundle, lambda d: d["isochrone_coverage"]["entries"][position].__setitem__(field, change(d["isochrone_coverage"]["entries"][position][field])))
    entries = document["isochrone_coverage"]["entries"]
    expected = n1c_expected_block(entries)
    if not passes:
        expected[1 + position] = (expected[1 + position][0], "FAIL")
    assert n1c_pairs(n1c_block(n1c_run(bundle))) == expected  # Later entries and the final identity record are still emitted.


def test_n1c_a_nonfinite_text_value_is_a_strict_read_error_or_a_pair_fail_never_a_pass(n1c_v2, monkeypatch):
    bundle, document = n1c_v2
    n1c_only(monkeypatch)
    path = bundle[1] / N1C_SIDECAR
    text = path.read_text(encoding="utf-8")
    feeder = json.dumps(document["isochrone_coverage"]["entries"][0]["snap_feeder_km"])
    assert feeder in text
    path.write_text(text.replace(f'"snap_feeder_km": {feeder}', '"snap_feeder_km": 1e999', 1), encoding="utf-8")
    block = n1c_block(n1c_run(bundle))
    expected_type = n1c_read_type(path)
    if expected_type is not None:
        assert n1c_triples(block) == [("section crashed", "FAIL", expected_type)]
    else:
        assert (n1c_label(document["isochrone_coverage"]["entries"][0]), "FAIL") in n1c_pairs(block)


def test_n1c_a_numeric_mismatch_cannot_be_blessed_by_refreshed_provenance(n1c_v2, monkeypatch):
    bundle, document = n1c_v2
    n1c_only(monkeypatch)
    n1c_rewrite(bundle, lambda d: d["isochrone_coverage"]["entries"][0].__setitem__("snap_component_node_count", 5))
    refresh(bundle)  # The manifest and artifact hashes are refreshed; the pair is still wrong.
    block = n1c_block(n1c_run(bundle))
    assert (n1c_label(document["isochrone_coverage"]["entries"][0]), "FAIL") in n1c_pairs(block)


# T07: the identity SEQUENCE is compared (never as a set), and unknown identities fail without borrowing a neighbour's fact.
N1C_IDENTITIES = {
    "reorder": (lambda es: es.insert(0, es.pop(1)), ()), "drop": (lambda es: es.pop(5), ()),
    "extra_duplicate": (lambda es: es.append(dict(es[3])), ()),
    "unknown_name": (lambda es: es[4].__setitem__("hub_name", "No Such Port"), (4,)),
    "type_swapped": (lambda es: es[4].__setitem__("hub_type", "mmlp"), (4,)),
    "unsupported_type": (lambda es: es[4].__setitem__("hub_type", "icd"), (4,)),
    "negative_order": (lambda es: es[4].__setitem__("hub_order", -1), (4,)),
    "order_out_of_range": (lambda es: es[4].__setitem__("hub_order", 99), (4,)),
    "name_case": (lambda es: es[4].__setitem__("hub_name", es[4]["hub_name"].lower()), (4,)),
    "name_trailing_space": (lambda es: es[4].__setitem__("hub_name", es[4]["hub_name"] + " "), (4,)),
}


@pytest.mark.parametrize("name", sorted(N1C_IDENTITIES))
def test_n1c_identity_sequence_not_set_and_unknown_identities_fail_without_borrowing(n1c_v2, monkeypatch, name):
    bundle, _document = n1c_v2
    n1c_only(monkeypatch)
    mutate, failing = N1C_IDENTITIES[name]
    changed = n1c_rewrite(bundle, lambda d: mutate(d["isochrone_coverage"]["entries"]))
    entries = changed["isochrone_coverage"]["entries"]
    expected = n1c_expected_block(entries)
    for position in failing:
        expected[1 + position] = (expected[1 + position][0], "FAIL")
    expected[-1] = (N1C_FINAL, "FAIL")
    assert n1c_pairs(n1c_block(n1c_run(bundle))) == expected


def test_n1c_per_type_order_skips_an_interleaved_excluded_minor_port(n1c_v2, monkeypatch):
    bundle, document = n1c_v2
    source = bundle[0]
    n1c_only(monkeypatch)
    ports = pd.read_csv(source / "logistics_hubs/ports.csv")
    minor = ports.iloc[[0]].copy()
    minor["name"], minor["hub_type"] = "Minor Port X", "minor_port"
    pd.concat([ports.iloc[:5], minor, ports.iloc[5:]]).to_csv(source / "logistics_hubs/ports.csv", index=False)
    assert n1c_pairs(n1c_block(n1c_run(bundle))) == n1c_expected_block(document["isochrone_coverage"]["entries"])


def test_n1c_the_same_literal_name_across_hub_types_is_allowed(n1c_v2, monkeypatch):
    bundle, document = n1c_v2
    source = bundle[0]
    n1c_only(monkeypatch)
    port_name = document["isochrone_coverage"]["entries"][0]["hub_name"]
    mmlps = pd.read_csv(source / "logistics_hubs/mmlps.csv")
    mmlps.loc[0, "name"] = port_name
    mmlps.to_csv(source / "logistics_hubs/mmlps.csv", index=False)
    changed = n1c_rewrite(bundle, lambda d: d["isochrone_coverage"]["entries"][12].__setitem__("hub_name", port_name))
    assert n1c_pairs(n1c_block(n1c_run(bundle))) == n1c_expected_block(changed["isochrone_coverage"]["entries"])


# Source identity content errors are ValueError; native I/O and parse errors keep their own type (never normalized).
def n1c_edit_csv(source, relative, change):
    frame = pd.read_csv(source / relative)
    change(frame)
    frame.to_csv(source / relative, index=False)


N1C_SOURCE_CONTENT = {
    "ports_missing_hub_type": ("logistics_hubs/ports.csv", lambda f: f.drop(columns=["hub_type"], inplace=True)),
    "ports_missing_latitude": ("logistics_hubs/ports.csv", lambda f: f.drop(columns=["latitude"], inplace=True)),
    "mmlps_missing_longitude": ("logistics_hubs/mmlps.csv", lambda f: f.drop(columns=["longitude"], inplace=True)),
    "mmlps_missing_name": ("logistics_hubs/mmlps.csv", lambda f: f.rename(columns={"name": "label"}, inplace=True)),
    "zero_major_ports": ("logistics_hubs/ports.csv", lambda f: f.__setitem__("hub_type", "minor_port")),
    "zero_mmlp_rows": ("logistics_hubs/mmlps.csv", lambda f: f.drop(f.index, inplace=True)),
    "blank_port_name": ("logistics_hubs/ports.csv", lambda f: f.__setitem__("name", [" "] + list(f["name"][1:]))),
    "null_mmlp_name": ("logistics_hubs/mmlps.csv", lambda f: f.__setitem__("name", [None] + list(f["name"][1:]))),
    "duplicate_port_after_casefold": ("logistics_hubs/ports.csv", lambda f: f.__setitem__("name", [f["name"][0]] + [" " + f["name"][0].upper() + " "] + list(f["name"][2:]))),
    "duplicate_mmlp_after_casefold": ("logistics_hubs/mmlps.csv", lambda f: f.__setitem__("name", ["M1", " m1"] + list(f["name"][2:]))),
}


@pytest.mark.parametrize("name", sorted(N1C_SOURCE_CONTENT))
def test_n1c_source_content_errors_are_value_errors_after_the_availability_record(n1c_v2, monkeypatch, name):
    bundle, _document = n1c_v2
    n1c_only(monkeypatch)
    relative, change = N1C_SOURCE_CONTENT[name]
    n1c_edit_csv(bundle[0], relative, change)
    block = n1c_block(n1c_run(bundle))
    assert n1c_pairs(block) == [(N1C_AVAILABILITY, "PASS"), ("section crashed", "FAIL")] and block[-1]["detail"] == "ValueError"


def n1c_expected_read_type(path):
    try:
        pd.read_csv(path)
    except Exception as exc:
        return type(exc).__name__
    return None


N1C_SOURCE_IO = {
    "ports_missing_file": ("logistics_hubs/ports.csv", None), "mmlps_missing_file": ("logistics_hubs/mmlps.csv", None),
    "ports_empty_file": ("logistics_hubs/ports.csv", ""), "mmlps_empty_file": ("logistics_hubs/mmlps.csv", ""),
    "ports_parse_error": ("logistics_hubs/ports.csv", "name,hub_type\na,b\nc,d,e,f\n"),
}


@pytest.mark.parametrize("name", sorted(N1C_SOURCE_IO))
def test_n1c_native_source_io_and_parse_exceptions_keep_their_type_and_leak_nothing(n1c_v2, monkeypatch, name):
    bundle, _document = n1c_v2
    n1c_only(monkeypatch)
    relative, text = N1C_SOURCE_IO[name]
    path = bundle[0] / relative
    if text is None:
        path.unlink()
        expected = "FileNotFoundError"
    else:
        path.write_text(text, encoding="utf-8")
        expected = n1c_expected_read_type(path)
        assert expected is not None
    block = n1c_block(n1c_run(bundle))
    assert n1c_pairs(block) == [(N1C_AVAILABILITY, "PASS"), ("section crashed", "FAIL")] and block[-1]["detail"] == expected
    assert str(bundle[0]) not in json.dumps(block)


# T08: the oracle's own strict coordinate predicate; the old coercive coordinate is unchanged.
N1C_COORDINATES = [
    {"longitude": 77.0, "latitude": 20.0}, {"longitude": 77, "latitude": 20}, {"longitude": np.float32(77.0), "latitude": np.int64(20)},
    {"longitude": "77", "latitude": 20.0}, {"longitude": 77.0, "latitude": "20"}, {"longitude": True, "latitude": 20.0},
    {"longitude": 77.0, "latitude": np.True_}, {"longitude": float("nan"), "latitude": 20.0}, {"longitude": 77.0, "latitude": float("inf")},
    {"longitude": 180.5, "latitude": 20.0}, {"longitude": 77.0, "latitude": -90.5}, {"longitude": 1e308, "latitude": 20.0},
    {"longitude": None, "latitude": 20.0}, {"longitude": 77.0}, {}, {"longitude": 180.0, "latitude": 90.0}, {"longitude": -180.0, "latitude": -90.0},
    {"longitude": 0.0, "latitude": 0.0}, {"longitude": [77.0], "latitude": 20.0},
]


@pytest.mark.parametrize("row", N1C_COORDINATES, ids=[str(i) for i in range(len(N1C_COORDINATES))])
def test_n1c_strict_coordinate_predicate_matches_the_producer_policy_independently(row):
    from scripts.analyze import generate_isochrones as isochrones
    actual = n1c_oracle()._strict_coordinate(row)
    expected = isochrones._coordinate(row)
    assert (actual is None) == (expected is None)
    if expected is not None:
        assert np.array_equal(actual, expected)


def test_n1c_old_coercive_coordinate_is_unchanged_and_numeric_text_still_differs_from_the_strict_predicate():
    oracle = n1c_oracle()
    tree = ast.parse(Path(oracle.__file__).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "coordinate")
    assert hashlib.sha256(ast.dump(node).encode()).hexdigest() == N1C_COORDINATE_AST_SHA256
    text_row = {"longitude": "77", "latitude": "20"}
    assert oracle.coordinate(text_row) is not None and oracle._strict_coordinate(text_row) is None


def test_n1c_actual_csv_reader_types_are_real_numbers_so_text_rejection_is_not_claimed_from_a_csv(tmp_path):
    numeric, text = tmp_path / "numeric.csv", tmp_path / "text.csv"
    numeric.write_text("name,hub_type,latitude,longitude\nA,major_port,20,77\n", encoding="utf-8")
    text.write_text("name,hub_type,latitude,longitude\nB,major_port,abc,77\n", encoding="utf-8")
    row, bad = pd.read_csv(numeric).to_dict("records")[0], pd.read_csv(text).to_dict("records")[0]
    assert isinstance(row["latitude"], (int, float, np.number)) and n1c_oracle()._strict_coordinate(row) is not None  # Numeric text was inferred as a number.
    assert type(bad["latitude"]) is str and n1c_oracle()._strict_coordinate(bad) is None  # A real non-numeric string.


# T09: physical snap on a validated toy graph: node-ID ties, the exact 150 km boundary, and the nearest tiny island.
def n1c_make_graph(xy, edges, label_map=None):
    from scipy.sparse.csgraph import connected_components
    from scripts.clean import graph_builder as builder
    xy = np.asarray(xy, dtype=float)
    count = len(xy)
    km, hours = np.zeros((count, count)), np.zeros((count, count))
    for a, b in edges:
        length = float(np.linalg.norm(xy[a] - xy[b])) / 1000
        km[a, b] = km[b, a] = length
        hours[a, b] = hours[b, a] = length / 50
    labels = connected_components(csr_matrix(hours), directed=False)[1].astype(np.int64)
    if label_map is not None:
        labels = np.array([label_map[v] for v in labels], dtype=np.int64)
    graph = SimpleNamespace(nh_graph=csr_matrix(hours), nh_distance_graph=csr_matrix(km), nh_node_xy=xy, nh_comp_labels=labels,
                            nh_tree=KDTree(xy), nh_bridge_mask=csr_matrix((count, count)))
    builder.validate_graph_metadata(graph.nh_graph, graph.nh_distance_graph, graph.nh_node_xy, graph.nh_comp_labels, graph.nh_tree, graph.nh_bridge_mask)
    return graph


@pytest.mark.parametrize("distance,snapped", [(150000.0, True), (149999.99999, True), (150000.000001, False), (float("nan"), False),
                                              (float("inf"), False), (-1.0, False), (0.0, True)])
def test_n1c_snap_distance_boundary_is_inclusive_and_filters_nonfinite_and_negative(monkeypatch, distance, snapped):
    oracle = n1c_oracle()
    monkeypatch.setattr(oracle.kernel, "radius_candidates", lambda point, graph: (np.array([0]), np.array([distance])))
    result = oracle._coverage_snap(np.array([0., 0.]), SimpleNamespace(nh_comp_labels=np.zeros(5, dtype=int)))
    assert (result is not None) == snapped and (not snapped or result == (distance, 0))


def test_n1c_snap_ties_resolve_by_node_id_and_a_nearer_node_beats_a_lower_id(monkeypatch):
    oracle = n1c_oracle()
    for order in ([4, 3, 0], [0, 3, 4], [3, 0, 4]):
        monkeypatch.setattr(oracle.kernel, "radius_candidates", lambda point, graph, order=order: (np.array(order), np.full(len(order), 25000.0)))
        assert oracle._coverage_snap(np.array([0., 0.]), SimpleNamespace(nh_comp_labels=np.zeros(5, dtype=int))) == (25000.0, 0)
    monkeypatch.setattr(oracle.kernel, "radius_candidates", lambda point, graph: (np.array([0, 3]), np.array([30000.0, 29999.0])))
    assert oracle._coverage_snap(np.array([0., 0.]), SimpleNamespace(nh_comp_labels=np.zeros(5, dtype=int))) == (29999.0, 3)
    assert oracle._coverage_snap(None, SimpleNamespace(nh_comp_labels=np.zeros(5, dtype=int))) is None


def test_n1c_supported_mainland_beats_a_nearer_tiny_island_two_sided(monkeypatch):
    oracle = n1c_oracle()
    graph = n1c_make_graph([(5000., 0.), (6000., 0.), (40000., 0.), (41000., 0.), (42000., 0.), (43000., 0.)], [(0, 1), (2, 3), (3, 4), (4, 5)])
    sizes = oracle._coverage_component_sizes(graph.nh_comp_labels)
    assert sorted(sizes.values()) == [2, 4]
    monkeypatch.setattr(oracle, "ORACLE_MIN_SUPPORTED_COMPONENT_NODES", 3)
    distance, node = oracle._coverage_snap(np.array([0., 0.]), graph)
    assert (distance, node) == (40000.0, 2) and sizes[int(graph.nh_comp_labels[node])] == 4
    monkeypatch.setattr(oracle, "ORACLE_MIN_SUPPORTED_COMPONENT_NODES", 1)
    distance, node = oracle._coverage_snap(np.array([0., 0.]), graph)
    assert (distance, node) == (5000.0, 0) and sizes[int(graph.nh_comp_labels[node])] == 2
    assert oracle._coverage_snap(np.array([0., 400000.]), graph) is None


# T10: one np.unique partition map, plain ints, safe for sparse and huge labels.
def test_n1c_component_sizes_use_one_unique_partition_with_plain_ints_and_no_bincount(monkeypatch):
    oracle = n1c_oracle()
    monkeypatch.setattr(np, "bincount", lambda *args, **kwargs: pytest.fail("a max-label allocation was attempted"))
    sizes = oracle._coverage_component_sizes(np.array([5, 5, 9, 10 ** 12, 9, 5], dtype=np.int64))
    assert sizes == {5: 3, 9: 2, 10 ** 12: 1} and all(type(k) is int and type(v) is int for k, v in sizes.items())


def test_n1c_non_dense_validated_labels_give_the_same_pass_block(n1c_v2, monkeypatch):
    bundle, document = n1c_v2
    oracle = n1c_oracle()
    n1c_only(monkeypatch)
    real = oracle.builder.load_or_build_cached_graph

    def relabeled(*args, **kwargs):
        values = list(real(*args, **kwargs))
        values[3] = values[3].astype(np.int64) * 7_000_000_000 + 3  # A validated, partition-preserving, non-dense relabel.
        return tuple(values)
    monkeypatch.setattr(oracle.builder, "load_or_build_cached_graph", relabeled)
    assert n1c_pairs(n1c_block(n1c_run(bundle))) == n1c_expected_block(document["isochrone_coverage"]["entries"])


# T11: no shared helpers or producers, one loader capture, and no Dijkstra in the NEW section (older sections need it).
def test_n1c_oracle_module_imports_no_coverage_helper_sidecar_module_or_producer():
    oracle = n1c_oracle()
    tree = ast.parse(Path(oracle.__file__).read_text(encoding="utf-8"))
    names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    imported = [alias.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for alias in n.names]
    assert not any(token in name for name in [*names, *imported] for token in ("isochrone_coverage", "producer_limitations", "nh_travel_matrix", "generate_isochrones"))


def test_n1c_oracle_run_never_calls_the_shared_helpers_and_loads_one_capture(n1c_v2, monkeypatch):
    from scripts.analyze import isochrone_coverage as coverage_module
    from scripts.analyze import producer_limitations as limitations
    bundle, document = n1c_v2  # Setup used the helpers; from here on they are forbidden.
    oracle = n1c_oracle()

    def forbidden(*args, **kwargs):
        pytest.fail("a shared coverage/limitations helper was called while the oracle ran")
    for module, names in ((coverage_module, ("component_facts", "coverage_entries", "coverage_issues", "hub_point", "component_sizes")),
                          (limitations, ("build_limitations", "validate_limitations"))):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    loads = []
    real_cache, real_source = oracle.builder.load_or_build_cached_graph, oracle.builder.load_highway_source
    monkeypatch.setattr(oracle.builder, "load_or_build_cached_graph", lambda *a, **k: (loads.append("cache"), real_cache(*a, **k))[1])
    monkeypatch.setattr(oracle.builder, "load_highway_source", lambda *a, **k: (loads.append("source"), real_source(*a, **k))[1])
    results = n1c_run(bundle)
    assert n1c_pairs(n1c_block(results)) == n1c_expected_block(document["isochrone_coverage"]["entries"])
    assert loads == ["source", "cache"]


def test_n1c_the_new_section_runs_no_dijkstra_pair_metrics_or_component_search(n1c_v2, monkeypatch):
    bundle, document = n1c_v2
    oracle = n1c_oracle()
    n1c_only(monkeypatch)  # The older sections legitimately use Dijkstra; only the new section is exercised here.

    def forbidden(*args, **kwargs):
        pytest.fail("the coverage section performed graph search work")
    monkeypatch.setattr(oracle, "dijkstra", forbidden)
    monkeypatch.setattr(oracle.kernel, "pair_network_metrics", forbidden)
    monkeypatch.setattr(oracle.kernel, "select_compatible_pair", forbidden)
    monkeypatch.setattr(oracle.kernel, "nearest_per_component", forbidden)
    assert n1c_pairs(n1c_block(n1c_run(bundle))) == n1c_expected_block(document["isochrone_coverage"]["entries"])


# T12: partial records and sibling failures.
def test_n1c_an_earlier_section_crash_does_not_suppress_the_coverage_block(n1c_v2, monkeypatch):
    from server.services.exceptions import DataIntegrityError
    bundle, document = n1c_v2
    oracle = n1c_oracle()

    def corrupt(*args, **kwargs):
        raise DataIntegrityError("Synthetic corrupt time-predecessor metadata")
    monkeypatch.setattr(oracle.kernel, "pair_network_metrics", corrupt)
    results = n1c_run(bundle)
    assert any(r["check"] == "section crashed" and r["dataset"] == "route_oracle" for r in results)
    assert n1c_pairs(n1c_block(results)) == n1c_expected_block(document["isochrone_coverage"]["entries"])


def test_n1c_a_coverage_failure_leaves_sibling_blocks_and_never_leaks_paths(n1c_v2):
    bundle, _document = n1c_v2
    (bundle[0] / "logistics_hubs/mmlps.csv").unlink()
    results = n1c_run(bundle)
    crashed = [r for r in results if r["check"] == "section crashed"]
    assert "coverage_oracle" in {r["dataset"] for r in crashed}
    assert all(r["detail"] in {"FileNotFoundError", "ValueError"} for r in crashed)
    assert any(r["dataset"] == "route_oracle" and r["status"] == "PASS" for r in results)
    assert str(bundle[0]) not in json.dumps(n1c_block(results))


# T13: the approved single old-test supersession (domain suffix) and the single-record early stops stay.
def test_n1c_the_domain_corruption_test_ends_with_iso_pass_then_exactly_the_legacy_availability_pass(bundle, monkeypatch):
    from server.services.exceptions import DataIntegrityError
    oracle = n1c_oracle()

    def corrupt(*args, **kwargs):
        raise DataIntegrityError("Synthetic corrupt time-predecessor metadata")
    monkeypatch.setattr(oracle.kernel, "pair_network_metrics", corrupt)
    results = n1c_run(bundle)
    outcomes = [(r["dataset"], r["check"], r["status"]) for r in results]
    assert outcomes[:4] == [("graph_oracle", "validated six-field graph capture", "PASS"), ("graph_oracle", "manifest matches actual canonical graph", "PASS"),
                            ("route_oracle", "section crashed", "FAIL"), ("category_oracle", "section crashed", "FAIL")]
    assert outcomes[-1] == ("coverage_oracle", N1C_AVAILABILITY, "PASS") and results[-1]["detail"] == N1C_LEGACY
    assert len(outcomes) > 5 and all(o[0] == "iso_oracle" and o[2] == "PASS" for o in outcomes[4:-1])


# ---- N1c-F1 (test-only repair): the setup must call the real strict sidecar validator ------------------------------
# W1 is a newly persisted prerequisite witness (not a production or numerical RED); C1 documents the layered boundary.
@pytest.mark.parametrize("v2", [True, False])
def test_n1c_f1_setup_calls_real_strict_validator(bundle, monkeypatch, v2):
    from scripts.analyze import producer_limitations as limitations
    source, root, cache, _ = bundle
    real_validate, calls = limitations.validate_limitations, []

    def recording(*args, **kwargs):
        issues = real_validate(*args, **kwargs)  # The real strict validator; its result is returned unaltered.
        calls.append((args, dict(kwargs), issues))
        return issues
    monkeypatch.setattr(limitations, "validate_limitations", recording)
    n1c_write_sidecar(bundle, v2=v2)
    assert len(calls) == 1  # Exactly one validation, made by the setup helper itself.
    args, kwargs, issues = calls[0]
    assert len(args) == 2 and Path(args[0]) == root / N1C_SIDECAR and args[1] == pm.read_manifest(root / pm.MANIFEST_FILENAME)
    assert Path(kwargs["source_root"]) == source and type(kwargs["require_coverage"]) is bool and kwargs["require_coverage"] is v2
    assert issues == []


def test_n1c_f1_schema_valid_wrong_cardinality_fails_only_the_independent_entry(bundle):
    from scripts.analyze import producer_limitations as limitations
    source, root, cache, _ = bundle
    document = n1c_write_sidecar(bundle)
    entries = document["isochrone_coverage"]["entries"]
    position = next(i for i, e in enumerate(entries) if type(e["snap_component_node_count"]) is int)

    def change(written):
        entry = written["isochrone_coverage"]["entries"][position]
        wrong = entry["snap_component_node_count"] + 1  # A different, positive plain int above every reachable count.
        assert wrong > max(entry["reachable_node_counts"])
        entry["snap_component_node_count"] = wrong
        entry["component_bounded_thresholds"] = [t for t, s, c in zip(entry["thresholds_hours"], entry["statuses"], entry["reachable_node_counts"])
                                                 if s in ("ok", "unsupported_threshold") and c == wrong]  # The existing semantic rule.
    n1c_rewrite(bundle, change)  # Only the sidecar is rewritten (real writer); sources, artifacts, manifest and cache are untouched.
    manifest = pm.read_manifest(root / pm.MANIFEST_FILENAME)
    assert limitations.validate_limitations(root / N1C_SIDECAR, manifest, source_root=source, require_coverage=True) == []  # Schema-valid.
    before = [hashes(source), hashes(root), hashes(cache)]
    results = n1c_run(bundle)  # The public full path, not the coverage-only isolation.
    assert [hashes(source), hashes(root), hashes(cache)] == before
    expected = n1c_expected_block(entries)
    expected[1 + position] = (expected[1 + position][0], "FAIL")
    assert n1c_pairs(n1c_block(results)) == expected
    assert [r["check"] for r in results if r["status"] == "FAIL"] == [n1c_label(entries[position])]  # Exactly one FAIL overall.
    assert {r["dataset"] for r in results} >= {"graph_oracle", "route_oracle", "category_oracle", "iso_oracle", "coverage_oracle"}


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
