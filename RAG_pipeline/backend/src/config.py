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
    kimi_api_key: str = ""
    kimi_llm_model: str = "kimi-k2.5"
    kimi_temperature: float = 0.6
    kimi_timeout: float = 60.0

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "gemma3:4b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_router_model: str = "gemma3:4b"
    ollama_timeout: float = 300.0

    # Embedding provider chain
    embedding_providers: list[str] = ["voyage", "ollama"]
    voyage_api_key: str = ""
    voyage_embed_model: str = "voyage-4-large"
    voyage_embed_dim: int = 1024

    # Qdrant
    qdrant_api_key: str = ""
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "documents"

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://admin:@localhost:5432/intodata_kb"

    # Hybrid search
    hybrid_search_enabled: bool = True
    sparse_embed_model: str = "Qdrant/bm25"

    # Reranking
    reranker_enabled: bool = True
    reranker_model: str = "rerank-2.5"
    rerank_top_n: int = 30
    reranker_min_score: float = 0.15

    # Query expansion
    query_expansion_enabled: bool = False

    # Search tuning
    default_top_k: int = 8
    default_threshold: float = 0.3
    prefetch_limit: int = 50
    max_result_documents: int = 6

    # Metadata queries
    metadata_queries_enabled: bool = True
    system_knowledge_injection: bool = True

    # Hindsight memory
    hindsight_enabled: bool = False
    hindsight_url: str = "http://localhost:8888"
    hindsight_recall_budget: str = "low"
    hindsight_recall_max_tokens: int = 1024
    hindsight_timeout: float = 300.0
    hindsight_project_banks_enabled: bool = True
    hindsight_distill_before_retain: bool = False
    hindsight_max_project_memories: int = 5

    # Auth
    jwt_secret_key: str = "change-me-in-production-use-a-strong-random-key"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    admin_default_password: str = "admin"
    cookie_secure: bool = False
    cookie_domain: str = ""
    allowed_origins: str = "http://localhost:3000,http://localhost:5173,http://localhost:8001"

    # Deployment-local email/invite settings. Empty SMTP config is allowed in
    # development, where invite links are logged instead of sent.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    smtp_from_name: str = "IntoData Nexus"
    smtp_use_tls: bool = True
    public_app_url: str = "http://localhost:3000"

    # MCP ingestion auth
    mcp_ingest_api_key: str = ""

    # Network hardening — comma-separated IPv4/IPv6 addresses or CIDRs. Empty = disabled.
    # ALLOWED_IPS is enforced by ip_allowlist middleware; TRUSTED_PROXIES is passed to
    # uvicorn's forwarded_allow_ips so slowapi / request.client.host see the real client IP
    # behind the reverse proxy.
    allowed_ips: str = ""
    trusted_proxies: str = "127.0.0.1"

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    # Tenant identity — surfaced by GET /api/v1/tenant/config. One-VPS-per-client
    # model means these are set per deployment in .env; the frontend reads them
    # once at boot and threads them through the sidebar / login / wizard chrome.
    tenant_name: str = "IntoData"
    tenant_slug: str = "intodata"
    tenant_environment: str = "development"  # development | staging | production

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    def validate_production_secrets(self) -> None:
        """In production mode, ensure secrets are not left at default values."""
        if self.app_env != "production":
            return
        if self.jwt_secret_key == "change-me-in-production-use-a-strong-random-key":
            raise ValueError(
                "CRITICAL: JWT_SECRET_KEY is still the default value. "
                "Set a strong random secret in production."
            )
        if self.admin_default_password == "admin":
            raise ValueError(
                "CRITICAL: ADMIN_DEFAULT_PASSWORD is still 'admin'. "
                "Set a strong password in production."
            )
        if not self.mcp_ingest_api_key:
            raise ValueError(
                "CRITICAL: MCP_INGEST_API_KEY is empty. "
                "Set a strong random key in production (used by MCP server to push docs)."
            )
        if "localhost" in self.public_app_url or "127.0.0.1" in self.public_app_url:
            raise ValueError(
                "CRITICAL: PUBLIC_APP_URL must be a real HTTPS app URL in production."
            )


settings = Settings()
settings.validate_production_secrets()
