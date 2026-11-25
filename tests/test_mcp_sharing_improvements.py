"""Tests for MCP sharing improvements: lazy imports, lazy graph, protocol negotiation,
tool annotations, inline PNG responses, portable launcher, and subdistrict/freight tools."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_handshake_defers_heavy_runtime_imports():
    script = """
import json
import sys
from mcp_server.server import handle_rpc_request

init = handle_rpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
ping = handle_rpc_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
listed = handle_rpc_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})

heavy = [
    m for m in (
        "geopandas",
        "shapely",
        "pandas",
        "scipy",
        "fastapi",
        "matplotlib",
        "server.dependencies",
        "mcp_server.tools",
    )
    if m in sys.modules
]
print(json.dumps({"init": init, "ping": ping, "count": len(listed["result"]["tools"]), "heavy": heavy}))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["init"]["result"]["protocolVersion"] == "2025-06-18"
    assert payload["ping"]["result"] == {}
    assert payload["count"] == 10
    assert payload["heavy"] == []


@pytest.mark.parametrize(
    "requested,expected",
    [
        ("2024-11-05", "2024-11-05"),
        ("2025-03-26", "2025-03-26"),
        ("2025-06-18", "2025-06-18"),
        ("2099-01-01", "2025-06-18"),
        (None, "2025-06-18"),
    ],
)
def test_protocol_version_negotiation(requested, expected):
    from mcp_server.server import handle_rpc_request

    params = {} if requested is None else {"protocolVersion": requested}
    res = handle_rpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
    assert res["result"]["protocolVersion"] == expected


def test_tools_carry_explicit_annotations():
    from mcp_server.server import handle_rpc_request

    listed = handle_rpc_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {t["name"]: t for t in listed["result"]["tools"]}
    assert len(tools) == 10
    for name, meta in tools.items():
        ann = meta["annotations"]
        assert ann["destructiveHint"] is False
        assert ann["idempotentHint"] is True
        assert ann["openWorldHint"] is False
        assert ann["readOnlyHint"] is (name != "gis_plot_villages_map")


def test_lazy_highway_graph_skips_build_on_non_routing_calls(monkeypatch):
    from mcp_server.server import handle_rpc_request
    from server.app import app
    from server.dependencies import DataStore
    from server.services.exceptions import DataIntegrityError

    # Raw uninitialized store must still reject direct get_highway_snapshot()
    raw = DataStore()
    with pytest.raises(DataIntegrityError):
        raw.get_highway_snapshot()

    calls = []
    orig_build = DataStore._build_highway_graph

    def tracked_build(self):
        calls.append(1)
        return orig_build(self)

    monkeypatch.setattr(DataStore, "_build_highway_graph", tracked_build)
    monkeypatch.setattr(DataStore, "_instance", None)

    try:
        store = DataStore.get_instance()
        assert store.nh_graph is None
        assert calls == []

        for tool_name, arguments in (
            ("gis_get_district_scorecard", {"district_name_or_code": "Pune"}),
            ("gis_get_subdistrict_profile", {"sub_district_code": "00285N001"}),
            ("gis_get_subdistrict_census", {"state": "Jammu and Kashmir", "district": "Kupwara", "limit": 5}),
            ("gis_get_freight_statistics", {"series": "rail", "fy": "2024-25", "commodity_group": "coal"}),
        ):
            res = handle_rpc_request({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            })
            assert not res["result"].get("isError"), res
        assert calls == []

        # Readiness endpoint and routing calls trigger lazy build once
        with TestClient(app) as client:
            ready = client.get("/health/ready")
            assert ready.status_code == 200
            assert ready.json()["status"] == "ready"
        assert len(calls) == 1
        assert store.nh_graph is not None
    finally:
        monkeypatch.setattr(DataStore, "_instance", None)


def test_plot_villages_map_returns_inline_png_and_text_or_text_only(tmp_path, monkeypatch):
    from mcp_server.server import handle_rpc_request
    from server.config import settings

    monkeypatch.setattr(settings, "OUTPUT_PATH", tmp_path)

    res_both = handle_rpc_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "gis_plot_villages_map",
            "arguments": {"state": "Haryana", "district": "Ambala", "output_format": "both"},
        },
    })
    content_both = res_both["result"]["content"]
    assert len(content_both) == 2
    assert content_both[0]["type"] == "text"
    meta = json.loads(content_both[0]["text"])
    assert meta["outputs"]["png_map_path"]
    assert content_both[1]["type"] == "image"
    assert content_both[1]["mimeType"] == "image/png"
    assert base64.b64decode(content_both[1]["data"]).startswith(b"\x89PNG\r\n\x1a\n")

    res_html = handle_rpc_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "gis_plot_villages_map",
            "arguments": {"state": "Haryana", "district": "Ambala", "output_format": "html"},
        },
    })
    assert len(res_html["result"]["content"]) == 1
    assert res_html["result"]["content"][0]["type"] == "text"


def test_portable_plugin_launcher_runs_from_arbitrary_cwd(tmp_path):
    launcher = ROOT / "plugins" / "gisindia4logistics" / "launch_mcp.py"
    env = {k: v for k, v in os.environ.items() if k not in ("GIS4LOGISTICS_DATA_PATH", "PYTHONPATH")}
    requests = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "gis_get_subdistrict_profile", "arguments": {"sub_district_code": "00285N001"}},
        }) + "\n"
    )
    proc = subprocess.run(
        [sys.executable, str(launcher)],
        cwd=tmp_path,
        env=env,
        input=requests,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["result"]["protocolVersion"] == "2025-06-18"
    payload = json.loads(lines[1]["result"]["content"][0]["text"])
    assert payload["total_matches"] == 1
    assert payload["subdistricts"][0]["sub_district_code"] == "00285N001"
    assert payload["subdistricts"][0]["sub_district"] == "GHAT"


def test_subdistrict_census_and_freight_tools_validation_and_queries():
    from mcp_server.server import handle_rpc_request

    # 1. Subdistrict census with null area_sqkm handled cleanly & zero-padded code
    res_census = handle_rpc_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "gis_get_subdistrict_census",
            "arguments": {"census_subdistrict_code": "1"},
        },
    })
    assert not res_census["result"].get("isError")
    census_payload = json.loads(res_census["result"]["content"][0]["text"])
    assert census_payload["total_matches"] >= 1
    assert census_payload["records"][0]["census_subdistrict_code"] == "00001"

    # 2. Freight statistics across series
    res_freight = handle_rpc_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "gis_get_freight_statistics",
            "arguments": {"series": "port", "entity_code": "INPRT", "fy": "2023-24"},
        },
    })
    freight_payload = json.loads(res_freight["result"]["content"][0]["text"])
    assert freight_payload["total_matches"] == 1
    assert freight_payload["records"][0]["value"] == pytest.approx(145.38)

    # 3. Bounded validation failures return isError=True (422)
    for tool_name, bad_args in (
        ("gis_get_subdistrict_profile", {}),
        ("gis_get_subdistrict_profile", {"state": "   "}),
        ("gis_get_subdistrict_profile", {"state": "Haryana", "limit": 101}),
        ("gis_get_subdistrict_census", {}),
        ("gis_get_freight_statistics", {"limit": 0}),
        ("gis_get_freight_statistics", {"series": "air"}),
    ):
        err = handle_rpc_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": bad_args},
        })
        assert err["result"]["isError"] is True
        assert json.loads(err["result"]["content"][0]["text"])["status"] == 422
