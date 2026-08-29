"""
server/config.py
Configuration settings for GIS4Logistics API Server.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

class Settings(BaseSettings):
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "GISIndia4Logistics API"
    VERSION: str = "1.0.0"
    DESCRIPTION: str = (
        "Open, curated GIS and multimodal freight logistics API platform for India. "
        "Provides administrative boundaries, logistics hubs, highway routing, "
        "and multi-modal freight cost simulations."
    )
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8000"
    CORS_ALLOW_CREDENTIALS: bool = False
    
    # Path mappings
    DATA_PATH: Path = DATA_DIR
    OUTPUT_PATH: Path = BASE_DIR / "outputs"

    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_prefix="GIS4LOGISTICS_",
    )

    @property
    def cors_origins(self) -> list[str]:
        origins = [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
        if "*" in origins and self.CORS_ALLOW_CREDENTIALS:
            raise ValueError("CORS credentials cannot be enabled with a wildcard origin")
        return origins

settings = Settings()
