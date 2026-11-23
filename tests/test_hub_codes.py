"""Hub codes, UN/LOCODEs and coordinate precision on the transport hub files (audit items 46 and 52)."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from server.dependencies import DataStore
from server.models.phase1 import HubLocation
from server.services.location_resolver import LocationResolver

ROOT = Path(__file__).resolve().parents[1]
HUBS = ROOT / "data" / "logistics_hubs"
FILES = {"ports": "PRT", "icps": "ICP", "icds": "ICD", "air_cargo": "AIR", "mmlps": "MMLP",
         "inland_waterway_terminals": "IWT", "fci_depots": "FCI"}


@pytest.fixture(scope="module")
def resolver():
    return LocationResolver(aliases_path=ROOT / "data" / "aliases" / "location_aliases.yaml")


@pytest.fixture(scope="module")
def store():
    return DataStore.get_instance()


@pytest.mark.parametrize("stem,prefix", FILES.items())
def test_every_row_has_a_unique_code_and_a_precision(stem, prefix):
    table = pd.read_csv(HUBS / f"{stem}.csv", dtype=str, keep_default_na=False)
    assert table.hub_code.str.fullmatch(rf"{prefix}-\d{{3}}").all() and table.hub_code.is_unique
    no_xy = (table.latitude == "") | (table.longitude == "")
    assert ((table.coord_precision == "pending") == no_xy).all()
    assert set(table.coord_precision) <= {"site", "station", "village", "town", "city", "unverified", "pending"}


def test_major_ports_carry_the_unlocodes_of_the_throughput_table():
    ports = pd.read_csv(HUBS / "ports.csv", dtype=str, keep_default_na=False)
    throughput = pd.read_csv(ROOT / "data/freight/port_throughput_annual.csv")
    majors = ports[ports.hub_type == "major_port"]
    assert set(majors.unlocode) == set(throughput.loc[throughput.entity_type == "port", "entity_code"])
    assert (ports.loc[ports.hub_type != "major_port", "unlocode"] == "").all()


def test_shared_city_points_are_city_but_the_port_that_owns_one_is_not_downgraded():
    mmlps = pd.read_csv(HUBS / "mmlps.csv", dtype=str, keep_default_na=False).set_index("name")
    ports = pd.read_csv(HUBS / "ports.csv", dtype=str, keep_default_na=False).set_index("name")
    assert mmlps.loc["MMLP Pune", "coord_precision"] == "city"        # = FCI Depot Pune
    assert mmlps.loc["MMLP Visakhapatnam", "coord_precision"] == "city"  # = the port's point
    assert ports.loc["Visakhapatnam Port", "coord_precision"] != "city"


@pytest.mark.parametrize("query,expected", [
    (dict(hub_type="port", code="INNSA"), "Jawaharlal Nehru Port (JNPT/Navi Mumbai)"),
    (dict(hub_type="port", code="PRT-002"), "Mumbai Port"),
    (dict(hub_type="port", name="Bombay Port"), "Mumbai Port"),
    (dict(hub_type="port", name="Haldia"), "Kolkata Port (Syama Prasad Mookerjee Port) incl. Haldia Dock Complex"),
    (dict(hub_type="port", name="Mundra"), "Mundra Port"),       # registry code, no unlocode in the row
    (dict(hub_type="icd", name="TKD"), "ICD Tughlakabad"),
])
def test_resolve_by_hub_code_unlocode_and_alias(resolver, store, query, expected):
    assert resolver.resolve(HubLocation(**query), store).canonical_name == expected


def test_hub_code_and_name_must_agree(resolver, store):
    from server.services.exceptions import InvalidLocationError
    with pytest.raises(InvalidLocationError):
        resolver.resolve(HubLocation(hub_type="port", code="PRT-002", name="Paradip Port"), store)


def test_precision_rules():
    spec = importlib.util.spec_from_file_location("hp", ROOT / "scripts/clean/add_hub_codes_precision.py")
    hp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hp)
    row = lambda notes, lat="20.0", lon="78.0", name="X": pd.Series(  # noqa: E731
        {"name": name, "latitude": lat, "longitude": lon, "capacity_notes": notes})
    assert hp.precision(row("", lat=""), "icds", set(), set()) == "pending"
    assert hp.precision(row("coordinates approximate; coordinate from OpenStreetMap (Nominatim) Raxaul station node"),
                        "icps", set(), set()) == "station"
    assert hp.precision(row("city-level location"), "fci_depots", set(), set()) == "city"
    assert hp.precision(row(""), "mmlps", {(20.0, 78.0)}, set()) == "city"
    assert hp.precision(row(""), "ports", {(20.0, 78.0)}, set()) == "unverified"
    assert hp.precision(row("", name="P"), "ports", set(), {("ports", "P")}) == "site"


def test_hubs_endpoint_returns_null_for_blank_unlocode():
    from fastapi.testclient import TestClient
    from server.app import app
    ports = TestClient(app).get("/api/v1/hubs", params={"hub_type": "ports"}).json()
    by_name = {p["name"]: p for p in ports}
    assert by_name["Jawaharlal Nehru Port (JNPT/Navi Mumbai)"]["unlocode"] == "INNSA"
    assert by_name["Mundra Port"]["unlocode"] is None
    assert by_name["Mundra Port"]["hub_code"].startswith("PRT-") and by_name["Mundra Port"]["coord_precision"]
