"""Synthetic, scratch-only matrix producer contract and scalar-oracle checks."""
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix
from scipy.spatial import KDTree
from shapely.geometry import LineString, Point

from scripts.analyze import nh_travel_matrix as matrix
from scripts.analyze import highway_pair_kernel as kernel


def test_missing_root_does_not_fall_back(tmp_path):
    with pytest.raises(FileNotFoundError):
        matrix.build_travel_outputs(data_dir=tmp_path, cache_dir=tmp_path / 'cache')
    assert not (tmp_path / 'cache').exists()


def test_candidates_seeded_scalar_parity():
    rng = np.random.default_rng(401)
    xy = rng.uniform(-180000, 180000, (4000, 2))
    xy[:3] = [[150000, 0], [-150000, 0], [0, 0]]
    labels = rng.choice([0, 91, 1000000000], len(xy))
    graph = SimpleNamespace(nh_tree=KDTree(xy), nh_node_xy=xy, nh_comp_labels=labels)
    for p in np.vstack(([0, 0], rng.uniform(-200000, 200000, (30, 2)))):
        ids, distances = kernel.radius_candidates(p, graph)
        expected = kernel.nearest_per_component(distances, ids, labels)
        assert matrix._point_candidates(p, graph) == expected


def test_pairs_vectorized_scalar_parity():
    rng = np.random.default_rng(33)
    labels = rng.choice([1, 99, 100000000], 100)
    origin = kernel.nearest_per_component(rng.integers(0, 4, 100), np.arange(100), labels)
    hubs = [kernel.nearest_per_component(rng.integers(0, 4, 100), np.arange(100), labels)
            for _ in range(200)]
    for _ in range(200):
        ids = rng.choice(100, size=int(rng.integers(0, 10)), replace=False)
        hubs.append(kernel.nearest_per_component(rng.uniform(0, 180000, len(ids)), ids, labels))
    hubs.append({})
    selected = matrix._compatible_hubs(origin, matrix._hub_components(hubs), len(hubs))
    for i, dest in enumerate(hubs):
        expected = kernel.select_compatible_pair(
            [v[0] for v in origin.values()], [v[1] for v in origin.values()],
            [v[0] for v in dest.values()], [v[1] for v in dest.values()], labels)
        assert matrix._selected_pair(selected, i) == expected


def test_writer_same_frame_nulls(tmp_path):
    frame = pd.DataFrame({'state': ['X', 'Y'], 'district': ['A', 'B'],
                          'district_code': [1.0, np.nan], 'pop_2011': [np.nan, 10.0]})
    matrix.write_travel_outputs(frame, frame, summary_csv=tmp_path / 's.csv',
                               summary_parquet=tmp_path / 's.parquet',
                               port_matrix_csv=tmp_path / 'm.csv')
    pd.testing.assert_frame_equal(pd.read_csv(tmp_path / 's.csv'), pd.read_parquet(tmp_path / 's.parquet'))


def test_declared_hub_name_columns_match_committed_sources():
    for kind, (relative, name_column) in matrix.HUB_FILES.items():
        source = matrix.DATA_DIR / relative
        assert name_column in pd.read_csv(source, nrows=0).columns, kind


def test_freight_terminal_uses_actual_name_schema(synthetic, tmp_path):
    root, _, _ = synthetic
    stations = pd.read_csv(root / 'rail/railway_stations.csv')
    terminal = stations.iloc[[0]].rename(columns={'station_name': 'name'})
    terminal['name'] = 'Actual-schema terminal'
    terminal.to_csv(root / 'rail/freight_terminals.csv', index=False)
    summary, _, _ = matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert summary.loc[0, 'nearest_freight_terminal_name'] == 'Actual-schema terminal'
    assert summary.loc[0, 'freight_terminal_drive_time_hours'] == .2
    assert summary.loc[0, 'freight_terminal_road_distance_km'] == round(2 * np.hypot(5, 10), 1)


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    for directory in ('administrative', 'roads', 'rail', 'logistics_hubs', 'demographic'):
        (root / directory).mkdir(parents=True)
    base = np.array([3000000., 3000000.])
    xy = base + np.array([[0, 0], [10000, 0], [5000, 10000], [1000, 0], [2000000, 0]])
    edges = [(0, 1, 1.), (0, 2, .1), (2, 1, .1), (0, 3, .5)]
    rows, cols, times, distances = [], [], [], []
    for u, v, hours in edges:
        rows.extend([u, v])
        cols.extend([v, u])
        times.extend([hours, hours])
        distance = np.linalg.norm(xy[u] - xy[v]) / 1000
        distances.extend([distance, distance])
    gt = csr_matrix((times, (rows, cols)), shape=(5, 5))
    gd = csr_matrix((distances, (rows, cols)), shape=(5, 5))
    bridge = csr_matrix((5, 5))
    fields = (gt, gd, xy, np.array([9, 9, 9, 9, 1000000]), KDTree(xy), bridge)
    graph = SimpleNamespace(**dict(zip(('nh_graph', 'nh_distance_graph', 'nh_node_xy',
                                       'nh_comp_labels', 'nh_tree', 'nh_bridge_mask'), fields)))
    nh = gpd.GeoDataFrame({'highway': ['primary'] * len(edges)},
                         geometry=[LineString([xy[u], xy[v]]) for u, v, _ in edges], crs=7755)
    nh.to_parquet(root / 'roads/india_nh_network.parquet', index=False)
    # Preferred parquet must be used despite corrupt fallback.
    (root / 'roads/india_nh_network.geojson').write_text('not geojson')
    districts = gpd.GeoDataFrame({'state': ['X', 'X', 'X', ' LaKsHaDwEeP '],
                                  'district': ['Same', 'Far', 'Missing', 'Same'],
                                  'district_code': [1., 2., np.nan, 4.]},
                                 geometry=[Point(xy[0]), Point(xy[4]), None, Point(xy[0])], crs=7755)
    districts.to_file(root / 'administrative/india_districts_lgd.geojson', driver='GeoJSON')
    population = pd.DataFrame({'state': ['X', 'X', 'lakshadweep'], 'district': ['Same', 'Far', 'Same'],
                               'district_code': [1., 2., 4.], 'pop_2011': [12., 23., 45.]})
    population.to_csv(root / 'demographic/district_population_estimates.csv', index=False)
    locations = gpd.GeoSeries([Point(p) for p in xy], crs=7755).to_crs(4326)
    stations = pd.DataFrame({'station_name': ['Fast distant', 'Slow near', 'Fast tie'],
                             'latitude': [locations[i].y for i in [1, 3, 1]],
                             'longitude': [locations[i].x for i in [1, 3, 1]]})
    stations.to_csv(root / 'rail/railway_stations.csv', index=False)
    ports = pd.DataFrame({'name': ['Port of First'] + [f'P{i} Port' for i in range(1, 12)],
                         'hub_type': ['major_port'] * 12,
                         'latitude': [locations[1].y] * 10 + [100., locations[4].y],
                         'longitude': [locations[1].x] * 10 + [0., locations[4].x]})
    ports.to_csv(root / 'logistics_hubs/ports.csv', index=False)
    captures = []

    def graph_loader(frame, cache_dir):
        captures.append((frame.crs.to_epsg(), cache_dir))
        return fields

    monkeypatch.setattr(matrix, 'load_or_build_cached_graph', graph_loader)
    return root, graph, captures


def test_build_fastest_path_nulls_identity_and_port_order(synthetic, tmp_path, monkeypatch):
    root, graph, captures = synthetic
    calls, requests = [], []
    original = matrix.dijkstra
    original_metrics = kernel.pair_network_metrics

    def spy(csgraph, *, directed, indices):
        assert csgraph is graph.nh_graph
        assert directed is False
        assert len(indices) <= 2
        calls.append(tuple(indices))
        return original(csgraph, directed=directed, indices=indices)

    def spy_metrics(capture, pairs, *, chunk_size):
        assert capture.nh_graph is graph.nh_graph
        requests.extend(pairs)
        return original_metrics(capture, pairs, chunk_size=chunk_size)

    monkeypatch.setattr(matrix, 'dijkstra', spy)
    monkeypatch.setattr(kernel, 'pair_network_metrics', spy_metrics)
    summary, ports, provenance = matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache', chunk_size=2)
    assert captures == [(7755, tmp_path / 'cache')]
    assert len(summary) == 4 and len(summary.columns) == 52 and len(ports.columns) == 27
    assert list(summary.columns) == matrix.SUMMARY_COLUMNS
    assert list(ports.columns[:5]) == matrix.IDENTITY_COLUMNS + ['drive_hours_to_first', 'road_km_to_first']
    assert summary.loc[0, 'nearest_rail_station_name'] == 'Fast distant'
    # Longer 0->2->1 is faster than the direct 0->1 path.
    assert summary.loc[0, 'rail_station_drive_time_hours'] == .2
    assert summary.loc[0, 'rail_station_road_distance_km'] == round(2 * np.hypot(5, 10), 1)
    assert summary.loc[0, 'rail_station_drive_time_min'] == 12.
    assert summary.loc[0, 'highway_access_dist_km'] == 0.
    assert summary.loc[0, 'pop_2011'] == 12.
    assert np.isnan(summary.loc[2, 'pop_2011'])
    assert summary.loc[3, 'pop_2011'] == 45.
    assert summary.loc[3, 'is_island']
    # Geographic flag is not a prohibition on supported local routes.
    assert summary.loc[3, 'rail_station_drive_time_hours'] == .2
    assert pd.isna(summary.loc[1, 'nearest_rail_station_name'])
    assert summary.loc[2, 'highway_access_dist_km'] != summary.loc[2, 'highway_access_dist_km']
    assert summary.loc[2, 'district'] == 'Missing'
    assert ports['drive_hours_to_p10'].isna().all()  # invalid port keeps column, no fabricated snap
    assert np.isnan(ports.loc[0, 'drive_hours_to_p11'])  # disconnected valid component
    assert ports.loc[1, 'drive_hours_to_p11'] == 0.
    assert 'is_island' not in ports
    assert provenance['invalid_hub_coordinates']['port'] == 1
    assert provenance['invalid_district_geometries'] == 1
    assert 'roads/india_nh_network.parquet' in provenance['sources_sha256']
    assert 'roads/india_nh_network.geojson' not in provenance['sources_sha256']
    assert calls and all(target != 3 for _, target in requests)  # loser never gets path walked
    assert len(requests) == 26  # 2*(rail winner + port winner + 10 fixed ports) + remote winner/fixed port
    matrix.write_travel_outputs(summary, ports, summary_csv=tmp_path / 's.csv',
                               summary_parquet=tmp_path / 's.parquet', port_matrix_csv=tmp_path / 'm.csv')
    csv, parquet = pd.read_csv(tmp_path / 's.csv'), pd.read_parquet(tmp_path / 's.parquet')
    pd.testing.assert_frame_equal(csv.astype(object).where(csv.notna(), np.nan),
                                  parquet.astype(object).where(parquet.notna(), np.nan))
    pd.testing.assert_frame_equal(csv.isna(), parquet.isna())
    assert list(csv.columns) == matrix.SUMMARY_COLUMNS
    assert csv.district_code.dtype == np.dtype('float64')


@pytest.mark.parametrize('failure', ['missing_nh', 'corrupt_preferred', 'missing_identity', 'invalid_population', 'ambiguous_population'])
def test_invalid_source_fails_before_cache_or_outputs(synthetic, tmp_path, failure):
    root, _, captures = synthetic
    if failure == 'missing_nh':
        (root / 'roads/india_nh_network.parquet').unlink()
        (root / 'roads/india_nh_network.geojson').unlink()
    elif failure == 'corrupt_preferred':
        (root / 'roads/india_nh_network.parquet').write_text('bad parquet')
    elif failure == 'missing_identity':
        frame = gpd.read_file(root / 'administrative/india_districts_lgd.geojson')
        frame.drop(columns='district').to_file(root / 'administrative/india_districts_lgd.geojson', driver='GeoJSON')
    else:
        pop_path = root / 'demographic/district_population_estimates.csv'
        pop = pd.read_csv(pop_path)
        if failure == 'invalid_population':
            pop.loc[0, 'pop_2011'] = -1
        else:
            pop = pd.concat([pop, pd.DataFrame({'state': ['X', 'x'], 'district': ['Missing', ' missing '],
                                              'district_code': [20., 21.], 'pop_2011': [2., 3.]})])
        pop.to_csv(pop_path, index=False)
    with pytest.raises(Exception):
        matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert captures == []
    assert not (tmp_path / 'cache').exists()
    assert not (root / 'analysis').exists()


def test_source_inputs_unchanged_and_optional_absent(synthetic, tmp_path):
    root, _, _ = synthetic
    sources = {p: matrix.sha256_file(p) for p in root.rglob('*') if p.is_file()}
    summary, _, provenance = matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert all(matrix.sha256_file(p) == digest for p, digest in sources.items())
    assert summary.filter(regex='^icd_|^nearest_icd_').isna().all().all()
    assert provenance['invalid_hub_coordinates']['icd'] == 0


def test_zero_point_and_distinct_same_snap(synthetic, tmp_path):
    root, graph, _ = synthetic
    points = gpd.GeoSeries([Point(graph.nh_node_xy[0]), Point(graph.nh_node_xy[0] + [100, 0])], crs=7755).to_crs(4326)
    pd.DataFrame({'name': ['zero', 'distinct'], 'latitude': points.y, 'longitude': points.x}).to_csv(
        root / 'logistics_hubs/icds.csv', index=False)
    summary, _, _ = matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert summary.loc[0, 'nearest_icd_name'] == 'zero'
    assert summary.loc[0, 'icd_road_distance_km'] == 0.
    assert summary.loc[0, 'icd_drive_time_hours'] == 0.
    # Remove zero-point hub: distinct point on same snap uses actual feeders, not zero override.
    frame = pd.read_csv(root / 'logistics_hubs/icds.csv').iloc[1:]
    frame.to_csv(root / 'logistics_hubs/icds.csv', index=False)
    summary, _, _ = matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert summary.loc[0, 'icd_road_distance_km'] == .1
    assert summary.loc[0, 'icd_drive_time_min'] == .2


@pytest.mark.parametrize('chunk_size', [0, 33, True, 1.5])
def test_invalid_chunk_size_before_io(tmp_path, chunk_size):
    with pytest.raises(ValueError, match='chunk_size'):
        matrix.build_travel_outputs(data_dir=tmp_path, cache_dir=tmp_path / 'cache', chunk_size=chunk_size)


def test_inclusive_radius_sparse_ties_and_permutations():
    xy = np.array([[150000., 0], [-150000., 0], [150000.01, 0], [0, 0]])
    labels = np.array([999999999, 999999999, 8, 100])
    graph = SimpleNamespace(nh_tree=KDTree(xy), nh_node_xy=xy, nh_comp_labels=labels)
    assert matrix._point_candidates(np.array([0., 0.]), graph) == {100: (0., 3), 999999999: (150000., 0)}
    origin = {999999999: (2., 1), 100: (2., 0)}
    dest = {999999999: (3., 0), 100: (3., 3)}
    expected = (0, 3, 2., 3., 100)
    for o in (origin, dict(reversed(list(origin.items())))):
        for d in (dest, dict(reversed(list(dest.items())))):
            selected = matrix._compatible_hubs(o, matrix._hub_components([d]), 1)
            assert matrix._selected_pair(selected, 0) == expected


def test_summary_contract_against_authoritative_headers():
    root = Path(__file__).resolve().parents[1]
    baseline = pd.read_csv(root / 'data/analysis/nh_district_travel_time_summary.csv', nrows=0)
    assert matrix.SUMMARY_COLUMNS == list(baseline.columns)
    ports = pd.read_csv(root / 'data/logistics_hubs/ports.csv')
    keys = [matrix._port_key(name) for name in ports.loc[ports.hub_type == 'major_port', 'name']]
    columns = matrix.IDENTITY_COLUMNS + [c for key in keys for c in (f'drive_hours_to_{key}', f'road_km_to_{key}')]
    actual = pd.read_csv(root / 'data/analysis/nh_district_port_matrix.csv', nrows=0)
    assert columns == list(actual.columns)


def test_cli_explicit_roots_and_no_second_writer(synthetic, tmp_path, monkeypatch):
    root, _, _ = synthetic
    output = tmp_path / 'output'
    monkeypatch.setattr('sys.argv', ['nh_travel_matrix.py', '--data-dir', str(root), '--cache-dir',
                                    str(tmp_path / 'cache'), '--output-dir', str(output)])
    matrix.main()
    assert sorted(p.name for p in output.iterdir()) == ['nh_district_port_matrix.csv',
        'nh_district_travel_time_summary.csv', 'nh_district_travel_time_summary.parquet']
    source = (Path(__file__).resolve().parents[1] / 'scripts/clean/export_geoparquet.py').read_text()
    assert 'nh_district_travel_time_summary' not in source


def test_cli_custom_root_default_output_is_not_repo(synthetic, tmp_path, monkeypatch):
    root, _, _ = synthetic
    monkeypatch.setattr('sys.argv', ['nh_travel_matrix.py', '--data-dir', str(root),
                                    '--cache-dir', str(tmp_path / 'cache')])
    matrix.main()
    assert (root / 'analysis/nh_district_travel_time_summary.parquet').is_file()
    assert (root / 'analysis/nh_district_travel_time_summary.csv').is_file()
    assert (root / 'analysis/nh_district_port_matrix.csv').is_file()


def test_pipeline_seeded_scalar_pair_kernel_oracle(synthetic, tmp_path):
    root, graph, _ = synthetic
    rng = np.random.default_rng(502)
    xy = graph.nh_node_xy[0] + rng.uniform(-20000, 20000, (45, 2))
    locations = gpd.GeoSeries([Point(p) for p in xy], crs=7755).to_crs(4326)
    frame = pd.DataFrame({'station_name': [f'R{i}' for i in range(len(xy))],
                          'latitude': locations.y, 'longitude': locations.x})
    frame.to_csv(root / 'rail/railway_stations.csv', index=False)
    summary, _, _ = matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache', chunk_size=1)
    districts = gpd.read_file(root / 'administrative/india_districts_lgd.geojson').to_crs(7755)
    actual_hubs = gpd.GeoSeries(gpd.points_from_xy(frame.longitude, frame.latitude), crs=4326).to_crs(7755)
    for pos, geom in enumerate(districts.geometry):
        if geom is None:
            continue
        point = np.array([geom.representative_point().x, geom.representative_point().y])
        oi, od = kernel.radius_candidates(point, graph)
        results = []
        for slot, target in enumerate(actual_hubs):
            hp = np.array([target.x, target.y])
            hi, hd = kernel.radius_candidates(hp, graph)
            pair = kernel.select_compatible_pair(od, oi, hd, hi, graph.nh_comp_labels)
            if pair is None:
                continue
            metric = kernel.pair_network_metrics(graph, [pair[:2]])[0]
            total = metric.network_time_hours + pair[2] / 1000 / 35 + pair[3] / 1000 / 35
            distance = metric.network_distance_km + pair[2] / 1000 + pair[3] / 1000
            results.append((total, slot, distance))
        if not results:
            assert pd.isna(summary.loc[pos, 'nearest_rail_station_name'])
        else:
            hours, slot, distance = min(results, key=lambda r: (r[0], r[1]))
            assert summary.loc[pos, 'nearest_rail_station_name'] == frame.station_name.iloc[slot]
            assert summary.loc[pos, 'rail_station_drive_time_hours'] == round(hours, 2)
            assert summary.loc[pos, 'rail_station_road_distance_km'] == round(distance, 1)
            assert summary.loc[pos, 'rail_station_drive_time_min'] == round(hours * 60, 1)


def test_geojson_fallback_actual_graph_cache_and_population_pair(synthetic, tmp_path, monkeypatch):
    from scripts.clean.graph_builder import load_or_build_cached_graph
    root, _, _ = synthetic
    path = root / 'roads/india_nh_network.parquet'
    nh = gpd.read_parquet(path)
    nh.to_file(root / 'roads/india_nh_network.geojson', driver='GeoJSON')
    path.unlink()
    monkeypatch.setattr(matrix, 'load_or_build_cached_graph', load_or_build_cached_graph)
    pd.DataFrame({'state': [' x '], 'district': [' MISSING '], 'district_code': [100.],
                  'pop_2011': [111.]}).to_csv(root / 'demographic/district_population_estimates.csv', index=False)
    sources = {p: matrix.sha256_file(p) for p in root.rglob('*') if p.is_file()}
    cache = tmp_path / 'only-cache'
    summary, ports, provenance = matrix.build_travel_outputs(data_dir=root, cache_dir=cache)
    assert (cache / 'canonical_nh_graph.npz').is_file()
    assert summary.loc[2, 'pop_2011'] == 111.
    assert np.isnan(summary.loc[0, 'pop_2011'])  # coded primary never name falls back
    assert 'roads/india_nh_network.geojson' in provenance['sources_sha256']
    assert all(matrix.sha256_file(p) == digest for p, digest in sources.items())
    again, again_ports, _ = matrix.build_travel_outputs(data_dir=root, cache_dir=cache)
    pd.testing.assert_frame_equal(summary, again)
    pd.testing.assert_frame_equal(ports, again_ports)


def test_absent_population_and_hubs_preserve_rows(synthetic, tmp_path):
    root, _, _ = synthetic
    (root / 'demographic/district_population_estimates.csv').unlink()
    (root / 'rail/railway_stations.csv').unlink()
    (root / 'logistics_hubs/ports.csv').unlink()
    summary, ports, _ = matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert summary.pop_2011.isna().all()
    assert summary.loc[:, matrix.SUMMARY_COLUMNS[7:]].isna().all().all()
    assert len(summary) == len(ports) == 4
    assert list(ports.columns) == matrix.IDENTITY_COLUMNS


def test_uncapped_access_but_radius_null_routes(synthetic, tmp_path):
    root, graph, _ = synthetic
    districts = gpd.read_file(root / 'administrative/india_districts_lgd.geojson')
    districts.loc[0, 'geometry'] = Point(graph.nh_node_xy[0] + [0, 500000])
    districts.to_file(root / 'administrative/india_districts_lgd.geojson', driver='GeoJSON')
    summary, _, _ = matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert summary.loc[0, 'highway_access_dist_km'] > 150
    assert not summary.loc[0, 'is_island']
    assert summary.loc[0, matrix.SUMMARY_COLUMNS[7:]].isna().all()


def test_invalid_graph_partition_fails_before_writers(synthetic, tmp_path, monkeypatch):
    root, graph, _ = synthetic
    labels = np.zeros(5, dtype=int)  # incorrectly declares isolated node connected
    monkeypatch.setattr(matrix, 'load_or_build_cached_graph', lambda nh, cache_dir: (
        graph.nh_graph, graph.nh_distance_graph, graph.nh_node_xy, labels, graph.nh_tree, graph.nh_bridge_mask))
    with pytest.raises(ValueError, match='component'):
        matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert not (root / 'analysis').exists()


def test_many_origins_time_csr_chunk_and_buffer_release(synthetic, tmp_path, monkeypatch):
    import weakref
    root, original_graph, _ = synthetic
    count = 70
    xy = original_graph.nh_node_xy[0] + np.column_stack((np.arange(count) * 1000., np.zeros(count)))
    rows = np.r_[np.arange(count - 1), np.arange(1, count)]
    cols = np.r_[np.arange(1, count), np.arange(count - 1)]
    gt = csr_matrix((np.full(len(rows), .02), (rows, cols)), shape=(count, count))
    gd = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(count, count))
    fields = (gt, gd, xy, np.zeros(count, dtype=int), KDTree(xy), csr_matrix((count, count)))
    monkeypatch.setattr(matrix, 'load_or_build_cached_graph', lambda nh, cache_dir: fields)
    districts = gpd.GeoDataFrame({'state': ['X'] * count, 'district': [f'D{i}' for i in range(count)],
                                  'district_code': np.arange(1, count + 1, dtype=float)},
                                 geometry=[Point(p) for p in xy], crs=7755)
    districts.to_file(root / 'administrative/india_districts_lgd.geojson', driver='GeoJSON')
    hub = gpd.GeoSeries([Point(xy[-1])], crs=7755).to_crs(4326).iloc[0]
    pd.DataFrame({'station_name': ['Last'], 'latitude': [hub.y], 'longitude': [hub.x]}).to_csv(
        root / 'rail/railway_stations.csv', index=False)
    (root / 'logistics_hubs/ports.csv').unlink()
    original = matrix.dijkstra
    buffers, calls = [], []

    def spy(csgraph, *, directed, indices):
        assert csgraph is gt and directed is False
        assert 1 <= len(indices) <= 32
        assert len(indices) == len(set(indices))
        assert all(ref() is None for ref in buffers)
        result = original(csgraph, directed=directed, indices=indices)
        assert result.shape == (len(indices), count)
        buffers.append(weakref.ref(result))
        calls.append(len(indices))
        return result

    monkeypatch.setattr(matrix, 'dijkstra', spy)
    original_kernel = kernel.dijkstra
    kernel_calls = []

    def kernel_spy(csgraph, *, directed, indices, return_predecessors):
        assert csgraph is gt and directed is False and return_predecessors
        assert len(indices) <= 32
        kernel_calls.append(len(indices))
        return original_kernel(csgraph, directed=directed, indices=indices, return_predecessors=True)

    monkeypatch.setattr(kernel, 'dijkstra', kernel_spy)
    summary, ports, _ = matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert calls == [32, 32, 6]
    assert kernel_calls == [32, 32, 5]  # same-snap final origin avoids path Dijkstra
    assert all(ref() is None for ref in buffers)
    assert len(summary) == 70 and list(ports.columns) == matrix.IDENTITY_COLUMNS
    assert summary.rail_station_drive_time_hours.iloc[0] == 1.38


def test_pass_time_disagreement_is_integrity_error(synthetic, tmp_path, monkeypatch):
    from dataclasses import replace
    root, _, _ = synthetic
    original = kernel.pair_network_metrics

    def corrupt(capture, pairs, *, chunk_size):
        values = original(capture, pairs, chunk_size=chunk_size)
        return [replace(v, network_time_hours=v.network_time_hours + .01) for v in values]

    monkeypatch.setattr(kernel, 'pair_network_metrics', corrupt)
    with pytest.raises(Exception) as caught:
        matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert type(caught.value).__name__ == 'DataIntegrityError'
    assert not (root / 'analysis').exists()


def test_exact_projected_point_zero_despite_nonzero_feeder(synthetic, tmp_path, monkeypatch):
    root, graph, _ = synthetic
    xy = graph.nh_node_xy[0] + [50., 0]
    point = gpd.GeoSeries([Point(xy)], crs=7755).to_crs(4326).iloc[0]
    pd.DataFrame({'name': ['Identical'], 'latitude': [point.y], 'longitude': [point.x]}).to_csv(
        root / 'logistics_hubs/icds.csv', index=False)
    projected = gpd.GeoSeries(gpd.points_from_xy([point.x], [point.y]), crs=4326).to_crs(7755).iloc[0]
    original_points = matrix._district_points

    def exact_points(districts):
        points = original_points(districts)
        points[0] = [projected.x, projected.y]
        return points

    monkeypatch.setattr(matrix, '_district_points', exact_points)
    summary, _, _ = matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert summary.loc[0, 'highway_access_time_min'] > 0
    for column in ('icd_straight_km', 'icd_road_distance_km', 'icd_drive_time_hours', 'icd_drive_time_min'):
        assert summary.loc[0, column] == 0.


def test_bridge_diagnostics_on_winner_path(synthetic, tmp_path, monkeypatch):
    root, graph, _ = synthetic
    xy = np.vstack((graph.nh_node_xy[0], graph.nh_node_xy[0] + [100., 0]))
    rows, cols = [0, 1], [1, 0]
    gt = csr_matrix(([.002, .002], (rows, cols)), shape=(2, 2))
    gd = csr_matrix(([.1, .1], (rows, cols)), shape=(2, 2))
    bridge = csr_matrix(([1., 1.], (rows, cols)), shape=(2, 2))
    fields = (gt, gd, xy, np.array([91, 91]), KDTree(xy), bridge)
    monkeypatch.setattr(matrix, 'load_or_build_cached_graph', lambda nh, cache_dir: fields)
    point = gpd.GeoSeries([Point(xy[1])], crs=7755).to_crs(4326).iloc[0]
    pd.DataFrame({'station_name': ['Bridge'], 'latitude': [point.y], 'longitude': [point.x]}).to_csv(
        root / 'rail/railway_stations.csv', index=False)
    summary, _, provenance = matrix.build_travel_outputs(data_dir=root, cache_dir=tmp_path / 'cache')
    assert summary.loc[0, 'rail_station_road_distance_km'] == .1
    diagnostics = [r for r in provenance['winner_and_port_diagnostics']
                   if r['district_row'] == 0 and r['kind'] == 'rail_station']
    assert len(diagnostics) == 1
    assert diagnostics[0]['synthetic_bridge_count'] == 1
    assert diagnostics[0]['synthetic_bridge_distance_km'] == .1
    assert diagnostics[0]['maximum_synthetic_bridge_m'] == 100.




