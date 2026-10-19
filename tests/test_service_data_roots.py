"""Configured roots must scope aliases, boundary coordinates and provenance."""
import json
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import Point

from server.config import settings
from server.models.phase1 import CoordinateLocation, DistrictLocation, HubLocation
from server.services.exceptions import LocationNotFoundError
from server.services.location_resolver import LocationResolver, get_location_resolver
from server.services.metadata_service import MetadataService, get_metadata_service


@pytest.fixture
def roots(tmp_path):
    roots = [tmp_path / name for name in ("a", "b")]
    for root, lon in zip(roots, (80, 85)):
        (root / "aliases").mkdir(parents=True)
        (root / "administrative").mkdir()
        (root / "aliases/location_aliases.yaml").write_text(yaml.safe_dump({
            "Trade": {"type": "port", "canonical_name": root.name}}), encoding="utf-8")
        (root / "version_manifest.json").write_text(json.dumps({
            "data_version": root.name, "api_version": "1.0.0", "model_version": "test",
            "datasets": {"national_highway_network": {"vintage": root.name}}}), encoding="utf-8")
        rows = [dict(district="Example", state="Odisha", district_code=1)]
        gpd.GeoDataFrame(rows, geometry=[Point(lon, 20)], crs=4326).to_file(
            root / "administrative/india_districts_lgd.geojson", driver="GeoJSON")
    return roots


def store(root):
    return SimpleNamespace(data_dir=root, districts_df=pd.DataFrame([
        dict(district="Example", state="Odisha", district_code=1)]),
        hubs_dict={"ports": pd.DataFrame([dict(name=root.name, latitude=20, longitude=80)])})


def test_configured_singletons_follow_root_and_back(monkeypatch, roots):
    monkeypatch.setattr(LocationResolver, "_instance", None)
    monkeypatch.setattr(MetadataService, "_instance", None)
    for root, lon in zip([roots[0], roots[1], roots[0]], (80, 85, 80)):
        monkeypatch.setattr(settings, "DATA_PATH", root)
        resolver = get_location_resolver()
        assert resolver.resolve(HubLocation(hub_type="port", name="Trade"), store(root)).canonical_name == root.name
        assert resolver.resolve(DistrictLocation(district_code=1), store(root)).longitude == lon
        meta = get_metadata_service().get_metadata()
        assert meta.data_version == root.name
        assert meta.road_network_vintage == root.name


def test_existing_resolver_honors_actual_store_root_and_back(monkeypatch, roots):
    monkeypatch.setattr(settings, "DATA_PATH", roots[0])
    resolver = LocationResolver()
    for root, lon in zip([roots[0], roots[1], roots[0]], (80, 85, 80)):
        assert resolver.resolve(DistrictLocation(district_code=1), store(root)).longitude == lon
        assert resolver.resolve(HubLocation(hub_type="port", name="Trade"), store(root)).canonical_name == root.name


def test_missing_custom_root_does_not_use_repo_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATA_PATH", tmp_path)
    resolver = LocationResolver()
    custom = store(tmp_path)
    custom.districts_df = pd.DataFrame([dict(district="Pune", state="Maharashtra", district_code=521)])
    custom.hubs_dict = {"ports": pd.DataFrame([dict(name="Jawaharlal Nehru Port (JNPT/Navi Mumbai)", latitude=19, longitude=73)])}
    with pytest.raises(LocationNotFoundError):
        resolver.resolve(HubLocation(hub_type="port", name="JNPT"), custom)
    with pytest.raises(LocationNotFoundError):
        resolver.resolve(DistrictLocation(district_code=521), custom)
    meta = MetadataService().get_metadata()
    assert meta.data_version == "unknown"
    assert meta.road_network_vintage is None


def test_coordinates_never_read_boundaries(monkeypatch, roots):
    monkeypatch.setattr(settings, "DATA_PATH", roots[0])
    def fail(*args, **kwargs):
        pytest.fail("coordinate resolution loaded boundary layer")
    monkeypatch.setattr(gpd, "read_file", fail)
    result = LocationResolver().resolve(CoordinateLocation(latitude=20, longitude=80), store(roots[1]))
    assert result.longitude == 80


def test_explicit_paths_remain_overrides(monkeypatch, roots):
    monkeypatch.setattr(settings, "DATA_PATH", roots[1])
    resolver = LocationResolver(aliases_path=roots[0] / "aliases/location_aliases.yaml")
    custom = store(roots[1])
    custom.hubs_dict = store(roots[0]).hubs_dict
    assert resolver.resolve(HubLocation(hub_type="port", name="Trade"), custom).canonical_name == "a"
    assert resolver.resolve(DistrictLocation(district_code=1), custom).longitude == 85
    svc = MetadataService(manifest_path=roots[0] / "version_manifest.json")
    assert svc.data_version == "a"
    assert MetadataService.get_instance(roots[0] / "version_manifest.json").data_version == "a"
    assert MetadataService.get_instance(roots[1] / "version_manifest.json").data_version == "b"


@pytest.mark.parametrize("body", ["{invalid", "[]", '{"datasets": []}', '{"legal_compliance": []}'])
def test_malformed_manifest_never_claims_repo_vintage(tmp_path, body):
    path = tmp_path / "version_manifest.json"
    path.write_text(body, encoding="utf-8")
    meta = MetadataService(manifest_path=path).get_metadata()
    assert meta.data_version == "unknown"
    assert meta.road_network_vintage is None


@pytest.mark.parametrize("field", ["data_version", "api_version", "model_version"])
@pytest.mark.parametrize("value", [None, True, 2026.10, ["2026.08"], {}, "", "  "])
def test_invalid_manifest_version_scalars_degrade_without_invented_evidence(tmp_path, field, value):
    path = tmp_path / "version_manifest.json"
    body = {"data_version": "example", field: value,
            "datasets": {"national_highway_network": {"vintage": "2020"}}}
    path.write_text(json.dumps(body), encoding="utf-8")
    meta = MetadataService(manifest_path=path).get_metadata()
    assert meta.data_version == "unknown"
    assert meta.road_network_vintage is None
    assert meta.port_capacity_vintage is None
    assert meta.population_vintage is None
    assert any("provenance is unavailable or invalid" in text for text in meta.limitations)


@pytest.mark.parametrize("field", ["vintage", "disclaimer", "assumptions_url", "sources_url"])
@pytest.mark.parametrize("value", [True, 2026, ["text"], {}, "", "  "])
def test_invalid_manifest_provenance_scalars_degrade_without_crashing(tmp_path, field, value):
    body = {"data_version": "example", "datasets": {}, "legal_compliance": {}}
    if field == "vintage":
        body["datasets"]["national_highway_network"] = {"vintage": value}
    else:
        body["legal_compliance"][field] = value
    path = tmp_path / "version_manifest.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    meta = MetadataService(manifest_path=path).get_metadata()
    assert meta.data_version == "unknown"
    assert meta.road_network_vintage is None
    assert any("provenance is unavailable or invalid" in text for text in meta.limitations)


@pytest.mark.parametrize("field", ["disclaimer", "assumptions_url", "sources_url"])
def test_null_legal_manifest_scalar_is_invalid(tmp_path, field):
    path = tmp_path / "version_manifest.json"
    path.write_text(json.dumps({"data_version": "example", "legal_compliance": {field: None}}), encoding="utf-8")
    meta = MetadataService(manifest_path=path).get_metadata()
    assert meta.data_version == "unknown"
    assert meta.road_network_vintage is None


def test_absent_data_version_withholds_vintages_consistently(tmp_path):
    path = tmp_path / "version_manifest.json"
    path.write_text(json.dumps({"datasets": {
        "national_highway_network": {"vintage": "2020"},
        "port_throughput_annual": {"vintage": "2024"},
        "district_population_estimates": {"vintage": None}}}), encoding="utf-8")
    meta = MetadataService(manifest_path=path).get_metadata()
    assert meta.data_version == "unknown"
    assert meta.road_network_vintage is None
    assert meta.port_capacity_vintage is None
    assert meta.population_vintage is None
