import os
from pydantic_settings import BaseSettings

class PipelineConfig(BaseSettings):
    # Pipeline Versions
    VERSION: str = "4.0.0-elite"
    SCHEMA_VERSION: str = "2026.A"
    
    # Dataset Metadata (Move from hardcoded to here)
    GTFS_FEED_VERSION: str = os.getenv("GTFS_FEED_VERSION", "2024-Q1-SNAPSHOT")
    OSM_EXTRACT_DATE: str = os.getenv("OSM_EXTRACT_DATE", "2024-05-10")
    POI_SNAPSHOT_DATE: str = os.getenv("POI_SNAPSHOT_DATE", "2024-05-10")
    RAG_INDEX_VERSION: str = os.getenv("RAG_INDEX_VERSION", "v2.1")
    DATA_AGE_DAYS: int = 5

    # Resilience Settings
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 0.5

    class Config:
        env_file = ".env"

config = PipelineConfig()
