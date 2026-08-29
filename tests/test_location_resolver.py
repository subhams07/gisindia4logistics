"""
tests/test_location_resolver.py
Unit and regression tests for deterministic location resolution, alias matching, and ambiguity handling.
"""

from pathlib import Path
import pytest
import yaml

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

REPO_ROOT = Path(__file__).resolve().parents[1]
ALIASES_PATH = REPO_ROOT / "data" / "aliases" / "location_aliases.yaml"


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


def test_resolve_district_by_exact_unique_code(resolver, store):
    """Verifies resolving district by unique official LGD district_code."""
    dist = DistrictLocation(district_code=521)  # Pune
    res = resolver.resolve(dist, store)
    assert res.type == "district"
    assert res.district == "Pune"
    assert res.state == "Maharashtra"
    assert res.district_code == 521
    assert res.match_method == "exact_code"
    assert 18.0 <= res.latitude <= 19.5
    assert 73.5 <= res.longitude <= 75.0


def test_resolve_duplicate_district_code_raises_ambiguity_or_resolves_with_state(resolver, store):
    """Verifies that duplicate district code 598 raises AmbiguousLocationError when state is omitted, and resolves with state."""
    # Code 598 is shared by Alappuzha (Kerala) and Dhule (Maharashtra)
    dist_ambiguous = DistrictLocation(district_code=598)
    with pytest.raises(AmbiguousLocationError) as exc_info:
        resolver.resolve(dist_ambiguous, store)
    err = exc_info.value
    assert len(err.candidates) >= 2
    states = [c["state"].lower() for c in err.candidates]
    assert "kerala" in states
    assert "maharashtra" in states

    # Disambiguated by Kerala
    dist_kerala = DistrictLocation(district_code=598, state="Kerala")
    res_kerala = resolver.resolve(dist_kerala, store)
    assert res_kerala.district.lower() == "alappuzha"
    assert res_kerala.state.lower() == "kerala"
    assert 9.0 <= res_kerala.latitude <= 10.0
    assert 76.0 <= res_kerala.longitude <= 77.0

    # Disambiguated by Maharashtra
    dist_mh = DistrictLocation(district_code=598, state="Maharashtra")
    res_mh = resolver.resolve(dist_mh, store)
    assert res_mh.district.lower() == "dhule"
    assert res_mh.state.lower() == "maharashtra"
    assert 20.0 <= res_mh.latitude <= 22.0
    assert 74.0 <= res_mh.longitude <= 75.5


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


def test_resolve_ambiguous_duplicate_district_name_raises_error(resolver, store):
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
    assert res_jnpt.district is None  # Ensures city is not mislabeled as district

    # 2. Kandla
    hub_kandla = HubLocation(hub_type="port", name="Kandla")
    res_kandla = resolver.resolve(hub_kandla, store)
    assert "Deendayal Port" in res_kandla.canonical_name

    # 3. Dadri ICD
    hub_dadri = HubLocation(hub_type="icd", name="Dadri ICD")
    res_dadri = resolver.resolve(hub_dadri, store)
    assert "Dadri" in res_dadri.canonical_name


def test_resolve_hub_by_code(resolver, store):
    """Verifies resolving non-rail and rail hubs by explicit code."""
    # Port UN/LOCODE
    hub_port_code = HubLocation(hub_type="port", code="INNSA")
    res_port = resolver.resolve(hub_port_code, store)
    assert "Jawaharlal Nehru Port" in res_port.canonical_name
    assert res_port.match_method == "exact_code"

    # Rail station code
    hub_stn = HubLocation(hub_type="rail_station", code="PUNE")
    res_stn = resolver.resolve(hub_stn, store)
    assert "PUNE" in res_stn.canonical_name
    assert res_stn.match_method == "exact_code"


def test_resolve_freight_terminal_by_exact_name(resolver, store):
    """Verifies resolving a Gati Shakti freight terminal by exact name."""
    hub = HubLocation(hub_type="freight_terminal", name="Kamalajari")
    res = resolver.resolve(hub, store)
    assert res.canonical_name == "Kamalajari"
    assert res.match_method == "exact_name"
    assert res.latitude is not None and 20.0 <= res.latitude <= 21.0
    assert res.longitude is not None and 85.0 <= res.longitude <= 86.0


def test_resolve_freight_terminal_by_station_code(resolver, store):
    """Verifies resolving a Gati Shakti freight terminal by matched station code."""
    hub = HubLocation(hub_type="freight_terminal", code="KJR")
    res = resolver.resolve(hub, store)
    assert "Kamalajari" in res.canonical_name
    assert res.match_method == "exact_code"
    assert 20.0 <= res.latitude <= 21.0


def test_freight_terminal_without_coordinates_is_not_routable(resolver, store):
    """Verifies that a catalogued terminal without coordinates raises LocationNotFoundError."""
    hub = HubLocation(hub_type="freight_terminal", name="Vadalapudi")
    with pytest.raises(LocationNotFoundError) as exc_info:
        resolver.resolve(hub, store)
    assert "lacks geographic coordinates" in str(exc_info.value)


def test_every_declared_hub_type_has_valid_resolver_path(resolver, store):
    """Verifies that every declared category in HubLocation has at least one working resolver lookup."""
    samples = {
        "port": "Deendayal Port (Kandla)",
        "icd": "ICD Tughlakabad",
        "mmlp": "MMLP Jalna",
        "freight_terminal": "Kamalajari",
        "rail_station": "PUNE JN",
        "air_cargo": "Indira Gandhi International Airport (DEL)",
        "iw_terminal": "Varanasi Multi-Modal Terminal",
        "icp": "Attari ICP",
        "fci_depot": "FCI FSD Moga",
        "cold_chain": "Agra Cold Storage Cluster (Khandari)",
        "mandi": "Azadpur APMC Mandi (Asia's Largest)",
    }

    for hub_type, name in samples.items():
        hub = HubLocation(hub_type=hub_type, name=name)
        res = resolver.resolve(hub, store)
        assert res is not None, f"Failed to resolve hub_type: {hub_type}"
        assert res.latitude is not None and -90 <= res.latitude <= 90
        assert res.longitude is not None and -180 <= res.longitude <= 180


def test_alias_type_mismatch_raises_invalid_location_error(resolver, store):
    """Verifies that requesting an alias under the wrong category raises InvalidLocationError."""
    # JNPT is a port, not an ICD
    bad_hub = HubLocation(hub_type="icd", name="JNPT")
    with pytest.raises(InvalidLocationError) as exc_info:
        resolver.resolve(bad_hub, store)
    assert "refers to category 'port'" in str(exc_info.value)


def test_prefix_matching_is_rejected(resolver, store):
    """Verifies that single-letter or fuzzy prefix queries are rejected rather than picking first match."""
    bad_hub = HubLocation(hub_type="port", name="M")
    with pytest.raises(LocationNotFoundError):
        resolver.resolve(bad_hub, store)


def test_all_location_aliases_resolve_validly(resolver, store):
    """Validates that every alias defined in location_aliases.yaml resolves to a valid entity with non-null coords."""
    with open(ALIASES_PATH, "r", encoding="utf-8") as f:
        aliases = yaml.safe_load(f)

    for alias_name, data in aliases.items():
        hub_type = data["type"]
        loc = HubLocation(hub_type=hub_type, name=alias_name)
        res = resolver.resolve(loc, store)
        assert res is not None, f"Alias '{alias_name}' failed to resolve"
        assert res.latitude is not None and -90 <= res.latitude <= 90, f"Alias '{alias_name}' has invalid latitude"
        assert res.longitude is not None and -180 <= res.longitude <= 180, f"Alias '{alias_name}' has invalid longitude"
