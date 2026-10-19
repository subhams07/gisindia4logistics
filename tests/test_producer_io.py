import math

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from scripts.analyze import producer_io as io


def test_population_identity_and_missing():
    districts = pd.DataFrame({"district_code": [1., 2., None, 9., None], "state": ["A", "B", "C", "A", "E"], "district": ["Same", "Same", "UNCODED", "Same", "Unknown"]}, index=[8, 2, 7, 4, 9])
    population = pd.DataFrame({"district_code": [2., 1., None], "state": ["B", "A", "c"], "district": ["Same", "Same", "uncoded"], "pop_2011": [200, 100, 300]})
    result = io.district_population(districts, population)
    assert result.index.tolist() == districts.index.tolist()
    assert result.iloc[:3].tolist() == [100., 200., 300.]
    assert result.iloc[3:].isna().all()


def test_duplicate_code_and_ambiguous_uncoded_fail():
    frame = pd.DataFrame({"district_code": [1, 1], "state": ["A", "A"], "district": ["X", "Y"], "pop_2011": [1, 2]})
    with pytest.raises(ValueError):
        io.district_population(frame.iloc[:1], frame)
    frame["district_code"] = None
    frame["state"] = "A"
    frame["district"] = "X"
    with pytest.raises(ValueError):
        io.district_population(frame.iloc[:1], frame)


def test_cross_state_code_collision_requires_state_not_name_fallback():
    population = pd.DataFrame({"district_code": [598., 598.], "state": ["Kerala", "Maharashtra"],
                               "district": ["Alappuzha", "DHULE"], "pop_2011": [2103830, 2039162]})
    districts = pd.DataFrame({"district_code": [598., 598., 598.],
                             "state": [" maharashtra ", "KERALA", "Other"],
                             "district": ["DHULE", "Alappuzha", "DHULE"]})
    result = io.district_population(districts, population)
    assert result.iloc[:2].tolist() == [2039162., 2103830.]
    assert math.isnan(result.iloc[2])
    assert districts.district_code.tolist() == [598., 598., 598.]


def test_primary_cross_state_collision_with_partial_population_stays_missing():
    population = pd.DataFrame({"district_code": [598.], "state": ["Kerala"],
                               "district": ["Alappuzha"], "pop_2011": [2103830]})
    districts = pd.DataFrame({"district_code": [598., 598.], "state": ["Kerala", "Maharashtra"],
                             "district": ["Alappuzha", "DHULE"]})
    result = io.district_population(districts, population)
    assert result.iloc[0] == 2103830.
    assert math.isnan(result.iloc[1])


def test_unique_code_keeps_legacy_authority_and_ambiguous_code_never_falls_back():
    population = pd.DataFrame({"district_code": [1., 598., 598., 700.],
                               "state": ["A", "Kerala", "Maharashtra", "Goa"],
                               "district": ["X", "Alappuzha", "DHULE", "North Goa"],
                               "pop_2011": [100, 2103830, 2039162, 999]})
    districts = pd.DataFrame({"district_code": [1., 598.], "state": ["Z", "Goa"],
                             "district": ["Y", "North Goa"]})
    result = io.district_population(districts, population)
    assert result.iloc[0] == 100.
    assert math.isnan(result.iloc[1])


def test_same_state_primary_code_collision_fails_without_population_reuse():
    districts = pd.DataFrame({"district_code": [598., 598.], "state": ["Kerala", " kerala "],
                             "district": ["Alappuzha", "Other"]})
    population = districts.iloc[:1].assign(pop_2011=2103830)
    with pytest.raises(ValueError, match="Duplicate primary district code"):
        io.district_population(districts, population)


def test_real_population_collision_preserves_both_districts_and_totals():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "data"
    districts = gpd.read_file(root / "administrative/india_districts_lgd.geojson")
    population = pd.read_csv(root / "demographic/district_population_estimates.csv")
    result = io.district_population(districts, population)
    # State/name alone also collides: the source has DHULE with codes 498 and 598.
    def identity(row):
        return row.state, row.district, None if pd.isna(row.district_code) else row.district_code
    expected = {identity(row): float(row.pop_2011) for row in population.itertuples()}
    assert len(expected) == len(population)
    assert len({identity(row) for row in districts.itertuples()}) == len(population) == 781
    assert result.tolist() == [expected[identity(row)] for row in districts.itertuples()]
    assert result.sum() == population.pop_2011.sum()
    assert len(result) == len(districts) == 781


@pytest.mark.parametrize("value", [True, "100", -1, float("inf")])
def test_corrupt_population_fails(value):
    frame = pd.DataFrame({"district_code": [1], "state": ["A"], "district": ["X"], "pop_2011": pd.Series([value], dtype=object)})
    with pytest.raises(ValueError):
        io.district_population(frame, frame)


@pytest.mark.parametrize("value", [True, "1", 0, -1, .5, float("inf")])
def test_corrupt_code_fails(value):
    frame = pd.DataFrame({"district_code": pd.Series([value], dtype=object), "state": ["A"], "district": ["X"], "pop_2011": [1]})
    with pytest.raises(ValueError):
        io.district_population(frame, frame)


@pytest.mark.parametrize("value", [None, pd.NA, float("nan")])
def test_missing_population_stays_null(value):
    frame = pd.DataFrame({"district_code": [1], "state": ["A"], "district": ["X"], "pop_2011": pd.Series([value], dtype=object)})
    assert math.isnan(io.district_population(frame, frame).iloc[0])


def test_geographic_islands_not_unreachability():
    assert io.is_island_state(" Andaman and Nicobar Islands ")
    assert io.is_island_state("LAKSHADWEEP")
    assert not io.is_island_state("Assam")
    assert not io.is_island_state(None)


def test_roundtrip_all_writers(tmp_path):
    frame = pd.DataFrame({"id": [1, 2], "value": [2.5, float("nan")]})
    csv, parquet, geojson = (tmp_path / name for name in ("a.csv", "a.parquet", "a.geojson"))
    io.atomic_write_csv(frame, csv)
    io.atomic_write_parquet(frame, parquet)
    pd.testing.assert_frame_equal(pd.read_csv(csv), pd.read_parquet(parquet))
    geo = gpd.GeoDataFrame(frame, geometry=[Point(77, 28), None], crs="EPSG:4326")
    io.atomic_write_geojson(geo, geojson)
    loaded = gpd.read_file(geojson)
    assert len(loaded) == 2 and loaded.geometry.iloc[1] is None
    assert len(io.sha256_file(csv)) == 64
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.csv", "a.geojson", "a.parquet"]


@pytest.mark.parametrize("writer", [io.atomic_write_csv, io.atomic_write_parquet, io.atomic_write_geojson])
def test_publication_failure_preserves_old_target(tmp_path, monkeypatch, writer):
    output = tmp_path / "out.dat"
    output.write_bytes(b"prior")
    frame = gpd.GeoDataFrame({"id": [1]}, geometry=[Point(77, 28)], crs="EPSG:4326")
    def fail(*args, **kwargs):
        raise PermissionError("locked target")
    monkeypatch.setattr(io.os, "replace", fail)
    with pytest.raises(PermissionError):
        writer(frame, output)
    assert output.read_bytes() == b"prior"
    assert list(tmp_path.iterdir()) == [output]
