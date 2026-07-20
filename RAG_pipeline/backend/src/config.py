from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Default RAG chat model as a ``provider/model`` id. The provider is resolved
    # from the prefix by the provider registry. A superadmin override
    # (`rag_chat_model` in instance_settings) takes precedence. Selecting a model
    # routes to exactly its provider — there is no cross-provider fallback chain.
    rag_default_model: str = "kimi/kimi-k2.5"

    # Provider preference for non-agent generation surfaces.
    llm_providers: list[str] = ["kimi", "ollama"]

    # Unified bounded agent-loop limits. Chat routing is unconditional.
    agent_chat_max_iterations: int = Field(default=6, ge=1, le=20)
    agent_chat_max_tool_calls: int = Field(default=4, ge=0, le=12)
    agent_chat_deadline_seconds: float = Field(default=30.0, gt=0, le=300.0)
    agent_chat_max_cost_usd: float = Field(default=0.25, ge=0, le=100.0)

    # Standard agent runtime (V2) cohort routing — Slice 3. All default OFF so
    # merging this code changes nothing until a deployment explicitly opts in.
    # The master kill switch alone reverts every turn to the legacy runtime.
    agent_runtime_v2_enabled: bool = False
    # Explicit read-only canary allowlist by user id.
    agent_runtime_v2_readonly_users: list[int] = []
    # Stable per-user bucket percentage (0-100) for the read-only canary.
    agent_runtime_v2_readonly_percent: int = Field(default=0, ge=0, le=100)
    # Transports the V2 read-only cohort may serve.
    agent_runtime_v2_transports: list[str] = ["nonstream", "stream"]
    # Bounded grounding-correction rounds before the concise safe fallback.
    agent_runtime_v2_max_grounding_corrections: int = Field(default=1, ge=0, le=2)

    # Kimi (Moonshot AI)
    kimi_api_key: str = ""
    kimi_llm_model: str = "kimi-k2.5"
    kimi_temperature: float = 0.6
    kimi_timeout: float = 300.0

    # OpenAI (official) — provider id "openai"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout: float = 120.0

    # DeepSeek (OpenAI-compatible) — provider id "deepseek"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_timeout: float = 120.0

    # Anthropic (Claude) — provider id "anthropic"
    anthropic_api_key: str = ""
    anthropic_timeout: float = 120.0

    # Google Gemini — provider id "google". Reads GOOGLE_API_KEY; a deployment may
    # instead export GEMINI_API_KEY, mapped to this field below.
    google_api_key: str = ""
    google_timeout: float = 120.0

    # Shared sampling temperature for cloud chat providers (openai/deepseek/etc.).
    cloud_llm_temperature: float = 0.6

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
    mcp_docs_base_url: str = "http://localhost:8002/mcp"
    mcp_docs_request_timeout: float = 15.0

    # Accepted documentation generations can outlive a browser session. When a
    # deployment supplies a least-privilege MCP service access token, startup
    # runs a bounded background reconciler using it. Empty keeps polling off.
    # This is never persisted in workflow receipts or logged.
    mcp_docs_reconciliation_service_token: SecretStr | None = None
    mcp_docs_reconciliation_interval_seconds: int = Field(default=60, ge=1, le=3600)
    mcp_docs_reconciliation_batch_size: int = Field(default=50, ge=1, le=500)

    # Nexus orchestration packs. Definitions are data-only JSON; activation is
    # deployment configuration and can change the application without code edits.
    nexus_pack_directory: str = ""
    nexus_active_packs: str = "nexus.data-integration"

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

    # Live runtime status (EP-004 slice 1) — provider-agnostic contract surfaced by
    # GET /api/v1/runtime/status. Empty runtime_provider_key means disabled. A
    # configured key without runtime_provider_health_url means unavailable (the
    # provider integration exists but has no health check wired up yet).
    runtime_provider_key: str = ""
    runtime_provider_label: str = "Live runtime"
    runtime_provider_health_url: str = ""
    runtime_status_timeout: float = 5.0

    # Generic runtime tool boundary (EP-004 slice 2) — bounds applied uniformly
    # to every tool call in runtime/client.py, regardless of provider.
    runtime_tool_timeout: float = 10.0
    runtime_tool_result_max_chars: int = 12000

    # Backend tool-use loop (EP-004 slice 3) — hard cap on LLM<->tool round
    # trips per conversational turn, regardless of provider. 4 lets the model
    # chain a lookup (list_executions/detect_failures) -> fetch (logs/details)
    # within one turn while staying bounded.
    runtime_tool_loop_max_iterations: int = 4

    # Cap on tool calls executed from a single assistant turn (models may issue
    # several in parallel). Calls beyond the cap are NOT dropped — they receive a
    # synthetic budget-exceeded tool result, because OpenAI-compat APIs reject any
    # history where a tool_call_id has no tool message.
    runtime_tool_max_parallel_calls: int = 5

    # Runtime insight LLM fallback (EP-005 slice 4) — opt-in, off by default.
    # When True, an incident whose deterministic confidence is "low" escalates to a
    # SINGLE bounded, redacted LLM call (via the provider-neutral generate_answer).
    # When False (default), the deterministic path is byte-for-byte unchanged and no
    # LLM client is invoked. High/medium incidents never escalate regardless.
    runtime_llm_fallback_enabled: bool = False

    # Talend TMC runtime provider (EP-004 slice 5) — opt-in adapter behind the
    # generic runtime contract. Default empty keeps Talend disabled. The API token
    # is read from a mounted secret file, never a plaintext env var. Enable by
    # setting runtime_provider_key="talend_tmc" plus these two values.
    talend_tmc_base_url: str = ""
    talend_tmc_api_token_file: str = ""
    talend_tmc_request_timeout: float = 10.0

    # Runtime source sync scheduler (Runtime-Agnostic Failure Intelligence Slice 7).
    # Disabled by default. When enabled, the backend starts a background loop that
    # runs one sync cycle per configured project every runtime_sync_interval_seconds.
    runtime_sync_scheduler_enabled: bool = False
    runtime_sync_interval_seconds: int = 60
    runtime_sync_source_key: str = ""
    runtime_sync_project_ids: str = ""
    # Optional semantic recall over curated runtime summaries/incidents. Raw log
    # lines remain in Postgres and are never embedded.
    runtime_semantic_enabled: bool = False

    # Runtime source credentials. The primary Fernet key encrypts newly-written
    # source credentials; the previous key is optional and used only during lazy
    # rotation of old rows.
    source_credentials_encryption_key: str = ""
    source_credentials_encryption_key_previous: str = ""
    source_credentials_encryption_key_id: str = "primary"
    source_credentials_encryption_key_previous_id: str = "previous"

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
        if not self.source_credentials_encryption_key:
            raise ValueError(
                "CRITICAL: SOURCE_CREDENTIALS_ENCRYPTION_KEY is empty. "
                "Set a Fernet key before enabling runtime source credentials in production."
            )
        if "localhost" in self.public_app_url or "127.0.0.1" in self.public_app_url:
            raise ValueError(
                "CRITICAL: PUBLIC_APP_URL must be a real HTTPS app URL in production."
            )


settings = Settings()
settings.validate_production_secrets()
