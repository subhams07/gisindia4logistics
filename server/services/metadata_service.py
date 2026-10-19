"""
server/services/metadata_service.py
Centralized Metadata and Provenance Service for GISIndia4Logistics.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from server.config import settings
from server.models.phase1 import ResponseMetadata

LOGGER = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "data" / "version_manifest.json"


class MetadataService:
    """Singleton service for loading version manifest and constructing ResponseMetadata."""
    _instance: Optional["MetadataService"] = None

    def __init__(self, manifest_path: Optional[Path] = None):
        self.manifest_path = Path(
            manifest_path if manifest_path is not None else settings.DATA_PATH / "version_manifest.json"
        ).resolve()
        self._manifest_data: Dict[str, Any] = self._load_manifest()

    @classmethod
    def get_instance(cls, manifest_path: Optional[Path] = None) -> "MetadataService":
        path = Path(
            manifest_path if manifest_path is not None else settings.DATA_PATH / "version_manifest.json"
        ).resolve()
        if cls._instance is None or cls._instance.manifest_path != path:
            cls._instance = cls(manifest_path=path)
        return cls._instance

    def _load_manifest(self) -> Dict[str, Any]:
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        raise ValueError("Manifest must be an object")
                    datasets = data.get("datasets", {})
                    legal = data.get("legal_compliance", {})
                    if (not isinstance(datasets, dict) or not isinstance(legal, dict)
                            or any(not isinstance(item, dict) for item in datasets.values())):
                        raise ValueError("Manifest dataset and legal entries must be objects")
                    for field in ("data_version", "api_version", "model_version"):
                        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
                            raise ValueError("Manifest versions must be non-empty strings")
                    for item in datasets.values():
                        vintage = item.get("vintage")
                        if vintage is not None and (not isinstance(vintage, str) or not vintage.strip()):
                            raise ValueError("Manifest vintages must be strings or absent")
                    for field in ("disclaimer", "assumptions_url", "sources_url"):
                        if field in legal and (not isinstance(legal[field], str) or not legal[field].strip()):
                            raise ValueError("Manifest legal values must be non-empty strings")
                    return data
            except Exception as e:
                LOGGER.warning("Failed to load version manifest from %s: %s", self.manifest_path, e)
        return {
            "data_version": "unknown",
            "api_version": "1.0.0",
            "model_version": "phase1-decision-workbench",
            "datasets": {},
            "legal_compliance": {
                "assumptions_url": "https://github.com/subhams07/gisindia4logistics/blob/main/docs/model_assumptions.md",
                "sources_url": "https://github.com/subhams07/gisindia4logistics/blob/main/docs/sources.md",
                "disclaimer": "All spatial data and administrative boundaries are indicative representations published for logistics analytics, not authoritative Survey of India boundary certifications."
            }
        }

    @property
    def api_version(self) -> str:
        return self._manifest_data.get("api_version", "1.0.0")

    @property
    def package_version(self) -> str:
        try:
            import importlib.metadata
            return importlib.metadata.version("gisindia4logistics")
        except Exception:
            try:
                import gisindia4logistics
                return getattr(gisindia4logistics, "__version__", "1.0.0")
            except Exception:
                return "1.0.0"

    @property
    def model_version(self) -> str:
        return self._manifest_data.get("model_version", "phase1-decision-workbench")

    @property
    def data_version(self) -> str:
        return self._manifest_data.get("data_version") or "unknown"

    def get_metadata(self, custom_limitations: Optional[list[str]] = None) -> ResponseMetadata:
        """Constructs a fresh ResponseMetadata instance with current UTC timestamp."""
        datasets = self._manifest_data.get("datasets", {})
        legal = self._manifest_data.get("legal_compliance", {})

        limitations = [
            legal.get(
                "disclaimer",
                "All spatial data and administrative boundaries are indicative representations published for logistics analytics, not authoritative Survey of India boundary certifications."
            )
        ]
        if self.data_version == "unknown":
            datasets = {}
            limitations.append("Configured data provenance is unavailable or invalid; dataset vintages are unknown.")
        if custom_limitations:
            limitations.extend(custom_limitations)

        return ResponseMetadata(
            api_version=self.api_version,
            package_version=self.package_version,
            model_version=self.model_version,
            data_version=self.data_version,
            generated_at_utc=datetime.now(timezone.utc),
            road_network_vintage=datasets.get("national_highway_network", {}).get("vintage"),
            port_capacity_vintage=datasets.get("port_throughput_annual", {}).get("vintage"),
            population_vintage=datasets.get("district_population_estimates", {}).get("vintage"),
            assumptions_url=legal.get(
                "assumptions_url",
                "https://github.com/subhams07/gisindia4logistics/blob/main/docs/model_assumptions.md"
            ),
            sources_url=legal.get(
                "sources_url",
                "https://github.com/subhams07/gisindia4logistics/blob/main/docs/sources.md"
            ),
            limitations=limitations
        )


def get_metadata_service() -> MetadataService:
    """Dependency injector function for MetadataService."""
    return MetadataService.get_instance()
