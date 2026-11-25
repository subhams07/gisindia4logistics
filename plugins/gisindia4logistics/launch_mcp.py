"""Cross-platform portable launcher for the GISIndia4Logistics MCP stdio server.

Resolves the repository root, locates `.venv` Python on Windows or POSIX (falling
back to the invoking interpreter), sets `GIS4LOGISTICS_DATA_PATH` when absent,
and puts the repo root on `PYTHONPATH` so MCP clients can launch the server from
any working directory without manual virtualenv or env-var configuration.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def resolve_python_executable(repo_root: Path) -> str:
    for candidate in (
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def build_launch_env(repo_root: Path, base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env if base_env is not None else os.environ)
    data_dir = repo_root / "data"
    if not env.get("GIS4LOGISTICS_DATA_PATH") and data_dir.is_dir():
        env["GIS4LOGISTICS_DATA_PATH"] = str(data_dir)
    existing_pythonpath = env.get("PYTHONPATH", "")
    repo_str = str(repo_root)
    if existing_pythonpath:
        parts = existing_pythonpath.split(os.pathsep)
        if repo_str not in parts:
            env["PYTHONPATH"] = os.pathsep.join([repo_str, existing_pythonpath])
    else:
        env["PYTHONPATH"] = repo_str
    return env


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    python_exe = resolve_python_executable(repo_root)
    env = build_launch_env(repo_root)
    proc = subprocess.run(
        [python_exe, "-m", "mcp_server.server"],
        cwd=str(repo_root),
        env=env,
        check=False,
    )
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
