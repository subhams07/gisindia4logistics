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
