"""Configuration for MCP server (Pydantic Settings)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPServerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MCP_",
        extra="ignore",
    )

    # PostgreSQL (same DB as RAG pipeline)
    database_url: str = (
        "postgresql+asyncpg://admin:@localhost:5432/intodata_kb"
    )

    # RAG pipeline base URL
    rag_base_url: str = "http://localhost:8000"

    # intodoc CLI path (defaults to system 'intodoc')
    intodoc_command: str = "intodoc"

    # claude CLI path (for doc generation)
    claude_command: str = "claude"
    # Claude Code model alias/name used by the claude-cli provider. Empty lets
    # Claude Code use its own configured default.
    claude_model: str = "sonnet"

    # Base data directory for logs, uploads, repos
    data_dir: Path = Path(__file__).resolve().parent.parent.parent / "data"

    # intodoc default output directory
    intodoc_output_dir: Path = Path(__file__).resolve().parent.parent.parent.parent / "intodoc-output"

    # vaxxinova project root (parent of mcp-server)
    project_root: Path = Path(__file__).resolve().parent.parent.parent.parent

    # Hindsight memory
    hindsight_enabled: bool = False
    hindsight_url: str = "http://localhost:8888"

    # Auth (shared with RAG backend)
    jwt_secret_key: str = "change-me-in-production-use-a-strong-random-key"
    ingest_api_key: str = ""
    jwt_algorithm: str = "HS256"
    github_webhook_secret: str | None = None  # MCP_GITHUB_WEBHOOK_SECRET
    dev_webhook_skip_signature: bool = False  # MCP_DEV_WEBHOOK_SKIP_SIGNATURE
    token_encryption_key: str = ""            # MCP_TOKEN_ENCRYPTION_KEY — Fernet key for PAT storage
    public_url: str = ""                      # MCP_PUBLIC_URL — public-facing base URL for webhook callbacks
    allowed_origins: str = "http://localhost:3000,http://localhost:5173,http://localhost:8001"

    # Network hardening — mirror of RAG backend. Empty ALLOWED_IPS = disabled.
    allowed_ips: str = ""
    trusted_proxies: str = "127.0.0.1"

    # IntoData Ops telemetry. Disabled unless ops_telemetry_url and
    # ops_telemetry_api_key are both set. Payloads must contain metadata only.
    ops_telemetry_url: str = ""
    ops_telemetry_api_key: str = ""
    ops_client_slug: str = ""
    ops_environment: str = "production"
    ops_release_version: str = "unknown"
    ops_telemetry_interval_seconds: int = 300

    # App env (development | staging | production) — toggles hard-fail secret validation
    app_env: str = "development"

    # Doc generation
    doc_generation_provider: str = "custom"
    # Supported values:
    #   "custom" | "moonshot/<model>" | "openai/<model>" | "ollama/<model>" | "anthropic/<model>"
    #   "claude-cli" (legacy subprocess fallback)

    # "single_shot" — Python pre-reads files, model extracts evidence in one call (works with any model)
    # "agentic"     — multi-turn action loop (requires Claude-level instruction following)
    investigation_strategy: str = "single_shot"

    doc_generation_model: str = "moonshot/kimi-k2.6"
    # write_model for Phase 3 (doc writing) — use "provider/model" format or "claude-cli".
    # Example: "anthropic/claude-sonnet-4-6", "openai/gpt-4o", "moonshot/kimi-k2.6", "claude-cli"
    # Set to same value as doc_generation_provider to use one model for all phases.
    doc_generation_write_model: str = "openai/deepseek-v4-pro"
    # Phase 1 investigation model. Defaults to doc_generation_model if not set.
    doc_generation_investigation_model: str | None = None   # MCP_DOC_GENERATION_INVESTIGATION_MODEL
    # Fallback model on provider failure (rate-limit, network, auth). No fallback if not set.
    doc_generation_fallback_model: str | None = None         # MCP_DOC_GENERATION_FALLBACK_MODEL
    large_project_threshold: int = 200  # jobs; >=this splits Phase 3 into 4 agents

    # API-based providers (anthropic/, openai/, google/, ollama/)
    doc_generation_api_key: str | None = None
    # Overrides ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY env vars
    doc_generation_base_url: str | None = None
    # For Ollama or custom OpenAI-compatible endpoints: "http://localhost:11434"
    model_pricing_json: str = ""
    # JSON object mapping model names to USD-per-million-token rates.
    # Example:
    # MCP_MODEL_PRICING_JSON='{"deepseek-v4-pro":{"input":0.28,"output":0.88}}'

    # Ollama (for AI job summaries)
    ollama_base_url: str = "http://localhost:11434"
    ollama_summary_model: str = "gemma3:12b"
    ollama_summary_concurrency: int = 4
    auto_generate_summaries: bool = False

    # App
    log_level: str = "INFO"
    auto_reingest_poll_interval_seconds: int = 300
    auto_reingest_poll_timezone: str = "Europe/Amsterdam"
    auto_reingest_poll_hours: str = "0,12"
    git_diff_patch_max_files: int = 5
    git_diff_patch_max_chars: int = 50_000
    git_diff_patch_max_docs: int = 3
    git_diff_patch_max_input_tokens: int = 80_000
    git_diff_patch_context_lines: int = 80
    git_diff_patch_max_chunks: int = 4
    git_diff_patch_max_summary_chunks: int = 12
    git_diff_patch_summary_output_tokens: int = 6_000
    git_diff_patch_min_confidence: float = 0.7
    git_diff_patch_output_tokens_small: int = 8_000
    git_diff_patch_output_tokens_medium: int = 16_000
    git_diff_patch_output_tokens_large: int = 32_000
    git_diff_patch_max_obligations: int = 24

    # Abandoned docs_staging/ dirs older than this are GC'd at startup. Active
    # resumes bump mtime so in-flight work is safe.
    staging_gc_days: int = 7

    # Doc generation bundle size limits (bytes)
    max_prompt_bundle_bytes: int = 200_000   # MCP_MAX_PROMPT_BUNDLE_BYTES
    max_ref_bundle_bytes: int = 80_000       # MCP_MAX_REF_BUNDLE_BYTES
    max_param_val_chars: int = 400           # MCP_MAX_PARAM_VAL_CHARS

    # Quick scan anti-pattern thresholds (job/component counts)
    qs_god_profile_threshold: int = 15       # MCP_QS_GOD_PROFILE_THRESHOLD
    qs_large_job_threshold: int = 10         # MCP_QS_LARGE_JOB_THRESHOLD
    qs_complex_tmap_threshold: int = 3       # MCP_QS_COMPLEX_TMAP_THRESHOLD

    # LLM output token limits per phase
    investigation_max_tokens: int = 10_000   # MCP_INVESTIGATION_MAX_TOKENS
    phase2_max_tokens: int = 16_000          # MCP_PHASE2_MAX_TOKENS
    phase3_max_tokens: int = 32_000          # MCP_PHASE3_MAX_TOKENS
    context_max_tokens: int = 900_000        # MCP_CONTEXT_MAX_TOKENS — hard pre-flight ceiling
    core_context_soft_target_tokens: int = 80_000   # MCP_CORE_CONTEXT_SOFT_TARGET_TOKENS
    core_context_hard_ceiling_tokens: int = 120_000  # MCP_CORE_CONTEXT_HARD_CEILING_TOKENS
    core_slice_concurrency: int = 4       # MCP_CORE_SLICE_CONCURRENCY
    phase2_merge_mode: str = "deterministic"  # MCP_PHASE2_MERGE_MODE: deterministic | llm
    source_context_mode: str = "direct_redacted"  # MCP_SOURCE_CONTEXT_MODE: structured | direct_redacted
    direct_source_inline_max_bytes: int = 120_000  # MCP_DIRECT_SOURCE_INLINE_MAX_BYTES
    direct_source_chunk_bytes: int = 80_000  # MCP_DIRECT_SOURCE_CHUNK_BYTES

    # Bounded internal multi-agent runtime
    agent_phase2_concurrency: int = 6        # MCP_AGENT_PHASE2_CONCURRENCY
    agent_phase3_concurrency: int = 4        # MCP_AGENT_PHASE3_CONCURRENCY
    agent_repair_concurrency: int = 3        # MCP_AGENT_REPAIR_CONCURRENCY
    agent_max_tool_calls: int = 12           # MCP_AGENT_MAX_TOOL_CALLS
    agent_max_tool_bytes: int = 80_000       # MCP_AGENT_MAX_TOOL_BYTES
    agent_max_tool_result_bytes: int = 12_000  # MCP_AGENT_MAX_TOOL_RESULT_BYTES
    agent_max_task_attempts: int = 2         # MCP_AGENT_MAX_TASK_ATTEMPTS
    agent_max_repair_attempts: int = 3       # MCP_AGENT_MAX_REPAIR_ATTEMPTS
    agent_max_split_depth: int = 2           # MCP_AGENT_MAX_SPLIT_DEPTH

    # Doc generation safety guards. These are model/provider agnostic and stop
    # runaway retry storms without reducing prompt/evidence depth.
    docgen_empty_outputs_per_doc_limit: int = 3       # MCP_DOCGEN_EMPTY_OUTPUTS_PER_DOC_LIMIT
    docgen_empty_outputs_per_run_limit: int = 10      # MCP_DOCGEN_EMPTY_OUTPUTS_PER_RUN_LIMIT
    docgen_allowed_slices_per_doc: int = 4            # MCP_DOCGEN_ALLOWED_SLICES_PER_DOC
    docgen_min_run_call_budget: int = 100             # MCP_DOCGEN_MIN_RUN_CALL_BUDGET
    docgen_call_budget_base: int = 50                 # MCP_DOCGEN_CALL_BUDGET_BASE
    docgen_call_budget_per_group: int = 8             # MCP_DOCGEN_CALL_BUDGET_PER_GROUP
    docgen_adaptive_concurrency: bool = True          # MCP_DOCGEN_ADAPTIVE_CONCURRENCY

    # Deep Dive repo-agent runtime
    deep_dive_engine: str = "repo_agent"               # MCP_DEEP_DIVE_ENGINE: repo_agent | claude_cli
    deep_dive_model: str = "openai/deepseek-v4-pro"    # MCP_DEEP_DIVE_MODEL
    deep_dive_api_key: str | None = None               # MCP_DEEP_DIVE_API_KEY
    deep_dive_base_url: str | None = None              # MCP_DEEP_DIVE_BASE_URL
    deep_dive_max_tool_calls: int = 40                 # MCP_DEEP_DIVE_MAX_TOOL_CALLS
    deep_dive_max_file_bytes: int = 120_000            # MCP_DEEP_DIVE_MAX_FILE_BYTES
    deep_dive_max_search_results: int = 50             # MCP_DEEP_DIVE_MAX_SEARCH_RESULTS
    deep_dive_timeout_s: float = 300.0                 # MCP_DEEP_DIVE_TIMEOUT_S
    deep_dive_allow_claude_cli_fallback: bool = False  # MCP_DEEP_DIVE_ALLOW_CLAUDE_CLI_FALLBACK

    # Instruction pack directory (relative to project_root) — US-106
    instruction_pack_dir: str = "packs/default"  # MCP_INSTRUCTION_PACK_DIR

    # RAG bridge HTTP timeouts (seconds)
    rag_push_timeout_s: float = 60.0         # MCP_RAG_PUSH_TIMEOUT_S
    rag_delete_timeout_s: float = 60.0       # MCP_RAG_DELETE_TIMEOUT_S
    rag_usage_timeout_s: float = 10.0        # MCP_RAG_USAGE_TIMEOUT_S

    @property
    def logs_dir(self) -> Path:
        d = self.data_dir / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def uploads_dir(self) -> Path:
        d = self.data_dir / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def repos_dir(self) -> Path:
        d = self.data_dir / "repos"
        d.mkdir(parents=True, exist_ok=True)
        return d


config = MCPServerConfig()

from mcp_server.platforms import supported_platform_ids  # noqa: E402

SUPPORTED_PLATFORMS: frozenset[str] = supported_platform_ids()


def validate_production_secrets() -> None:
    """Hard-fail if secrets are left at defaults in production mode."""
    if config.app_env != "production":
        return
    if config.jwt_secret_key == "change-me-in-production-use-a-strong-random-key":
        raise ValueError(
            "CRITICAL: MCP_JWT_SECRET_KEY is still the default value. "
            "Set a strong random secret in production (must match RAG backend's JWT_SECRET_KEY)."
        )
    if not config.ingest_api_key:
        raise ValueError(
            "CRITICAL: MCP_INGEST_API_KEY is empty. "
            "Set a strong random key in production (used to push generated docs to RAG)."
        )


validate_production_secrets()
