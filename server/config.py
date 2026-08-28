"""
server/config.py
Configuration settings for GIS4Logistics API Server.
"""

from pathlib import Path
from pydantic_settings import BaseSettings

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
    
    # Path mappings
    DATA_PATH: Path = DATA_DIR

    class Config:
        case_sensitive = True

settings = Settings()
