"""Non-editable installs ship no ~1 GB data directory: every interface must say so clearly."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

from gisindia4logistics import data_root as dr
from gisindia4logistics import sdk
from gisindia4logistics.data_root import (
    ENV_VAR, PUBLIC_MESSAGE, REPO_URL, DataNotFoundError, data_not_found_contract,
    data_root_available, require_data_root,
)
from scripts.analyze import producer_manifest as pm
from server.config import settings
from server.dependencies import DataStore
from server.services.exceptions import DataIntegrityError, GISIndia4LogisticsError

ROOT = Path(__file__).resolve().parents[1]
COORD = {"type": "coordinate", "latitude": 22.0, "longitude": 78.0}
COORD2 = {"type": "coordinate", "latitude": 23.0, "longitude": 79.0}


@pytest.fixture
def absent(tmp_path, monkeypatch):
    path = tmp_path / "absent"
    monkeypatch.setattr(settings, "DATA_PATH", path)
    monkeypatch.setattr(DataStore, "_instance", None)

    def forbidden(self):
        raise AssertionError("initialize_store must not run without data")

    monkeypatch.setattr(DataStore, "initialize_store", forbidden)
    return path


SDK_CALLS = {
    "get_district": lambda: sdk.get_district("Pune"),
    "find_nearest": lambda: sdk.find_nearest(18.5, 73.8),
    "route_highway": lambda: sdk.route_highway((22.0, 78.0), (23.0, 79.0)),
    "plan_highway_route": lambda: sdk.plan_highway_route(COORD, COORD2),
    "calculate_freight_cost": lambda: sdk.calculate_freight_cost("Pune"),
    "simulate_port_catchment": lambda: sdk.simulate_port_catchment(),
    "plot_villages": lambda: sdk.plot_villages("Maharashtra", "Pune"),
}


@pytest.mark.parametrize("name", sorted(SDK_CALLS))
def test_sdk_raises_actionable_error(absent, name):
    with pytest.raises(DataNotFoundError) as caught:
        SDK_CALLS[name]()
    error = caught.value
    assert isinstance(error, DataIntegrityError) and isinstance(error, GISIndia4LogisticsError)
    message = str(error)
    assert str(absent) in message and ENV_VAR in message and REPO_URL in message
    assert message != "An internal routing error occurred."
    assert error.data_path == absent
    assert sdk.DataNotFoundError is DataNotFoundError


def test_sdk_validation_still_precedes_data_check(absent):
    bad = {"type": "coordinate", "latitude": 999, "longitude": 78}
    with pytest.raises(Exception) as caught:
        sdk.plan_highway_route(bad, COORD2)
    assert not isinstance(caught.value, DataNotFoundError)
    with pytest.raises(ValueError):
        sdk.plot_villages("Maharashtra", "Pune", output_format="bogus")


def test_cli_district_exits_one_with_clean_message(absent, monkeypatch, capsys):
    from gisindia4logistics import cli
    monkeypatch.setattr(sys, "argv", ["gisindia4logistics", "district", "Pune"])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: GISIndia4Logistics data not found at")
    assert "Traceback" not in captured.err and captured.out == ""


def test_cli_plot_villages_exits_one(absent, monkeypatch, capsys):
    from gisindia4logistics import cli
    monkeypatch.setattr(sys, "argv", ["gisindia4logistics", "plot-villages", "--state", "Maharashtra", "--district", "Pune"])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 1
    assert capsys.readouterr().err.startswith("error: GISIndia4Logistics data not found at")


def test_cli_serve_refuses_to_start(absent, monkeypatch, capsys):
    import uvicorn
    from gisindia4logistics import cli

    def fail(*args, **kwargs):
        raise AssertionError("uvicorn.run must not be called")

    monkeypatch.setattr(uvicorn, "run", fail)
    monkeypatch.setattr(sys, "argv", ["gisindia4logistics", "serve"])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 1
    assert capsys.readouterr().err.startswith("error: GISIndia4Logistics data not found at")


def test_cli_mcp_warns_but_serves(absent, monkeypatch, capsys):
    import mcp_server.server as server
    from gisindia4logistics import cli
    served = []
    monkeypatch.setattr(server, "run_stdio_server", lambda: served.append(True))
    monkeypatch.setattr(sys, "argv", ["gisindia4logistics", "mcp"])
    cli.main()
    assert served == [True]
    err = capsys.readouterr().err
    assert PUBLIC_MESSAGE in err and err.count("\n") == 1 and str(absent) not in err


def _call(name, arguments, request_id=1):
    from mcp_server.server import handle_rpc_request
    return handle_rpc_request({
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def test_mcp_tool_call_returns_data_not_found(absent, monkeypatch):
    from mcp_server import tools
    monkeypatch.setattr(tools, "store", tools._LazyDataStore())
    for name, arguments in (
        ("gis_get_district_scorecard", {"district_name_or_code": "Pune"}),
        ("gis_find_nearest_facilities", {"latitude": 18.5, "longitude": 73.8}),
        ("gis_simulate_port_catchment", {}),
        ("gis_plot_villages_map", {"state": "Maharashtra", "district": "Pune"}),
    ):
        result = _call(name, arguments)["result"]
        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert json.loads(text) == data_not_found_contract()
        assert str(absent.parent) not in text


def test_mcp_initialize_and_list_do_not_need_data(absent):
    from mcp_server.server import TOOLS_METADATA, handle_rpc_request
    init = handle_rpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "gisindia4logistics"
    listed = handle_rpc_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed["result"]["tools"] == TOOLS_METADATA


def test_mcp_invalid_input_is_still_reported_first(absent):
    result = _call("gis_get_district_scorecard", {})["result"]
    assert json.loads(result["content"][0]["text"])["code"] == "invalid_request"


def test_contract_has_no_path_and_fixed_message():
    contract = data_not_found_contract()
    assert contract == {"status": 503, "code": "data_not_found", "message": PUBLIC_MESSAGE}
    assert PUBLIC_MESSAGE == (
        "GISIndia4Logistics data not found. The installed package does not include the ~1 GB data "
        f"directory. Clone {REPO_URL} and set {ENV_VAR}=/path/to/gisindia4logistics/data before starting."
    )
    assert "/home" not in PUBLIC_MESSAGE.replace("/path/to", "")


def test_data_root_helpers(tmp_path, monkeypatch):
    assert dr.REQUIRED_MARKER == Path("administrative/india_districts_lgd.geojson")
    monkeypatch.setattr(settings, "DATA_PATH", tmp_path / "missing")
    assert not data_root_available() and not data_root_available(tmp_path)
    with pytest.raises(DataNotFoundError):
        require_data_root()
    fake = tmp_path / "fake"
    (fake / "administrative").mkdir(parents=True)
    (fake / dr.REQUIRED_MARKER).write_text("{}")
    assert data_root_available(fake) and require_data_root(fake) == fake
    monkeypatch.setattr(settings, "DATA_PATH", fake)
    assert data_root_available() and require_data_root() == fake


def test_real_data_present_and_injected_store_needs_no_data(tmp_path, monkeypatch):
    assert data_root_available(ROOT / "data")
    assert data_root_available()  # default settings point at the working tree
    monkeypatch.setattr(settings, "DATA_PATH", tmp_path / "absent")
    sentinel = object.__new__(DataStore)
    monkeypatch.setattr(DataStore, "_instance", sentinel)
    assert sdk.get_data_store() is sentinel
    sdk._ensure_data()


def test_data_root_module_stem_is_unique_and_import_light():
    stems = [p for top in ("gisindia4logistics", "gis4logistics", "server", "mcp_server", "scripts")
             for p in (ROOT / top).rglob("data_root.py")]
    assert stems == [ROOT / "gisindia4logistics" / "data_root.py"]
    import ast
    tree = ast.parse(stems[0].read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported == {"os", "pathlib", "server.config", "server.services.exceptions"}


def test_stage4_code_closure_is_untouched_and_excludes_data_root():
    manifest = json.loads((ROOT / "data/analysis/producer_manifest.json").read_text(encoding="utf-8"))
    hashes = manifest["code_hashes"]
    for logical, digest in hashes.items():
        assert hashlib.sha256((ROOT / logical).read_bytes()).hexdigest() == digest, logical
    closure = pm.national_code_closure(ROOT)
    assert set(closure) == set(hashes)
    assert "gisindia4logistics/data_root.py" not in closure
