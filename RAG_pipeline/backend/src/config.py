from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM provider chain (tried in order, first success wins)
    llm_providers: list[str] = ["groq", "openrouter", "ollama"]

    # Groq
    groq_api_key: str = "gsk_582vRAGn3fS0rVGN376EWGdyb3FYesvdd9P34kt2wLe3py6Ck2lZ8"
    groq_llm_model: str = "llama-3.3-70b-versatile"

    # OpenRouter
    openrouter_api_key: str = "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"
    openrouter_llm_model: str = "meta-llama/llama-3.3-70b-instruct:free"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "gemma3:12b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_router_model: str = "gemma3:4b"

    # Qdrant
    qdrant_api_key: str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIn0.fd5QDZ8upKoKMF5wq3ykD4qQ30X3i6HLX9NvD7WdMC8"
    qdrant_url: str = "https://8dbbe97c-2931-45fb-8c46-836c7fbcd3e7.europe-west3-0.gcp.cloud.qdrant.io:443"

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://postgres:voetbal123@localhost:5432/intodata_kb"

    # Hybrid search
    hybrid_search_enabled: bool = True
    sparse_embed_model: str = "Qdrant/bm25"

    # Reranking
    reranker_enabled: bool = True
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2"
    rerank_top_n: int = 15

    # Query expansion
    query_expansion_enabled: bool = True

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


settings = Settings()
