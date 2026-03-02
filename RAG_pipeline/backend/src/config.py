from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM provider chain (tried in order, first success wins)
    llm_providers: list[str] = ["kimi", "ollama"]

    # Kimi (Moonshot AI)
    kimi_api_key: str = "sk-1FSKsQ9pKQPwcw0uPyFwsO72uQtlcIxmRNboiIOJ5EdK17MY"
    kimi_llm_model: str = "kimi-k2.5"
    kimi_temperature: float = 0.6
    kimi_timeout: float = 60.0

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "gemma3:12b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_router_model: str = "gemma3:4b"
    ollama_timeout: float = 120.0

    # Embedding provider chain
    embedding_providers: list[str] = ["voyage", "ollama"]
    voyage_api_key: str = "pa-nu8c97r7H4mYSWwuEbbeYkTO3px2a3hbFYpCyxAN-gv"
    voyage_embed_model: str = "voyage-4-large"
    voyage_embed_dim: int = 1024

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
    reranker_model: str = "rerank-2.5"
    rerank_top_n: int = 20
    reranker_min_score: float = 0.05

    # Query expansion
    query_expansion_enabled: bool = False

    # Search tuning
    default_top_k: int = 5
    default_threshold: float = 0.3
    prefetch_limit: int = 30
    max_result_documents: int = 3

    # Hindsight memory
    hindsight_enabled: bool = False
    hindsight_url: str = "http://localhost:8888"
    hindsight_recall_budget: str = "low"
    hindsight_recall_max_tokens: int = 1024

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


settings = Settings()
