from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    rag_default_model: str = "kimi/kimi-k2.6"

    # Provider preference for non-agent generation surfaces.
    llm_providers: list[str] = ["kimi", "ollama"]

    # Maximum tokens ANY answer-generating model call may emit. One value for
    # every provider and every surface.
    #
    # This was previously four hardcoded numbers that disagreed: 4096 in the
    # OpenAI-compatible adapter, 2048 in the Anthropic and Google adapters, 2048
    # in the agent runtime's extra_body, and no ceiling at all on the agent's
    # openai/deepseek branch. The same question therefore truncated at a
    # different length -- or not at all -- depending only on which provider
    # served it, and the LOWEST ceiling sat on the agent path that produces the
    # longest answers. Six answers in the production history stopped exactly on
    # one of these ceilings.
    #
    # 8192 is deliberately moderate: double the previous highest ceiling and
    # comfortably inside every configured model's output limit. Output tokens are
    # billed only when actually emitted, so raising the ceiling costs nothing for
    # answers that were never going to reach it -- it only stops the long ones
    # being cut off. Truncation is still DETECTED and surfaced above this limit;
    # the ceiling reduces how often it happens, it is not the fix on its own.
    llm_max_output_tokens: int = Field(default=8192, ge=256, le=32768)

    # Explicitly approved, contract-compatible fallbacks for documentation answer
    # generation. Values are full provider/model ids. Empty by default: notably,
    # the locally configured Ollama model is not silently treated as equivalent.
    rag_approved_fallback_models: list[str] = []
    rag_documentation_provider_max_retries: int = Field(default=1, ge=0, le=3)

    # Unified bounded agent-loop limits. Chat routing is unconditional.
    # Raised 2026-07-22: default ceilings were tight enough that structured-output
    # retries (not real work) could exhaust the budget before the model reached
    # its actual tool call. Ceilings stay in place (never fully removed) so a
    # stuck retry loop still can't run unbounded cost/time.
    agent_chat_max_iterations: int = Field(default=15, ge=1, le=40)
    agent_chat_max_tool_calls: int = Field(default=20, ge=0, le=24)
    agent_chat_deadline_seconds: float = Field(default=120.0, gt=0, le=300.0)
    agent_chat_max_cost_usd: float = Field(default=1.50, ge=0, le=100.0)

    # Standard agent runtime (V2) cohort routing — Slice 3. All default OFF so
    # merging this code changes nothing until a deployment explicitly opts in.
    # The master kill switch alone reverts every turn to the legacy runtime.
    agent_runtime_v2_canary_stage: str = "off"
    agent_runtime_v2_enabled: bool = False
    # Explicit read-only canary allowlist by user id.
    agent_runtime_v2_readonly_users: list[int] = []
    # Stable per-user bucket percentage (0-100) for the read-only canary.
    agent_runtime_v2_readonly_percent: int = Field(default=0, ge=0, le=100)
    # Transports the V2 read-only cohort may serve.
    agent_runtime_v2_transports: list[str] = ["nonstream", "stream"]
    # Bounded grounding-correction rounds before the concise safe fallback.
    agent_runtime_v2_max_grounding_corrections: int = Field(default=1, ge=0, le=2)
    # Slice 4 planning cohort. It is independently default-off and therefore
    # cannot widen the existing read-only canary accidentally.
    agent_runtime_v2_planning_enabled: bool = False
    agent_runtime_v2_planning_users: list[int] = []
    agent_runtime_v2_planning_percent: int = Field(default=0, ge=0, le=100)
    # Slice 5 effect/workflow cohort. Independently default-off.
    agent_runtime_v2_effects_enabled: bool = False
    agent_runtime_v2_effect_users: list[int] = []
    agent_runtime_v2_effect_percent: int = Field(default=0, ge=0, le=100)
    # Slice 6 persisted live-event projection. Independently default-off.
    agent_runtime_v2_live_streaming_enabled: bool = False
    agent_runtime_v2_live_streaming_users: list[int] = []
    agent_runtime_v2_live_streaming_percent: int = Field(default=0, ge=0, le=100)
    # Stream the model's answer prose while it is still being written, on every
    # capability path rather than only the deterministically-cited documentation
    # answer. The text is provisional: it is published `replayable=False` and the
    # authoritative post-persistence replay overwrites it, so what the server
    # stored always wins on screen. Default-on because it is the streaming
    # behaviour users expect, and separately switchable because it does put
    # pre-validation prose on screen -- an operator with a stricter disclosure
    # posture can turn it off without losing the live progress projection.
    agent_runtime_v2_draft_answer_streaming_enabled: bool = True
    agent_runtime_v2_model_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    # Read tools include answer_from_documentation, which runs full retrieval
    # + answer generation in one call (not just retrieval) and can legitimately
    # take longer than a pure evidence lookup on broad/exhaustive questions.
    # Upper bound matches the hard deadline's, because `reconcile_turn_deadlines`
    # raises this to the hard deadline: a finished answer must not be discarded by
    # a limit lower than the turn's own.
    agent_runtime_v2_read_tool_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    agent_runtime_v2_write_tool_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    agent_runtime_v2_workflow_handoff_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    # Upper bound matches the hard deadline's: `reconcile_turn_deadlines` raises
    # this toward `hard - model_timeout`, which can exceed 300 on a long budget.
    agent_runtime_v2_soft_deadline_seconds: float = Field(default=180.0, gt=0, le=600)
    agent_runtime_v2_hard_deadline_seconds: float = Field(default=300.0, gt=0, le=600)
    agent_runtime_v2_max_model_calls: int = Field(default=40, ge=2, le=64)
    # Keep one answer call plus one grounding-correction call available after
    # bounded evidence gathering. Provider retries are transport-level only and
    # do not permit additional tool execution.
    agent_runtime_v2_synthesis_reserve_calls: int = Field(default=2, ge=1, le=4)
    agent_runtime_v2_provider_max_retries: int = Field(default=1, ge=0, le=3)

    @model_validator(mode="after")
    def validate_agent_runtime_canary_stage(self) -> "Settings":
        from src.agent_runtime_canary import CanaryStagePolicy

        CanaryStagePolicy(stage=self.agent_runtime_v2_canary_stage).validate_rollout_settings(self)
        return self

    @model_validator(mode="after")
    def reconcile_turn_deadlines(self) -> "Settings":
        """Make the four interacting turn deadlines mutually consistent.

        A chat turn is bounded by four independently-configured numbers, and a
        deployment can set them so that a *completed* answer is thrown away:
        ``answer_from_documentation`` runs full retrieval plus one long answer
        generation inside the read-tool timeout, so a read-tool timeout below the
        hard deadline discards finished work rather than preventing an overrun.

        SAFETY PROPERTY: every clamp here can only move a turn-level abort LATER,
        never earlier. The hard deadline is the single authority and is never
        modified. Specifically:

        * ``hard_deadline``   -- untouched, always. It alone decides when a turn dies.
        * ``soft_deadline``   -- only ever raised (capped just under the hard
          deadline). It gates *starting* new work, so raising it can only allow
          work that was previously refused.
        * ``read_tool_timeout`` -- only ever raised, up to the hard deadline. The
          budget controller already returns ``min(configured, remaining_hard)``,
          so the hard deadline still binds; raising this only stops the tool
          timeout from firing *before* the turn's real limit.
        * ``model_timeout``   -- untouched. It bounds a single OUTER agent model
          call (~2s in practice), and the soft deadline's synthesis reserve is
          derived from it, so changing it here would move that reserve too.
        * provider timeouts   -- only ever *lowered*, and never below the hard
          deadline. A provider socket that outlives the turn cannot rescue it, it
          can only leak; clamping to the hard deadline changes nothing about when
          the turn ends because the turn was already over.

        Nothing is lowered except a provider timeout that exceeded the whole turn
        budget, so no configuration can be made to abort sooner than it does today.
        """

        hard = self.agent_runtime_v2_hard_deadline_seconds
        # A read tool that finished its work must not be killed by a limit lower
        # than the turn's own. The budget controller still returns
        # min(configured, remaining_hard), so the hard deadline keeps binding.
        self.agent_runtime_v2_read_tool_timeout_seconds = max(
            self.agent_runtime_v2_read_tool_timeout_seconds, hard
        )
        # The soft deadline exists to reserve time for the final answer call, so
        # its principled value is "one full model call before the hard deadline".
        # Configured lower than that, it refuses second rounds that would have
        # comfortably finished; it is therefore raised, never lowered. Left alone
        # when model_timeout >= hard, where the subtraction says nothing useful.
        synthesis_reserve = self.agent_runtime_v2_model_timeout_seconds
        if synthesis_reserve < hard:
            self.agent_runtime_v2_soft_deadline_seconds = max(
                self.agent_runtime_v2_soft_deadline_seconds, hard - synthesis_reserve
            )
        # A provider socket that outlives the whole turn cannot rescue it; it can
        # only keep a dead connection open. Clamped to the turn budget, never below.
        for field in ("kimi_timeout", "openai_timeout", "deepseek_timeout"):
            setattr(self, field, min(getattr(self, field), hard))
        return self

    # Kimi (Moonshot AI)
    kimi_api_key: str = ""
    # Read directly by the V2 read-only runtime (readonly_runtime._build_model),
    # which does NOT consult instance_settings -- so this default, not the
    # `rag_chat_model` override, decides which model a chat turn calls. Moonshot
    # retired kimi-k2.5 (404 "Not found the model or Permission denied"), which
    # surfaced as every turn failing with stop_reason=planner_unavailable.
    kimi_llm_model: str = "kimi-k2.6"
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

    # Markdown sections dropped at chunk time, matched on heading title (any
    # level, case-insensitive) together with their descendants.
    #
    # "Evidence Register" is a per-document provenance table: same columns, same
    # confidence tokens, heavily overlapping artifact paths in every document
    # that carries one. On the Scherpenhuizen corpus that is 64 KB across 12 of
    # 13 documents -- ~7% of the curated bytes -- and it is near-duplicate by
    # construction, so a query naming an artifact path can spend most of its
    # top-k on five interchangeable registers from five different documents
    # instead of on the prose that answers the question. The registers remain in
    # the published documents, where a human reading one wants them; they simply
    # stop competing for retrieval slots.
    chunk_excluded_sections: list[str] = ["Evidence Register"]

    # Retrieval ladder rung 2 (internal layers). Wider recall than rung 1:
    # the deep rung exists to find what rung 1 missed, so it can afford a
    # larger candidate pool, forced query expansion, and section expansion.
    # Rung 1 and /query keep the defaults above.
    rung2_prefetch_limit: int = 150
    rung2_rerank_top_n: int = 75
    rung2_query_expansion_enabled: bool = True
    rung2_entity_match_limit: int = 25
    rung2_section_expansion_limit: int = 40

    # Rung 1 exact-name guarantee.
    #
    # `search_layered(matched_units=...)` was the codebase's only "cannot lose the
    # ranking contest" path and nothing ever populated it, so a question naming a
    # known job could be answered from whatever the embedding happened to rank
    # first. Wiring it to rung 2 alone would not have fixed that: rung 2 fires
    # only when rung 1 comes back thin, and the measured failure -- a question
    # about nine named interfaces answered from five -- had a full rung 1.
    #
    # So the guarantee runs at rung 1 too, against the default canonical
    # partition, in parallel with the semantic searches. It costs one Postgres
    # alias lookup and one Qdrant scroll, and only for questions that actually
    # name something the project knows.
    entity_exact_match_enabled: bool = True
    #: Chunks the rung-1 guarantee may add. Smaller than rung 2's budget: these
    #: chunks are pinned to score 1.0 and therefore displace ranked results from
    #: the reference list, so the guarantee has to stay a floor and not become the
    #: answer.
    entity_exact_match_limit: int = 12

    # Rung 2 activation. `search_layered` had every knob above and no caller, so
    # the internal partitions were unreachable in production: on one measured
    # corpus 88% of the chunks (the per-group Phase 2 analyses and the
    # deterministic support maps) could not be retrieved by any question.
    #
    # Escalation is conditional, not unconditional: `search_layered` forces an
    # LLM query expansion, and chat turns are decode-bound, so paying for it on
    # every turn would tax the majority of questions that rung 1 already answers
    # well. Firing it when rung 1 comes back thin spends that budget exactly
    # where the alternative is an answer with no sources at all.
    deep_layer_escalation_enabled: bool = True
    deep_layer_source_layers: list[str] = [
        "technical_analysis",
        "support_maps",
        "scorecard",
        "evidence_slices",
    ]
    #: Escalate when rung 1 returned fewer than this many source chunks.
    deep_layer_min_source_hits: int = 3
    deep_layer_top_k: int = 6

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

    # Topology artifact reads (src/topology) reuse mcp_docs_base_url and
    # mcp_docs_request_timeout — same upstream service. Only the artifact cache
    # is topology-specific: the landscape document changes on a rescan or a
    # documentation publish, so a short TTL keeps a chat turn cheap while a
    # fingerprint change still invalidates immediately on the next fetch.
    topology_landscape_cache_ttl_seconds: float = Field(default=60.0, ge=0, le=3600)

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
    # Per-sync ceiling on how many FAILED runs have their logs downloaded. This was
    # a hardcoded 25, which left most failures with no log lines at all and so no
    # diagnosable content — the insight layer could only emit a placeholder cause
    # for them. The batch still must stay bounded because each download is a
    # separate TMC round trip and each captured run may contribute
    # 10,000 Pydantic log models to the in-memory sync batch. Ten recent failures
    # keeps a poll bounded under the backend's 1GiB container limit while still
    # continuously capturing new operational evidence. Set to 0 for the provider
    # fallback ceiling; unbounded batches are intentionally unsupported.
    talend_tmc_failed_log_run_limit: int = Field(default=10, ge=0)
    # Route throughput lives in the deployment log, not in the API payload, and
    # recovering it costs two small requests per route. Kept separate from the
    # failed-job ceiling: sharing that one left 40 of 46 routes without any
    # exchange numbers at all.
    talend_tmc_deployment_counter_run_limit: int = Field(default=100, ge=0)
    # Per-sync ceiling on how many runs have component metrics downloaded.
    talend_tmc_component_metric_run_limit: int = Field(default=25, ge=0)
    # Per-executable ceiling on how many runs of execution HISTORY are ingested per
    # sync. The provider walks its 10-day windows newest-first and stops as soon as
    # this budget is spent, so the value decides how far back a task's history
    # actually reaches -- not talend_tmc history_days, which only sets the outer
    # window. At 250 a task running every 10 minutes reaches back about 1.7 days
    # while a twice-weekly task reaches the full window, which produces a
    # two-tiered history that reads as a broken trend chart. Raise it to even the
    # coverage out, remembering that every sync re-walks the whole window: runs
    # ingested per poll is roughly tasks x this value. Set to 0 for the provider
    # fallback ceiling.
    talend_tmc_runs_per_executable_limit: int = Field(default=250, ge=0)

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

    # Subscription licence (src/licensing). ``enforce`` makes an expired or
    # missing licence view-only: no chat/LLM turns. ``off`` is for local dev and
    # is refused in production. The same file and slug are mounted into the
    # mcp-server, which enforces generations and scans.
    licence_enforcement: Literal["enforce", "off"] = "off"
    licence_file: str = "/etc/intodata-kb/licence/licence.jwt"
    licence_client_slug: str = ""

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
        if self.licence_enforcement != "enforce":
            raise ValueError(
                "CRITICAL: LICENCE_ENFORCEMENT must be 'enforce' in production."
            )
        if not self.licence_client_slug:
            raise ValueError(
                "CRITICAL: LICENCE_CLIENT_SLUG is empty. It must match the licence subject."
            )


settings = Settings()
settings.validate_production_secrets()
