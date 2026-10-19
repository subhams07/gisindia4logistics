"""Synthetic producer/transport regressions; all publications stay in tmp_path."""
import json
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely.geometry import LineString, MultiPoint, Point

from scripts.analyze import generate_isochrones as iso
from scripts.clean import graph_builder as builder
from server.dependencies import get_data_store
from server.routers import routing


@pytest.fixture
def sources(tmp_path):
    root = tmp_path / "read-only-source"
    (root / "roads").mkdir(parents=True)
    (root / "logistics_hubs").mkdir()
    center = gpd.GeoSeries([Point(78, 22)], crs=4326).to_crs(7755).iloc[0]
    x, y = builder.snap_pt(center.x, center.y)
    nh = gpd.GeoDataFrame({"class": ["missing-old-speed"]}, geometry=[
        LineString([(x, y), (x + 30000, y), (x + 60000, y)])], crs=7755)
    nh.to_crs(4326).to_file(root / "roads/india_nh_network.geojson", driver="GeoJSON")
    point = gpd.GeoSeries([Point(x, y)], crs=7755).to_crs(4326).iloc[0]
    hubs = pd.DataFrame([dict(source_id="a", name="A", state="State", city="City",
        hub_type="major_port", longitude=point.x, latitude=point.y),
        dict(source_id="missing", name="Missing", state="State", city=None,
        hub_type="major_port", longitude=None, latitude=point.y)])
    hubs.to_csv(root / "logistics_hubs/ports.csv", index=False)
    hubs.assign(hub_type="mmlp").to_csv(root / "logistics_hubs/mmlps.csv", index=False)
    return root, tmp_path / "separate-cache", nh


def build(sources, **kwargs):
    return iso.build_isochrones(data_dir=sources[0], cache_dir=sources[1], **kwargs)


def test_canonical_delegation_speed_counts_and_geometry(sources, monkeypatch):
    calls = []
    real = builder.load_or_build_cached_graph
    def cached(frame, *, cache_dir):
        calls.append((frame.crs.to_epsg(), cache_dir))
        return real(frame, cache_dir=cache_dir)
    monkeypatch.setattr(builder, "load_or_build_cached_graph", cached)
    ports, mmlps, provenance = build(sources, thresholds_ports=(0.1, 1, 1.21), thresholds_mmlps=(1.21,))
    assert calls == [(7755, sources[1])]
    good = ports[ports.source_id == "a"]
    assert good.reachable_node_count.tolist() == [1, 2, 3]  # missing highway = 50, NOT old 65
    assert good.status.tolist() == ["unsupported_threshold", "unsupported_threshold", "ok"]
    assert good.geometry.iloc[:2].isna().all()
    graph = real(builder.load_highway_source(sources[0])[0], cache_dir=None)
    expected = MultiPoint(graph[2]).convex_hull.buffer(8000).simplify(500, preserve_topology=True)
    actual = good.to_crs(7755).geometry.iloc[-1]
    assert actual.hausdorff_distance(expected) < 0.01
    assert actual.geom_type in {"Polygon", "MultiPolygon"} and actual.is_valid
    assert len(mmlps) == 2
    assert provenance["coverage_method"] == "approximate_highway_catchment"
    assert provenance["feeder_speed_kmh"] == 35 and provenance["max_snap_km"] == 150
    assert provenance["vehicle_factor"] == 1.0
    assert provenance["buffer_m"] == 8000 and provenance["simplify_m"] == 500
    assert provenance["component_policy"] == "single_source_component"


def test_null_geometry_identity_roundtrip_and_both_rest(sources):
    ports, mmlps, _ = build(sources)
    analysis = sources[0] / "analysis"
    iso.write_isochrones(ports, mmlps, ports_path=analysis / "major_ports_isochrones.geojson",
                         mmlps_path=analysis / "mmlp_isochrones.geojson")
    app = FastAPI()
    app.include_router(routing.router)
    app.dependency_overrides[get_data_store] = lambda: SimpleNamespace(data_dir=sources[0])
    with TestClient(app, raise_server_exceptions=False) as client:
        for kind, count in [("ports", 8), ("mmlps", 6)]:
            response = client.get(f"/route/isochrones/{kind}")
            assert response.status_code == 200, response.text
            payload = response.json()
            json.dumps(payload, allow_nan=False)
            assert len(payload["features"]) == count
            missing = [f for f in payload["features"] if f["properties"]["source_id"] == "missing"]
            assert missing
            for f in missing:
                assert f["geometry"] is None
                assert f["properties"]["status"] == "invalid_coordinates"
                assert f["properties"]["snap_feeder_km"] is None
                assert f["properties"]["feeder_time_hours"] is None
                assert f["properties"]["city"] is None
                assert f["properties"]["reachable_node_count"] == 0


@pytest.mark.parametrize("point,expected", [((0, 0), (0, 1.005)), ((0, -150000), None),
                                              ((-151005, 0), (0, 150.0))])
def test_nearest_inclusive_and_node_id_tie(point, expected):
    graph = builder.build_canonical_highway_graph(gpd.GeoDataFrame(geometry=[
        LineString([(-1005, 0), (1005, 0), (1005, 30000)])], crs=7755))
    capture = SimpleNamespace(nh_graph=graph[0], nh_distance_graph=graph[1], nh_node_xy=graph[2],
        nh_comp_labels=graph[3], nh_tree=graph[4], nh_bridge_mask=graph[5])
    result = iso._nearest_source(np.array(point, dtype=float), capture)
    if expected is None:
        assert result is None
    else:
        assert result[0] == expected[0]
        assert result[1] == pytest.approx(expected[1], abs=0.006)


def test_feeder_deducted_and_same_snap_monotonic(sources):
    root, _, _ = sources
    frame = pd.read_csv(root / "logistics_hubs/ports.csv").iloc[:1].copy()
    pt = gpd.GeoSeries(gpd.points_from_xy(frame.longitude, frame.latitude), crs=4326).to_crs(7755).iloc[0]
    shifted = gpd.GeoSeries([Point(pt.x, pt.y + 35000)], crs=7755).to_crs(4326).iloc[0]
    frame["longitude"], frame["latitude"] = shifted.x, shifted.y
    frame.to_csv(root / "logistics_hubs/ports.csv", index=False)
    ports, _, _ = build(sources, thresholds_ports=(0.5, 1.01, 1.61, 2.21))
    assert ports.status.tolist() == ["feeder_exceeds_threshold", "unsupported_threshold", "unsupported_threshold", "ok"]
    assert ports.reachable_node_count.tolist() == [0, 1, 2, 3]
    assert ports.snap_feeder_km.tolist() == pytest.approx([35] * 4)
    assert ports.feeder_time_hours.tolist() == pytest.approx([1] * 4)


def test_source_count_generalization_chunks_limits_and_no_graph_copy(sources, monkeypatch):
    root, _, _ = sources
    frame = pd.read_csv(root / "logistics_hubs/ports.csv").iloc[:1]
    pd.concat([frame.assign(source_id=str(i), name=f"A{i}") for i in range(12)]).to_csv(root / "logistics_hubs/ports.csv", index=False)
    pd.concat([frame.assign(source_id=str(i), name=f"A{i}", hub_type="mmlp") for i in range(20)]).to_csv(root / "logistics_hubs/mmlps.csv", index=False)
    calls = []
    real = iso.dijkstra
    def search(graph, **kwargs):
        calls.append(kwargs)
        assert kwargs["directed"] is False
        assert len(kwargs["indices"]) <= 3
        assert 0 <= kwargs["limit"] <= 24
        return real(graph, **kwargs)
    monkeypatch.setattr(iso, "dijkstra", search)
    ports, mmlps, _ = build(sources, chunk_size=3)
    assert len(ports) == 48 and len(mmlps) == 60
    assert calls
    assert list(ports.source_id.unique()) == list(range(12))


@pytest.mark.parametrize("chunk", [0, 33, True, 1.5])
def test_chunk_invalid(sources, chunk):
    with pytest.raises(ValueError):
        build(sources, chunk_size=chunk)


@pytest.mark.parametrize("threshold", [0, -1, float("inf"), float("nan"), True, "4"])
def test_threshold_invalid(sources, threshold):
    with pytest.raises(ValueError):
        build(sources, thresholds_ports=(threshold,))


def test_missing_custom_root_fatal(tmp_path):
    with pytest.raises(FileNotFoundError):
        iso.build_isochrones(data_dir=tmp_path / "missing", cache_dir=tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("filename", ["ports.csv", "mmlps.csv"])
@pytest.mark.parametrize("name", [None, "", "   ", 123, True, "Repeated"])
def test_invalid_or_duplicate_hub_identity_fails_before_publication(sources, tmp_path, filename, name):
    path = sources[0] / "logistics_hubs" / filename
    frame = pd.read_csv(path)
    frame["name"] = name
    frame.to_csv(path, index=False)
    output = tmp_path / "outputs"
    output.mkdir()
    for target in ("major_ports_isochrones.geojson", "mmlp_isochrones.geojson"):
        (output / target).write_bytes(b"prior output")
    with pytest.raises(ValueError, match="identity"):
        iso.generate_isochrones(data_dir=sources[0], cache_dir=sources[1], output_dir=output)
    assert not sources[1].exists()
    assert all(path.read_bytes() == b"prior output" for path in output.iterdir())


def test_far_snap_no_fabrication_and_invalid_coords(sources):
    root = sources[0]
    hubs = pd.read_csv(root / "logistics_hubs/ports.csv")
    hubs.loc[0, ["longitude", "latitude"]] = [0, 0]
    hubs.to_csv(root / "logistics_hubs/ports.csv", index=False)
    ports, _, _ = build(sources)
    assert ports[ports.source_id == "a"].status.eq("no_highway_within_150km").all()
    assert ports.geometry.isna().all() and ports.reachable_node_count.eq(0).all()


def test_cli_custom_paths_and_legacy_alias(sources, monkeypatch):
    calls = []
    monkeypatch.setattr(iso, "generate_isochrones", lambda **kw: calls.append(kw))
    iso.main(["--data-dir", str(sources[0]), "--cache-dir", str(sources[1]),
              "--output-dir", str(sources[1] / "output")])
    assert calls[0]["data_dir"] == sources[0]
    assert calls[0]["cache_dir"] == sources[1]
    assert callable(iso.generate_hub_isochrones)


def test_nearest_small_component_not_mainland_or_larger_component(monkeypatch):
    nh = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (1005, 0)]),
        LineString([(0, 10005), (30000, 10005), (60000, 10005)])], crs=7755)
    values = builder.build_canonical_highway_graph(nh)
    capture = SimpleNamespace(nh_graph=values[0], nh_distance_graph=values[1], nh_node_xy=values[2],
        nh_comp_labels=values[3], nh_tree=values[4], nh_bridge_mask=values[5])
    monkeypatch.setattr(iso, "_coordinate", lambda row: np.array([0.0, 0.0]))
    frame = pd.DataFrame([dict(name="Island physical source", state="Lakshadweep", source_id="x")])
    result = iso._hub_features(frame, (4, 8, 24), "mmlp", capture, 32)
    assert result.status.eq("unsupported_threshold").all()
    assert result.reachable_node_count.tolist() == [2, 2, 2]
    assert result.geometry.isna().all()
    assert result.snap_feeder_km.eq(0).all()


def test_exact_threshold_boundary_and_limited_search(monkeypatch):
    nh = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (30000, 0), (60000, 0)])], crs=7755)
    values = builder.build_canonical_highway_graph(nh)
    capture = SimpleNamespace(nh_graph=values[0], nh_distance_graph=values[1], nh_node_xy=values[2],
        nh_comp_labels=values[3], nh_tree=values[4], nh_bridge_mask=values[5])
    monkeypatch.setattr(iso, "_coordinate", lambda row: np.array([0.0, 35000.0]))
    real = iso.dijkstra
    def limited(graph, **kw):
        assert graph is capture.nh_graph and kw["limit"] == pytest.approx(1.2)
        return real(graph, **kw)
    monkeypatch.setattr(iso, "dijkstra", limited)
    frame = pd.DataFrame([dict(name="Boundary")])
    result = iso._hub_features(frame, (0.5, 1.0, 1.6, 2.2), "major_port", capture, 1)
    assert result.reachable_node_count.tolist() == [0, 1, 2, 3]


def test_root_source_unchanged_exact_capture_and_external_cache(sources, monkeypatch):
    root, cache, _ = sources
    paths = [root / "roads/india_nh_network.geojson", root / "logistics_hubs/ports.csv",
             root / "logistics_hubs/mmlps.csv"]
    before = {p: p.read_bytes() for p in paths}
    values = builder.build_canonical_highway_graph(builder.load_highway_source(root)[0])
    real_cached, real_features = builder.load_or_build_cached_graph, iso._hub_features
    def cached(frame, *, cache_dir):
        assert cache_dir == cache and not cache.is_relative_to(root)
        real_cached(frame, cache_dir=cache_dir)
        return values
    def features(frame, thresholds, kind, graph, chunk):
        names = ("nh_graph", "nh_distance_graph", "nh_node_xy", "nh_comp_labels", "nh_tree", "nh_bridge_mask")
        assert set(vars(graph)) == set(names)
        assert all(getattr(graph, name) is value for name, value in zip(names, values))
        return real_features(frame, thresholds, kind, graph, chunk)
    monkeypatch.setattr(builder, "load_or_build_cached_graph", cached)
    monkeypatch.setattr(iso, "_hub_features", features)
    from pathlib import Path
    real_mkdir = Path.mkdir
    def writable_only_outside_source(path, *args, **kwargs):
        assert not path.is_relative_to(root), "Source root must remain read-only"
        return real_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", writable_only_outside_source)
    build(sources)
    assert before == {p: p.read_bytes() for p in paths}
    assert (cache / "canonical_nh_graph.npz").exists()
    assert not (root / "cache").exists() and not (root / "analysis").exists()


@pytest.mark.parametrize("coordinates", [(181, 22), (78, 91), (float("inf"), 22), (True, 22), ("78", 22)])
def test_invalid_coordinate_values(coordinates):
    assert iso._coordinate(dict(longitude=coordinates[0], latitude=coordinates[1])) is None


def test_actual_synthetic_cli_and_zero_argument_alias(sources, monkeypatch):
    destination = sources[1] / "derivatives"
    result = iso.main(["--data-dir", str(sources[0]), "--cache-dir", str(sources[1]),
                       "--output-dir", str(destination)])
    assert len(result[0]) == 8 and len(result[1]) == 6
    assert len(gpd.read_file(destination / "major_ports_isochrones.geojson")) == 8
    monkeypatch.setenv("GIS4LOGISTICS_DATA_PATH", str(sources[0]))
    monkeypatch.setenv("GIS4LOGISTICS_CACHE_PATH", str(sources[1]))
    result = iso.generate_hub_isochrones()
    assert len(result[0]) == 8


def test_zero_sources_return_empty_not_forced_counts(sources):
    root = sources[0]
    pd.read_csv(root / "logistics_hubs/ports.csv").iloc[:0].to_csv(root / "logistics_hubs/ports.csv", index=False)
    pd.read_csv(root / "logistics_hubs/mmlps.csv").iloc[:0].to_csv(root / "logistics_hubs/mmlps.csv", index=False)
    ports, mmlps, provenance = build(sources)
    assert ports.empty and mmlps.empty
    assert provenance["source_ports"] == 0 and provenance["source_mmlps"] == 0


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


def _fragment_and_main_graph():
    """Fragment A (3 nodes, x 0..2000) beside the supported main line B (6 nodes) 800 m north."""
    nh = gpd.GeoDataFrame(geometry=[
        LineString([(0, 0), (1000, 0), (2000, 0)]),
        LineString([(0, 800), (10000, 800), (20000, 800), (30000, 800), (40000, 800), (50000, 800)])], crs=7755)
    values = builder.build_canonical_highway_graph(nh)
    graph = SimpleNamespace(nh_graph=values[0], nh_distance_graph=values[1], nh_node_xy=values[2],
        nh_comp_labels=values[3], nh_tree=values[4], nh_bridge_mask=values[5])
    return graph


def test_nearest_source_skips_fragment_for_supported_component(monkeypatch):
    from scripts.analyze import highway_pair_kernel as kernel
    graph = _fragment_and_main_graph()
    monkeypatch.setattr(kernel, "MIN_SUPPORTED_COMPONENT_NODES", 5)
    node, feeder_km, size = iso._nearest_source(np.array([0.0, 0.0]), graph)
    assert size == 6 and graph.nh_node_xy[node].tolist() == pytest.approx([0.0, 800.0], abs=5.0)
    assert feeder_km == pytest.approx(0.8, abs=0.01)
    # With the floor lowered the physically nearest fragment is chosen again.
    monkeypatch.setattr(kernel, "MIN_SUPPORTED_COMPONENT_NODES", 1)
    node, feeder_km, size = iso._nearest_source(np.array([0.0, 0.0]), graph)
    assert size == 3 and feeder_km == 0.0


def test_hub_features_report_supported_component_and_node_count(monkeypatch):
    from scripts.analyze import highway_pair_kernel as kernel
    graph = _fragment_and_main_graph()
    monkeypatch.setattr(kernel, "MIN_SUPPORTED_COMPONENT_NODES", 5)
    monkeypatch.setattr(iso, "_coordinate", lambda row: np.array([0.0, 0.0]))
    frame = pd.DataFrame([{"name": "Near fragment", "state": "X", "source_id": "x"}])
    result = iso._hub_features(frame, (1, 4), "mmlp", graph, 32)
    assert result.snap_component_node_count.tolist() == [6, 6]
    assert result.snap_feeder_km.tolist() == pytest.approx([0.8, 0.8], abs=0.01)
    assert result.status.eq("ok").all() and result.reachable_node_count.min() >= 3


def test_no_supported_vertex_within_limit_stays_unsupported(monkeypatch):
    from scripts.analyze import highway_pair_kernel as kernel
    graph = _fragment_and_main_graph()
    monkeypatch.setattr(kernel, "MIN_SUPPORTED_COMPONENT_NODES", 7)  # no component qualifies
    assert iso._nearest_source(np.array([0.0, 0.0]), graph) is None
    monkeypatch.setattr(kernel, "MIN_SUPPORTED_COMPONENT_NODES", 5)
    assert iso._nearest_source(np.array([0.0, 160000.0]), graph) is None  # supported line is beyond 150 km
    monkeypatch.setattr(iso, "_coordinate", lambda row: np.array([0.0, 160000.0]))
    frame = pd.DataFrame([{"name": "Remote", "state": "X", "source_id": "x"}])
    result = iso._hub_features(frame, (4,), "mmlp", graph, 32)
    assert result.status.tolist() == ["no_highway_within_150km"]
    assert result.snap_feeder_km.isna().all() and result.snap_component_node_count.isna().all()
    assert result.geometry.isna().all() and result.reachable_node_count.tolist() == [0]


def test_kernel_nearest_supported_ties_and_matrix_snapping_unchanged():
    from scripts.analyze import highway_pair_kernel as kernel
    labels = np.array([0, 0, 1, 1, 1])
    sizes = kernel.component_sizes(labels)
    idx, dist = np.array([0, 2, 3]), np.array([10.0, 5.0, 5.0])
    assert kernel.nearest_supported(dist, idx, labels, sizes, min_nodes=3) == (5.0, 2, 3)
    assert kernel.nearest_supported(dist, idx, labels, sizes, min_nodes=1) == (5.0, 2, 3)
    assert kernel.nearest_supported(dist, idx, labels, sizes, min_nodes=4) is None
    assert kernel.nearest_supported(dist, idx, labels, sizes, min_nodes=2, max_distance_m=4.0) is None
    # The closest-compatible matrix helper still has no size preference.
    assert kernel.nearest_per_component(dist, idx, labels) == {0: (10.0, 0), 1: (5.0, 2)}
