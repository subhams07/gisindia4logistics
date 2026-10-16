"""Shared highway loader policy, using only tiny synthetic on-disk sources."""
import hashlib
from unittest.mock import Mock

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString

from scripts.clean import graph_builder as builder
from server import dependencies
from server.dependencies import DataStore
from server.services.exceptions import DataIntegrityError


@pytest.fixture
def load_source():
    # Attribute lookup makes the missing proposed API a durable TDD failure.
    return builder.load_highway_source


def frame(ref="A", crs=4326):
    result = gpd.GeoDataFrame(
        {"ref": [ref], "highway": ["trunk"], "osm_id": [1]},
        geometry=[LineString([(78, 22), (78.01, 22.01)])], crs=4326,
    )
    return result.to_crs(crs) if crs is not None else result.set_crs(None, allow_override=True)


def write_source(root, kind, data):
    path = root / "roads" / f"india_nh_network.{kind}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "parquet":
        data.to_parquet(path, index=False)
    else:
        data.to_file(path, driver="GeoJSON", index=False)
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


@pytest.mark.parametrize("kind", ["parquet", "geojson"])
def test_real_source_selected_projected_and_read_only(load_source, tmp_path, kind):
    original = frame()
    selected = write_source(tmp_path, kind, original)
    before = digest(selected)
    actual, path = load_source(tmp_path)
    assert path == selected
    assert actual.crs.to_epsg() == builder.PROJ_EPSG == 7755
    assert actual["ref"].tolist() == ["A"]
    expected = original.to_crs(7755)
    np.testing.assert_allclose(actual.geometry.iloc[0].coords, expected.geometry.iloc[0].coords)
    assert digest(selected) == before
    assert set(tmp_path.rglob("*")) == {selected.parent, selected}


def test_both_sources_prefer_parquet_without_reading_geojson(load_source, tmp_path, monkeypatch):
    preferred = write_source(tmp_path, "parquet", frame("preferred"))
    alternate = write_source(tmp_path, "geojson", frame("alternate"))
    before = {p: digest(p) for p in (preferred, alternate)}
    monkeypatch.setattr(gpd, "read_file", Mock(side_effect=AssertionError("alternate read")))
    actual, path = load_source(tmp_path)
    assert path == preferred
    assert actual["ref"].tolist() == ["preferred"]
    assert {p: digest(p) for p in before} == before


def test_absent_both_has_no_io_or_default_store_initialization(load_source, tmp_path, monkeypatch):
    monkeypatch.setattr(gpd, "read_file", Mock(side_effect=AssertionError("read_file")))
    monkeypatch.setattr(gpd, "read_parquet", Mock(side_effect=AssertionError("read_parquet")))
    monkeypatch.setattr(DataStore, "get_instance", Mock(side_effect=AssertionError("singleton")))
    before_root = dependencies.settings.DATA_PATH
    before_cache = dependencies.settings.CACHE_PATH
    before_instance = DataStore._instance
    assert load_source(tmp_path / "missing") is None
    assert not (tmp_path / "missing").exists()
    assert dependencies.settings.DATA_PATH == before_root
    assert dependencies.settings.CACHE_PATH == before_cache
    assert DataStore._instance is before_instance


def test_custom_roots_a_b_a_and_missing_never_use_configured_root(load_source, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    pa = write_source(a, "parquet", frame("A"))
    pb = write_source(b, "geojson", frame("B"))
    monkeypatch.setattr(dependencies.settings, "DATA_PATH", a)
    for root, path, ref in ((a, pa, "A"), (b, pb, "B"), (a, pa, "A")):
        actual, selected = load_source(root)
        assert selected == path
        assert actual["ref"].tolist() == [ref]
    assert load_source(tmp_path / "empty") is None


def test_malformed_preferred_parquet_never_reads_valid_alternate(load_source, tmp_path, monkeypatch):
    alternate = write_source(tmp_path, "geojson", frame())
    preferred = alternate.with_suffix(".parquet")
    preferred.write_bytes(b"not a parquet file")
    before = {p: digest(p) for p in (preferred, alternate)}
    # Confirm that the alternate is independently readable before forbidding fallback.
    assert len(gpd.read_file(alternate)) == 1
    read_alternate = Mock(side_effect=AssertionError("must not fall back"))
    monkeypatch.setattr(gpd, "read_file", read_alternate)
    with pytest.raises(ValueError, match="Parquet"):
        load_source(tmp_path)
    read_alternate.assert_not_called()
    assert {p: digest(p) for p in before} == before


@pytest.mark.parametrize("kind", ["parquet", "geojson"])
def test_reader_failures_propagate_unchanged(load_source, tmp_path, monkeypatch, kind):
    selected = write_source(tmp_path, kind, frame())
    error = OSError("injected source read failure")
    reader = Mock(side_effect=error)
    monkeypatch.setattr(dependencies.gpd, "read_parquet" if kind == "parquet" else "read_file", reader)
    with pytest.raises(OSError) as exc:
        load_source(tmp_path)
    assert exc.value is error
    reader.assert_called_once_with(selected)


@pytest.mark.parametrize("crs", [4326, 3857, 7755])
def test_native_crs_is_respected_not_relabelled(load_source, tmp_path, crs):
    original = frame(crs=crs)
    path = write_source(tmp_path, "parquet", original)
    actual, selected = load_source(tmp_path)
    assert selected == path
    assert actual.crs.to_epsg() == 7755
    np.testing.assert_allclose(actual.geometry.iloc[0].coords,
                               original.to_crs(7755).geometry.iloc[0].coords)
    assert gpd.read_parquet(path).crs.to_epsg() == crs


def test_missing_crs_preferred_parquet_raises_without_fallback(load_source, tmp_path, monkeypatch):
    preferred = write_source(tmp_path, "parquet", frame(crs=None))
    write_source(tmp_path, "geojson", frame())
    before = digest(preferred)
    alternate_reader = Mock(side_effect=AssertionError("CRS failure is not absence"))
    monkeypatch.setattr(gpd, "read_file", alternate_reader)
    with pytest.raises(ValueError, match="naive geometries"):
        load_source(tmp_path)
    alternate_reader.assert_not_called()
    assert digest(preferred) == before


def test_invalid_crs_projection_failure_propagates(load_source, tmp_path, monkeypatch):
    preferred = write_source(tmp_path, "parquet", frame())
    write_source(tmp_path, "geojson", frame())
    from pyproj.exceptions import CRSError
    error = CRSError("invalid source CRS")
    projected = Mock(side_effect=error)
    reader_frame = Mock(to_crs=projected)
    monkeypatch.setattr(gpd, "read_parquet", Mock(return_value=reader_frame))
    alternate = Mock(side_effect=AssertionError("no alternate on CRS failure"))
    monkeypatch.setattr(gpd, "read_file", alternate)
    with pytest.raises(CRSError) as exc:
        load_source(tmp_path)
    assert exc.value is error
    projected.assert_called_once_with(builder.PROJ_EPSG)
    gpd.read_parquet.assert_called_once_with(preferred)
    alternate.assert_not_called()


def test_store_absent_source_remains_unpublished(load_source, tmp_path, monkeypatch):
    store = DataStore()
    store.data_dir = tmp_path
    cache = Mock(side_effect=AssertionError("cache called without source"))
    publish = Mock(side_effect=AssertionError("published without source"))
    monkeypatch.setattr(builder, "load_or_build_cached_graph", cache)
    monkeypatch.setattr(store, "publish_highway_graph", publish)
    store._build_highway_graph()
    assert store._highway_snapshot is None
    with pytest.raises(DataIntegrityError, match="unpublished"):
        store.get_highway_snapshot()
    cache.assert_not_called()
    publish.assert_not_called()


def test_store_delegates_then_publishes_all_six_without_raw_assignments(load_source, tmp_path, monkeypatch):
    store = DataStore()
    store.data_dir = tmp_path / "source"
    cache_dir = tmp_path / "separate-cache"
    projected = frame(crs=7755)
    graph = builder.build_canonical_highway_graph(projected)
    events = []
    def load(root):
        assert root == store.data_dir
        events.append("source")
        return projected, root / "roads/india_nh_network.parquet"
    def cached(data, *, cache_dir):
        assert data is projected
        assert cache_dir == dependencies.settings.CACHE_PATH
        events.append("cache")
        return graph
    def publish(*fields):
        assert len(fields) == 6
        assert all(actual is expected for actual, expected in zip(fields, graph))
        events.append("publish")
    monkeypatch.setattr(dependencies.settings, "CACHE_PATH", cache_dir)
    monkeypatch.setattr(builder, "load_highway_source", load)
    monkeypatch.setattr(builder, "load_or_build_cached_graph", cached)
    monkeypatch.setattr(store, "publish_highway_graph", publish)
    before = {k: v for k, v in vars(store).items() if k.startswith("nh_")}
    store._build_highway_graph()
    assert events == ["source", "cache", "publish"]
    assert all(getattr(store, k) is v for k, v in before.items())
    assert store._highway_snapshot is None


def test_store_reuses_separately_selected_existing_cache(load_source, tmp_path, monkeypatch):
    root = tmp_path / "source"
    selected = write_source(root, "parquet", frame())
    before = digest(selected)
    projected, _ = load_source(root)
    cache_dir = tmp_path / "writable-cache"
    expected = builder.load_or_build_cached_graph(projected, cache_dir=cache_dir)
    cache_file = cache_dir / "canonical_nh_graph.npz"
    before_cache = digest(cache_file)
    monkeypatch.setattr(dependencies.settings, "CACHE_PATH", cache_dir)
    monkeypatch.setattr(builder, "build_canonical_highway_graph",
                        Mock(side_effect=AssertionError("warm cache rebuilt")))
    store = DataStore()
    store.data_dir = root
    store._build_highway_graph()
    snapshot = store.get_highway_snapshot()
    assert snapshot.generation == 1
    assert (store.nh_graph != expected[0]).nnz == 0
    assert (store.nh_distance_graph != expected[1]).nnz == 0
    np.testing.assert_array_equal(store.nh_node_xy, expected[2])
    np.testing.assert_array_equal(store.nh_comp_labels, expected[3])
    np.testing.assert_array_equal(store.nh_tree.data, expected[4].data)
    assert (store.nh_bridge_mask != expected[5]).nnz == 0
    assert digest(selected) == before
    assert digest(cache_file) == before_cache
    assert not (root / "canonical_nh_graph.npz").exists()
    assert not (root / "roads/canonical_nh_graph.npz").exists()
