from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AURA_", extra="ignore")

    database_url: str
    data_dir: str = "./data"
    max_upload_bytes: int = 500 * 1024 * 1024
    max_duration_ms: int = 15 * 60 * 1000


settings = Settings()
