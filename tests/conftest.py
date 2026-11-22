"""Shared pytest fixtures."""
import numpy as np
import pytest


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
