"""Synthetic isochrone coverage facts and schema invariants (sidecar schema 2, N1a): no national data."""
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pytest
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import KDTree
from shapely.geometry import Point

from scripts.analyze import generate_isochrones as iso
from scripts.analyze import highway_pair_kernel as kernel
from scripts.analyze import isochrone_coverage as cov
from scripts.clean import graph_builder

ENTRY_ORDER = ["hub_type", "hub_name", "hub_order", "thresholds_hours", "statuses", "reachable_node_counts",
               "snap_feeder_km", "snap_component_node_count", "ok_threshold_count", "saturated_across_ok_thresholds",
               "component_bounded_thresholds"]
FACT_KEYS = ["hub_type", "hub_name", "hub_order", "snap_feeder_km", "snap_component_node_count"]


def project(lon, lat):
    point = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(7755).iloc[0]
    return np.array([point.x, point.y])


def make_graph(xy, edges, label_map=None):
    """Real six-field namespace that passes the accepted graph metadata validation."""
    xy = np.asarray(xy, dtype=float)
    n = len(xy)
    km, hours = np.zeros((n, n)), np.zeros((n, n))
    for a, b in edges:
        length = float(np.linalg.norm(xy[a] - xy[b])) / 1000
        km[a, b] = km[b, a] = length
        hours[a, b] = hours[b, a] = length / 50
    time_graph, dist_graph = csr_matrix(hours), csr_matrix(km)
    labels = connected_components(time_graph, directed=False)[1].astype(np.int64)
    if label_map is not None:
        labels = np.array([label_map[v] for v in labels], dtype=np.int64)
    graph = SimpleNamespace(nh_graph=time_graph, nh_distance_graph=dist_graph, nh_node_xy=xy, nh_comp_labels=labels,
                            nh_tree=KDTree(xy), nh_bridge_mask=csr_matrix((n, n)))
    graph_builder.validate_graph_metadata(graph.nh_graph, graph.nh_distance_graph, graph.nh_node_xy,
                                          graph.nh_comp_labels, graph.nh_tree, graph.nh_bridge_mask)
    return graph


HUB = (77.0, 20.0)


def hub_row(name, lon=HUB[0], lat=HUB[1], **extra):
    return {"name": name, "longitude": lon, "latitude": lat, **extra}


def hub_graph(label_map=None):
    """Hub at HUB: component A nodes 0-2 near it (10 km, 11 km, 12 km east); component B nodes 3-4 farther."""
    origin = project(*HUB)
    xy = [origin + [10000, 0], origin + [11000, 0], origin + [12000, 0], origin + [-40000, 0], origin + [-41000, 0]]
    return make_graph(xy, [(0, 1), (1, 2), (3, 4)], label_map)


# --- API surface and module boundary ---------------------------------------------------------------------------

def test_constants_exact_contract():
    assert cov.SNAP_RULE == "closest NH vertex within 150 km on a supported connected component (at least 1000 nodes), node-ID ties; hubs with no supported-component vertex within 150 km are unsnapped; catchment limited to that vertex's connected component"
    assert tuple(cov.STATUSES) == ("invalid_coordinates", "no_highway_within_150km", "feeder_exceeds_threshold",
                                   "unsupported_threshold", "ok")
    assert tuple(cov.HUB_TYPES) == ("major_port", "mmlp")
    assert list(cov.ENTRY_KEYS) == ENTRY_ORDER and len(cov.ENTRY_KEYS) == 11
    assert cov.FEEDER_SPEED_KMH == 35.0 and type(cov.FEEDER_SPEED_KMH) is float
    assert cov.MAX_SNAP_M == kernel.MAX_FEEDER_DISTANCE_M == 150000.0


def test_module_imports_only_allowed_dependencies_and_never_the_sidecar_module():
    tree = ast.parse(Path(cov.__file__).read_text(encoding="utf-8"))
    modules, imported = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module)
            imported |= {f"{node.module}.{alias.name}" for alias in node.names}
    assert {n for n in modules if not n.startswith("scripts")} <= {"__future__", "numpy", "geopandas", "shapely.geometry", "math", "numbers"}
    assert {n for n in imported if n.startswith("scripts")} <= {"scripts.analyze.highway_pair_kernel"}
    assert not any("producer_limitations" in n for n in modules | imported)
    assert cov.kernel is kernel


# --- hub_point parity with the producer's coordinate policy -------------------------------------------------------

COORDINATE_CASES = [
    {"longitude": 77.0, "latitude": 20.0}, {"longitude": 77, "latitude": 20}, {"longitude": np.float32(77.0), "latitude": np.int64(20)},
    {"longitude": "77", "latitude": 20.0}, {"longitude": 77.0, "latitude": "20"}, {"longitude": True, "latitude": 20.0},
    {"longitude": 77.0, "latitude": np.True_}, {"longitude": float("nan"), "latitude": 20.0}, {"longitude": 77.0, "latitude": float("inf")},
    {"longitude": -float("inf"), "latitude": 20.0}, {"longitude": 180.5, "latitude": 20.0}, {"longitude": 77.0, "latitude": -90.5},
    {"longitude": 1e308, "latitude": 20.0}, {"longitude": None, "latitude": 20.0}, {"longitude": 77.0}, {"latitude": 20.0}, {},
    {"longitude": 180.0, "latitude": 90.0}, {"longitude": -180.0, "latitude": -90.0}, {"longitude": 0.0, "latitude": 0.0},
    {"longitude": [77.0], "latitude": 20.0}, {"longitude": np.nan, "latitude": np.nan},
]


@pytest.mark.parametrize("row", COORDINATE_CASES, ids=[str(i) for i in range(len(COORDINATE_CASES))])
def test_hub_point_matches_producer_coordinate_policy(row):
    actual, expected = cov.hub_point(row), iso._coordinate(row)
    assert (actual is None) == (expected is None)
    if expected is not None:
        assert np.array_equal(actual, expected) and actual.shape == (2,) and np.isfinite(actual).all()


def test_hub_point_valid_control_projects_to_epsg7755():
    assert np.array_equal(cov.hub_point(hub_row("A")), project(*HUB))


# --- component_sizes -------------------------------------------------------------------------------------------

def test_component_sizes_counts_are_python_ints_for_sparse_and_large_labels():
    sizes = cov.component_sizes(np.array([5, 5, 9, 1000000000000, 9, 5]))
    assert sizes == {5: 3, 9: 2, 1000000000000: 1}
    assert all(type(k) is int and type(v) is int for k, v in sizes.items())
    assert cov.component_sizes(np.array([], dtype=np.int64)) == {}


# --- component_facts ---------------------------------------------------------------------------------------------

def test_component_facts_snaps_nearest_vertex_and_reports_its_component_size():
    facts = cov.component_facts(hub_graph(), [hub_row("Alpha")], "major_port")
    assert facts == [{"hub_type": "major_port", "hub_name": "Alpha", "hub_order": 0,
                      "snap_feeder_km": pytest.approx(10.0, abs=1e-9), "snap_component_node_count": 3}]
    assert list(facts[0]) == FACT_KEYS and type(facts[0]["snap_feeder_km"]) is float and type(facts[0]["snap_component_node_count"]) is int


def test_component_facts_uses_the_far_small_component_when_it_is_nearest():
    origin = project(*HUB)
    graph = make_graph([origin + [60000, 0], origin + [61000, 0], origin + [-5000, 0], origin + [-6000, 0], origin + [-7000, 0]],
                       [(0, 1), (2, 3), (3, 4)])
    row = hub_row("Near", *HUB)
    assert cov.component_facts(graph, [row], "mmlp")[0]["snap_component_node_count"] == 3
    # Nearest vertex is in the 2-node component when the hub moves east: size follows the snap, not the largest component.
    east = gpd.GeoSeries([Point(*(origin + [60500, 0]))], crs=7755).to_crs(4326).iloc[0]
    assert cov.component_facts(graph, [hub_row("East", east.x, east.y)], "mmlp")[0]["snap_component_node_count"] == 2


def test_component_facts_order_types_and_unsnapped_rows():
    rows = [hub_row("Bad", lon="x"), hub_row("Alpha"), hub_row("Far", 70.0, 5.0), hub_row("Beta")]
    facts = cov.component_facts(hub_graph(), rows, "mmlp")
    assert [(f["hub_type"], f["hub_name"], f["hub_order"]) for f in facts] == [("mmlp", n, i) for i, n in enumerate(["Bad", "Alpha", "Far", "Beta"])]
    assert (facts[0]["snap_feeder_km"], facts[0]["snap_component_node_count"]) == (None, None)
    assert (facts[2]["snap_feeder_km"], facts[2]["snap_component_node_count"]) == (None, None)
    assert facts[1]["snap_component_node_count"] == facts[3]["snap_component_node_count"] == 3


def test_component_facts_node_id_ties_pick_lowest_node_not_input_order(monkeypatch):
    graph = hub_graph()
    # Nodes 3 and 0 are in different components; equal distances must resolve to node 0 whatever the candidate order.
    for order in ([4, 3, 0], [0, 3, 4], [3, 0, 4]):
        monkeypatch.setattr(cov.kernel, "radius_candidates",
                            lambda point, g, order=order: (np.array(order), np.full(len(order), 25000.0)))
        fact = cov.component_facts(graph, [hub_row("Tie")], "major_port")[0]
        assert fact["snap_component_node_count"] == 3 and fact["snap_feeder_km"] == 25.0
    monkeypatch.setattr(cov.kernel, "radius_candidates", lambda point, g: (np.array([3, 4]), np.array([25000.0, 25000.0])))
    assert cov.component_facts(graph, [hub_row("Tie")], "major_port")[0]["snap_component_node_count"] == 2


def test_component_facts_minimum_distance_beats_lower_node_id(monkeypatch):
    monkeypatch.setattr(cov.kernel, "radius_candidates", lambda point, g: (np.array([0, 3]), np.array([30000.0, 29999.0])))
    fact = cov.component_facts(hub_graph(), [hub_row("Closer")], "major_port")[0]
    assert fact["snap_component_node_count"] == 2 and fact["snap_feeder_km"] == 29.999


@pytest.mark.parametrize("distance,snapped", [(150000.0, True), (149999.99999, True), (150000.000001, False),
                                              (float("nan"), False), (float("inf"), False), (-1.0, False), (0.0, True)])
def test_component_facts_snap_distance_boundary_is_inclusive_150km_and_filters_invalid(monkeypatch, distance, snapped):
    monkeypatch.setattr(cov.kernel, "radius_candidates", lambda point, g: (np.array([0]), np.array([distance])))
    fact = cov.component_facts(hub_graph(), [hub_row("Edge")], "major_port")[0]
    assert (fact["snap_feeder_km"] is not None) == (fact["snap_component_node_count"] is not None) == snapped
    if snapped:
        assert fact["snap_feeder_km"] == distance / 1000


def test_component_facts_real_tree_boundary_includes_inside_and_excludes_outside():
    origin = project(*HUB)
    graph = make_graph([origin + [149000, 0], origin + [149500, 0], origin + [151000, 0]], [(0, 1)])
    assert cov.component_facts(graph, [hub_row("In")], "major_port")[0]["snap_component_node_count"] == 2
    outside = make_graph([origin + [150500, 0], origin + [151000, 0]], [(0, 1)])
    assert cov.component_facts(outside, [hub_row("Out")], "major_port")[0] == {
        "hub_type": "major_port", "hub_name": "Out", "hub_order": 0, "snap_feeder_km": None, "snap_component_node_count": None}


def test_component_facts_handles_non_dense_large_labels():
    graph = hub_graph({0: 7_000_000_000, 1: 3})
    facts = cov.component_facts(graph, [hub_row("Alpha")], "major_port")
    assert facts[0]["snap_component_node_count"] == 3 and type(facts[0]["snap_component_node_count"]) is int


def test_component_facts_output_is_scalar_only():
    for fact in cov.component_facts(hub_graph(), [hub_row("A"), hub_row("B", lon="bad")], "major_port"):
        assert all(type(v) in (str, int, float, type(None)) for v in fact.values())
        assert not any(isinstance(v, (np.ndarray, np.generic)) for v in fact.values())
    json.dumps(cov.component_facts(hub_graph(), [hub_row("A")], "major_port"), allow_nan=False)


def broken_graphs():
    good = hub_graph()
    yield "labels_2d", SimpleNamespace(**{**vars(good), "nh_comp_labels": good.nh_comp_labels.reshape(-1, 1)})
    yield "labels_float", SimpleNamespace(**{**vars(good), "nh_comp_labels": good.nh_comp_labels.astype(float)})
    yield "labels_short", SimpleNamespace(**{**vars(good), "nh_comp_labels": good.nh_comp_labels[:-1]})
    yield "labels_list", SimpleNamespace(**{**vars(good), "nh_comp_labels": good.nh_comp_labels.tolist()})
    yield "tree_misaligned", SimpleNamespace(**{**vars(good), "nh_tree": KDTree(good.nh_node_xy[:-1])})
    yield "xy_misaligned", SimpleNamespace(**{**vars(good), "nh_node_xy": good.nh_node_xy[:-1]})


@pytest.mark.parametrize("name,graph", list(broken_graphs()), ids=[n for n, _ in broken_graphs()])
def test_component_facts_rejects_misaligned_or_malformed_graph_structure(name, graph):
    with pytest.raises(ValueError):
        cov.component_facts(graph, [hub_row("A")], "major_port")


@pytest.mark.parametrize("hub_type,rows", [("port", [hub_row("A")]), ("", [hub_row("A")]), (None, [hub_row("A")]),
                                           ("major_port", [hub_row("")]), ("major_port", [hub_row("  ")]),
                                           ("major_port", [{"longitude": 77.0, "latitude": 20.0}]),
                                           ("major_port", [hub_row(5)]), ("major_port", [hub_row(None)])])
def test_component_facts_rejects_bad_hub_type_or_names(hub_type, rows):
    with pytest.raises(ValueError):
        cov.component_facts(hub_graph(), rows, hub_type)


# --- coverage_entries ----------------------------------------------------------------------------------------------

def fact(hub_type, name, order, feeder, component):
    return {"hub_type": hub_type, "hub_name": name, "hub_order": order, "snap_feeder_km": feeder, "snap_component_node_count": component}


def features(name, hub_type, thresholds, statuses, counts, feeder):
    return [{"type": "Feature", "geometry": None,
             "properties": {"hub_name": name, "hub_type": hub_type, "time_threshold_hours": t, "status": s,
                            "reachable_node_count": c, "snap_feeder_km": feeder, "feeder_time_hours": None}}
            for t, s, c in zip(thresholds, statuses, counts)]


PORT_T, MMLP_T = [4.0, 8.0, 12.0, 24.0], [4.0, 8.0, 12.0]


def documents():
    ports = features("Alpha", "major_port", PORT_T, ["ok"] * 4, [5, 20, 40, 40], 10.0)
    ports += features("Beta", "major_port", PORT_T, ["invalid_coordinates"] * 4, [0] * 4, None)
    mmlps = features("Mm1", "mmlp", MMLP_T, ["feeder_exceeds_threshold", "unsupported_threshold", "ok"], [0, 2, 7], 150.0)
    ports_doc, mmlps_doc = ({"type": "FeatureCollection", "features": f} for f in (ports, mmlps))
    port_facts = [fact("major_port", "Alpha", 0, 10.0, 40), fact("major_port", "Beta", 1, None, None)]
    return ports_doc, mmlps_doc, port_facts, [fact("mmlp", "Mm1", 0, 150.0, 7)]


def valid_coverage():
    return cov.coverage_entries(*documents())


def test_coverage_entries_derives_exact_entries_and_validates():
    coverage = valid_coverage()
    assert list(coverage) == ["snap_rule", "entries"] and coverage["snap_rule"] == cov.SNAP_RULE
    alpha, beta, mm1 = coverage["entries"]
    assert all(list(e) == ENTRY_ORDER for e in coverage["entries"])
    assert alpha == {"hub_type": "major_port", "hub_name": "Alpha", "hub_order": 0, "thresholds_hours": PORT_T,
                     "statuses": ["ok"] * 4, "reachable_node_counts": [5, 20, 40, 40], "snap_feeder_km": 10.0,
                     "snap_component_node_count": 40, "ok_threshold_count": 4, "saturated_across_ok_thresholds": False,
                     "component_bounded_thresholds": [12.0, 24.0]}
    assert beta["snap_feeder_km"] is None and beta["snap_component_node_count"] is None and beta["ok_threshold_count"] == 0
    assert beta["saturated_across_ok_thresholds"] is None and beta["component_bounded_thresholds"] == [] and beta["statuses"] == ["invalid_coordinates"] * 4
    assert (mm1["hub_type"], mm1["hub_order"], mm1["thresholds_hours"]) == ("mmlp", 0, MMLP_T)
    assert mm1["statuses"] == ["feeder_exceeds_threshold", "unsupported_threshold", "ok"] and mm1["reachable_node_counts"] == [0, 2, 7]
    assert mm1["ok_threshold_count"] == 1 and mm1["saturated_across_ok_thresholds"] is None and mm1["component_bounded_thresholds"] == [12.0]
    assert cov.coverage_issues(coverage, None) == [] and cov.coverage_issues(coverage, {"major_port": PORT_T, "mmlp": MMLP_T}) == []
    assert json.loads(json.dumps(coverage, allow_nan=False)) == coverage


def test_coverage_entries_accepts_integer_artifact_thresholds_as_canonical_floats():
    ports_doc, mmlps_doc, port_facts, mmlp_facts = documents()
    for feature in ports_doc["features"] + mmlps_doc["features"]:
        feature["properties"]["time_threshold_hours"] = int(feature["properties"]["time_threshold_hours"])
    coverage = cov.coverage_entries(ports_doc, mmlps_doc, port_facts, mmlp_facts)
    assert all(type(t) is float for e in coverage["entries"] for t in e["thresholds_hours"])


def test_coverage_entries_empty_hub_types_are_valid_empty_entries():
    coverage = cov.coverage_entries({"type": "FeatureCollection", "features": []}, {"type": "FeatureCollection", "features": []}, [], [])
    assert coverage == {"snap_rule": cov.SNAP_RULE, "entries": []} and cov.coverage_issues(coverage, None) == []


def mutate_documents(change):
    ports_doc, mmlps_doc, port_facts, mmlp_facts = copy.deepcopy(documents())
    change(ports_doc, mmlps_doc, port_facts, mmlp_facts)
    return ports_doc, mmlps_doc, port_facts, mmlp_facts


BINDING_ERRORS = {
    "fact_name_changed": lambda p, m, pf, mf: pf[0].__setitem__("hub_name", "Other"),
    "fact_order_swapped": lambda p, m, pf, mf: (pf[0].__setitem__("hub_order", 1), pf[1].__setitem__("hub_order", 0)),
    "fact_missing": lambda p, m, pf, mf: pf.pop(),
    "fact_extra": lambda p, m, pf, mf: pf.append(fact("major_port", "Gamma", 2, None, None)),
    "fact_type_wrong": lambda p, m, pf, mf: pf[0].__setitem__("hub_type", "mmlp"),
    "mmlp_fact_type_wrong": lambda p, m, pf, mf: mf[0].__setitem__("hub_type", "major_port"),
    "feeder_differs": lambda p, m, pf, mf: pf[0].__setitem__("snap_feeder_km", 10.001),
    "feeder_null_vs_value": lambda p, m, pf, mf: pf[1].__setitem__("snap_feeder_km", 5.0),
    "component_null_vs_value": lambda p, m, pf, mf: pf[0].__setitem__("snap_component_node_count", None),
    "artifact_feeder_changed": lambda p, m, pf, mf: [f["properties"].__setitem__("snap_feeder_km", 11.0) for f in p["features"][:4]],
    "artifact_feeder_mixed_within_hub": lambda p, m, pf, mf: p["features"][1]["properties"].__setitem__("snap_feeder_km", 12.0),
    "artifact_wrong_hub_type": lambda p, m, pf, mf: p["features"][0]["properties"].__setitem__("hub_type", "mmlp"),
    "artifact_name_mismatch": lambda p, m, pf, mf: [f["properties"].__setitem__("hub_name", "Alpha ") for f in p["features"][:4]],
    "artifact_run_split": lambda p, m, pf, mf: p["features"].insert(2, copy.deepcopy(p["features"][5])),
    "artifact_missing_hub": lambda p, m, pf, mf: [p["features"].pop() for _ in range(4)],
    "artifact_status_unknown": lambda p, m, pf, mf: p["features"][0]["properties"].__setitem__("status", "maybe"),
    "artifact_count_bool": lambda p, m, pf, mf: p["features"][0]["properties"].__setitem__("reachable_node_count", True),
    "artifact_thresholds_unsorted": lambda p, m, pf, mf: p["features"][0]["properties"].__setitem__("time_threshold_hours", 30.0),
    "artifact_status_vs_feeder": lambda p, m, pf, mf: m["features"][0]["properties"].__setitem__("status", "ok"),
    "artifact_ok_below_three": lambda p, m, pf, mf: m["features"][1]["properties"].__setitem__("status", "ok"),
    "artifact_count_over_component": lambda p, m, pf, mf: p["features"][3]["properties"].__setitem__("reachable_node_count", 41),
    "artifact_not_a_collection": lambda p, m, pf, mf: p.__setitem__("features", None),
    "artifact_feature_not_dict": lambda p, m, pf, mf: p["features"].append("x"),
    "artifact_properties_missing": lambda p, m, pf, mf: p["features"][0].pop("properties"),
}


@pytest.mark.parametrize("name", sorted(BINDING_ERRORS))
def test_coverage_entries_binding_and_invariant_errors_raise_value_error(name):
    with pytest.raises(ValueError):
        cov.coverage_entries(*mutate_documents(BINDING_ERRORS[name]))


def _string_threshold(p, m, pf, mf):
    p["features"][0]["properties"]["time_threshold_hours"] = "4"
    return p, m, pf, mf


MALFORMED_INPUTS = {
    "facts_none": lambda p, m, pf, mf: (p, m, None, mf),
    "facts_not_dicts": lambda p, m, pf, mf: (p, m, ["x", "y"], mf),
    "fact_feeder_string": lambda p, m, pf, mf: (p, m, [{**pf[0], "snap_feeder_km": "10"}, pf[1]], mf),
    "fact_component_string": lambda p, m, pf, mf: (p, m, [{**pf[0], "snap_component_node_count": "40"}, pf[1]], mf),
    "fact_hub_order_bool": lambda p, m, pf, mf: (p, m, pf, [{**mf[0], "hub_order": False}]),
    "documents_none": lambda p, m, pf, mf: (None, None, pf, mf),
    "threshold_string": _string_threshold,
}


@pytest.mark.parametrize("name", sorted(MALFORMED_INPUTS))
def test_coverage_entries_malformed_input_shapes_raise_only_value_error(name):
    args = MALFORMED_INPUTS[name](*copy.deepcopy(documents()))
    with pytest.raises(ValueError):
        cov.coverage_entries(*args)


def test_coverage_entries_feeder_within_1e9_km_binds():
    ports_doc, mmlps_doc, port_facts, mmlp_facts = documents()
    port_facts[0]["snap_feeder_km"] = 10.0 + 5e-10
    assert cov.coverage_entries(ports_doc, mmlps_doc, port_facts, mmlp_facts)["entries"][0]["snap_feeder_km"] == 10.0


# --- coverage_issues invariant matrix -------------------------------------------------------------------------------

def tampered(change):
    coverage = copy.deepcopy(valid_coverage())
    change(coverage)
    return coverage


def entry(coverage, position):
    return coverage["entries"][position]


INVALID = {
    "not_dict": lambda c: None,
    "snap_rule_changed": lambda c: c.__setitem__("snap_rule", "other"),
    "extra_top_key": lambda c: c.__setitem__("x", 1),
    "missing_entries": lambda c: c.pop("entries"),
    "entries_not_list": lambda c: c.__setitem__("entries", {}),
    "entry_not_dict": lambda c: c["entries"].append("x"),
    "entry_extra_key": lambda c: entry(c, 0).__setitem__("x", 1),
    "entry_key_missing": lambda c: entry(c, 0).pop("hub_name"),
    "hub_type_unknown": lambda c: entry(c, 0).__setitem__("hub_type", "icd"),
    "hub_name_blank": lambda c: entry(c, 0).__setitem__("hub_name", " "),
    "hub_name_type": lambda c: entry(c, 0).__setitem__("hub_name", 7),
    "order_bool": lambda c: entry(c, 0).__setitem__("hub_order", False),
    "order_float": lambda c: entry(c, 1).__setitem__("hub_order", 1.0),
    "order_gap": lambda c: entry(c, 1).__setitem__("hub_order", 2),
    "order_duplicate": lambda c: entry(c, 1).__setitem__("hub_order", 0),
    "order_swapped_entries": lambda c: c["entries"].insert(0, c["entries"].pop(1)),
    "types_interleaved": lambda c: c["entries"].insert(1, c["entries"].pop(2)),
    "mmlp_before_port": lambda c: c["entries"].reverse(),
    "duplicate_entry": lambda c: c["entries"].append(copy.deepcopy(entry(c, 2))),
    "thresholds_empty": lambda c: entry(c, 0).update(thresholds_hours=[], statuses=[], reachable_node_counts=[]),
    "thresholds_not_list": lambda c: entry(c, 0).__setitem__("thresholds_hours", (4.0, 8.0, 12.0, 24.0)),
    "thresholds_int": lambda c: entry(c, 0)["thresholds_hours"].__setitem__(0, 4),
    "thresholds_bool": lambda c: entry(c, 0)["thresholds_hours"].__setitem__(0, True),
    "thresholds_nan": lambda c: entry(c, 0)["thresholds_hours"].__setitem__(0, float("nan")),
    "thresholds_inf": lambda c: entry(c, 0)["thresholds_hours"].__setitem__(3, float("inf")),
    "thresholds_zero": lambda c: entry(c, 0)["thresholds_hours"].__setitem__(0, 0.0),
    "thresholds_negative": lambda c: entry(c, 0)["thresholds_hours"].__setitem__(0, -4.0),
    "thresholds_duplicate": lambda c: entry(c, 0)["thresholds_hours"].__setitem__(1, 4.0),
    "thresholds_decreasing": lambda c: entry(c, 0)["thresholds_hours"].reverse(),
    "thresholds_numpy": lambda c: entry(c, 0)["thresholds_hours"].__setitem__(0, np.float64(4.0)),
    "statuses_short": lambda c: entry(c, 0)["statuses"].pop(),
    "counts_short": lambda c: entry(c, 0)["reachable_node_counts"].pop(),
    "status_unknown": lambda c: entry(c, 0)["statuses"].__setitem__(0, "maybe"),
    "status_type": lambda c: entry(c, 0)["statuses"].__setitem__(0, 1),
    "count_bool": lambda c: entry(c, 0)["reachable_node_counts"].__setitem__(0, True),
    "count_float": lambda c: entry(c, 0)["reachable_node_counts"].__setitem__(0, 5.0),
    "count_negative": lambda c: entry(c, 0)["reachable_node_counts"].__setitem__(0, -1),
    "count_nan": lambda c: entry(c, 0)["reachable_node_counts"].__setitem__(0, float("nan")),
    "count_numpy": lambda c: entry(c, 0)["reachable_node_counts"].__setitem__(0, np.int64(5)),
    "count_huge": lambda c: entry(c, 0)["reachable_node_counts"].__setitem__(3, 10 ** 400),
    "count_decreasing": lambda c: entry(c, 0)["reachable_node_counts"].__setitem__(3, 39),
    "count_over_component": lambda c: entry(c, 0)["reachable_node_counts"].__setitem__(3, 41),
    "feeder_nan": lambda c: entry(c, 0).__setitem__("snap_feeder_km", float("nan")),
    "feeder_inf": lambda c: entry(c, 0).__setitem__("snap_feeder_km", float("inf")),
    "feeder_int": lambda c: entry(c, 0).__setitem__("snap_feeder_km", 10),
    "feeder_bool": lambda c: entry(c, 0).__setitem__("snap_feeder_km", True),
    "feeder_negative": lambda c: entry(c, 0).__setitem__("snap_feeder_km", -1.0),
    "feeder_over_150": lambda c: entry(c, 0).__setitem__("snap_feeder_km", 150.0000001),
    "feeder_numpy": lambda c: entry(c, 0).__setitem__("snap_feeder_km", np.float64(10.0)),
    "feeder_null_with_component": lambda c: entry(c, 0).__setitem__("snap_feeder_km", None),
    "component_null_with_feeder": lambda c: entry(c, 0).__setitem__("snap_component_node_count", None),
    "component_zero": lambda c: entry(c, 0).__setitem__("snap_component_node_count", 0),
    "component_bool": lambda c: entry(c, 0).__setitem__("snap_component_node_count", True),
    "component_float": lambda c: entry(c, 0).__setitem__("snap_component_node_count", 40.0),
    "component_huge": lambda c: entry(c, 0).__setitem__("snap_component_node_count", 10 ** 400),
    "unsnapped_with_feeder": lambda c: entry(c, 1).__setitem__("snap_feeder_km", 1.0),
    "unsnapped_with_component": lambda c: entry(c, 1).__setitem__("snap_component_node_count", 3),
    "unsnapped_mixed_statuses": lambda c: entry(c, 1)["statuses"].__setitem__(0, "no_highway_within_150km"),
    "unsnapped_status_ok": lambda c: entry(c, 1).__setitem__("statuses", ["ok"] * 4),
    "unsnapped_nonzero_count": lambda c: entry(c, 1)["reachable_node_counts"].__setitem__(0, 1),
    "unsnapped_ok_count": lambda c: entry(c, 1).__setitem__("ok_threshold_count", 1),
    "unsnapped_saturation_value": lambda c: entry(c, 1).__setitem__("saturated_across_ok_thresholds", False),
    "unsnapped_bounded": lambda c: entry(c, 1).__setitem__("component_bounded_thresholds", [4.0]),
    "snapped_status_invalid_coordinates": lambda c: entry(c, 0)["statuses"].__setitem__(0, "invalid_coordinates"),
    "snapped_status_no_highway": lambda c: entry(c, 0)["statuses"].__setitem__(0, "no_highway_within_150km"),
    "snapped_exceeds_without_cause": lambda c: entry(c, 0)["statuses"].__setitem__(0, "feeder_exceeds_threshold"),
    "exceeds_with_nonzero_count": lambda c: entry(c, 2)["reachable_node_counts"].__setitem__(0, 1),
    "exceeds_flipped_to_ok": lambda c: entry(c, 2)["statuses"].__setitem__(0, "ok"),
    "ok_with_two_nodes": lambda c: entry(c, 2)["statuses"].__setitem__(2, "unsupported_threshold"),
    "unsupported_with_zero": lambda c: entry(c, 2)["reachable_node_counts"].__setitem__(1, 0),
    "unsupported_with_three": lambda c: entry(c, 2)["reachable_node_counts"].__setitem__(1, 3),
    "ok_count_wrong": lambda c: entry(c, 0).__setitem__("ok_threshold_count", 3),
    "ok_count_bool": lambda c: entry(c, 0).__setitem__("ok_threshold_count", True),
    "saturation_wrong": lambda c: entry(c, 0).__setitem__("saturated_across_ok_thresholds", True),
    "saturation_null_with_two_ok": lambda c: entry(c, 0).__setitem__("saturated_across_ok_thresholds", None),
    "saturation_value_with_one_ok": lambda c: entry(c, 2).__setitem__("saturated_across_ok_thresholds", False),
    "saturation_int": lambda c: entry(c, 0).__setitem__("saturated_across_ok_thresholds", 0),
    "bounded_missing": lambda c: entry(c, 0).__setitem__("component_bounded_thresholds", [24.0]),
    "bounded_extra": lambda c: entry(c, 0).__setitem__("component_bounded_thresholds", [8.0, 12.0, 24.0]),
    "bounded_not_a_threshold": lambda c: entry(c, 0).__setitem__("component_bounded_thresholds", [12.0, 25.0]),
    "bounded_int": lambda c: entry(c, 0).__setitem__("component_bounded_thresholds", [12, 24.0]),
    "bounded_unsorted": lambda c: entry(c, 0).__setitem__("component_bounded_thresholds", [24.0, 12.0]),
    "bounded_by_saturation_not_size": lambda c: entry(c, 0).__setitem__("component_bounded_thresholds", []),
}


def test_valid_control_has_no_issues():
    assert cov.coverage_issues(valid_coverage(), None) == []


@pytest.mark.parametrize("name", sorted(INVALID))
def test_coverage_issues_report_every_invariant_violation_without_raising(name):
    coverage = tampered(INVALID[name]) if name != "not_dict" else []
    issues = cov.coverage_issues(coverage, None)
    assert issues and all(type(i) is str for i in issues), name
    # Issue text never echoes arbitrary values such as private paths or huge integers.
    assert not any("10000000000000000000" in i for i in issues)


@pytest.mark.parametrize("value", [None, 5, "x", [], (), 1.5, True, object()])
def test_coverage_issues_non_dict_inputs_never_raise(value):
    assert cov.coverage_issues(value, None)


def test_coverage_issues_with_thresholds_requires_exact_equality():
    expected = {"major_port": PORT_T, "mmlp": MMLP_T}
    assert cov.coverage_issues(valid_coverage(), expected) == []
    assert cov.coverage_issues(valid_coverage(), {"major_port": PORT_T, "mmlp": [4.0, 8.0]})
    assert cov.coverage_issues(valid_coverage(), {"major_port": [4.0, 8.0, 12.0, 24.5], "mmlp": MMLP_T})
    assert cov.coverage_issues(valid_coverage(), {"major_port": PORT_T})
    assert cov.coverage_issues(valid_coverage(), {"major_port": PORT_T, "mmlp": MMLP_T, "extra": [1.0]}) == []  # Only entry types are checked.
    assert cov.coverage_issues(valid_coverage(), {"major_port": [4, 8, 12, 24], "mmlp": MMLP_T})  # Integers are not canonical floats here.


def test_saturation_is_not_boundedness():
    # Saturated (all OK counts equal) but the component is larger than the count: not component bounded.
    coverage = {"snap_rule": cov.SNAP_RULE, "entries": [{
        "hub_type": "major_port", "hub_name": "Sat", "hub_order": 0, "thresholds_hours": [4.0, 8.0], "statuses": ["ok", "ok"],
        "reachable_node_counts": [10, 10], "snap_feeder_km": 1.0, "snap_component_node_count": 500, "ok_threshold_count": 2,
        "saturated_across_ok_thresholds": True, "component_bounded_thresholds": []}]}
    assert cov.coverage_issues(coverage, None) == []
    coverage["entries"][0]["component_bounded_thresholds"] = [4.0, 8.0]
    assert cov.coverage_issues(coverage, None)
    # Bounded by the whole component and saturated: both facts hold independently.
    coverage["entries"][0]["snap_component_node_count"] = 10
    assert cov.coverage_issues(coverage, None) == []
    coverage["entries"][0]["component_bounded_thresholds"] = []
    assert cov.coverage_issues(coverage, None)


def test_unsupported_counts_equal_to_component_are_bounded():
    coverage = {"snap_rule": cov.SNAP_RULE, "entries": [{
        "hub_type": "mmlp", "hub_name": "Tiny", "hub_order": 0, "thresholds_hours": [4.0, 8.0],
        "statuses": ["unsupported_threshold", "unsupported_threshold"], "reachable_node_counts": [1, 2], "snap_feeder_km": 0.0,
        "snap_component_node_count": 2, "ok_threshold_count": 0, "saturated_across_ok_thresholds": None,
        "component_bounded_thresholds": [8.0]}]}
    assert cov.coverage_issues(coverage, None) == []


def test_feeder_threshold_rule_uses_35_kmh_boundary():
    # feeder 140 km / 35 km/h = 4.0 h: equal to the 4 h threshold is NOT exceeded; just above is.
    base = {"snap_rule": cov.SNAP_RULE, "entries": [{
        "hub_type": "major_port", "hub_name": "Edge", "hub_order": 0, "thresholds_hours": [4.0], "statuses": ["ok"],
        "reachable_node_counts": [3], "snap_feeder_km": 140.0, "snap_component_node_count": 3, "ok_threshold_count": 1,
        "saturated_across_ok_thresholds": None, "component_bounded_thresholds": [4.0]}]}
    assert cov.coverage_issues(base, None) == []
    above = copy.deepcopy(base)
    above["entries"][0]["snap_feeder_km"] = 140.0000001
    assert cov.coverage_issues(above, None)  # Status ok is now inconsistent with the feeder time.
    above["entries"][0].update(statuses=["feeder_exceeds_threshold"], reachable_node_counts=[0], ok_threshold_count=0,
                               component_bounded_thresholds=[])
    assert cov.coverage_issues(above, None) == []


def test_coverage_issues_semantic_derived_values_are_recomputed_not_trusted():
    coverage = valid_coverage()
    alpha = entry(coverage, 0)
    derived = (alpha["ok_threshold_count"], alpha["saturated_across_ok_thresholds"], alpha["component_bounded_thresholds"])
    assert derived == (4, False, [12.0, 24.0])
    for key, wrong in (("ok_threshold_count", 2), ("saturated_across_ok_thresholds", True), ("component_bounded_thresholds", [4.0])):
        changed = copy.deepcopy(coverage)
        entry(changed, 0)[key] = wrong
        assert cov.coverage_issues(changed, None), key


# ---- F1 (plain-JSON type boundary) and A1 (same-type hub-name uniqueness) additions: exclusive f1_/a1_ names ----

F1_PLAIN_MESSAGE = ["isochrone_coverage must contain only plain JSON values"]
F1_THRESHOLDS_MESSAGE = ["configured thresholds must be an object of plain float lists"]


class F1List(list):
    pass


class F1Dict(dict):
    pass


class F1Str(str):
    pass


def f1_apply(change):
    coverage = copy.deepcopy(valid_coverage())
    replaced = change(coverage)
    return coverage if replaced is None else replaced


def f1_np_key_top(c):
    c[np.str_("snap_rule")] = c.pop("snap_rule")


def f1_np_key_entry(c):
    e = entry(c, 0)
    e[np.str_("hub_name")] = e.pop("hub_name")


def f1_sets(c):
    entry(c, 0)["statuses"] = set(entry(c, 0)["statuses"])


def f1_cycle_entries(c):
    c["entries"].append(c["entries"])


def f1_cycle_dict(c):
    entry(c, 0)["component_bounded_thresholds"] = [c]


F1_NOT_PLAIN = {
    "np_str_snap_rule": lambda c: c.__setitem__("snap_rule", np.str_(c["snap_rule"])),
    "np_str_hub_type": lambda c: entry(c, 0).__setitem__("hub_type", np.str_("major_port")),
    "np_str_hub_type_mmlp": lambda c: entry(c, 2).__setitem__("hub_type", np.str_("mmlp")),
    "np_str_hub_name": lambda c: entry(c, 0).__setitem__("hub_name", np.str_("Alpha")),
    "np_str_status": lambda c: entry(c, 0)["statuses"].__setitem__(0, np.str_("ok")),
    "np_str_top_key": f1_np_key_top,
    "np_str_entry_key": f1_np_key_entry,
    "array_snap_rule": lambda c: c.__setitem__("snap_rule", np.array([c["snap_rule"]])),
    "array_hub_type": lambda c: entry(c, 0).__setitem__("hub_type", np.array(["major_port"])),
    "array_hub_name": lambda c: entry(c, 0).__setitem__("hub_name", np.array(["Alpha"])),
    "array_status": lambda c: entry(c, 0)["statuses"].__setitem__(0, np.array(["ok"])),
    "array_entries": lambda c: c.__setitem__("entries", np.array([0])),
    "np_int64_count": lambda c: entry(c, 0)["reachable_node_counts"].__setitem__(0, np.int64(5)),
    "np_int64_order": lambda c: entry(c, 0).__setitem__("hub_order", np.int64(0)),
    "np_int64_ok_count": lambda c: entry(c, 0).__setitem__("ok_threshold_count", np.int64(4)),
    "np_int64_component": lambda c: entry(c, 0).__setitem__("snap_component_node_count", np.int64(40)),
    "np_float64_feeder": lambda c: entry(c, 0).__setitem__("snap_feeder_km", np.float64(10.0)),
    "np_float64_threshold": lambda c: entry(c, 0)["thresholds_hours"].__setitem__(0, np.float64(4.0)),
    "np_bool_saturated": lambda c: entry(c, 0).__setitem__("saturated_across_ok_thresholds", np.bool_(False)),
    "tuple_entries": lambda c: c.__setitem__("entries", tuple(c["entries"])),
    "tuple_thresholds": lambda c: entry(c, 0).__setitem__("thresholds_hours", tuple(entry(c, 0)["thresholds_hours"])),
    "set_statuses": f1_sets,
    "bytes_hub_name": lambda c: entry(c, 0).__setitem__("hub_name", b"Alpha"),
    "list_subclass_statuses": lambda c: entry(c, 0).__setitem__("statuses", F1List(entry(c, 0)["statuses"])),
    "list_subclass_entries": lambda c: c.__setitem__("entries", F1List(c["entries"])),
    "dict_subclass_entry": lambda c: c["entries"].__setitem__(0, F1Dict(entry(c, 0))),
    "dict_subclass_top": lambda c: F1Dict(c),
    "str_subclass_hub_name": lambda c: entry(c, 0).__setitem__("hub_name", F1Str("Alpha")),
    "str_subclass_snap_rule": lambda c: c.__setitem__("snap_rule", F1Str(c["snap_rule"])),
    "cycle_entries": f1_cycle_entries,
    "cycle_dict": f1_cycle_dict,
    "nonfinite_feeder": lambda c: entry(c, 0).__setitem__("snap_feeder_km", float("nan")),
    "nonfinite_threshold": lambda c: entry(c, 0)["thresholds_hours"].__setitem__(3, float("inf")),
    "non_str_key": lambda c: entry(c, 0).__setitem__(1, "x"),
}


@pytest.mark.parametrize("name", sorted(F1_NOT_PLAIN))
def test_f1_non_plain_coverage_returns_the_exact_fixed_plain_json_issue(name):
    assert cov.coverage_issues(f1_apply(F1_NOT_PLAIN[name]), None) == F1_PLAIN_MESSAGE


def test_f1_valid_plain_control_and_shared_acyclic_containers_stay_valid():
    assert cov.coverage_issues(valid_coverage(), None) == []
    shared = [4.0, 8.0, 12.0, 24.0]
    first, second = copy.deepcopy(entry(valid_coverage(), 0)), copy.deepcopy(entry(valid_coverage(), 0))
    first["thresholds_hours"] = second["thresholds_hours"] = shared  # Alias, not a cycle.
    second.update(hub_name="Alpha two", hub_order=1)
    coverage = {"snap_rule": cov.SNAP_RULE, "entries": [first, second]}
    assert cov.coverage_issues(coverage, None) == []
    assert json.loads(json.dumps(coverage, allow_nan=False)) == coverage
    huge = copy.deepcopy(coverage)
    huge["entries"][0]["reachable_node_counts"][3] = 10 ** 400  # Arbitrary plain ints are not capped by the guard.
    assert F1_PLAIN_MESSAGE != cov.coverage_issues(huge, None)


def test_f1_deeply_nested_plain_lists_do_not_exhaust_the_recursion_limit():
    nested = []
    for _ in range(50000):
        nested = [nested]
    coverage = copy.deepcopy(valid_coverage())
    entry(coverage, 0)["statuses"] = [nested]
    issues = cov.coverage_issues(coverage, None)
    assert issues and "isochrone_coverage has an unusable structure" not in issues


F1_BAD_THRESHOLD_MAPS = {
    "tuple_map_value": {"major_port": tuple(PORT_T), "mmlp": MMLP_T},
    "tuple_of_pairs": (("major_port", PORT_T), ("mmlp", MMLP_T)),
    "numpy_float_values": {"major_port": [np.float64(v) for v in PORT_T], "mmlp": MMLP_T},
    "numpy_array_values": {"major_port": np.array(PORT_T), "mmlp": MMLP_T},
    "numpy_str_key": {np.str_("major_port"): PORT_T, "mmlp": MMLP_T},
    "int_key": {1: PORT_T, "major_port": PORT_T, "mmlp": MMLP_T},
    "int_values": {"major_port": [4, 8, 12, 24], "mmlp": MMLP_T},
    "bool_values": {"major_port": [True, 8.0, 12.0, 24.0], "mmlp": MMLP_T},
    "nonfinite_values": {"major_port": [4.0, float("nan")], "mmlp": MMLP_T},
    "dict_subclass": F1Dict({"major_port": PORT_T, "mmlp": MMLP_T}),
    "list_subclass_value": {"major_port": F1List(PORT_T), "mmlp": MMLP_T},
    "float_subclass_value": {"major_port": [type("F1Float", (float,), {})(4.0), 8.0, 12.0, 24.0], "mmlp": MMLP_T},
    "list_value_not_list": {"major_port": "4,8", "mmlp": MMLP_T},
    "extra_key_with_bad_value": {"major_port": PORT_T, "mmlp": MMLP_T, "extra": (1.0,)},
    "list_instead_of_object": [PORT_T, MMLP_T],
}


@pytest.mark.parametrize("name", sorted(F1_BAD_THRESHOLD_MAPS))
def test_f1_threshold_map_guard_returns_the_exact_fixed_issue(name):
    assert cov.coverage_issues(valid_coverage(), F1_BAD_THRESHOLD_MAPS[name]) == F1_THRESHOLDS_MESSAGE


def test_f1_threshold_map_valid_controls_including_extra_plain_key():
    assert cov.coverage_issues(valid_coverage(), {"major_port": PORT_T, "mmlp": MMLP_T}) == []
    assert cov.coverage_issues(valid_coverage(), {"major_port": PORT_T, "mmlp": MMLP_T, "extra": [1.0]}) == []
    assert cov.coverage_issues(valid_coverage(), {"major_port": PORT_T}) and cov.coverage_issues(valid_coverage(), {"major_port": PORT_T}) != F1_THRESHOLDS_MESSAGE


def test_f1_plain_check_precedes_the_threshold_check():
    bad = f1_apply(F1_NOT_PLAIN["np_str_snap_rule"])
    assert cov.coverage_issues(bad, F1_BAD_THRESHOLD_MAPS["tuple_map_value"]) == F1_PLAIN_MESSAGE


@pytest.mark.parametrize("name", ["np_str_snap_rule", "array_hub_type", "np_float64_feeder", "np_int64_count"])
def test_f1_builder_raises_value_error_not_type_error_for_numpy_coverage(tmp_path, name):
    import importlib.util
    from scripts.analyze import producer_limitations as lim
    spec = importlib.util.spec_from_file_location("f1_existing_limitation_helpers", Path(__file__).with_name("test_producer_limitations.py"))
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    world = helpers.V2World(tmp_path)  # Real synthetic sources and artifacts through the existing helper definitions.
    bad = F1_NOT_PLAIN[name]
    coverage = copy.deepcopy(helpers.v2_coverage())
    replaced = bad(coverage)
    coverage = coverage if replaced is None else replaced
    with pytest.raises(ValueError):
        lim.build_limitations(world.source, world.artifacts, world.run_id, "windows_peak_working_set", isochrone_coverage=coverage)


# --- A1: hub names are unique per hub type after strip().casefold(); literal names are never normalized ---------

A1_MESSAGE = "entries[{}]: hub_name duplicates an earlier hub of the same hub_type"


def a1_coverage(names, hub_type="major_port", *, start=0):
    template = copy.deepcopy(entry(valid_coverage(), 0))
    entries = []
    for offset, name in enumerate(names):
        item = copy.deepcopy(template)
        item.update(hub_type=hub_type, hub_name=name, hub_order=start + offset)
        entries.append(item)
    return entries


def a1_document(entries):
    return {"snap_rule": cov.SNAP_RULE, "entries": entries}


@pytest.mark.parametrize("names,duplicates", [
    (["A", "B", "A"], [2]),
    (["Port A", " port a "], [1]),
    (["Straße", "STRASSE"], [1]),
    (["A", "B", "a ", " B", "C"], [2, 3]),
    (["A", "A", "A"], [1, 2]),
])
def test_a1_same_type_duplicate_names_are_reported_by_index_without_echo(names, duplicates):
    issues = cov.coverage_issues(a1_document(a1_coverage(names)), None)
    assert issues == [A1_MESSAGE.format(i) for i in duplicates]  # Fixed text: no hub name is echoed.


def test_a1_duplicate_check_is_per_hub_type_and_cross_type_same_name_is_valid():
    entries = a1_coverage(["Same", "Other"]) + a1_coverage(["Same", "SAME"], "mmlp")
    assert cov.coverage_issues(a1_document(entries), None) == [A1_MESSAGE.format(3)]
    cross = a1_coverage(["Same"]) + a1_coverage(["Same"], "mmlp")
    assert cov.coverage_issues(a1_document(cross), None) == []


def test_a1_unique_names_stay_literal_and_valid():
    names = [" Port A ", "Port B", "PORT C"]
    coverage = a1_document(a1_coverage(names))
    assert cov.coverage_issues(coverage, None) == []
    assert [e["hub_name"] for e in coverage["entries"]] == names


def test_a1_blank_names_keep_existing_issue_and_skip_uniqueness():
    issues = cov.coverage_issues(a1_document(a1_coverage(["  ", "  ", ""])), None)
    assert issues == [f"entries[{i}]: hub_name must be a non-blank string" for i in range(3)]


def test_a1_unsupported_hub_type_reports_existing_issue_and_does_not_crash():
    entries = a1_coverage(["A", "A"], "icd")
    issues = cov.coverage_issues(a1_document(entries), None)
    assert issues and all(issue.startswith("entries[") for issue in issues)
    assert not any("duplicates an earlier hub" in issue for issue in issues)
    assert all("unsupported hub_type" in issue for issue in issues if "hub_type" in issue)


def test_a1_ordinal_rules_stay_independent_of_name_uniqueness():
    gap = a1_coverage(["A", "B"])
    gap[1]["hub_order"] = 2
    assert cov.coverage_issues(a1_document(gap), None) == ["entries[1]: hub_order must run 0..n-1 without gaps, duplicates or swaps"]
    both = a1_coverage(["A", "a"])
    both[1]["hub_order"] = 2
    assert cov.coverage_issues(a1_document(both), None) == [A1_MESSAGE.format(1),
                                                           "entries[1]: hub_order must run 0..n-1 without gaps, duplicates or swaps"]


def a1_artifacts(names, hub_type="major_port"):
    thresholds = PORT_T
    feature_rows, fact_rows = [], []
    for order, name in enumerate(names):
        feature_rows += features(name, hub_type, thresholds, ["ok"] * 4, [5, 20, 40, 40], 10.0)
        fact_rows.append(fact(hub_type, name, order, 10.0, 40))
    return {"type": "FeatureCollection", "features": feature_rows}, fact_rows


@pytest.mark.parametrize("names", [["A", "B", "A"], ["A", "A"], ["Port A", "B", " port a "]])
def test_a1_coverage_entries_rejects_duplicate_hubs_with_value_error(names):
    ports_doc, port_facts = a1_artifacts(names)
    with pytest.raises(ValueError):
        cov.coverage_entries(ports_doc, {"type": "FeatureCollection", "features": []}, port_facts, [])


def test_a1_coverage_entries_valid_distinct_names_control():
    ports_doc, port_facts = a1_artifacts(["A", "B", "C"])
    coverage = cov.coverage_entries(ports_doc, {"type": "FeatureCollection", "features": []}, port_facts, [])
    assert [e["hub_name"] for e in coverage["entries"]] == ["A", "B", "C"] and cov.coverage_issues(coverage, None) == []


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
