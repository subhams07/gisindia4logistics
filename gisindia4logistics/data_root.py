"""
gisindia4logistics.data_root
Detection of the (non-packaged) data directory and the user-facing error for
installs that do not ship it.

Deliberately imports only the standard library, ``server.config`` and
``server.services.exceptions`` so MCP paths can use it without importing the SDK.
"""

import os
from pathlib import Path

from server.config import settings
from server.services.exceptions import DataIntegrityError

REQUIRED_MARKER = Path("administrative/india_districts_lgd.geojson")
ENV_VAR = "GIS4LOGISTICS_DATA_PATH"
REPO_URL = "https://github.com/subhams07/gisindia4logistics"
PUBLIC_MESSAGE = (
    f"GISIndia4Logistics data not found. The installed package does not include the ~1 GB data "
    f"directory. Clone {REPO_URL} and set {ENV_VAR}=/path/to/gisindia4logistics/data before starting."
)


class DataNotFoundError(DataIntegrityError):
    """The data directory is missing; the MCP adapter maps this to 503 ``data_not_found``.

    REST served through a bare ``uvicorn server.app:app`` is unchanged (no mapping)."""

    def __init__(self, data_path):
        self.data_path = Path(data_path)
        super().__init__(
            f"GISIndia4Logistics data not found at {self.data_path} (missing {REQUIRED_MARKER.as_posix()}). "
            f"The pip/wheel package does not ship the ~1 GB data directory. "
            f"Clone {REPO_URL} and set {ENV_VAR}=/path/to/gisindia4logistics/data"
        )


def _resolve(path=None) -> Path:
    return Path(os.fspath(path if path is not None else settings.DATA_PATH))


def data_root_available(path=None) -> bool:
    """True when ``(path or settings.DATA_PATH)/administrative/india_districts_lgd.geojson`` is a file."""
    return (_resolve(path) / REQUIRED_MARKER).is_file()


def require_data_root(path=None) -> Path:
    """Return the data root, or raise DataNotFoundError if the marker file is missing."""
    root = _resolve(path)
    if not (root / REQUIRED_MARKER).is_file():
        try:
            shown = root.resolve()
        except OSError:
            shown = root
        raise DataNotFoundError(shown)
    return root


def data_not_found_contract() -> dict:
    """Fixed public error contract; never contains a local path."""
    return {"status": 503, "code": "data_not_found", "message": PUBLIC_MESSAGE}
