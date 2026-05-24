from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MC_",
        env_file=".env",
        extra="ignore",
    )

    api_key: str
    snapshots_dir: Path = Path("snapshots")
    host: str = "0.0.0.0"
    port: int = 9100


settings = Settings()
