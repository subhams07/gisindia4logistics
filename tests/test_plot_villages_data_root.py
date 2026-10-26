"""plot_villages honours GIS4LOGISTICS_DATA_PATH and reports a missing data directory clearly."""
import importlib
import json
import pathlib

import pytest

from gisindia4logistics import sdk
from gisindia4logistics.data_root import DataNotFoundError, data_not_found_contract
from scripts.analyze import plot_villages

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def restore_module():
    yield
    importlib.reload(plot_villages)  # env var is undone by monkeypatch before/after as needed


def test_env_var_selects_data_dir_on_reload(monkeypatch, tmp_path, restore_module):
    monkeypatch.setenv("GIS4LOGISTICS_DATA_PATH", str(ROOT / "data"))
    importlib.reload(plot_villages)
    assert plot_villages.DATA_DIR == ROOT / "data"
    monkeypatch.setenv("GIS4LOGISTICS_DATA_PATH", str(tmp_path / "elsewhere"))
    importlib.reload(plot_villages)
    assert plot_villages.DATA_DIR == tmp_path / "elsewhere"
    monkeypatch.setenv("GIS4LOGISTICS_DATA_PATH", "")
    importlib.reload(plot_villages)
    assert plot_villages.DATA_DIR == plot_villages._REPO_DATA_DIR  # empty value counts as unset
    monkeypatch.delenv("GIS4LOGISTICS_DATA_PATH")
    importlib.reload(plot_villages)
    assert plot_villages.DATA_DIR == plot_villages._REPO_DATA_DIR


def test_sdk_plot_villages_checks_the_loader_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(plot_villages, "DATA_DIR", tmp_path / "absent")
    with pytest.raises(DataNotFoundError) as caught:
        sdk.plot_villages("Haryana", "Ambala")
    assert caught.value.data_path == tmp_path / "absent"


def test_mcp_plot_tool_checks_the_loader_directory(monkeypatch, tmp_path):
    from mcp_server import tools
    from mcp_server.server import handle_rpc_request
    monkeypatch.setattr(plot_villages, "DATA_DIR", tmp_path / "absent")
    with pytest.raises(DataNotFoundError):
        tools.tool_plot_villages_map("Haryana", "Ambala")
    result = handle_rpc_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "gis_plot_villages_map", "arguments": {"state": "Haryana", "district": "Ambala"}},
    })["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert json.loads(text) == data_not_found_contract() and str(tmp_path) not in text
