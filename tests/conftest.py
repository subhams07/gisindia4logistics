"""Shared pytest fixtures and constants."""
import numpy as np
import pytest


@pytest.fixture(scope="module")
def tiny_component_floor():
    """Synthetic fixtures are far below the production component-size floor, so modules that
    use this run with the floor at 1; supported-component snapping is covered in
    tests/test_isochrone_producer.py and tests/test_supported_component_floor.py."""
    from scripts.analyze import highway_pair_kernel as _kernel
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_kernel, "MIN_SUPPORTED_COMPONENT_NODES", 1)
        from scripts.audit import producer_graph_oracle as _oracle
        patch.setattr(_oracle, "ORACLE_MIN_SUPPORTED_COMPONENT_NODES", 1)
        yield


SHARED_GRAPH_CACHE_ENV = "GIS4L_TEST_SHARED_GRAPH_CACHE"


@pytest.fixture(scope="session", autouse=True)
def shared_graph_cache(tmp_path_factory):
    """One highway-graph cache for every subprocess that routes on the committed data.

    The cache is validated against the source fingerprint on load, so sharing it is
    safe; it saves a cold national graph build per CLI/MCP subprocess.
    """
    import os
    path = tmp_path_factory.mktemp("shared-graph-cache")
    previous = os.environ.get(SHARED_GRAPH_CACHE_ENV)
    os.environ[SHARED_GRAPH_CACHE_ENV] = str(path)
    yield path
    if previous is None:
        os.environ.pop(SHARED_GRAPH_CACHE_ENV, None)
    else:
        os.environ[SHARED_GRAPH_CACHE_ENV] = previous


@pytest.fixture
def planar(monkeypatch):
    """Planar metre metric in place of WGS84 geodesic distance.

    For arithmetic, tie-break and threshold fixtures laid out near the EPSG:7755
    false origin (~14S, 49E), where the projection's scale is ~1.2: their
    hand-picked metre offsets then mean what they say. Geodesic measurement is
    tested on Indian coordinates elsewhere.
    """
    from scripts.clean import graph_builder

    def norm(a, b):
        a, b = np.broadcast_arrays(np.asarray(a, dtype=float), np.asarray(b, dtype=float))
        return np.linalg.norm(a - b, axis=-1)

    monkeypatch.setattr(graph_builder, "geodesic_m", norm)
