"""Graph publication, immutability, coherence and domain failure regressions."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from server.dependencies import DataStore
from server.services.exceptions import GISIndia4LogisticsError, RouteNotAvailableError
from server.services.routing_service import RoutingService
from tests.test_routing_edge_cases import graph, location


def fixture_graph():
    service = RoutingService()
    base = np.array(service.to_proj.transform(78, 22))
    raw = graph([base, base + (1000, 0)], [(0, 1)])
    return service, raw, location(service, base), location(service, base + (1000, 0))


def publish(store, raw):
    return store.publish_highway_graph(raw.nh_graph, raw.nh_distance_graph,
                                       raw.nh_node_xy, raw.nh_comp_labels,
                                       raw.nh_tree, raw.nh_bridge_mask)


@pytest.mark.parametrize('operation', ['comparison', 'multiply', 'addition', 'validate'])
@pytest.mark.parametrize('with_bridge', [False, True])
def test_published_csr_read_operations_match_raw_without_source_changes(operation, with_bridge):
    from scipy.sparse import csr_matrix

    from scripts.clean.graph_builder import validate_graph_metadata
    from server.services.graph_snapshot import GRAPH_FIELDS

    _, raw, _, _ = fixture_graph()
    if with_bridge:
        base = raw.nh_node_xy[0]
        raw = graph([base, base + (100, 0)], [(0, 1)], bridges=[(0, 1)])
    store = DataStore()
    snapshot = publish(store, raw)
    matrices = (snapshot.nh_graph, snapshot.nh_distance_graph, snapshot.nh_bridge_mask)
    metadata = [matrix.__dict__.copy() for matrix in matrices]
    buffers = snapshot._current_buffers()
    contents = [np.asarray(buffer).copy() for buffer in buffers]

    def read(source):
        if operation == 'comparison':
            return source.nh_graph != source.nh_graph.T
        if operation == 'multiply':
            return source.nh_bridge_mask.multiply(source.nh_graph != 0)
        if operation == 'addition':
            return source.nh_graph + source.nh_graph
        return validate_graph_metadata(*(getattr(source, field) for field in GRAPH_FIELDS))

    expected = read(raw)
    result = read(snapshot)
    if operation == 'validate':
        assert result == expected
    else:
        assert type(result) is csr_matrix
        np.testing.assert_array_equal(result.toarray(), expected.toarray())
        for buffer in (result.data, result.indices, result.indptr):
            assert buffer.flags.writeable
            assert not any(np.shares_memory(buffer, original) for original in buffers)
        # Even an empty comparison/mask result must accept structural mutation.
        from scipy.sparse import SparseEfficiencyWarning

        with pytest.warns(SparseEfficiencyWarning):
            result[0, 0] = 1
        result.data[:] = 0

    for matrix, original in zip(matrices, metadata):
        assert matrix.__dict__.keys() == original.keys()
        assert all(matrix.__dict__[key] is value for key, value in original.items())
        for field in ('data', 'indices', 'indptr', 'shape', 'dtype', 'has_sorted_indices'):
            with pytest.raises((AttributeError, TypeError, ValueError)):
                setattr(matrix, field, getattr(matrix, field))
        with pytest.raises(AttributeError):
            del matrix.data
    for buffer, original, content in zip(snapshot._current_buffers(), buffers, contents):
        assert buffer is original
        np.testing.assert_array_equal(buffer, content)
        assert not buffer.flags.writeable
        with pytest.raises(ValueError):
            buffer.flags.writeable = True
        with pytest.raises(AttributeError):
            buffer.shape = buffer.shape
        with pytest.raises(AttributeError):
            buffer.dtype = buffer.dtype
    with pytest.raises(AttributeError):
        snapshot.nh_tree.data = raw.nh_node_xy
    with pytest.raises(AttributeError):
        del snapshot.nh_tree.data
    assert store.get_highway_snapshot() is snapshot


def test_publication_accepts_large_component_labels_without_dense_allocation(monkeypatch):
    _, raw, _, _ = fixture_graph()
    raw.nh_comp_labels = np.full(2, 2**40, dtype=np.int64)

    def no_bincount(*args, **kwargs):
        pytest.fail('Publication must not allocate by maximum component label')

    monkeypatch.setattr(np, 'bincount', no_bincount)
    store = DataStore()
    snapshot = publish(store, raw)
    assert np.unique(snapshot.nh_comp_labels).size == 1
    assert store.main_comp_label == 2**40
    assert store.get_highway_snapshot() is snapshot


def test_main_component_count_ties_choose_smallest_label():
    _, raw, _, _ = fixture_graph()
    base = raw.nh_node_xy[0]
    raw = graph([base, base + (1000, 0), base + (3000, 0), base + (4000, 0)],
                [(0, 1), (2, 3)])
    raw.nh_comp_labels = np.array([9, 9, 3, 3])
    store = DataStore()
    snapshot = publish(store, raw)
    assert np.unique(snapshot.nh_comp_labels).size == 2
    assert store.main_comp_label == 3


def test_publication_and_warm_requests_validate_once():
    from scripts.clean.graph_builder import validate_graph_metadata

    service, raw, origin, destination = fixture_graph()
    store = DataStore()
    with patch('scripts.clean.graph_builder.validate_graph_metadata', wraps=validate_graph_metadata) as validation:
        snapshot = publish(store, raw)
        assert validation.call_count == 1
        for _ in range(3):
            assert service.calculate_highway_route(origin, destination, store=store).network_distance_km == 1
        assert validation.call_count == 1
        assert store.get_highway_snapshot() is snapshot
        assert publish(store, raw).generation == snapshot.generation + 1
        assert validation.call_count == 2


def test_integrity_error_is_distinct_and_safe():
    service, raw, origin, destination = fixture_graph()
    raw.nh_comp_labels[:] = -1
    with pytest.raises(GISIndia4LogisticsError) as error:
        service.calculate_highway_route(origin, destination, store=raw)
    assert type(error.value).__name__ == 'DataIntegrityError'
    assert not isinstance(error.value, RouteNotAvailableError)
    assert 'C:' not in str(error.value)


@pytest.mark.parametrize('failure', ['unreachable', 'broken', 'bad_index', 'cycle', 'bad_edge'])
def test_injected_path_integrity_errors(failure, monkeypatch):
    from server.services.exceptions import DataIntegrityError

    service, raw, origin, destination = fixture_graph()
    if failure == 'bad_edge':
        raw.nh_distance_graph.data[:] = 0
        with pytest.raises(DataIntegrityError):
            service._analyze_path_edges([0, 1], raw)
        return
    distances = np.array([0., np.inf if failure == 'unreachable' else 1.])
    predecessor = {'broken': -9999, 'bad_index': 99, 'cycle': 1}.get(failure, 0)
    monkeypatch.setattr('server.services.routing_service.dijkstra',
                        lambda *a, **k: (distances, np.array([-9999, predecessor])))
    with pytest.raises(DataIntegrityError):
        service.calculate_highway_route(origin, destination, store=raw)


@pytest.mark.parametrize('field', ['nh_graph', 'nh_distance_graph', 'nh_bridge_mask',
                                   'nh_node_xy', 'nh_comp_labels', 'nh_tree'])
def test_partial_replacement_requires_explicit_validated_publication(field):
    from server.services.exceptions import DataIntegrityError

    service, raw, origin, destination = fixture_graph()
    store = DataStore()
    old = publish(store, raw)
    setattr(store, field, getattr(raw, field))
    with pytest.raises(DataIntegrityError):
        service.calculate_highway_route(origin, destination, store=store)
    raw.nh_comp_labels[:] = -1
    with pytest.raises(DataIntegrityError):
        publish(store, raw)
    # Invalid publication never replaces the last valid generation.
    assert store._highway_snapshot is old
    raw.nh_comp_labels[:] = 0
    new = publish(store, raw)
    assert new.generation == old.generation + 1
    assert service.calculate_highway_route(origin, destination, store=store).network_distance_km == 1


@pytest.mark.parametrize('mutation', ['array', 'flags', 'csr_item', 'csr_method',
                                      'buffer_swap', 'indices_swap', 'tree_content', 'tree_replace',
                                      'array_shape', 'array_dtype', 'csr_flags', 'tree_shape',
                                      'array_resize', 'array_fill', 'csr_eliminate'])
def test_published_graph_cannot_be_mutated(mutation):
    service, raw, origin, destination = fixture_graph()
    store = DataStore()
    snapshot = publish(store, raw)
    with pytest.raises((ValueError, AttributeError, TypeError)):
        if mutation == 'array':
            snapshot.nh_comp_labels[0] = -1
        elif mutation == 'flags':
            snapshot.nh_node_xy.flags.writeable = True
        elif mutation == 'csr_item':
            snapshot.nh_graph[0, 1] = 99
        elif mutation == 'csr_method':
            snapshot.nh_graph.setdiag(99)
        elif mutation == 'buffer_swap':
            snapshot.nh_graph.data = np.ones(2)
        elif mutation == 'indices_swap':
            snapshot.nh_graph.indices = np.zeros(2, dtype=int)
        elif mutation == 'tree_content':
            snapshot.nh_tree.data.flags.writeable = True
        elif mutation == 'array_shape':
            snapshot.nh_node_xy.shape = (4,)
        elif mutation == 'array_dtype':
            snapshot.nh_comp_labels.dtype = np.float32
        elif mutation == 'csr_flags':
            snapshot.nh_graph.data.flags.writeable = True
        elif mutation == 'tree_shape':
            snapshot.nh_tree.data.shape = (4,)
        elif mutation == 'array_resize':
            snapshot.nh_node_xy.resize((4,))
        elif mutation == 'array_fill':
            snapshot.nh_node_xy.fill(0)
        elif mutation == 'csr_eliminate':
            snapshot.nh_graph.eliminate_zeros()
        else:
            snapshot.nh_tree.data = raw.nh_node_xy + 1
    assert service.calculate_highway_route(origin, destination, store=store).network_distance_km == 1
    # Original mutable builder buffers are detached from the publication.
    raw.nh_node_xy[:] += 123
    raw.nh_graph.data[:] = 99
    assert service.calculate_highway_route(origin, destination, store=store).network_distance_km == 1


def test_raw_stores_revalidate_each_call():
    from scripts.clean.graph_builder import validate_graph_metadata
    from server.services.exceptions import DataIntegrityError

    service, raw, origin, destination = fixture_graph()
    with patch('scripts.clean.graph_builder.validate_graph_metadata', wraps=validate_graph_metadata) as validation:
        service.calculate_highway_route(origin, destination, store=raw)
        service.calculate_highway_route(origin, destination, store=raw)
        raw.nh_tree.data[0, 0] += 1
        with pytest.raises(DataIntegrityError):
            service.calculate_highway_route(origin, destination, store=raw)
        assert validation.call_count == 3


def test_reload_during_request_keeps_one_generation(monkeypatch):
    service, raw, origin, destination = fixture_graph()
    store = DataStore()
    old = publish(store, raw)
    replacement = graph([raw.nh_node_xy[0], raw.nh_node_xy[0] + (500, 1000),
                         raw.nh_node_xy[1]], [(0, 1), (1, 2)])
    original = service._radius_candidates

    def reload(point, snapshot):
        publish(store, replacement)
        return original(point, snapshot)

    monkeypatch.setattr(service, '_radius_candidates', reload)
    result, path = service.calculate_highway_route_with_path(origin, destination, store=store)
    assert result.network_distance_km == 1
    assert path.node_ids == (0, 1)
    assert store.get_highway_snapshot().generation > old.generation


def test_parallel_publications_and_requests_are_coherent():
    service, raw, origin, destination = fixture_graph()
    store = DataStore()
    publish(store, raw)
    other = graph([raw.nh_node_xy[0], raw.nh_node_xy[0] + (500, 1000),
                   raw.nh_node_xy[1]], [(0, 1), (1, 2)])

    def work(i):
        if i % 3 == 0:
            publish(store, raw if i % 2 else other)
        result, path = service.calculate_highway_route_with_path(origin, destination, store=store)
        return result.network_distance_km, path.node_ids

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert set(pool.map(work, range(30))) <= {(1., (0, 1)), (2.24, (0, 1, 2))}


def test_unchecked_trusted_flag_does_not_skip_validation():
    from server.services.exceptions import DataIntegrityError

    service, raw, origin, destination = fixture_graph()
    raw.graph_validated = True
    raw._highway_snapshot = SimpleNamespace(validated=True)
    raw.nh_comp_labels[:] = -1
    with pytest.raises(DataIntegrityError):
        service.calculate_highway_route(origin, destination, store=raw)


@pytest.mark.parametrize('field', ['data', 'indices', 'indptr'])
def test_cheap_checks_detect_forced_csr_buffer_replacement(field):
    from server.services.exceptions import DataIntegrityError

    service, raw, origin, destination = fixture_graph()
    store = DataStore()
    snapshot = publish(store, raw)
    # Bypass normal assignment prohibition to exercise defensive request checks.
    object.__setattr__(snapshot.nh_graph, field, getattr(raw.nh_graph, field).copy())
    with pytest.raises(DataIntegrityError, match='altered snapshot'):
        service.calculate_highway_route(origin, destination, store=store)


def test_explicit_snapshot_is_trusted_only_after_validated_construction():
    from scripts.clean.graph_builder import validate_graph_metadata
    from server.services.graph_snapshot import GraphSnapshot

    service, raw, origin, destination = fixture_graph()
    with patch('scripts.clean.graph_builder.validate_graph_metadata', wraps=validate_graph_metadata) as validation:
        snapshot = GraphSnapshot(raw.nh_graph, raw.nh_distance_graph, raw.nh_node_xy,
                                 raw.nh_comp_labels, raw.nh_tree, raw.nh_bridge_mask)
        service.calculate_highway_route(origin, destination, store=snapshot)
        assert validation.call_count == 1


def test_injected_nonedge_predecessor_path_is_integrity_failure(monkeypatch):
    from server.services.exceptions import DataIntegrityError

    service, raw, origin, _ = fixture_graph()
    base = raw.nh_node_xy[0]
    raw = graph([base, base + (1000, 0), base + (2000, 0)], [(0, 1), (1, 2)])
    destination = location(service, raw.nh_node_xy[2])
    monkeypatch.setattr('server.services.routing_service.dijkstra',
                        lambda *a, **k: (np.array([0., 1., 2.]), np.array([-9999, 0, 0])))
    with pytest.raises(DataIntegrityError, match='edge distance'):
        service.calculate_highway_route(origin, destination, store=raw)


def test_source_error_message_does_not_leak_paths(monkeypatch):
    from server.services.exceptions import DataIntegrityError

    service, raw, origin, destination = fixture_graph()

    def invalid(*args):
        raise ValueError('C:/private/source/graph.npz is corrupt')

    monkeypatch.setattr('scripts.clean.graph_builder.validate_graph_metadata', invalid)
    with pytest.raises(DataIntegrityError) as error:
        service.calculate_highway_route(origin, destination, store=raw)
    assert str(error.value) == 'Invalid highway graph metadata'
