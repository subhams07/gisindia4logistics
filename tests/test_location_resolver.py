"""
tests/test_location_resolver.py
Unit tests for deterministic location resolution, alias matching, and ambiguity handling.
"""

import pytest
from server.dependencies import DataStore
from server.models.phase1 import (
    CoordinateLocation,
    DistrictLocation,
    HubLocation,
    ResolvedLocation,
)
from server.services.location_resolver import LocationResolver
from server.services.exceptions import (
    LocationNotFoundError,
    AmbiguousLocationError,
    InvalidLocationError,
)


@pytest.fixture(scope="module")
def store():
    """Initializes and returns the singleton DataStore instance."""
    return DataStore.get_instance()


@pytest.fixture(scope="module")
def resolver():
    """Returns the singleton LocationResolver instance."""
    return LocationResolver.get_instance()


def test_resolve_coordinate_valid(resolver, store):
    """Verifies that valid coordinates resolve cleanly."""
    coord = CoordinateLocation(latitude=18.5204, longitude=73.8567, label="Pune Node")
    res = resolver.resolve(coord, store)
    assert isinstance(res, ResolvedLocation)
    assert res.type == "coordinate"
    assert res.latitude == 18.5204
    assert res.longitude == 73.8567
    assert res.match_method == "coordinate"
    assert res.canonical_name == "Pune Node"


def test_resolve_coordinate_invalid_bounds(resolver, store):
    """Verifies that out-of-bounds coordinates raise error."""
    with pytest.raises(Exception):
        CoordinateLocation(latitude=95.0, longitude=73.8567)


def test_resolve_district_by_exact_code(resolver, store):
    """Verifies resolving district by official LGD district_code."""
    dist = DistrictLocation(district_code=521)  # Pune LGD code
    res = resolver.resolve(dist, store)
    assert res.type == "district"
    assert res.district == "Pune"
    assert res.state == "Maharashtra"
    assert res.district_code == 521
    assert res.match_method == "exact_code"


def test_resolve_district_by_state_and_name(resolver, store):
    """Verifies resolving district by state and district name."""
    dist = DistrictLocation(district="Indore", state="Madhya Pradesh")
    res = resolver.resolve(dist, store)
    assert res.district == "Indore"
    assert res.state == "Madhya Pradesh"
    assert res.match_method == "exact_name"


def test_resolve_unique_district_without_state(resolver, store):
    """Verifies that unique district names resolve without state specification."""
    dist = DistrictLocation(district="Pune")
    res = resolver.resolve(dist, store)
    assert res.district == "Pune"
    assert res.state == "Maharashtra"
    assert res.match_method == "exact_name"


def test_resolve_ambiguous_duplicate_district_raises_error(resolver, store):
    """Verifies that duplicate district names across states raise AmbiguousLocationError."""
    # Bilaspur exists in both Chhattisgarh and Himachal Pradesh
    dist = DistrictLocation(district="Bilaspur")
    with pytest.raises(AmbiguousLocationError) as exc_info:
        resolver.resolve(dist, store)
    err = exc_info.value
    assert len(err.candidates) >= 2
    states = [c["state"].lower() for c in err.candidates]
    assert any("chhattisgarh" in s for s in states)
    assert any("himachal pradesh" in s for s in states)

    # Disambiguated by state resolves cleanly
    dist_disambiguated = DistrictLocation(district="Bilaspur", state="Chhattisgarh")
    res = resolver.resolve(dist_disambiguated, store)
    assert res.district == "Bilaspur"
    assert "chhattisgarh" in res.state.lower()


def test_resolve_non_existent_district_raises_not_found(resolver, store):
    """Verifies that unknown district names raise LocationNotFoundError."""
    dist = DistrictLocation(district="NonExistentFictionalDistrict")
    with pytest.raises(LocationNotFoundError):
        resolver.resolve(dist, store)


def test_resolve_hub_by_alias(resolver, store):
    """Verifies resolving hubs via trade aliases (e.g. JNPT, Kandla, Dadri ICD)."""
    # 1. JNPT
    hub_jnpt = HubLocation(hub_type="port", name="JNPT")
    res_jnpt = resolver.resolve(hub_jnpt, store)
    assert res_jnpt.type == "hub"
    assert "Jawaharlal Nehru Port" in res_jnpt.canonical_name
    assert res_jnpt.match_method == "alias_lookup"
    assert 18.0 <= res_jnpt.latitude <= 19.5
    assert 72.0 <= res_jnpt.longitude <= 73.5

    # 2. Kandla
    hub_kandla = HubLocation(hub_type="port", name="Kandla")
    res_kandla = resolver.resolve(hub_kandla, store)
    assert "Deendayal Port" in res_kandla.canonical_name

    # 3. Dadri ICD
    hub_dadri = HubLocation(hub_type="icd", name="Dadri ICD")
    res_dadri = resolver.resolve(hub_dadri, store)
    assert "Dadri" in res_dadri.canonical_name


def test_resolve_railway_station_by_code(resolver, store):
    """Verifies resolving railway stations by official IR station code."""
    stn = HubLocation(hub_type="rail_station", code="PUNE")
    res = resolver.resolve(stn, store)
    assert res.type == "hub"
    assert "PUNE" in res.canonical_name
    assert res.match_method == "exact_code"
    assert 18.0 <= res.latitude <= 19.0


def test_resolve_unknown_hub_raises_not_found(resolver, store):
    """Verifies that non-existent hub names raise LocationNotFoundError."""
    hub = HubLocation(hub_type="port", name="NonExistentFictionalPortXYZ")
    with pytest.raises(LocationNotFoundError):
        resolver.resolve(hub, store)
