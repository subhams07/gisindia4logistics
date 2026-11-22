"""One canonical label per major port (ports.csv names); earlier labels stay accepted (decision item 24)."""
import pandas as pd
import pytest
from fastapi import HTTPException

from scripts.analyze import port_hinterland_model as gravity
from scripts.clean.standardize import PORT_NAME_ALIASES, canonical_port_label
from server.routers import simulation

JNPT = "Jawaharlal Nehru Port (JNPT/Navi Mumbai)"
KOLKATA = "Kolkata Port (Syama Prasad Mookerjee Port) incl. Haldia Dock Complex"


def test_matrix_labels_are_the_ports_file_names():
    ports = pd.read_csv("data/logistics_hubs/ports.csv")
    majors = set(ports.loc[ports.hub_type == "major_port", "name"])
    assert {name for name, _ in gravity.PORT_MATRIX_DATA.values()} == majors
    assert set(PORT_NAME_ALIASES.values()) <= majors


@pytest.mark.parametrize("alias,label", [("Jawaharlal Nehru Port (JNPT)", JNPT), ("nhava sheva", JNPT),
                                         ("Syama Prasad Mookerjee Port (Kolkata/Haldia)", KOLKATA),
                                         ("MUMBAI", "Mumbai Port"), ("Paradip Port", "Paradip Port")])
def test_aliases_resolve_to_one_label(alias, label):
    assert canonical_port_label(alias) == label


def _route(target):
    cols = {}
    for i, column in enumerate(simulation.PORT_MATRIX_DATA):
        cols[column] = [1.0 + i]
        cols[column.replace("drive_hours_to_", "road_km_to_", 1)] = [100.0 + i]
    matrix = pd.DataFrame({"state": ["Maharashtra"], "district": ["Pune"], "district_code": [521], **cols})
    row = pd.Series({"state": "Maharashtra", "district": "Pune", "district_code": 521})
    return simulation._requested_port_route(target, row, matrix)[0]


@pytest.mark.parametrize("target,label", [("Jawaharlal Nehru Port (JNPT)", JNPT), ("JNPT", JNPT), (JNPT, JNPT),
                                          ("Mumbai", "Mumbai Port"), ("Mumbai Port", "Mumbai Port"),
                                          ("Syama Prasad Mookerjee Port (Kolkata/Haldia)", KOLKATA), ("Kolkata", KOLKATA)])
def test_freight_cost_target_port_accepts_canonical_and_earlier_labels(target, label):
    assert _route(target) == label


def test_capacity_overrides_take_aliases_but_not_two_keys_for_one_port():
    assert gravity.canonical_port_capacities({"Jawaharlal Nehru Port (JNPT)": 90.0}) == {JNPT: 90.0}
    with pytest.raises(ValueError, match="Duplicate"):
        gravity.canonical_port_capacities({"Jawaharlal Nehru Port (JNPT)": 90.0, JNPT: 95.0})


def test_unknown_port_still_404():
    with pytest.raises(HTTPException) as exc:
        _route("Atlantis Port")
    assert exc.value.status_code == 404


def test_sdk_and_mcp_pass_custom_port_capacities(monkeypatch):
    """Decision item 25: the SDK and the MCP tool expose the REST endpoint's capacity overrides."""
    from gisindia4logistics import sdk
    from mcp_server import server, tools

    seen = []
    fake = lambda req, store: seen.append(req.custom_port_capacities) or []  # noqa: E731
    monkeypatch.setattr(sdk, "simulate_port_gravity", fake)
    monkeypatch.setattr(sdk, "get_data_store", lambda: None)
    monkeypatch.setattr(tools, "simulate_port_gravity", fake)
    monkeypatch.setattr(tools, "_get_store", lambda: None)
    sdk.simulate_port_catchment(custom_port_capacities={"Paradip Port": 160.0})
    tools.tool_simulate_port_catchment(custom_port_capacities={"Paradip Port": 150.0})
    assert seen == [{"Paradip Port": 160.0}, {"Paradip Port": 150.0}]
    schema = next(t for t in server.TOOLS_METADATA if t["name"] == "gis_simulate_port_catchment")["inputSchema"]
    assert "custom_port_capacities" in schema["properties"]
    server._validate_input("gis_simulate_port_catchment", schema, {"custom_port_capacities": {"Paradip Port": 160}})
    for bad in ({"Paradip Port": 0}, {"Paradip Port": "160"}, ["Paradip Port"]):
        with pytest.raises(Exception):
            server._validate_input("gis_simulate_port_catchment", schema, {"custom_port_capacities": bad})
