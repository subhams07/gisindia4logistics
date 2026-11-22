"""Synthetic routing helpers shared by the routing tests (not a test module)."""
from types import SimpleNamespace

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import KDTree

from scripts.clean.graph_builder import edge_lengths_km, geodesic_m
from server.models.phase1 import ResolvedLocation


# Modelled toll rate per plaza (INR) by vehicle class, as priced by
# server/services/routing_facade.py. Tests compare against this one table.
TOLL_RATES = {"LMV": 120.0, "LCV": 195.0, "2_AXLE_TRUCK": 280.0, "MAV_20T": 340.0, "7_AXLE_OVERSIZED": 410.0}


def toll_rate(vehicle) -> float:
    return TOLL_RATES[getattr(vehicle, "value", vehicle)]


def location(service, xy):
    lon, lat = service.to_wgs84.transform(*xy)
    return ResolvedLocation(type="coordinate", canonical_name="synthetic", latitude=lat,
                            longitude=lon, source_dataset="test", match_method="coordinate")


def gkm(a, b):
    """WGS84 geodesic km between two EPSG:7755 points, as routing measures feeders."""
    return float(geodesic_m(np.asarray(a, dtype=float), np.asarray(b, dtype=float))) / 1000


def graph(xy, edges, bridges=()):
    xy = np.asarray(xy, dtype=float)
    rows, cols, distances = [], [], []
    for u, v in edges:
        for a, b in [(u, v), (v, u)]:
            rows.append(a)
            cols.append(b)
            distances.append(float(edge_lengths_km(xy, [u], [v])[0]))  # geodesic, as the builder measures
    dist = csr_matrix((distances, (rows, cols)), shape=(len(xy), len(xy)))
    time = dist / 70
    br = [(a, b) for u, v in bridges for a, b in [(u, v), (v, u)]]
    mask = csr_matrix(([1] * len(br), ([a for a, b in br], [b for a, b in br])), shape=time.shape)
    return SimpleNamespace(nh_graph=time, nh_distance_graph=dist, nh_bridge_mask=mask,
                           nh_node_xy=xy, nh_tree=KDTree(xy),
                           nh_comp_labels=connected_components(time, directed=False)[1], main_comp_label=999)
