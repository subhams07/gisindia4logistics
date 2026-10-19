"""Unavailable identity sources differ from missing/non-routable identities."""
from types import SimpleNamespace

import pandas as pd
import pytest

from server.models.phase1 import DistrictLocation, HubLocation
from server.services.exceptions import DataIntegrityError, LocationNotFoundError
from server.services.location_resolver import LocationResolver


@pytest.mark.parametrize("source", [None, [], {}, pd.DataFrame(),
    pd.DataFrame(columns=["district_code", "district", "state"]),
    pd.DataFrame({"district": ["Pune"], "state": ["Maharashtra"]}),
    pd.DataFrame({"district_code": [521], "state": ["Maharashtra"]}),
    pd.DataFrame({"district_code": [521], "district": ["Pune"]})])
def test_unavailable_district_source(source, tmp_path):
    resolver = LocationResolver(data_dir=tmp_path)
    with pytest.raises(DataIntegrityError):
        resolver.resolve(DistrictLocation(district_code=521),
                         SimpleNamespace(data_dir=tmp_path, districts_df=source))


@pytest.mark.parametrize("kind,attribute,name_column", [
    ("port", "hubs_dict", "name"),
    ("rail_station", "rail_stations_df", "station_name"),
    ("freight_terminal", "freight_terminals_df", "terminal_name"),
])
@pytest.mark.parametrize("source", [None, [], {}, pd.DataFrame({"latitude": [22.]})])
def test_unavailable_facility_source(kind, attribute, name_column, source, tmp_path):
    resolver = LocationResolver(data_dir=tmp_path)
    store = SimpleNamespace(data_dir=tmp_path)
    setattr(store, attribute, {"ports": source} if attribute == "hubs_dict" else source)
    with pytest.raises(DataIntegrityError):
        resolver.resolve(HubLocation(hub_type=kind, name="Missing"), store)


@pytest.mark.parametrize("source", [pd.DataFrame(columns=["name"]),
    pd.DataFrame({"name": ["Other"], "latitude": [22.], "longitude": [78.]})])
@pytest.mark.parametrize("identity", [{"name": "Missing"}, {"code": "ABSENT"}])
def test_valid_uncoded_facility_source_missing_identity(source, identity, tmp_path):
    resolver = LocationResolver(data_dir=tmp_path)
    with pytest.raises(LocationNotFoundError):
        resolver.resolve(HubLocation(hub_type="port", **identity),
                         SimpleNamespace(data_dir=tmp_path, hubs_dict={"ports": source}))


def test_catalogued_missing_coordinates_retains_404(tmp_path):
    resolver = LocationResolver(data_dir=tmp_path)
    with pytest.raises(LocationNotFoundError):
        resolver.resolve(HubLocation(hub_type="port", name="Known"),
                         SimpleNamespace(data_dir=tmp_path,
                                         hubs_dict={"ports": pd.DataFrame({"name": ["Known"]})}))


@pytest.mark.parametrize("kind,column,attribute", [
    ("port", "name", "hubs_dict"),
    ("rail_station", "station_name", "rail_stations_df"),
    ("freight_terminal", "terminal_name", "freight_terminals_df"),
    ("freight_terminal", "name", "freight_terminals_df"),
])
def test_present_facility_without_optional_codes_resolves(kind, column, attribute, tmp_path):
    resolver = LocationResolver(data_dir=tmp_path)
    frame = pd.DataFrame({column: ["Known"], "latitude": [22.], "longitude": [78.]})
    store = SimpleNamespace(data_dir=tmp_path)
    setattr(store, attribute, {"ports": frame} if attribute == "hubs_dict" else frame)
    result = resolver.resolve(HubLocation(hub_type=kind, name="Known"), store)
    assert result.latitude == 22.
    assert result.longitude == 78.


def test_missing_boundary_point_retains_404_and_unknown_identity_404(tmp_path):
    resolver = LocationResolver(data_dir=tmp_path)
    store = SimpleNamespace(data_dir=tmp_path, districts_df=pd.DataFrame({
        "district_code": [521], "district": ["Pune"], "state": ["Maharashtra"]}))
    for code in [521, 999]:
        with pytest.raises(LocationNotFoundError):
            resolver.resolve(DistrictLocation(district_code=code), store)
