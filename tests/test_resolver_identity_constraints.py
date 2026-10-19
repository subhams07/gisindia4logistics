"""Shared resolver identity constraints, including committed data regressions."""
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from server.models.phase1 import DistrictLocation, HubLocation
from server.services.exceptions import (
    AmbiguousLocationError, InvalidLocationError, LocationNotFoundError,
)
from server.services.location_resolver import LocationResolver

ROOT = Path(__file__).resolve().parents[1] / "data"


def district_store(tmp_path, rows):
    return SimpleNamespace(data_dir=tmp_path, districts_df=pd.DataFrame(rows))


@pytest.mark.parametrize("query", [
    dict(district_code=521, district="Indore"),
    dict(district_code=521, district="Unknown"),
    dict(district_code=521, state="Kerala"),
    dict(district="Pune", state="Kerala"),
])
def test_conflicting_district_constraints(tmp_path, query):
    store = district_store(tmp_path, [dict(district="Pune", state="Maharashtra", district_code=521)])
    with pytest.raises(InvalidLocationError):
        LocationResolver().resolve(DistrictLocation(**query), store)


@pytest.mark.parametrize("query", [dict(district_code=999), dict(district="Unknown")])
def test_unknown_district_is_not_found(tmp_path, query):
    store = district_store(tmp_path, [dict(district="Pune", state="Maharashtra", district_code=521)])
    with pytest.raises(LocationNotFoundError):
        LocationResolver().resolve(DistrictLocation(**query), store)


@pytest.mark.parametrize("query", [
    dict(district="Twin", state="Odisha"),
    dict(district_code=1, state="Odisha"),
    dict(district_code=1, district="Twin", state="Odisha"),
])
def test_same_state_duplicate_rows_are_ambiguous(tmp_path, query):
    store = district_store(tmp_path, [dict(district="Twin", state="Odisha", district_code=1)] * 2)
    with pytest.raises(AmbiguousLocationError) as exc:
        LocationResolver().resolve(DistrictLocation(**query), store)
    assert len(exc.value.candidates) == 2


def test_state_alias_and_punctuation_and_name_disambiguate_code(tmp_path):
    rows = [dict(district="A.B.", state="Odisha", district_code=1),
            dict(district="Other", state="Odisha", district_code=2)]
    (tmp_path / "administrative").mkdir()
    gpd.GeoDataFrame(rows, geometry=[Point(85, 20), Point(86, 21)], crs=4326).to_file(
        tmp_path / "administrative/india_districts_lgd.geojson", driver="GeoJSON")
    store = district_store(tmp_path, rows[:1])
    result = LocationResolver().resolve(DistrictLocation(district="AB", state="Orissa", district_code=1), store)
    assert result.district == "A.B."
    assert result.state == "Odisha"
    assert result.longitude == 85


def hub_store(tmp_path, rows, kind="rail_station"):
    df = pd.DataFrame(rows)
    return SimpleNamespace(data_dir=tmp_path, rail_stations_df=df if kind == "rail_station" else None,
                           freight_terminals_df=df if kind == "freight_terminal" else None,
                           hubs_dict={"ports": df} if kind == "port" else {})


@pytest.mark.parametrize("kind,name_col,code_col", [
    ("rail_station", "station_name", "station_code"),
    ("freight_terminal", "terminal_name", "matched_station_code"),
    ("freight_terminal", "name", "station_code"),
    ("port", "name", "code"),
])
def test_hub_all_identifiers_constrain_same_entity(tmp_path, kind, name_col, code_col):
    rows = [{name_col: "Alpha", code_col: "AAA", "latitude": 20, "longitude": 80},
            {name_col: "Beta", code_col: "BBB", "latitude": 21, "longitude": 81}]
    store = hub_store(tmp_path, rows, kind)
    resolver = LocationResolver()
    with pytest.raises(InvalidLocationError):
        resolver.resolve(HubLocation(hub_type=kind, code="AAA", name="Beta"), store)
    with pytest.raises(LocationNotFoundError):
        resolver.resolve(HubLocation(hub_type=kind, code="UNKNOWN", name="Alpha"), store)
    result = resolver.resolve(HubLocation(hub_type=kind, code="AAA", name="Alpha"), store)
    assert result.latitude == 20


@pytest.mark.parametrize("query", [dict(code="AAA"), dict(name="Alpha"), dict(code="AAA", name="Alpha")])
def test_duplicate_hubs_are_ambiguous(tmp_path, query):
    row = dict(station_name="Alpha", station_code="AAA", latitude=20, longitude=80)
    store = hub_store(tmp_path, [row, row])
    with pytest.raises(AmbiguousLocationError) as exc:
        LocationResolver().resolve(HubLocation(hub_type="rail_station", **query), store)
    assert len(exc.value.candidates) == 2


def test_real_port_alias_code_constraints():
    store = hub_store(ROOT, pd.read_csv(ROOT / "logistics_hubs/ports.csv").to_dict("records"), "port")
    resolver = LocationResolver()
    result = resolver.resolve(HubLocation(hub_type="port", name="JNPT", code="INNSA"), store)
    assert "Jawaharlal" in result.canonical_name
    with pytest.raises(InvalidLocationError):
        resolver.resolve(HubLocation(hub_type="port", name="Kandla", code="INNSA"), store)
    with pytest.raises(LocationNotFoundError):
        resolver.resolve(HubLocation(hub_type="port", name="JNPT", code="UNKNOWN"), store)


def test_real_dhule_is_a_single_keyed_boundary_point():
    store = district_store(ROOT, pd.read_csv(ROOT / "demographic/district_population_estimates.csv").to_dict("records"))
    resolver = LocationResolver()
    by_code = resolver.resolve(DistrictLocation(district_code=498, district="Dhule", state="Maharashtra"), store)
    by_name = resolver.resolve(DistrictLocation(district="Dhule", state="Maharashtra"), store)
    boundary = gpd.read_file(ROOT / "administrative/india_districts_lgd.geojson")
    row = boundary[(boundary.district.str.upper() == "DHULE") & (boundary.state.str.lower() == "maharashtra")]
    assert row.district_code.tolist() == [498]
    point = row.to_crs(7755).geometry.representative_point().to_crs(4326).iloc[0]
    for result in (by_code, by_name):
        assert result.district_code == 498
        assert (result.latitude, result.longitude) == (round(point.y, 5), round(point.x, 5))
    with pytest.raises(InvalidLocationError):
        resolver.resolve(DistrictLocation(district_code=598, district="Dhule", state="Maharashtra"), store)


@pytest.mark.parametrize("code", [None, 1])
def test_boundary_duplicates_never_supply_arbitrary_points(tmp_path, code):
    rows = [dict(district="Twin", state="Odisha", district_code=1)] * 2
    (tmp_path / "administrative").mkdir()
    gpd.GeoDataFrame(rows, geometry=[Point(80, 20), Point(85, 21)], crs=4326).to_file(
        tmp_path / "administrative/india_districts_lgd.geojson", driver="GeoJSON")
    resolver = LocationResolver(data_dir=tmp_path)
    with pytest.raises(LocationNotFoundError):
        resolver._get_district_centroid(code, "Orissa", "Twin")


def test_unique_name_only_boundary_point_and_unknown_code(tmp_path):
    rows = [dict(district="Unique", state="Odisha", district_code=1)]
    (tmp_path / "administrative").mkdir()
    gpd.GeoDataFrame(rows, geometry=[Point(80, 20)], crs=4326).to_file(
        tmp_path / "administrative/india_districts_lgd.geojson", driver="GeoJSON")
    resolver = LocationResolver(data_dir=tmp_path)
    assert resolver._get_district_centroid(None, "Orissa", "Unique") == (20, 80)
    with pytest.raises(LocationNotFoundError):
        resolver._get_district_centroid(999, "Orissa", "Unique")


def test_name_can_disambiguate_duplicate_codes(tmp_path):
    rows = [dict(station_name="Alpha", station_code="AAA", latitude=20, longitude=80),
            dict(station_name="Beta", station_code="AAA", latitude=21, longitude=81)]
    store = hub_store(tmp_path, rows)
    result = LocationResolver().resolve(HubLocation(hub_type="rail_station", code="AAA", name="Beta"), store)
    assert result.latitude == 21


def test_district_name_can_disambiguate_cross_state_code():
    store = district_store(ROOT, pd.read_csv(ROOT / "demographic/district_population_estimates.csv").to_dict("records"))
    result = LocationResolver().resolve(DistrictLocation(district_code=598, district="Alappuzha"), store)
    assert result.state == "Kerala"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), None])
def test_unroutable_terminal_coordinates_are_not_found(tmp_path, value):
    store = hub_store(tmp_path, [dict(name="Terminal", station_code="AAA", latitude=value, longitude=80)], "freight_terminal")
    with pytest.raises(LocationNotFoundError):
        LocationResolver().resolve(HubLocation(hub_type="freight_terminal", code="AAA"), store)


def test_registry_code_does_not_override_contradictory_source_code(tmp_path):
    aliases = tmp_path / "aliases.yaml"
    aliases.write_text("Trade:\n  type: port\n  canonical_name: Alpha\n  code: AAA\n", encoding="utf-8")
    resolver = LocationResolver(aliases)
    store = hub_store(tmp_path, [dict(name="Alpha", code="BBB", latitude=20, longitude=80)], "port")
    with pytest.raises(LocationNotFoundError):
        resolver.resolve(HubLocation(hub_type="port", code="AAA"), store)
    with pytest.raises(InvalidLocationError):
        resolver.resolve(HubLocation(hub_type="port", code="BBB", name="Trade"), store)


def test_punctuation_cleanup_is_scoped_after_state_constraint(tmp_path):
    rows = [dict(district="AB", state="Kerala", district_code=1),
            dict(district="A.B.", state="Odisha", district_code=2)]
    (tmp_path / "administrative").mkdir()
    gpd.GeoDataFrame(rows, geometry=[Point(80, 20), Point(85, 21)], crs=4326).to_file(
        tmp_path / "administrative/india_districts_lgd.geojson", driver="GeoJSON")
    result = LocationResolver().resolve(DistrictLocation(district="AB", state="Orissa"), district_store(tmp_path, rows))
    assert result.district_code == 2


@pytest.mark.parametrize("name,code", [
    ("Dadri", "DER"), ("Ennore", "ENR"), ("Haldia", "HLZ"),
    ("Karaikal", "KIK"), ("Whitefield", "WFD"),
])
@pytest.mark.parametrize("include_code", [False, True])
def test_real_station_name_beats_foreign_category_alias(name, code, include_code):
    store = SimpleNamespace(data_dir=ROOT,
                            rail_stations_df=pd.read_csv(ROOT / "rail/railway_stations.csv"))
    query = HubLocation(hub_type="rail_station", name=name,
                        code=code if include_code else None)
    result = LocationResolver().resolve(query, store)
    source = store.rail_stations_df[store.rail_stations_df.station_code == code]
    assert len(source) == 1
    assert result.canonical_name == f"{source.iloc[0].station_name} ({code})"
    assert result.match_method == ("exact_code" if include_code else "exact_name")


@pytest.mark.parametrize("include_name", [False, True])
def test_literal_source_code_beats_foreign_category_alias(tmp_path, include_name):
    aliases = tmp_path / "aliases.yaml"
    aliases.write_text("Trade:\n  type: port\n  canonical_name: Foreign\n  code: DER\n", encoding="utf-8")
    store = hub_store(tmp_path, [dict(station_name="DADRI", station_code="DER",
                                   latitude=20, longitude=80)])
    result = LocationResolver(aliases).resolve(HubLocation(
        hub_type="rail_station", code="DER", name="Dadri" if include_name else None), store)
    assert result.canonical_name == "DADRI (DER)"
