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

    # Public starter repo admins can clone and branch to create swap packs.
    swap_pack_starter_repo_url: str = "https://github.com/joshitachu/swap-pack-starter"

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

    # GitHub App (repo connection via install flow instead of stored PATs).
    # When configured, repos connect by installing this App on the owner's org;
    # the backend stores only a non-secret installation id and mints short-lived
    # installation tokens on demand. PAT connection remains a fallback.
    github_app_id: str = ""                   # MCP_GITHUB_APP_ID — numeric App ID (the JWT issuer)
    github_app_slug: str = ""                 # MCP_GITHUB_APP_SLUG — App URL slug, for the install redirect
    github_app_private_key: str = ""          # MCP_GITHUB_APP_PRIVATE_KEY — PEM (literal or with \n escapes)
    github_app_private_key_file: str = ""     # MCP_GITHUB_APP_PRIVATE_KEY_FILE — path to PEM (preferred over inline)
    # App OAuth client (user-authorization during install). Used to verify — via the
    # connecting user's own GET /user/installations — that they actually control the
    # installation before binding it. Required for multi-tenant deployments.
    github_app_client_id: str = ""            # MCP_GITHUB_APP_CLIENT_ID
    github_app_client_secret: str = ""        # MCP_GITHUB_APP_CLIENT_SECRET
    # Frontend URL to send the browser back to after a GitHub App install. The
    # callback appends ?installation_id=... so the UI can show the repo picker.
    # Empty = the callback returns JSON instead of redirecting.
    github_app_post_install_redirect: str = ""  # MCP_GITHUB_APP_POST_INSTALL_REDIRECT
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
    # Mission Control inbound control key — set this to allow Mission Control to
    # trigger scans and doc generation on this VPS remotely.
    ops_control_key: str = ""

    # App env (development | staging | production) — toggles hard-fail secret validation
    app_env: str = "development"

    # Doc generation
    doc_generation_provider: str = "custom"
    # Supported values:
    #   "custom" | "openai/<model>" | "anthropic/<model>" | "google/<model>" | "ollama/<model>"
    #   "claude-cli" (legacy subprocess fallback)

    # "single_shot" — Python pre-reads files, model extracts evidence in one call (works with any model)
    # "agentic"     — multi-turn action loop (requires Claude-level instruction following)
    investigation_strategy: str = "single_shot"

    doc_generation_model: str = "anthropic/claude-sonnet-4-6"
    # write_model for Phase 3 (doc writing) — use "provider/model" format or "claude-cli".
    # Example: "anthropic/claude-sonnet-4-6", "google/gemini-2.5-pro", "openai/gpt-4.1", "claude-cli"
    # Set to same value as doc_generation_model to use one model for all phases.
    doc_generation_write_model: str = "anthropic/claude-sonnet-4-6"
    # Phase 1 investigation model. Defaults to doc_generation_model if not set.
    doc_generation_investigation_model: str | None = (
        "google/gemini-2.5-flash"
    )  # MCP_DOC_GENERATION_INVESTIGATION_MODEL
    # Fallback model on provider failure (rate-limit, network, auth). No fallback if not set.
    doc_generation_fallback_model: str | None = None  # MCP_DOC_GENERATION_FALLBACK_MODEL
    doc_generation_enterprise_mode_default: bool = True
    doc_generation_quality_threshold: float = 4.0
    large_project_threshold: int = 200  # jobs; >=this splits Phase 3 into 4 agents
    small_project_threshold: int = 10   # MCP_SMALL_PROJECT_THRESHOLD — unit count; <=this uses direct path (no Phase 1, no agent runtime for Phase 2)

    # API-based providers. First-party providers read ANTHROPIC_API_KEY,
    # OPENAI_API_KEY, GOOGLE_API_KEY/GEMINI_API_KEY directly. Keep the generic
    # override for custom OpenAI-compatible endpoints and legacy DeepSeek setups.
    doc_generation_api_key: str | None = None
    doc_generation_base_url: str | None = None
    doc_generation_deepseek_api_key: str | None = None
    doc_generation_deepseek_base_url: str | None = None
    # Route33 is used only through explicit route33/<model> doc-generation model
    # ids. Do not commit keys; set MCP_DOC_GENERATION_ROUTE33_API_KEY or
    # ROUTE33_API_KEY in the runtime environment.
    doc_generation_route33_api_key: str | None = None
    doc_generation_route33_base_url: str = "https://api.route33.ai/v1"
    doc_generation_lock_stale_seconds: int = 7200
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
    git_diff_patch_max_docs: int = 10
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

    # Living-Document Reingestion (Slice 1). When enabled, the fingerprint
    # dirty-set is the single front-line routing driver for BOTH Phase 2 analysis
    # scope and the Phase 3 document set. The doc-count thresholds below
    # (reingestion_full_regen_doc_threshold, git_diff_patch_max_docs) and the
    # path-based resolve_changed_groups are RETIRED as routing drivers under this
    # flag. Default off so deploy is a no-op until baselines are primed.
    impact_routing_enabled: bool = False  # MCP_IMPACT_ROUTING_ENABLED

    # Deterministic reingestion routing (Slice 3). The affected-doc set is
    # computed in Python; a change touching more than this many docs routes to a
    # full regeneration rather than a scoped one.
    # RETIRED as a routing driver when impact_routing_enabled is True (kept for
    # the flag-off legacy path and removed in a later cleanup once defaulted on).
    reingestion_full_regen_doc_threshold: int = 8  # MCP_REINGESTION_FULL_REGEN_DOC_THRESHOLD
    # Optional expand-only, non-blocking LLM semantic pass. Disabled by default
    # so routing is fully deterministic (no model call on the floor path). When
    # enabled it may only ADD docs to Python's authoritative set; it can never
    # remove a doc, downgrade to skip, or change the chosen strategy.
    reingestion_llm_expand_enabled: bool = False  # MCP_REINGESTION_LLM_EXPAND_ENABLED
    # Slice 3 -- Group-delta classifier threshold.
    # Fraction of a group's fields (param values) that may change while still
    # qualifying for patch mode. At or above this fraction, scoped regen is used.
    reingestion_patch_field_edit_threshold: float = 0.20  # MCP_REINGESTION_PATCH_FIELD_EDIT_THRESHOLD

    # Abandoned docs_staging/ dirs older than this are GC'd at startup. Active
    # resumes bump mtime so in-flight work is safe.
    staging_gc_days: int = 7

    # Doc generation bundle size limits (bytes)
    max_prompt_bundle_bytes: int = 200_000   # MCP_MAX_PROMPT_BUNDLE_BYTES
    max_ref_bundle_bytes: int = 80_000       # MCP_MAX_REF_BUNDLE_BYTES
    max_param_val_chars: int = 16_000        # MCP_MAX_PARAM_VAL_CHARS (was 400; raised 2026-06-10 to stop truncating Lobster field_wiring past 14 of 123 arrows)

    # Quick scan anti-pattern thresholds (job/component counts)
    qs_god_profile_threshold: int = 15       # MCP_QS_GOD_PROFILE_THRESHOLD
    qs_large_job_threshold: int = 10         # MCP_QS_LARGE_JOB_THRESHOLD
    qs_complex_tmap_threshold: int = 3       # MCP_QS_COMPLEX_TMAP_THRESHOLD

    # Minimum preprocess content bytes to attempt LLM investigation.
    # Groups with stripped content below this threshold are skipped immediately
    # (all units marked as scanner-limited) rather than burning retries on empty source.
    min_investigation_content_bytes: int = 200  # MCP_MIN_INVESTIGATION_CONTENT_BYTES

    # LLM output token limits per phase
    investigation_max_tokens: int = 10_000   # MCP_INVESTIGATION_MAX_TOKENS
    phase2_max_tokens: int = 32_000          # MCP_PHASE2_MAX_TOKENS
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
    agent_phase1_concurrency: int = 4        # MCP_AGENT_PHASE1_CONCURRENCY
    agent_phase1_agentic: bool = False       # MCP_AGENT_PHASE1_AGENTIC — use chunk-manifest investigator
    agent_phase1_batch_size: int = 20        # MCP_AGENT_PHASE1_BATCH_SIZE — units per extraction call
    agent_phase2_concurrency: int = 6        # MCP_AGENT_PHASE2_CONCURRENCY
    agent_phase2_max_slices: int = 7         # MCP_AGENT_PHASE2_MAX_SLICES — hard cap on _split_context_pack fan-out
    agent_phase3_concurrency: int = 4        # MCP_AGENT_PHASE3_CONCURRENCY
    agent_repair_concurrency: int = 3        # MCP_AGENT_REPAIR_CONCURRENCY
    agent_max_tool_calls: int = 25           # MCP_AGENT_MAX_TOOL_CALLS
    agent_max_tool_bytes: int = 600_000      # MCP_AGENT_MAX_TOOL_BYTES
    agent_max_tool_result_bytes: int = 50_000  # MCP_AGENT_MAX_TOOL_RESULT_BYTES
    agent_phase2_tool_input_token_cap: int = 180_000  # MCP_AGENT_PHASE2_TOOL_INPUT_TOKEN_CAP
    agent_phase3_tool_input_token_cap: int = 600_000  # MCP_AGENT_PHASE3_TOOL_INPUT_TOKEN_CAP
    # First-pass Phase 3 doc generation runs single-shot (no tool loop) by default:
    # the targeted doc pack is primary and sufficient, so token use scales with pack
    # size instead of an accumulating tool conversation. Set True to restore the
    # agentic tool loop for first-pass docs (repair phases keep tools regardless).
    agent_phase3_first_pass_tools: bool = False  # MCP_AGENT_PHASE3_FIRST_PASS_TOOLS
    agent_max_task_attempts: int = 2         # MCP_AGENT_MAX_TASK_ATTEMPTS
    agent_max_repair_attempts: int = 3       # MCP_AGENT_MAX_REPAIR_ATTEMPTS
    agent_max_split_depth: int = 2           # MCP_AGENT_MAX_SPLIT_DEPTH
    agent_dag_enabled: bool = False          # MCP_AGENT_DAG_ENABLED — use validated DAG orchestration
    agent_planner_enabled: bool = False      # MCP_AGENT_PLANNER_ENABLED — ask model for execution graph
    agent_planner_mode: str = "llm"          # MCP_AGENT_PLANNER_MODE: llm | deterministic
    agent_planner_max_tokens: int = 8_000    # MCP_AGENT_PLANNER_MAX_TOKENS
    agent_planner_require_valid_json: bool = True  # MCP_AGENT_PLANNER_REQUIRE_VALID_JSON
    agent_dag_strict_validation: bool = True # MCP_AGENT_DAG_STRICT_VALIDATION
    agent_dag_max_nodes: int = 200           # MCP_AGENT_DAG_MAX_NODES
    agent_dag_max_depth: int = 20            # MCP_AGENT_DAG_MAX_DEPTH
    agent_smart_repair_enabled: bool = False # MCP_AGENT_SMART_REPAIR_ENABLED — classify failures before repair

    # Pipeline forensics: when enabled, human-only debug artifacts (model-call
    # prompt/context/request/response snapshots, agent-task traces) are written
    # under docs_staging/_debug/. Default runs persist only consumed artifacts.
    pipeline_debug: bool = False             # MCP_PIPELINE_DEBUG

    # Doc generation safety guards. These are model/provider agnostic and stop
    # runaway retry storms without reducing prompt/evidence depth.
    docgen_empty_outputs_per_doc_limit: int = 3       # MCP_DOCGEN_EMPTY_OUTPUTS_PER_DOC_LIMIT
    docgen_empty_outputs_per_run_limit: int = 10      # MCP_DOCGEN_EMPTY_OUTPUTS_PER_RUN_LIMIT
    docgen_allowed_slices_per_doc: int = 4            # MCP_DOCGEN_ALLOWED_SLICES_PER_DOC
    docgen_min_run_call_budget: int = 100             # MCP_DOCGEN_MIN_RUN_CALL_BUDGET
    docgen_call_budget_base: int = 50                 # MCP_DOCGEN_CALL_BUDGET_BASE
    docgen_call_budget_per_group: int = 8             # MCP_DOCGEN_CALL_BUDGET_PER_GROUP
    docgen_adaptive_concurrency: bool = True          # MCP_DOCGEN_ADAPTIVE_CONCURRENCY
    phase_cost_warning_input_tokens: int = 500_000   # MCP_PHASE_COST_WARNING_INPUT_TOKENS

    # Deep Dive repo-agent runtime
    deep_dive_engine: str = "repo_agent"               # MCP_DEEP_DIVE_ENGINE: repo_agent | claude_cli
    deep_dive_model: str = "anthropic/claude-sonnet-4-6"  # MCP_DEEP_DIVE_MODEL
    deep_dive_api_key: str | None = None               # MCP_DEEP_DIVE_API_KEY
    deep_dive_base_url: str | None = None              # MCP_DEEP_DIVE_BASE_URL
    deep_dive_max_tool_calls: int = 40                 # MCP_DEEP_DIVE_MAX_TOOL_CALLS
    deep_dive_max_file_bytes: int = 120_000            # MCP_DEEP_DIVE_MAX_FILE_BYTES
    deep_dive_max_search_results: int = 50             # MCP_DEEP_DIVE_MAX_SEARCH_RESULTS
    deep_dive_timeout_s: float = 300.0                 # MCP_DEEP_DIVE_TIMEOUT_S
    deep_dive_allow_claude_cli_fallback: bool = False  # MCP_DEEP_DIVE_ALLOW_CLAUDE_CLI_FALLBACK
    deep_dive_max_answer_tokens: int = 8_000          # MCP_DEEP_DIVE_MAX_ANSWER_TOKENS

    # Instruction pack directory (relative to project_root) — US-106
    instruction_pack_dir: str = "packs/default"  # MCP_INSTRUCTION_PACK_DIR

    # Upload limits
    max_upload_zip_bytes: int = 500 * 1024 * 1024  # MCP_MAX_UPLOAD_ZIP_BYTES — 500 MB default
    max_upload_zip_entries: int = 20_000  # MCP_MAX_UPLOAD_ZIP_ENTRIES
    max_upload_zip_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024  # MCP_MAX_UPLOAD_ZIP_UNCOMPRESSED_BYTES
    upload_receipt_ttl_seconds: int = 24 * 60 * 60  # MCP_UPLOAD_RECEIPT_TTL_SECONDS

    # Local path security — comma-separated allowed roots; empty = no restriction
    allowed_local_roots: str = ""            # MCP_ALLOWED_LOCAL_ROOTS

    # RAG bridge HTTP timeouts (seconds)
    rag_push_timeout_s: float = 60.0         # MCP_RAG_PUSH_TIMEOUT_S
    rag_delete_timeout_s: float = 60.0       # MCP_RAG_DELETE_TIMEOUT_S
    rag_usage_timeout_s: float = 10.0        # MCP_RAG_USAGE_TIMEOUT_S

    # Vector-store content-hash skip (Slice 2).
    # When enabled, push_docs() skips re-embedding docs whose SHA-256 matches
    # the last successfully pushed hash. Default False so existing deployments
    # can bootstrap baselines before activation.
    incremental_hash_skip_enabled: bool = False  # MCP_INCREMENTAL_HASH_SKIP_ENABLED

    # Living-Document Reingestion (Slice 4) -- doc versioning + change history.
    # File-based, project-scoped, append-only storage under
    # output/{project_id}/{doc_version_history_dirname}/ (see
    # .plans/slice-4-versioning-history/plan.md for the storage-choice rationale).
    doc_version_history_dirname: str = "_version_history"  # MCP_DOC_VERSION_HISTORY_DIRNAME
    doc_version_history_log_filename: str = "change_history.jsonl"  # MCP_DOC_VERSION_HISTORY_LOG_FILENAME
    doc_version_history_sidecar_filename: str = "doc_commit_ranges.json"  # MCP_DOC_VERSION_HISTORY_SIDECAR_FILENAME

    # Living-Document Reingestion (Slice 5) -- drift visibility + manual
    # full-rebuild trigger. This reason code is the ONLY signal
    # `drift_summary.compute_drift_summary()` treats as "the operator reset
    # the drift counters" -- it is assigned unconditionally by
    # `run_git_reingestion`'s `full_rederive=True` branch (never derived from
    # a caller-supplied string) so the reset boundary is the code path, not a
    # naming convention. See .plans/slice-5-drift-manual-rebuild/critique.md
    # (Attack 3) for why this must not be keyed off `triggered_by`.
    manual_full_rebuild_reason_code: str = "manual_full_rebuild"  # MCP_MANUAL_FULL_REBUILD_REASON_CODE
    manual_full_rebuild_trigger: str = "manual_full_rebuild"  # MCP_MANUAL_FULL_REBUILD_TRIGGER

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

_PROVIDER_KEY_MAP: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "google": ("GOOGLE_API_KEY",),
    "gemini": ("GOOGLE_API_KEY",),
    "deepseek": ("MCP_DOC_GENERATION_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    "route33": ("MCP_DOC_GENERATION_ROUTE33_API_KEY", "ROUTE33_API_KEY"),
}


def check_provider_credentials() -> list[str]:
    """Return missing-key warnings for all configured doc generation providers."""
    import os as _os

    model_fields = [
        config.doc_generation_model,
        config.doc_generation_write_model,
        config.doc_generation_investigation_model,
        config.doc_generation_fallback_model,
    ]
    warnings_out: list[str] = []
    seen: set[str] = set()
    for model_str in model_fields:
        if not model_str or model_str == "claude-cli":
            continue
        prefix = model_str.split("/")[0].lower()
        required_vars = _PROVIDER_KEY_MAP.get(prefix)
        if required_vars and prefix not in seen and not any(_os.environ.get(var) for var in required_vars):
            warnings_out.append(
                f"provider '{prefix}' is configured (model: {model_str}) "
                f"but {' or '.join(required_vars)} is not set — generation will fail"
            )
            seen.add(prefix)
    return warnings_out


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
    # GitHub App is optional (PAT fallback exists), but a PARTIAL configuration is a
    # latent failure: the install redirect or token minting will break at runtime.
    _app_id = bool(config.github_app_id)
    _app_key = bool(config.github_app_private_key or config.github_app_private_key_file)
    _app_slug = bool(config.github_app_slug)
    if (_app_id or _app_key or _app_slug) and not (_app_id and _app_key and _app_slug):
        raise ValueError(
            "CRITICAL: GitHub App is partially configured. Set all of "
            "MCP_GITHUB_APP_ID, MCP_GITHUB_APP_SLUG, and "
            "MCP_GITHUB_APP_PRIVATE_KEY (or MCP_GITHUB_APP_PRIVATE_KEY_FILE), "
            "or leave all unset to use PAT connection only."
        )


validate_production_secrets()
