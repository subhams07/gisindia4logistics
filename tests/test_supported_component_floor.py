"""Supported-component snapping floor: two-sided controls for the coverage helper and audit oracle,
the oracle's literal expectation and the disclosure strings. No module-wide floor override here."""
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.sparse import csr_matrix
from scipy.spatial import KDTree

from scripts.analyze import generate_isochrones as iso
from scripts.analyze import highway_pair_kernel as kernel
from scripts.analyze import isochrone_coverage as cov
from scripts.analyze import producer_limitations as lim
from scripts.audit import producer_graph_oracle as oracle


def _graph():
    """Tiny island (2 nodes, 5 km east of the hub) and a 4-node mainland line (40 km east)."""
    xy = np.array([[5000., 0.], [6000., 0.], [40000., 0.], [41000., 0.], [42000., 0.], [43000., 0.]])
    edges = [(0, 1), (2, 3), (3, 4), (4, 5)]
    rows = [a for a, b in edges] + [b for a, b in edges]
    cols = [b for a, b in edges] + [a for a, b in edges]
    matrix = csr_matrix((np.full(len(rows), 1 / 70.), (rows, cols)), shape=(6, 6))
    labels = np.array([0, 0, 1, 1, 1, 1])
    return SimpleNamespace(nh_graph=matrix, nh_distance_graph=matrix * 70., nh_node_xy=xy, nh_comp_labels=labels,
                           nh_tree=KDTree(xy), nh_bridge_mask=csr_matrix((6, 6)))


def test_oracle_literal_matches_kernel_constant():
    assert oracle.ORACLE_MIN_SUPPORTED_COMPONENT_NODES == kernel.MIN_SUPPORTED_COMPONENT_NODES == 1000


def test_component_facts_skip_fragment_two_sided(monkeypatch):
    graph, rows = _graph(), [{"name": "Hub", "longitude": 0.0, "latitude": 0.0}]
    monkeypatch.setattr(cov, "hub_point", lambda row: np.array([0., 0.]))
    monkeypatch.setattr(kernel, "MIN_SUPPORTED_COMPONENT_NODES", 3)
    fact = cov.component_facts(graph, rows, "mmlp")[0]
    assert fact["snap_component_node_count"] == 4 and fact["snap_feeder_km"] == pytest.approx(40.0)
    monkeypatch.setattr(kernel, "MIN_SUPPORTED_COMPONENT_NODES", 1)
    fact = cov.component_facts(graph, rows, "mmlp")[0]
    assert fact["snap_component_node_count"] == 2 and fact["snap_feeder_km"] == pytest.approx(5.0)
    monkeypatch.setattr(kernel, "MIN_SUPPORTED_COMPONENT_NODES", 5)
    fact = cov.component_facts(graph, rows, "mmlp")[0]
    assert fact["snap_feeder_km"] is None and fact["snap_component_node_count"] is None


def test_oracle_iso_expectations_skip_fragment_two_sided(monkeypatch):
    graph = _graph()
    monkeypatch.setattr(oracle, "ORACLE_MIN_SUPPORTED_COMPONENT_NODES", 3)
    far = oracle.iso_expectations(graph, np.array([0., 0.]), [24.])[0]
    assert far["snap_feeder_km"] == pytest.approx(40.0) and far["reachable_node_count"] == 4 and far["status"] == "ok"
    monkeypatch.setattr(oracle, "ORACLE_MIN_SUPPORTED_COMPONENT_NODES", 1)
    near = oracle.iso_expectations(graph, np.array([0., 0.]), [24.])[0]
    assert near["snap_feeder_km"] == pytest.approx(5.0) and near["reachable_node_count"] == 2
    assert near["status"] == "unsupported_threshold"
    monkeypatch.setattr(oracle, "ORACLE_MIN_SUPPORTED_COMPONENT_NODES", 5)
    assert oracle.iso_expectations(graph, np.array([0., 0.]), [24.])[0]["status"] == "no_highway_within_150km"


def test_disclosure_strings_carry_the_floor():
    value = str(kernel.MIN_SUPPORTED_COMPONENT_NODES)
    for text in (cov.SNAP_RULE, lim.COVERAGE_LIMITATION, iso.COVERAGE_ASSUMPTIONS):
        assert value in text
