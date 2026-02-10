from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "gemma3:12b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_router_model: str = "gemma3:4b"

    qdrant_api_key: str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIn0.fd5QDZ8upKoKMF5wq3ykD4qQ30X3i6HLX9NvD7WdMC8"
    qdrant_url: str = "https://8dbbe97c-2931-45fb-8c46-836c7fbcd3e7.europe-west3-0.gcp.cloud.qdrant.io"

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://postgres:voetbal123@localhost:5432/intodata_kb"
    
    # App
    app_env: str = "development"
    log_level: str = "INFO"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


settings = Settings()
