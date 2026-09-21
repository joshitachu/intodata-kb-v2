"""Configuration for MCP server (Pydantic Settings)."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class MCPServerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MCP_",
        extra="ignore",
    )

    # PostgreSQL (same DB as RAG pipeline)
    database_url: str = "postgresql+asyncpg://admin:@localhost:5432/intodata_kb"

    # RAG pipeline base URL
    rag_base_url: str = "http://localhost:8000"

    # intodoc CLI path (defaults to system 'intodoc')
    intodoc_command: str = "intodoc"

    # claude CLI path (for doc generation)
    claude_command: str = "claude"
    # Claude Code model alias/name used by the claude-cli provider. Empty lets
    # Claude Code use its own configured default.
    claude_model: str = "sonnet"

    # OpenCode CLI (documentation generation only). The command is configurable
    # because OpenCode is an operator-owned runtime, not a bundled SDK.
    opencode_command: str = "opencode"
    opencode_config_path: str = ""
    opencode_model: str = ""
    opencode_primary_agent: str = "build"
    opencode_version: str = "1.18.7"
    opencode_auto_approve: bool = True
    #: Startup + fixed-cost allowance for a run. NOT the whole budget: the real
    #: timeout scales with the estate (see opencode_timeout_per_group_seconds).
    #: A flat 1800 gave a 4-job project and a 1345-job project the same 30
    #: minutes, and ETTM (20 groups) was cancelled 56s short of dying on it with
    #: 5 groups and all of Phase 3 still to run.
    opencode_timeout_seconds: int = 1800
    #: Added per scanned group. Measured on ETTM run 259: 15 group analyses took
    #: ~20 minutes wall-clock (~80 s/group) because OpenCode runs sub-agents
    #: 3-4 at a time, not the 20-wide fan-out the prompt asks for. 180 s/group
    #: covers that plus the Phase 3 share.
    opencode_timeout_per_group_seconds: int = 180
    #: Absolute ceiling, so a very large estate cannot wedge a worker forever.
    #: VW_FULL_LANDSCAPE (178 groups) would otherwise compute to ~9.4 hours.
    opencode_timeout_max_seconds: int = 21600
    # OpenCode treats Ollama as a custom OpenAI-compatible provider. This URL
    # is reachable from the Docker service and can be overridden by operators.
    opencode_ollama_base_url: str = "http://host.docker.internal:11434/v1"
    # OpenCode Go subscription key. Nexus materializes this as an isolated,
    # per-phase OpenCode credential file rather than sharing a user's auth store.
    opencode_go_api_key: str = ""

    # Base data directory for logs, uploads, repos
    data_dir: Path = Path(__file__).resolve().parent.parent.parent / "data"

    # The SHARED pack repository. Its branches are the packs available to every
    # user; a personal pack becomes shared by being merged into it through a
    # pull request. Also the source of the starter skeleton.
    swap_pack_starter_repo_url: str = "https://github.com/joshitachu/swap-pack-starter"

    # Token used to push a personal pack's branch and open its pull request
    # against the shared repository (MCP_PACK_REPO_TOKEN). A GitHub App
    # installation on that repository is preferred and tried first; this is the
    # fallback for installs that do not run the App. Publishing is unavailable
    # -- explicitly, with instructions -- when neither is configured, rather
    # than silently doing nothing.
    pack_repo_token: str = ""  # MCP_PACK_REPO_TOKEN
    # Branch prefix for published personal packs. One namespace keeps the
    # shared repo's branch list readable and makes review scope obvious.
    pack_publish_branch_prefix: str = "pack/"  # MCP_PACK_PUBLISH_BRANCH_PREFIX

    # Dedicated direct model call used by the interactive Pack Architect. A
    # Pack turn is one structured batch response, never an OpenCode tool loop.
    pack_author_timeout_seconds: int = 120  # MCP_PACK_AUTHOR_TIMEOUT_SECONDS
    pack_author_max_output_tokens: int = 32_768  # MCP_PACK_AUTHOR_MAX_OUTPUT_TOKENS

    # Pack Governance V1. A JSON array of explicit certification authorities.
    # Example:
    # [{"kind":"git","repo_url":"https://git.example.com/docs/packs.git",
    #   "refs":["main","release/*"],"provenance":"platform-release"}]
    # A Git pack must also declare the same provenance in plugin.yaml under
    # ``certification.provenance``. ``kind: bundled`` may include
    # ``manifest_name`` and ``allow_legacy_migration: true`` to establish a
    # one-time digest baseline for an existing installed bundle. Empty or
    # invalid configuration trusts no packs.
    pack_governance_trusted_sources_json: str = ""

    # intodoc default output directory
    intodoc_output_dir: Path = (
        Path(__file__).resolve().parent.parent.parent.parent / "intodoc-output"
    )

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
    token_encryption_key: str = ""  # MCP_TOKEN_ENCRYPTION_KEY — Fernet key for PAT storage
    public_url: str = ""  # MCP_PUBLIC_URL — public-facing base URL for webhook callbacks

    # GitHub App (repo connection via install flow instead of stored PATs).
    # When configured, repos connect by installing this App on the owner's org;
    # the backend stores only a non-secret installation id and mints short-lived
    # installation tokens on demand. PAT connection remains a fallback.
    github_app_id: str = ""  # MCP_GITHUB_APP_ID — numeric App ID (the JWT issuer)
    github_app_slug: str = ""  # MCP_GITHUB_APP_SLUG — App URL slug, for the install redirect
    github_app_private_key: str = (
        ""  # MCP_GITHUB_APP_PRIVATE_KEY — PEM (literal or with \n escapes)
    )
    github_app_private_key_file: str = (
        ""  # MCP_GITHUB_APP_PRIVATE_KEY_FILE — path to PEM (preferred over inline)
    )
    # App OAuth client (user-authorization during install). Used to verify — via the
    # connecting user's own GET /user/installations — that they actually control the
    # installation before binding it. Required for multi-tenant deployments.
    github_app_client_id: str = ""  # MCP_GITHUB_APP_CLIENT_ID
    github_app_client_secret: str = ""  # MCP_GITHUB_APP_CLIENT_SECRET
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
    #   "claude-cli" (legacy subprocess fallback) | "opencode-cli" (OpenCode runtime)

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
        "google/gemini-2.5-flash"  # MCP_DOC_GENERATION_INVESTIGATION_MODEL
    )
    # Number of same-provider retries after the initial API request and before
    # switching to the configured fallback model.
    doc_generation_api_retries: int = 3  # MCP_DOC_GENERATION_API_RETRIES
    # Default fallback model after same-provider retries are exhausted
    # (rate-limit, network, auth). No fallback if not set.
    doc_generation_fallback_model: str | None = None  # MCP_DOC_GENERATION_FALLBACK_MODEL
    doc_generation_enterprise_mode_default: bool = True
    doc_generation_quality_threshold: float = 4.0
    large_project_threshold: int = 200  # jobs; >=this splits Phase 3 into 4 agents
    small_project_threshold: int = 5  # MCP_SMALL_PROJECT_THRESHOLD — unit count; <=this uses direct path (no Phase 1, no agent runtime for Phase 2)

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
    # OpenRouter is used through explicit openrouter/<vendor>/<model> doc-generation
    # model ids (legacy harness) and openrouter/... OpenCode routes. Set
    # MCP_DOC_GENERATION_OPENROUTER_API_KEY or OPENROUTER_API_KEY in the environment.
    doc_generation_openrouter_api_key: str | None = None
    doc_generation_openrouter_base_url: str = "https://openrouter.ai/api/v1"
    doc_generation_lock_stale_seconds: int = 7200
    # For Ollama or custom OpenAI-compatible endpoints: "http://localhost:11434"
    model_pricing_json: str = ""
    # OPTIONAL OVERRIDE, not the source of truth. The shipped rate table lives
    # in services/model_pricing.DEFAULT_MODEL_PRICING so a run's cost_usd
    # resolves with no environment configuration -- an empty value here used to
    # mean "no pricing at all", which is why every GenerateRun.cost_usd on the
    # deployed instance was NULL.
    #
    # This JSON object is merged OVER the defaults PER MODEL, so correcting one
    # rate does not require restating the rest. Keys may be the full
    # provider-prefixed id or the bare model name. A null value REMOVES a
    # built-in default (that model then reports unknown cost rather than a
    # possibly-stale shipped rate). A model with no rate anywhere reports
    # cost_usd = NULL and logs a warning; it is never priced at zero.
    # Examples:
    # MCP_MODEL_PRICING_JSON='{"deepseek-v4-flash":{"input":0.44,"output":1.32}}'
    # MCP_MODEL_PRICING_JSON='{"claude-opus-4-7":null}'

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

    # Candidate-first living-document patch publication. Disabled until an
    # operator has deployed the matching RAG revision migration and primed a
    # baseline; when enabled, bounded Git patches never write active docs or
    # invoke legacy direct-RAG publication.
    living_doc_patching_enabled: bool = False  # MCP_LIVING_DOC_PATCHING_ENABLED
    living_doc_rollout_stage: str = "approval_only"  # MCP_LIVING_DOC_ROLLOUT_STAGE
    living_doc_low_risk_max_artifacts: int = 3  # MCP_LIVING_DOC_LOW_RISK_MAX_ARTIFACTS

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
    reingestion_patch_field_edit_threshold: float = (
        0.20  # MCP_REINGESTION_PATCH_FIELD_EDIT_THRESHOLD
    )

    # Abandoned docs_staging/ dirs older than this are GC'd at startup. Active
    # resumes bump mtime so in-flight work is safe.
    staging_gc_days: int = 7

    # Doc generation bundle size limits (bytes)
    max_prompt_bundle_bytes: int = 200_000  # MCP_MAX_PROMPT_BUNDLE_BYTES
    max_ref_bundle_bytes: int = 80_000  # MCP_MAX_REF_BUNDLE_BYTES
    max_param_val_chars: int = 16_000  # MCP_MAX_PARAM_VAL_CHARS (was 400; raised 2026-06-10 to stop truncating Lobster field_wiring past 14 of 123 arrows)

    # Phase 2 batching in the claude-cli / opencode-cli lane
    # (services/claude_service.py::plan_phase2_workers). Sub-agents dispatch
    # 3-4 at a time whatever they are asked for, so one worker covers several
    # groups. This is the ATTRIBUTION-SAFETY bound on that batching, not a
    # performance knob: it caps how many groups' evidence sit in one context
    # and therefore how far a component misattributed from one group to
    # another can travel. Lower it if
    # _assessment/group_attribution.json starts reporting findings.
    phase2_max_groups_per_worker: int = 6  # MCP_PHASE2_MAX_GROUPS_PER_WORKER

    # Quick scan anti-pattern thresholds (job/component counts)
    qs_god_profile_threshold: int = 15  # MCP_QS_GOD_PROFILE_THRESHOLD
    qs_large_job_threshold: int = 10  # MCP_QS_LARGE_JOB_THRESHOLD
    qs_complex_tmap_threshold: int = 3  # MCP_QS_COMPLEX_TMAP_THRESHOLD

    # Minimum preprocess content bytes to attempt LLM investigation.
    # Groups with stripped content below this threshold are skipped immediately
    # (all units marked as scanner-limited) rather than burning retries on empty source.
    min_investigation_content_bytes: int = 200  # MCP_MIN_INVESTIGATION_CONTENT_BYTES

    # LLM output token limits per phase
    investigation_max_tokens: int = 10_000  # MCP_INVESTIGATION_MAX_TOKENS
    phase2_max_tokens: int = 32_000  # MCP_PHASE2_MAX_TOKENS
    # Phase 2 unit/group tasks must be materially smaller than the provider's
    # absolute response ceiling.  Large outputs are harder to validate and were
    # observed to truncate repeatedly while omitting late required sections.
    phase2_task_max_tokens: int = 10_000  # MCP_PHASE2_TASK_MAX_TOKENS
    phase2_synthesis_max_tokens: int = 6_000  # MCP_PHASE2_SYNTHESIS_MAX_TOKENS
    # Claim-mode Phase 2 is the provider-neutral default. Each canonical unit
    # packet produces one compact JSON batch; semantic splitting happens only
    # when the provider proves the bounded response is still too large.
    phase2_claim_max_tokens: int = 6_000  # MCP_PHASE2_CLAIM_MAX_TOKENS
    phase2_dense_unit_bytes: int = 24_000  # MCP_PHASE2_DENSE_UNIT_BYTES
    phase2_claim_units_per_task: int = 1  # MCP_PHASE2_CLAIM_UNITS_PER_TASK
    phase3_max_tokens: int = 32_000  # MCP_PHASE3_MAX_TOKENS
    context_max_tokens: int = 900_000  # MCP_CONTEXT_MAX_TOKENS — hard pre-flight ceiling
    core_context_soft_target_tokens: int = 80_000  # MCP_CORE_CONTEXT_SOFT_TARGET_TOKENS
    core_context_hard_ceiling_tokens: int = 120_000  # MCP_CORE_CONTEXT_HARD_CEILING_TOKENS
    core_slice_concurrency: int = 4  # MCP_CORE_SLICE_CONCURRENCY
    # Per-task evidence byte budget for job-boundary bin packing (Phase 1/2).
    # Whole jobs are packed into tasks up to this size; a job is never split.
    phase2_task_evidence_bytes: int = 48_000  # MCP_PHASE2_TASK_EVIDENCE_BYTES
    # Complete per-task packet budget, including typed source evidence,
    # task-scoped deterministic maps, and static analysis instructions.
    phase2_task_context_bytes: int = 140_000  # MCP_PHASE2_TASK_CONTEXT_BYTES
    phase2_synthesis_context_bytes: int = 140_000  # MCP_PHASE2_SYNTHESIS_CONTEXT_BYTES
    phase2_filtered_map_bytes: int = 16_000  # MCP_PHASE2_FILTERED_MAP_BYTES
    # Per-target evidence bundle for unit-scoped Phase 3 docs: raw scan facts
    # (code/tMap/SQL/context, verbatim) delivered ahead of the analysis doc
    # pack. Derived niceties are trimmed first; verbatim evidence is never
    # silently cut (over-budget bundles ship whole with a logged warning).
    phase3_target_evidence_bytes: int = 120_000  # MCP_PHASE3_TARGET_EVIDENCE_BYTES
    # Byte cap for the business-topology PUSHED evidence context. The topology
    # step no longer pulls evidence via budget-limited tools; the high-signal
    # scanner evidence (systems/applications/edges/connections/relations,
    # trimmed artifacts, the factual overview, cross-project index and the
    # Phase 1/2 starter pack) is injected directly into the model context up to
    # this cap. Validation-critical evidence is pushed first and never dropped;
    # lower-signal bulk (components/lineage/channels) is added only if it fits.
    topology_context_evidence_bytes: int = 300_000  # MCP_TOPOLOGY_CONTEXT_EVIDENCE_BYTES
    # Byte cap for unit-filtered copies of derived fact maps (dependency_map
    # and friends) handed to unit-scoped Phase 3 docs instead of full maps.
    phase3_filtered_map_bytes: int = 24_000  # MCP_PHASE3_FILTERED_MAP_BYTES
    # Tier-3 non-code documented context (Excel schema exports, XSLT/XSL
    # mappings, business notes, sample XML) associated to a unit and appended,
    # already redacted, to that unit's Phase 3 evidence bundle. Additive
    # documented intent: it is bounded by this budget and is trimmed before
    # verbatim scanner evidence, never ahead of it.
    phase3_noncode_evidence_bytes: int = 40_000  # MCP_PHASE3_NONCODE_EVIDENCE_BYTES
    # Byte budget for the project-tier non-code documented-context section handed
    # to group/summary Phase 3 docs (the full prepared set, association-agnostic).
    phase3_noncode_project_bytes: int = 120_000  # MCP_PHASE3_NONCODE_PROJECT_BYTES
    # Runtime execution evidence: bounded aggregates (run counts, outcomes,
    # latency, recent redacted failure headlines) pulled from the RAG backend's
    # runtime mirror and appended to a unit's Phase 3 bundle. This is the only
    # part of a document grounded in what production actually did rather than in
    # what the source code says. Aggregates only -- no log lines cross the
    # boundary -- and the section is trimmed before verbatim scanner evidence.
    runtime_evidence_enabled: bool = True  # MCP_RUNTIME_EVIDENCE_ENABLED
    runtime_evidence_window_days: int = 90  # MCP_RUNTIME_EVIDENCE_WINDOW_DAYS
    runtime_evidence_max_failures: int = 5  # MCP_RUNTIME_EVIDENCE_MAX_FAILURES
    phase3_runtime_evidence_bytes: int = 8_000  # MCP_PHASE3_RUNTIME_EVIDENCE_BYTES
    runtime_evidence_timeout_s: float = 20.0  # MCP_RUNTIME_EVIDENCE_TIMEOUT_S
    # Project-level runtime rollup (`_analysis/_runtime_profile.md`) attached to
    # every GROUP/SUMMARY Phase 3 document. It must stay strictly below
    # `phase3_shared_context_file_bytes` or `plan_phase3_slices` reclassifies it
    # from SHARED to EVIDENCE and bin-packs it, so slice 1 would see the failing
    # jobs while slice 3 does not. Clamped (never asserted) in the validator
    # below: an operator lowering the shared threshold must not crash the server
    # at import, and `assert` is stripped under `python -O` anyway.
    phase3_runtime_profile_bytes: int = 12_000  # MCP_PHASE3_RUNTIME_PROFILE_BYTES
    # Client-side batching for the doc-evidence fetch. The server caps `units`
    # at 200 (a FastAPI `max_length`; over it the response is a 422 that used to
    # degrade to "no runtime data for the whole project"), and 200 names at ~30
    # characters is ~7 KB of query string against a common 8 KB request-line
    # limit -- so the batch is bounded by BOTH count and encoded query bytes.
    runtime_evidence_batch_units: int = 200  # MCP_RUNTIME_EVIDENCE_BATCH_UNITS
    runtime_evidence_max_query_bytes: int = 6_000  # MCP_RUNTIME_EVIDENCE_MAX_QUERY_BYTES
    # Runtime-side orphan ratio above which the rollup reports a scope mismatch.
    # Verified: werkorder 1/6 = 0.17 (a correct, deliberately partial scan of a
    # tRunJob-heavy estate) versus alitalend 91/92 = 0.99 (the one confirmed
    # mis-binding). The flag states the asymmetry; it never accuses.
    runtime_evidence_scope_mismatch_ratio: float = 0.75  # MCP_RUNTIME_EVIDENCE_SCOPE_MISMATCH_RATIO
    runtime_evidence_scope_mismatch_min_orphans: int = 5  # MCP_RUNTIME_EVIDENCE_SCOPE_MISMATCH_MIN_ORPHANS
    # A matched unit with no execution in this many days is reported as silent.
    runtime_evidence_silent_days: int = 30  # MCP_RUNTIME_EVIDENCE_SILENT_DAYS
    # Log-estate (Elastic/ESB) runtime authority. TMC reports what the control
    # plane scheduled and executed; a log estate reports what actually flowed --
    # message volumes, error volumes and end-to-end correlation latency, which
    # TMC cannot produce at all. `providers/custom/elastic_evidence.py` fetches
    # the normalised `elastic-aggregates-1` body from the RAG backend and
    # `runtime_authority.fuse_runtime_authorities` places it beside the TMC
    # figures with its own provenance (never merged into them).
    # Best-effort in every case: unconfigured, unreachable or malformed leaves
    # the Elastic authority `unavailable` with a stated limitation, and never
    # fails a documentation run.
    elastic_evidence_enabled: bool = True  # MCP_ELASTIC_EVIDENCE_ENABLED
    # Read-only JSON endpoint on the RAG backend, under the runtime API prefix.
    # A config value and not a module constant so a backend route rename is an
    # env change rather than a release; clearing it disables the lane outright.
    elastic_evidence_path: str = (  # MCP_ELASTIC_EVIDENCE_PATH
        "/api/v1/runtime/elastic-aggregates"
    )
    elastic_evidence_timeout_s: float = 20.0  # MCP_ELASTIC_EVIDENCE_TIMEOUT_S
    # 0 inherits `runtime_evidence_window_days` so both authorities describe the
    # same period by default. Widening it is legitimate -- a log estate commonly
    # retains months more than the TMC mirror -- and stays honest because every
    # fused record carries its own `provenance.window_days`.
    elastic_evidence_window_days: int = 0  # MCP_ELASTIC_EVIDENCE_WINDOW_DAYS
    # A token present in more than this fraction of scanned units is treated as
    # non-distinctive (project-wide boilerplate) and is dropped from non-code
    # association, so ubiquitous identifiers do not attach an item to every unit.
    noncode_unit_token_df_threshold: float = 0.1  # MCP_NONCODE_UNIT_TOKEN_DF_THRESHOLD
    # A non-code item must share at least this many distinctive tokens with a
    # unit to associate. One shared token is weak evidence; requiring two breaks
    # the over-association where schema exports touched every unit.
    noncode_min_distinctive_overlap: int = 2  # MCP_NONCODE_MIN_DISTINCTIVE_OVERLAP
    # Narrative docs receive a grouped view of an oversized project-wide impact
    # matrix. The complete deterministic matrix remains stored as evidence.
    # Kept at its historical value: 08_CHANGE_PLAYBOOK's prompt prefix is cached
    # on these exact bytes and an acceptance test pins them.
    phase3_change_impact_view_bytes: int = 48_000  # MCP_PHASE3_CHANGE_IMPACT_VIEW_BYTES
    # Cap for every OTHER derived fact-map view (`phase3_context.FACT_MAP_VIEWS`:
    # dependency_map, known_risks, config_runtime_map, data_flow_map, ...).
    # Group-level Phase 3 documents receive these views instead of the raw maps,
    # so no raw map reaches a document at all. Must stay strictly below
    # `phase3_shared_context_file_bytes` -- same reason as the runtime rollup
    # above: one byte over and `plan_phase3_slices` reclassifies a view from
    # SHARED to splittable EVIDENCE, so slice 1 and slice 3 of one document
    # would see different halves of the same reduction. Clamped in the validator
    # below with headroom for the grouped views' trailing omission note.
    phase3_fact_view_bytes: int = 12_000  # MCP_PHASE3_FACT_VIEW_BYTES
    # Supporting-evidence preparation caps (non-code artifacts: mappings, PDFs,
    # notes). These are LOUD: every file skipped by a cap is logged with its
    # relative path and size (never a silent drop). Raising max_file_bytes lets
    # bigger mapping workbooks/PDFs through; raising max_raw_bytes lifts the
    # cumulative raw-read budget. Both flow into SupportingEvidenceLimits.
    supporting_evidence_max_file_bytes: int = (
        4 * 1024 * 1024
    )  # MCP_SUPPORTING_EVIDENCE_MAX_FILE_BYTES
    supporting_evidence_max_raw_bytes: int = (
        100 * 1024 * 1024
    )  # MCP_SUPPORTING_EVIDENCE_MAX_RAW_BYTES
    source_context_mode: str = (
        "direct_redacted"  # MCP_SOURCE_CONTEXT_MODE: structured | direct_redacted
    )
    direct_source_inline_max_bytes: int = 120_000  # MCP_DIRECT_SOURCE_INLINE_MAX_BYTES
    direct_source_chunk_bytes: int = 80_000  # MCP_DIRECT_SOURCE_CHUNK_BYTES

    # Bounded internal multi-agent runtime
    agent_phase1_concurrency: int = 4  # MCP_AGENT_PHASE1_CONCURRENCY
    agent_phase1_agentic: bool = False  # MCP_AGENT_PHASE1_AGENTIC — use chunk-manifest investigator
    agent_phase1_batch_size: int = 20  # MCP_AGENT_PHASE1_BATCH_SIZE — units per extraction call
    agent_phase2_concurrency: int = 6  # MCP_AGENT_PHASE2_CONCURRENCY
    agent_phase2_max_slices: int = (
        7  # MCP_AGENT_PHASE2_MAX_SLICES — hard cap on _split_context_pack fan-out
    )
    agent_phase3_concurrency: int = 4  # MCP_AGENT_PHASE3_CONCURRENCY
    agent_repair_concurrency: int = 3  # MCP_AGENT_REPAIR_CONCURRENCY
    agent_max_tool_calls: int = 25  # MCP_AGENT_MAX_TOOL_CALLS
    agent_max_tool_bytes: int = 600_000  # MCP_AGENT_MAX_TOOL_BYTES
    agent_max_tool_result_bytes: int = 50_000  # MCP_AGENT_MAX_TOOL_RESULT_BYTES
    agent_phase2_tool_input_token_cap: int = 180_000  # MCP_AGENT_PHASE2_TOOL_INPUT_TOKEN_CAP
    agent_phase3_tool_input_token_cap: int = 600_000  # MCP_AGENT_PHASE3_TOOL_INPUT_TOKEN_CAP
    # First-pass Phase 3 doc generation runs single-shot (no tool loop) by default:
    # the targeted doc pack is primary and sufficient, so token use scales with pack
    # size instead of an accumulating tool conversation. Set True to restore the
    # agentic tool loop for first-pass docs (repair phases keep tools regardless).
    agent_phase3_first_pass_tools: bool = False  # MCP_AGENT_PHASE3_FIRST_PASS_TOOLS
    agent_max_task_attempts: int = 2  # MCP_AGENT_MAX_TASK_ATTEMPTS
    agent_max_repair_attempts: int = 3  # MCP_AGENT_MAX_REPAIR_ATTEMPTS
    agent_max_split_depth: int = 2  # MCP_AGENT_MAX_SPLIT_DEPTH
    agent_dag_enabled: bool = False  # MCP_AGENT_DAG_ENABLED — use validated DAG orchestration
    agent_planner_enabled: bool = False  # MCP_AGENT_PLANNER_ENABLED — ask model for execution graph
    agent_planner_mode: str = "llm"  # MCP_AGENT_PLANNER_MODE: llm | deterministic
    agent_planner_max_tokens: int = 8_000  # MCP_AGENT_PLANNER_MAX_TOKENS
    agent_planner_require_valid_json: bool = True  # MCP_AGENT_PLANNER_REQUIRE_VALID_JSON
    agent_dag_strict_validation: bool = True  # MCP_AGENT_DAG_STRICT_VALIDATION
    agent_dag_max_nodes: int = 200  # MCP_AGENT_DAG_MAX_NODES
    agent_dag_max_depth: int = 20  # MCP_AGENT_DAG_MAX_DEPTH
    agent_smart_repair_enabled: bool = (
        False  # MCP_AGENT_SMART_REPAIR_ENABLED — classify failures before repair
    )

    # Pipeline forensics: when enabled, human-only debug artifacts (model-call
    # prompt/context/request/response snapshots, agent-task traces) are written
    # under docs_staging/_debug/. Default runs persist only consumed artifacts.
    pipeline_debug: bool = False  # MCP_PIPELINE_DEBUG

    # Doc generation safety guards. These are model/provider agnostic and stop
    # runaway retry storms without reducing prompt/evidence depth.
    docgen_empty_outputs_per_doc_limit: int = 3  # MCP_DOCGEN_EMPTY_OUTPUTS_PER_DOC_LIMIT
    docgen_empty_outputs_per_run_limit: int = 10  # MCP_DOCGEN_EMPTY_OUTPUTS_PER_RUN_LIMIT
    docgen_allowed_slices_per_doc: int = 4  # MCP_DOCGEN_ALLOWED_SLICES_PER_DOC
    # Stub floor for the doc-depth gate. This is a "did anything get written"
    # check, NOT a depth measure. Depth is judged per required section against
    # the contract the Phase 3 prompt actually states (enumerated items / table
    # rows / substantive prose) -- see `validators.validate_doc_depth`.
    # It replaced `max(8_000, min(45_000, preprocess_bytes * 0.04))`, which
    # scaled the required document SIZE with the volume of scanned evidence: on
    # a large estate every document had to clear 45 KB, so padding and repeated
    # structure were the cheapest way to pass. Nothing measured redundancy, so
    # the multi-slice restatements the old Phase 3 slicing produced read as
    # depth. Never re-derive this from evidence volume.
    docgen_min_doc_bytes: int = 2_000  # MCP_DOCGEN_MIN_DOC_BYTES
    docgen_min_run_call_budget: int = 40  # MCP_DOCGEN_MIN_RUN_CALL_BUDGET
    docgen_call_budget_base: int = 20  # MCP_DOCGEN_CALL_BUDGET_BASE
    docgen_call_budget_per_group: int = 4  # MCP_DOCGEN_CALL_BUDGET_PER_GROUP
    docgen_max_run_calls: int = 250  # MCP_DOCGEN_MAX_RUN_CALLS
    # Composite/oversized-upload orchestration: when a scan partitions into
    # more than one sub-project (composite zips, or a single root split to fit
    # the call budget), the manual/api generate path loops one scoped run per
    # partition into output/{project_id}/{subproject}/docs. Disable to force
    # the legacy single whole-upload run.
    docgen_partition_orchestration: bool = True  # MCP_DOCGEN_PARTITION_ORCHESTRATION
    # Universal extraction cache (services/extraction_cache.py). docs_staging/
    # _preprocess/ is a pure function of the scan artifacts + the preprocess/
    # redaction code version + platform + the partition unit allowlist, and is
    # independent of the pack, so a second pack over the same project reuses it
    # instead of re-extracting. Phase 4 stores it outside docs_staging/ before
    # the cleanup rmtree; generate_docs restores it in place of preprocessing.
    # Disable to force a full deterministic re-extraction on every run.
    extraction_cache_enabled: bool = True  # MCP_EXTRACTION_CACHE_ENABLED
    extraction_cache_keep: int = 3  # MCP_EXTRACTION_CACHE_KEEP
    # Provider-neutral total-token accounting. Input usage remains recorded, but
    # the cumulative input ceiling is disabled by default because evidence-rich
    # multi-document runs legitimately reuse project facts across Phase 3. Set
    # MCP_DOCGEN_MAX_RUN_INPUT_TOKENS to a positive value to opt back into a cap.
    #
    # READ THIS BEFORE ENABLING IT. Input is the axis holding ~90% of a run's
    # spend (~2M input vs ~208K output on production runs), and it is the only
    # one of the three governors currently switched off -- calls (37/250) and
    # output (~15% of ceiling) are governed but nowhere near their limits.
    # The plumbing works: harness._run_input_token_budget early-returns 0 while
    # this is 0, which makes the `input_budget > 0` check in
    # _reserve_model_budget dead code; set it positive and reservations are
    # blocked with "input token budget would be exceeded" (pinned by
    # tests/test_services/test_run_cost_governance.py).
    #
    # The catch is the min() below it: the effective budget is
    #   min(docgen_max_run_input_tokens, base + per_unit*units + per_doc*docs)
    # so ANY positive value also activates the dynamic formula, and at the
    # constants below that formula is calibrated well under observed spend.
    # A 10-unit / 11-doc project gets 150K + 250K + 660K = 1.06M against ~2M
    # actually consumed -- it needs ~48 units before the formula clears 2M, so
    # enabling as-is would abort small and medium runs mid-Phase-3. Raise the
    # base/per-doc terms first (e.g. base 500_000, per_doc 250_000, giving that
    # same project ~3.5M) and treat MCP_DOCGEN_MAX_RUN_INPUT_TOKENS as a
    # runaway ceiling rather than a work bound -- the same lesson recorded for
    # docgen_max_run_output_tokens below, whose old 600K default starved
    # legitimate multi-doc partitions. Suggested starting ceiling once the
    # formula is re-calibrated: 6_000_000 (~3x observed). Size it from the
    # totals in _assessment/phase_costs.json per project class, with >=2x
    # headroom, and shadow it on real runs before enforcing.
    docgen_input_token_budget_base: int = 150_000  # MCP_DOCGEN_INPUT_TOKEN_BUDGET_BASE
    docgen_input_token_budget_per_unit: int = 25_000  # MCP_DOCGEN_INPUT_TOKEN_BUDGET_PER_UNIT
    docgen_input_token_budget_per_doc: int = 60_000  # MCP_DOCGEN_INPUT_TOKEN_BUDGET_PER_DOC
    docgen_max_run_input_tokens: int = 0  # MCP_DOCGEN_MAX_RUN_INPUT_TOKENS (0 = unlimited)
    docgen_output_token_budget_base: int = 80_000  # MCP_DOCGEN_OUTPUT_TOKEN_BUDGET_BASE
    docgen_output_token_budget_per_unit: int = 10_000  # MCP_DOCGEN_OUTPUT_TOKEN_BUDGET_PER_UNIT
    docgen_output_token_budget_per_doc: int = 35_000  # MCP_DOCGEN_OUTPUT_TOKEN_BUDGET_PER_DOC
    # Runaway ceiling only. The run's real output budget is sized per doc-task
    # (base + sum of per-doc output ceilings); this cap exists to stop a
    # misconfigured run, not to bound planned work. The old 600K default
    # starved legitimate multi-doc partitions (verified: VQ_MQ2RHM blocked at
    # exactly 560664/560000 while writing accepted docs).
    docgen_max_run_output_tokens: int = 2_000_000  # MCP_DOCGEN_MAX_RUN_OUTPUT_TOKENS
    # Per-doc-type output ceilings (P0.3 output governance). A brief must be
    # far smaller than an inventory; inventories scale with unit count.
    docgen_unit_doc_output_tokens: int = 6_000  # MCP_DOCGEN_UNIT_DOC_OUTPUT_TOKENS
    docgen_brief_output_tokens: int = 14_000  # MCP_DOCGEN_BRIEF_OUTPUT_TOKENS
    docgen_inventory_output_tokens_base: int = 10_000  # MCP_DOCGEN_INVENTORY_OUTPUT_TOKENS_BASE
    docgen_inventory_output_tokens_per_unit: int = (
        120  # MCP_DOCGEN_INVENTORY_OUTPUT_TOKENS_PER_UNIT
    )
    # Output-sized Phase 3 slicing (slicing redesign). A document whose
    # expected OUTPUT fits one model call is generated in ONE call with all
    # its evidence bounded to the context ceiling (no evidence-chunk restarts,
    # no merge). Only genuinely large docs (unit-scaled inventories / rule
    # catalogs) are partitioned by section/unit-range with a shared skeleton.
    # The single-call cap reflects the OBSERVED per-call output of production
    # models (~16-18K tokens on deepseek-v4-pro), not the configured
    # phase3_max_tokens safety ceiling.
    phase3_single_call_output_tokens: int = 16_000  # MCP_PHASE3_SINGLE_CALL_OUTPUT_TOKENS
    # Expected output for fixed-shape docs (00,01,03,05,06,07,08 style):
    # observed real deduplicated content is ~7-16K tokens per doc.
    docgen_doc_expected_output_tokens: int = 12_000  # MCP_DOCGEN_DOC_EXPECTED_OUTPUT_TOKENS
    # Business-rule catalogs (04 style) scale with unit count like inventories.
    docgen_rules_output_tokens_base: int = 4_000  # MCP_DOCGEN_RULES_OUTPUT_TOKENS_BASE
    docgen_rules_output_tokens_per_unit: int = 150  # MCP_DOCGEN_RULES_OUTPUT_TOKENS_PER_UNIT
    # Standard-catalog per-unit reference docs (deep per-unit dossiers).
    # Expanded one-per-scanned-unit from catalog entries marked per_unit; the
    # unit docs are written FIRST and the catalog/summary docs synthesize from
    # them (plus the analysis layer). Disable to restore the pre-unit-doc set.
    docgen_unit_docs_enabled: bool = False  # MCP_DOCGEN_UNIT_DOCS_ENABLED
    # Byte budget for the deterministic digest of finished unit docs handed to
    # the group/summary Phase 3 writers (units-first wiring).
    phase3_unit_digest_bytes: int = 150_000  # MCP_PHASE3_UNIT_DIGEST_BYTES
    # LLM scorecard (Phase 2.6) gate for STANDARD (non-plugin) runs. The
    # scorecard is advisory (it feeds only 07_QUALITY_SECURITY_RISK_SCORECARD);
    # on the verified Assetcheck run it planned 4 calls, used 13 (12 slices +
    # reducer, ~0.8M input tokens) and still shipped contract gaps. Default off:
    # a deterministic "scorecard omitted" marker is written instead so the 07
    # doc, executive summary, and enterprise gates degrade gracefully. Plugin
    # packs that declare a scorecard phase are unaffected by this flag.
    docgen_llm_scorecard_enabled: bool = False  # MCP_DOCGEN_LLM_SCORECARD_ENABLED
    # Built-in evidence agents on STANDARD (non-plugin) runs. Verified waste:
    # 3 of the 4 defaults (core_architecture_dependency, core_data_flow,
    # core_runtime_config) produced 0 claims + 1 validation issue each on the
    # Assetcheck run (~0.6M tokens of pure noise). Default off; plugin packs
    # that explicitly declare evidence_agents are unaffected.
    docgen_default_evidence_agents_enabled: bool = (
        False  # MCP_DOCGEN_DEFAULT_EVIDENCE_AGENTS_ENABLED
    )
    # Phase 2 plan allowance for validation-failure repair retries, as a
    # fraction of the planned claim tasks (verified: ~21 retries on 73 tasks =
    # 29% before the per-section claim demotion landed; the allowance keeps the
    # plan honest without inflating it now that retries are rarer).
    docgen_phase2_retry_allowance: float = 0.25  # MCP_DOCGEN_PHASE2_RETRY_ALLOWANCE
    # Phase 3 input-partitioned slicing: a context file at or below this size is
    # SHARED (replicated byte-stable into every slice: unit index, cross-refs,
    # small relational maps); anything larger is EVIDENCE, split on section
    # boundaries with each slice receiving only its own portion. Verified
    # failure: the generic splitter replicated ~195 KB of "common" context into
    # every slice and paged evidence into 19-22 tiny chunks, so the 4-slice cap
    # dropped most of the evidence (input = N x pack; units truncated).
    phase3_shared_context_file_bytes: int = 16_000  # MCP_PHASE3_SHARED_CONTEXT_FILE_BYTES
    docgen_token_estimate_safety_factor: float = 1.5  # MCP_DOCGEN_TOKEN_ESTIMATE_SAFETY_FACTOR
    docgen_adaptive_concurrency: bool = True  # MCP_DOCGEN_ADAPTIVE_CONCURRENCY
    phase_cost_warning_input_tokens: int = 500_000  # MCP_PHASE_COST_WARNING_INPUT_TOKENS

    # Instruction pack directory (relative to project_root) — US-106
    instruction_pack_dir: str = "packs/default"  # MCP_INSTRUCTION_PACK_DIR

    # Upload limits
    max_upload_zip_bytes: int = 1024 * 1024 * 1024  # MCP_MAX_UPLOAD_ZIP_BYTES — 1 GB default
    max_upload_zip_entries: int = 20_000  # MCP_MAX_UPLOAD_ZIP_ENTRIES
    max_upload_zip_uncompressed_bytes: int = (
        2 * 1024 * 1024 * 1024
    )  # MCP_MAX_UPLOAD_ZIP_UNCOMPRESSED_BYTES
    # Nested source bundles are common for Talend exports.  These limits apply
    # cumulatively across the outer archive and every nested archive.
    max_upload_zip_nesting_depth: int = 4  # MCP_MAX_UPLOAD_ZIP_NESTING_DEPTH
    upload_receipt_ttl_seconds: int = 24 * 60 * 60  # MCP_UPLOAD_RECEIPT_TTL_SECONDS

    # Local path security — comma-separated allowed roots; empty = no restriction
    allowed_local_roots: str = ""  # MCP_ALLOWED_LOCAL_ROOTS

    # RAG bridge HTTP timeouts (seconds)
    rag_push_timeout_s: float = 60.0  # MCP_RAG_PUSH_TIMEOUT_S
    rag_delete_timeout_s: float = 60.0  # MCP_RAG_DELETE_TIMEOUT_S
    rag_usage_timeout_s: float = 10.0  # MCP_RAG_USAGE_TIMEOUT_S
    # Runtime-source proxy calls are slower than a doc push: creating a source
    # makes the RAG backend call the vendor API twice (validate_credentials,
    # then resolve_physical_identity) before it answers.
    runtime_sources_timeout_s: float = 60.0  # MCP_RUNTIME_SOURCES_TIMEOUT_S

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
    doc_version_history_log_filename: str = (
        "change_history.jsonl"  # MCP_DOC_VERSION_HISTORY_LOG_FILENAME
    )
    doc_version_history_sidecar_filename: str = (
        "doc_commit_ranges.json"  # MCP_DOC_VERSION_HISTORY_SIDECAR_FILENAME
    )

    # Living-Document Reingestion (Slice 5) -- drift visibility + manual
    # full-rebuild trigger. This reason code is the ONLY signal
    # `drift_summary.compute_drift_summary()` treats as "the operator reset
    # the drift counters" -- it is assigned unconditionally by
    # `run_git_reingestion`'s `full_rederive=True` branch (never derived from
    # a caller-supplied string) so the reset boundary is the code path, not a
    # naming convention. See .plans/slice-5-drift-manual-rebuild/critique.md
    # (Attack 3) for why this must not be keyed off `triggered_by`.
    manual_full_rebuild_reason_code: str = (
        "manual_full_rebuild"  # MCP_MANUAL_FULL_REBUILD_REASON_CODE
    )
    manual_full_rebuild_trigger: str = "manual_full_rebuild"  # MCP_MANUAL_FULL_REBUILD_TRIGGER

    @model_validator(mode="after")
    def _clamp_phase3_shared_context_bytes(self) -> "MCPServerConfig":
        """Keep every always-attached artifact under the SHARED-context threshold.

        `plan_phase3_slices` classifies a Phase 3 context file as SHARED purely
        by size against `phase3_shared_context_file_bytes`. One byte over and
        the project runtime rollup is treated as splittable evidence, so slice 1
        sees the failure census and slice 3 does not -- a silently inconsistent
        document set rather than a visible failure.

        This CLAMPS rather than asserts. `MCPServerConfig` is instantiated at
        import time, so an unconditional assert turns "an operator lowered
        MCP_PHASE3_SHARED_CONTEXT_FILE_BYTES" into "the MCP server will not
        start"; `assert` is also stripped under `python -O`, which makes it no
        guarantee at all. Consistent with R17: runtime evidence never gates.
        """
        ceiling = max(1, self.phase3_shared_context_file_bytes - 1)
        if self.phase3_runtime_profile_bytes > ceiling:
            logger.warning(
                "phase3_runtime_profile_bytes (%d) must stay below "
                "phase3_shared_context_file_bytes (%d) or the runtime rollup is "
                "bin-packed as evidence instead of shared context; clamping to %d",
                self.phase3_runtime_profile_bytes,
                self.phase3_shared_context_file_bytes,
                ceiling,
            )
            self.phase3_runtime_profile_bytes = ceiling
        # Same rule, same reason, for the derived fact-map views. The extra
        # headroom covers the trailing omission note a grouped view appends
        # after its byte-fit check (verified: a 12,000-byte cap produced a
        # 12,068-byte view), so a view can never cross the SHARED threshold.
        view_ceiling = max(1, self.phase3_shared_context_file_bytes - 1_024)
        if self.phase3_fact_view_bytes > view_ceiling:
            logger.warning(
                "phase3_fact_view_bytes (%d) must stay below "
                "phase3_shared_context_file_bytes (%d) with headroom or a fact "
                "view is bin-packed as evidence instead of shared context; "
                "clamping to %d",
                self.phase3_fact_view_bytes,
                self.phase3_shared_context_file_bytes,
                view_ceiling,
            )
            self.phase3_fact_view_bytes = view_ceiling
        return self

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

from mcp_server.platforms import supported_platform_ids

SUPPORTED_PLATFORMS: frozenset[str] = supported_platform_ids()

_PROVIDER_KEY_MAP: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "google": ("GOOGLE_API_KEY",),
    "gemini": ("GOOGLE_API_KEY",),
    "deepseek": ("MCP_DOC_GENERATION_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    "route33": ("MCP_DOC_GENERATION_ROUTE33_API_KEY", "ROUTE33_API_KEY"),
    "openrouter": ("MCP_DOC_GENERATION_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
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
        if not model_str or model_str in {"claude-cli", "opencode-cli"}:
            continue
        prefix = model_str.split("/")[0].lower()
        required_vars = _PROVIDER_KEY_MAP.get(prefix)
        if (
            required_vars
            and prefix not in seen
            and not any(_os.environ.get(var) for var in required_vars)
        ):
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
