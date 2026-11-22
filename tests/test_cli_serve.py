"""`gisindia4logistics serve` takes host and port from the flags, else from GIS4LOGISTICS_HOST / _PORT."""
import sys

import pytest

from gisindia4logistics import cli


@pytest.fixture
def launched(monkeypatch):
    import uvicorn
    calls = []
    monkeypatch.setattr(cli, "_require_data_or_exit", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    return calls


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["gisindia4logistics", "serve", *argv])
    cli.main()


def test_serve_uses_the_configured_host_and_port(launched, monkeypatch):
    from server.config import settings
    monkeypatch.setattr(settings, "HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "PORT", 9123)
    run(monkeypatch)
    assert launched == [("server.app:app", {"host": "127.0.0.1", "port": 9123, "reload": False})]


def test_serve_flags_override_the_settings(launched, monkeypatch):
    from server.config import settings
    monkeypatch.setattr(settings, "PORT", 9123)
    run(monkeypatch, "--host", "0.0.0.0", "--port", "8080")
    assert launched[0][1]["host"] == "0.0.0.0" and launched[0][1]["port"] == 8080


def test_settings_read_the_prefixed_environment(monkeypatch):
    from server.config import Settings
    monkeypatch.setenv("GIS4LOGISTICS_HOST", "10.0.0.5")
    monkeypatch.setenv("GIS4LOGISTICS_PORT", "8800")
    s = Settings()
    assert (s.HOST, s.PORT) == ("10.0.0.5", 8800)
    assert not hasattr(s, "DEBUG")


def test_importing_the_server_package_does_not_load_the_app():
    import importlib
    import subprocess
    code = "import sys, server.services.exceptions; print('server.app' in sys.modules, 'fastapi' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert out[0] == "False"
    importlib.import_module("server.app")
