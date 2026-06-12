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
    kimi_timeout: float = 300.0

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
    neighbor_chunk_expansion_enabled: bool = True
    neighbor_chunk_window: int = 1
    # Default corpus partition: canonical_docs only. IsEmptyCondition in the filter
    # ensures legacy chunks (no source_layer payload key) are always included too.
    default_source_layers: list[str] = ["canonical_docs"]

    # Retrieval ladder rung 2 (internal layers). Wider recall than rung 1:
    # the deep rung exists to find what rung 1 missed, so it can afford a
    # larger candidate pool, forced query expansion, and section expansion.
    # Rung 1 and /query keep the defaults above.
    rung2_prefetch_limit: int = 150
    rung2_rerank_top_n: int = 75
    rung2_query_expansion_enabled: bool = True
    rung2_entity_match_limit: int = 25
    rung2_section_expansion_limit: int = 40

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

    # Admin bootstrap
    admin_initial_email: str = "admin@localhost"

    # Auth
    jwt_secret_key: str = "change-me-in-production-use-a-strong-random-key"
    # Comma-separated keys that are no longer used for signing but are still
    # accepted for token verification while existing tokens drain out after rotation.
    # Example rotation procedure: generate a new key, move the current key here,
    # set the new key as jwt_secret_key, then restart.
    jwt_previous_keys: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 14
    admin_default_password: str = "admin"
    cookie_secure: bool = False
    cookie_domain: str = ""
    allowed_origins: str = "http://localhost:3000,http://localhost:5173,http://localhost:8001"

    # CSRF — double-submit cookie pattern. Disable in test environments so
    # existing tests don't need to carry a CSRF token in every request.
    csrf_enabled: bool = True

    # SSO / OIDC — disabled by default, enabled per deployment via .env
    sso_enabled: bool = False
    oidc_discovery_url: str = ""    # e.g. https://accounts.google.com/.well-known/openid-configuration
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""     # must match the IdP application configuration

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

    # Metrics — set False in environments that don't scrape Prometheus
    metrics_enabled: bool = True

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
