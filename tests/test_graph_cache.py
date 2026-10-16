"""
tests/test_graph_cache.py
Regression and security test suite for canonical highway graph caching and fingerprint invalidation.
Uses fast synthetic GeoDataFrames to test graph build, caching, pickle safety, and invalidation in milliseconds.
"""

import json
from pathlib import Path
import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString

from scripts.clean.graph_builder import (
    load_or_build_cached_graph,
    compute_graph_fingerprint,
    CACHE_VERSION,
)


@pytest.fixture
def sample_nh_gdf() -> gpd.GeoDataFrame:
    """Creates a tiny synthetic National Highway GeoDataFrame in EPSG:7755."""
    lines = [
        LineString([(1000.0, 2000.0), (3000.0, 4000.0)]),
        LineString([(3000.0, 4000.0), (5000.0, 6000.0)]),
        LineString([(5000.0, 6000.0), (7000.0, 8000.0)]),
    ]
    return gpd.GeoDataFrame(
        {
            "highway": ["trunk", "motorway", "primary"],
            "ref": ["NH48", "NH48", "NH65"],
            "osm_id": [101, 102, 103],
            "geometry": lines,
        },
        crs=7755,
    )


def test_unchanged_graph_loads_cached_result(tmp_path: Path, sample_nh_gdf: gpd.GeoDataFrame):
    """Verifies that an unchanged graph builds on first call and loads from cache on second call."""
    cache_file = tmp_path / "canonical_nh_graph.npz"
    assert not cache_file.exists()

    # 1. Cold build
    gt1, gd1, coords1, labels1, tree1, bm1 = load_or_build_cached_graph(sample_nh_gdf, cache_dir=tmp_path)
    assert cache_file.exists()
    assert gt1.shape[0] > 0
    assert bm1.shape == gt1.shape

    # 2. Warm load
    gt2, gd2, coords2, labels2, tree2, bm2 = load_or_build_cached_graph(sample_nh_gdf, cache_dir=tmp_path)
    assert gt2.shape == gt1.shape
    assert gt2.nnz == gt1.nnz
    assert np.array_equal(coords1, coords2)
    assert np.array_equal(labels1, labels2)
    assert bm2.nnz == bm1.nnz


def test_cache_metadata_is_pickle_free(tmp_path: Path, sample_nh_gdf: gpd.GeoDataFrame):
    """Verifies that the cache file can be read securely with allow_pickle=False."""
    load_or_build_cached_graph(sample_nh_gdf, cache_dir=tmp_path)
    cache_file = tmp_path / "canonical_nh_graph.npz"

    with np.load(cache_file, allow_pickle=False) as data:
        assert "metadata" in data
        meta_json = str(data["metadata"][0])
        meta = json.loads(meta_json)
        assert meta["cache_version"] == CACHE_VERSION
        assert meta["row_count"] == 3
        assert "source_content_sha256" in meta
        assert len(meta["source_content_sha256"]) == 64


def test_geometry_change_invalidates_cache(tmp_path: Path, sample_nh_gdf: gpd.GeoDataFrame):
    """Verifies that modifying geometry changes fingerprint and invalidates stale cache."""
    # Build baseline
    load_or_build_cached_graph(sample_nh_gdf, cache_dir=tmp_path)
    base_fp = compute_graph_fingerprint(sample_nh_gdf)

    # Modify single coordinate
    modified_gdf = sample_nh_gdf.copy()
    modified_gdf.loc[0, "geometry"] = LineString([(1000.0, 2000.0), (3500.0, 4500.0)])
    mod_fp = compute_graph_fingerprint(modified_gdf)

    assert base_fp["source_content_sha256"] != mod_fp["source_content_sha256"]

    # Rebuild should update cache with new fingerprint
    load_or_build_cached_graph(modified_gdf, cache_dir=tmp_path)
    cache_file = tmp_path / "canonical_nh_graph.npz"

    with np.load(cache_file, allow_pickle=False) as data:
        meta = json.loads(str(data["metadata"][0]))
        assert meta["source_content_sha256"] == mod_fp["source_content_sha256"]


def test_highway_class_change_invalidates_cache(tmp_path: Path, sample_nh_gdf: gpd.GeoDataFrame):
    """Verifies that modifying highway classification changes fingerprint and invalidates stale cache."""
    load_or_build_cached_graph(sample_nh_gdf, cache_dir=tmp_path)
    base_fp = compute_graph_fingerprint(sample_nh_gdf)

    modified_gdf = sample_nh_gdf.copy()
    modified_gdf.loc[0, "highway"] = "motorway"
    mod_fp = compute_graph_fingerprint(modified_gdf)

    assert base_fp["source_content_sha256"] != mod_fp["source_content_sha256"]


def test_cache_version_change_invalidates_cache(tmp_path: Path, sample_nh_gdf: gpd.GeoDataFrame):
    """Verifies that mismatched cache version metadata triggers rebuild."""
    load_or_build_cached_graph(sample_nh_gdf, cache_dir=tmp_path)
    cache_file = tmp_path / "canonical_nh_graph.npz"

    # Corrupt metadata version
    with np.load(cache_file, allow_pickle=False) as data:
        meta = json.loads(str(data["metadata"][0]))
        meta["cache_version"] = 999
        np.savez_compressed(
            cache_file,
            N=data["N"],
            time_data=data["time_data"],
            time_indices=data["time_indices"],
            time_indptr=data["time_indptr"],
            dist_data=data["dist_data"],
            dist_indices=data["dist_indices"],
            dist_indptr=data["dist_indptr"],
            bridge_data=data["bridge_data"],
            bridge_indices=data["bridge_indices"],
            bridge_indptr=data["bridge_indptr"],
            coords_arr=data["coords_arr"],
            labels=data["labels"],
            metadata=np.array([json.dumps(meta)], dtype=np.str_),
        )

    # Calling load should detect mismatch and rebuild with current CACHE_VERSION
    load_or_build_cached_graph(sample_nh_gdf, cache_dir=tmp_path)
    with np.load(cache_file, allow_pickle=False) as data:
        meta_rebuilt = json.loads(str(data["metadata"][0]))
        assert meta_rebuilt["cache_version"] == CACHE_VERSION


def test_corrupted_cache_rebuilds_safely(tmp_path: Path, sample_nh_gdf: gpd.GeoDataFrame):
    """Verifies that corrupted cache files do not crash the engine and rebuild safely."""
    cache_file = tmp_path / "canonical_nh_graph.npz"
    cache_file.write_bytes(b"corrupted binary noise")

    # Should log warning and rebuild cleanly
    gt, gd, coords, labels, tree, bm = load_or_build_cached_graph(sample_nh_gdf, cache_dir=tmp_path)
    assert gt.shape[0] > 0
    assert bm.shape == gt.shape
    assert cache_file.stat().st_size > 100


def test_exact_bridge_sparse_mask_and_warm_route(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import scripts.clean.graph_builder as builder
    from server.models.phase1 import ResolvedLocation
    from server.services.routing_service import RoutingService

    service = RoutingService()
    base = np.round(np.array(service.to_proj.transform(78, 22)) / 15) * 15
    xy = [base, base + (990, 0), base + (1290, 0), base + (2280, 0)]
    gdf = gpd.GeoDataFrame({"highway": ["trunk", "trunk"],
                           "geometry": [LineString(xy[:2]), LineString(xy[2:])]}, crs=7755)
    cold = load_or_build_cached_graph(gdf, tmp_path)
    assert cold[-1].nnz == 2  # one undirected bridge; no stored physical-edge zeros
    assert np.all(cold[-1].data == 1)
    def no_build(*args):
        pytest.fail("Valid warm cache unexpectedly rebuilt")
    monkeypatch.setattr(builder, "build_canonical_highway_graph", no_build)
    warm = load_or_build_cached_graph(gdf, tmp_path)
    for matrices in (cold, warm):
        gt, gd, coords, labels, tree, mask = matrices
        store = SimpleNamespace(nh_graph=gt, nh_distance_graph=gd, nh_node_xy=coords,
                                nh_comp_labels=labels, nh_tree=tree, nh_bridge_mask=mask)
        def loc(pt):
            lon, lat = service.to_wgs84.transform(*pt)
            return ResolvedLocation(type="coordinate", canonical_name="cache fixture", latitude=lat,
                                    longitude=lon, source_dataset="test", match_method="coordinate")
        result, path = service.calculate_highway_route_with_path(loc(xy[0]), loc(xy[-1]), store=store)
        assert result.network_distance_km == 2.28
        assert result.route_quality.synthetic_bridge_count == 1
        assert result.route_quality.synthetic_bridge_distance_km == .3
        assert result.route_quality.maximum_synthetic_bridge_m == 300
        assert path.edge_kinds == ("physical", "bridge", "physical")


@pytest.mark.parametrize("crs", [None, 4326, 3857])
def test_non_metric_crs_rejected(sample_nh_gdf, tmp_path, crs):
    invalid = sample_nh_gdf.copy().set_crs(crs, allow_override=True)
    with pytest.raises(ValueError, match="EPSG:7755"):
        load_or_build_cached_graph(invalid, tmp_path)


@pytest.mark.parametrize("key", ["labels", "dist_data", "bridge_data", "positive_distance"])
def test_invalid_cached_metadata_rebuilds(sample_nh_gdf, tmp_path, key):
    expected = load_or_build_cached_graph(sample_nh_gdf, tmp_path)
    cache = tmp_path / "canonical_nh_graph.npz"
    with np.load(cache, allow_pickle=False) as archive:
        data = {name: archive[name].copy() for name in archive.files}
    if key == "labels":
        data[key] = np.arange(len(data[key]))
    elif key == "dist_data":
        data[key][0] = 0
    elif key == "positive_distance":
        data["dist_data"] *= 2
    else:
        # Explicit zeros on physical topology must not pass as a sparse bridge mask.
        data["bridge_data"] = np.zeros_like(data["time_data"])
        data["bridge_indices"] = data["time_indices"]
        data["bridge_indptr"] = data["time_indptr"]
    np.savez_compressed(cache, **data)
    actual = load_or_build_cached_graph(sample_nh_gdf, tmp_path)
    assert np.array_equal(actual[3], expected[3])
    assert (actual[1] != expected[1]).nnz == 0
    assert actual[-1].nnz == expected[-1].nnz


def test_failed_atomic_replacement_preserves_cache(sample_nh_gdf, tmp_path, monkeypatch):
    import scripts.clean.graph_builder as builder

    load_or_build_cached_graph(sample_nh_gdf, tmp_path)
    cache = tmp_path / "canonical_nh_graph.npz"
    before = cache.read_bytes()
    modified = sample_nh_gdf.copy()
    modified.loc[0, "highway"] = "motorway"
    def fail_replace(*args):
        raise OSError("injected replacement failure")
    monkeypatch.setattr(builder.os, "replace", fail_replace)
    result = load_or_build_cached_graph(modified, tmp_path)
    assert result[0].shape[0] > 0
    assert cache.read_bytes() == before
    assert list(tmp_path.iterdir()) == [cache]


def test_duplicate_physical_edges_never_sum_or_become_bridges(tmp_path):
    line = LineString([(0, 0), (300, 0)])
    gdf = gpd.GeoDataFrame({"geometry": [line, line],
                           "highway": ["primary", "motorway"]}, crs=7755)
    gt, gd, coords, labels, tree, mask = load_or_build_cached_graph(gdf, tmp_path)
    assert gt.nnz == gd.nnz == 2
    assert mask.nnz == 0
    assert gt[0, 1] == pytest.approx(.3 / 90)
    assert gd[0, 1] == .3
    assert len(coords) == tree.n == 2
    assert np.array_equal(labels, [0, 0])
