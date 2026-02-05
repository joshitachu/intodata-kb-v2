from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "mistral"
    ollama_embed_model: str = "nomic-embed-text"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://postgres:changeme@localhost:5432/intodata_kb"

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


settings = Settings()
