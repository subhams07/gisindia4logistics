"""Validated, detached graph generations for coherent routing requests.

Immutable bytes own array storage (not an unlockable NumPy writeable flag).
CSR attribute replacement and tree coordinate replacement are also prohibited.
This protects supported Python/NumPy/SciPy APIs, not deliberate private-state or
native-memory tampering. Mutable raw stores are always fully validated per call.
"""
from types import SimpleNamespace

import numpy as np
from scipy.sparse import csr_matrix, isspmatrix_csr
from scipy.spatial import KDTree

from scripts.clean import graph_builder
from server.services.exceptions import DataIntegrityError

GRAPH_FIELDS = ('nh_graph', 'nh_distance_graph', 'nh_node_xy',
                'nh_comp_labels', 'nh_tree', 'nh_bridge_mask')


class _FrozenArray(np.ndarray):
    """Also lock shape/dtype metadata, not just the bytes backing values."""

    def __setattr__(self, name, value):
        raise AttributeError('Published highway array is immutable')

    def resize(self, *args, **kwargs):
        raise TypeError('Published highway array is immutable')


def _immutable_array(value):
    array = np.asarray(value)
    return np.frombuffer(array.tobytes(order='C'), dtype=array.dtype).reshape(array.shape).view(_FrozenArray)


class _FrozenCSR(csr_matrix):
    """Read-compatible CSR with bytes-backed buffers and locked structural fields."""

    def __new__(cls, *args, **kwargs):
        # SciPy constructs results/operands via self.__class__, then prunes or
        # eliminates zeros. Only the explicit publication factory freezes CSR;
        # ordinary construction yields independent, mutable base-class buffers.
        kwargs['copy'] = True
        return csr_matrix(*args, **kwargs)

    @classmethod
    def published(cls, value):
        self = super().__new__(cls)
        csr_matrix.__init__(self, value, copy=True)
        # Populate SciPy's lazy format caches before locking attribute writes.
        _ = self.has_sorted_indices, self.has_canonical_format
        self.data = _immutable_array(self.data)
        self.indices = _immutable_array(self.indices)
        self.indptr = _immutable_array(self.indptr)
        self._locked = True
        return self

    def check_format(self, full_check=True):
        # SciPy's validator prunes/replaces buffers even for a valid CSR. Run
        # those checks on a detached ordinary matrix, never on the publication.
        if getattr(self, '_locked', False):
            return csr_matrix(self, copy=True).check_format(full_check=full_check)
        return super().check_format(full_check=full_check)

    def __setattr__(self, name, value):
        if getattr(self, '_locked', False):
            raise AttributeError('Published highway CSR is immutable')
        super().__setattr__(name, value)

    def __setitem__(self, key, value):
        raise TypeError('Published highway CSR is immutable')

    def __delattr__(self, name):
        raise AttributeError('Published highway CSR is immutable')

    def setdiag(self, values, k=0):
        raise TypeError('Published highway CSR is immutable')


class _FrozenTree:
    """Expose spatial queries without exposing mutable KDTree internals."""
    __slots__ = ('__coordinates', '__tree')

    def __init__(self, coordinates):
        coordinates = _immutable_array(coordinates)
        object.__setattr__(self, '_FrozenTree__coordinates', coordinates)
        object.__setattr__(self, '_FrozenTree__tree', KDTree(coordinates))

    def __setattr__(self, name, value):
        raise AttributeError('Published highway tree is immutable')

    def __delattr__(self, name):
        raise AttributeError('Published highway tree is immutable')

    @property
    def data(self):
        return self.__coordinates

    @property
    def n(self):
        return self.__tree.n

    def query(self, *args, **kwargs):
        return self.__tree.query(*args, **kwargs)

    def query_ball_point(self, *args, **kwargs):
        return self.__tree.query_ball_point(*args, **kwargs)


def _validate(values):
    try:
        graph_builder.validate_graph_metadata(*values)
    except (ValueError, AttributeError, TypeError, IndexError) as exc:
        # Do not echo source exceptions: injected or upstream errors can carry paths.
        raise DataIntegrityError('Invalid highway graph metadata') from exc


class GraphSnapshot:
    """Explicit trusted snapshot. Construction always validates its detached data.

    Fields retain legacy nh_* names. generation is a monotonically increasing
    publication identifier within a DataStore, not a data/model version.
    """
    __slots__ = (*GRAPH_FIELDS, 'generation', '_buffers', '_layouts')

    def __init__(self, graph_time, graph_dist, coords, labels, tree, bridge_mask,
                 generation=0):
        try:
            if (not all(isspmatrix_csr(matrix) for matrix in (graph_time, graph_dist, bridge_mask))
                    or tree.n != len(coords)):
                raise ValueError('Invalid graph representation')
            values = (csr_matrix(graph_time, copy=True), csr_matrix(graph_dist, copy=True),
                      _immutable_array(coords), _immutable_array(labels),
                      _FrozenTree(tree.data), csr_matrix(bridge_mask, copy=True))
        except (ValueError, AttributeError, TypeError, IndexError) as exc:
            raise DataIntegrityError('Invalid highway graph metadata') from exc
        _validate(values)
        values = (_FrozenCSR.published(values[0]), _FrozenCSR.published(values[1]), values[2],
                  values[3], values[4], _FrozenCSR.published(values[5]))
        for name, value in zip(GRAPH_FIELDS, values):
            object.__setattr__(self, name, value)
        object.__setattr__(self, 'generation', generation)
        buffers = self._current_buffers()
        object.__setattr__(self, '_buffers', buffers)
        object.__setattr__(self, '_layouts', tuple((a.shape, a.dtype) for a in buffers))

    def _current_buffers(self):
        return tuple(a for matrix in (self.nh_graph, self.nh_distance_graph, self.nh_bridge_mask)
                     for a in (matrix.data, matrix.indices, matrix.indptr)) + (
                         self.nh_node_xy, self.nh_comp_labels, self.nh_tree.data)

    def check_integrity(self):
        """Constant-size defensive checks, never a scan over graph contents."""
        try:
            buffers = self._current_buffers()
            n = len(self.nh_node_xy)
            valid = (all(a is b and (a.shape, a.dtype) == layout and not a.flags.writeable
                         for a, b, layout in zip(buffers, self._buffers, self._layouts))
                     and all(matrix.shape == (n, n) for matrix in
                             (self.nh_graph, self.nh_distance_graph, self.nh_bridge_mask))
                     and self.nh_tree.n == n)
        except (AttributeError, TypeError, ValueError) as exc:
            raise DataIntegrityError('Invalid highway graph metadata: altered snapshot') from exc
        if not valid:
            raise DataIntegrityError('Invalid highway graph metadata: altered snapshot')

    def __setattr__(self, name, value):
        raise AttributeError('Published highway snapshot is immutable')

    def __delattr__(self, name):
        raise AttributeError('Published highway snapshot is immutable')


def graph_for_request(store):
    """Capture one generation; do not trust identity caches or boolean flags."""
    # Import lazily: dependencies publishes snapshots but must not import services
    # during module initialization (services.__init__ also imports routing).
    from server.dependencies import DataStore

    if isinstance(store, DataStore):
        return store.get_highway_snapshot()
    if isinstance(store, GraphSnapshot):
        store.check_integrity()
        return store
    try:
        values = tuple(getattr(store, name) for name in GRAPH_FIELDS)
    except AttributeError as exc:
        raise DataIntegrityError('Invalid highway graph metadata') from exc
    _validate(values)
    # Capture each raw field once rather than rereading the store mid-request.
    # Raw fixtures must not be concurrently mutated; production uses snapshots.
    return SimpleNamespace(**dict(zip(GRAPH_FIELDS, values)))
