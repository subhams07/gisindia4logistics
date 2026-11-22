"""MCP stdio helpers shared by the MCP tests (not a test module)."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _rpc(request_id, method, params=None):
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    return request


def _handshake():
    return [
        _rpc(1, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "transport-regression", "version": "1"},
        }),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _rpc(2, "ping"),
        _rpc(3, "tools/list"),
    ]


def _stdio(requests, tmp_path, bootstrap=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    # Committed-data runs share the session graph cache (tests/conftest.py); a custom
    # bootstrap may patch the data, so it keeps its own cache.
    shared = os.environ.get("GIS4L_TEST_SHARED_GRAPH_CACHE") if bootstrap is None else None
    env["GIS4LOGISTICS_CACHE_PATH"] = shared or str(tmp_path / "graph-cache")
    env["GIS4LOGISTICS_OUTPUT_PATH"] = str(tmp_path / "outputs")
    command = [sys.executable, "-m", "mcp_server.server"]
    if bootstrap is not None:
        command = [sys.executable, "-c", bootstrap]
    process = subprocess.run(
        command, cwd=ROOT, env=env,
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True, text=True, encoding="utf-8", timeout=180, check=False,
    )
    assert process.returncode == 0, process.stderr
    # Parse EVERY line, including initialization messages and blank lines.
    responses = [_strict_json(line) for line in process.stdout.splitlines()]
    assert len(responses) == sum("id" in request for request in requests)
    assert [response["id"] for response in responses] == [
        request["id"] for request in requests if "id" in request
    ]
    assert all(response["jsonrpc"] == "2.0" for response in responses)
    return responses, process.stderr


def _success(response):
    assert "error" not in response, response
    result = response["result"]
    assert not result.get("isError", False), result
    assert result["content"][0]["type"] == "text"
    return _strict_json(result["content"][0]["text"])


def _strict_json(text):
    def reject_constant(value):
        raise AssertionError(f"Non-standard JSON constant: {value}")

    return json.loads(text, parse_constant=reject_constant)
