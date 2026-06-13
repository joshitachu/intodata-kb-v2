# Current State

**Status** (2026-06-13, Slice 6 — commit `344fa0c`): Layered RAG epic COMPLETE. Evaluation harness (`scripts/retrieval_eval.py`) is now an operator-facing golden-set runner — retrieval metrics (recall@30/@8 over filename cases, source-layer accuracy via `search_layered`) + ladder metrics (drives `_prepare_rag_context`, records fallback/layer/doc_gap/refusal/latency). Committed phase2 golden report (`scripts/eval_reports/layered_rag_slice6.{md,json}`): all gates pass — recall@8 100%, layer accuracy 100%, must-pass 5/5, unsupported zero-leak 2/2. Verdicts: LLM verifier warranted for refusal scoring only (substring matching can't see negation), `_doc_pack_*` indexing not warranted, no config tuning needed. 409 backend tests. To re-run: must execute inside the backend container (Qdrant empty-string api-key header) — see PROGRESS_LOG. Open follow-ups across the epic: alias-table full-load scaling (Slice 5), `_internal/` rows in admin document lists (Slice 3), optional LLM verifier (Slice 6 verdict), Hindsight `observation_scopes` version mismatch (unrelated, breaks project-memory retention silently).

**Status** (2026-06-13, Slice 5 — commit `1a34f96`): Deep-rung retrieval quality shipped. Query-time entity matching over `entity_aliases` (new `retrieval/entity_matcher.py`) replaces the PascalCase heuristic as the ladder's descent trigger — deterministic, per-project, boundary-aware; heuristic survives only for never-alias-synced projects. Rung 2 now: forced query expansion, prefetch 150/rerank 75 (rung 1 and `/query` unchanged), guaranteed unit-payload chunk fetch for alias-matched units (new `unit`/`section` Qdrant indexes, scores pinned 1.0, RBAC-filtered), and whole-section expansion replacing fragment hits. Doc-gap `detected_entities` carry alias canonical names. Inspector WARN (alias full-load scaling + deep-rung latency logged as follow-ups). 394 tests pass. Deploy: backend restart applies payload indexes on startup, no migration. Next: Slice 6 (golden-set eval harness) and the open follow-ups: alias lookup scaling, `_internal/` rows in admin document lists, Hindsight `observation_scopes` version mismatch (unrelated, breaks project-memory retention silently).

**Status** (2026-06-12, Slice 4 — commit `d10282a`): Conversational QA now runs a two-rung retrieval ladder. Rung 1 = default canonical partition with the answer generated under the [NEED_SEARCH] contract; insufficiency ([NEED_SEARCH], cant-answer safety net, identifier-presence check, or zero sources) descends to rung 2 = `search_layered()` over deterministically routed internal layers (technical_analysis / support_maps / scorecard / evidence_slices) with identical RBAC/project filters. Internal answers mark `fallback_used`/`doc_gap`/`primary_source_layer` (persisted on conversation_messages, returned in MessageResponse and SSE `done`) and log a `DocGapEvent` (migration 029) for repeated-omission analysis; unsupported questions refuse cleanly with no gap event. Citations use `friendly_display_name` — raw `_internal/` paths never render; source layer/display/project metadata survives persistence, reload, and the frontend (zod + SourceCitation). Inspector: BLOCK cycle 1 (vision images dropped by rung-1 precompute) fixed in-session; accepted WARNs: fresh QA streams as a single blob, fallback path costs up to 3 LLM calls. 373 backend tests pass. Deploy: `alembic upgrade head` (029) required. Next: Slice 5 (alias-table entity lookup, rung-2 recall widening, section-parent expansion).

**Status** (2026-06-12, Slice 1): Layered RAG Slice 1 is implemented, inspected, and validated. Markdown ingestion now uses structure-aware heading-path chunking with `section`/`unit` metadata; ingestion threads default and caller-provided chunk metadata into Qdrant payloads; Qdrant now indexes `source_layer`; retrieval applies the default `canonical_docs` plus legacy-missing-layer filter; source payload mapping is centralized; rerank/neighbor expansion preserve layer metadata; and map-reduce summary document resolution now reuses the shared search filter with project scope and default layer partitioning. During validation, an oversized-section hang was fixed by making the base token chunker cap effective overlap below chunk size, and older API tests were aligned with CSRF middleware. `/inspect` passed and `/validate` passed. Verification: `uv run python -m py_compile ...` passed for changed backend Python files; `uv run pytest` passed with `348 passed, 2 warnings`.

**Status** (2026-06-02): Normal Chat now has a constrained read-only RAG agent layer behind the existing conversation endpoints. This is not a free-form autonomous agent: backend code owns a fixed registry of read-only tool steps and caps the trace at 3 steps. The existing RAG behavior remains the source of truth for memory recall, previous-source reuse, metadata queries, document summaries, document search, citations, RBAC filtering, usage logging, and persistence. Streaming chat can now emit optional `step` SSE events before the existing `sources` / `token` / `usage` / `done` events; the frontend parses those events and shows the latest step label while the assistant is working. Conversation API tests were also updated to include CSRF double-submit headers on state-changing requests. Verification: backend targeted suite `57/57` passed, frontend stream tests `5/5` passed, and `npm run build` passed.

**Status** (2026-06-01): RAG ingestion incident triage complete. Backend health is now fully healthy after rebuild: database, Qdrant, Ollama, and Hindsight all report `ok`. The Documents-page upload path had two issues: `fastapiUpload()` forwarded cookies but omitted the required `X-CSRF-Token` header, and `CSRFMiddleware` turned that CSRF rejection into a raw 500 by raising `HTTPException` from middleware dispatch. Upload proxy now forwards the CSRF header, and middleware now returns a structured `403 csrf_invalid` JSON response directly for real CSRF failures. Direct `/api/v1/ingest/md` markdown ingestion succeeded via Voyage embeddings, and `/mcp/push-to-rag` is present on the MCP server (unauthenticated probe returns 401, not 404). `MCP_INGEST_API_KEY` is currently empty on both backend and MCP, so the observed failure was not an expired shared ingest key.

**Status** (2026-05-21): Kimi Chat timeout default is now 300s, matching Ollama so slower streamed answers do not fail at the previous 60s cap. Normal Chat stream failures no longer disappear silently. Failed streamed attempts persist assistant failure metadata via migration 027, render a failed-response bubble in chat, and keep a live Retry action while the exact failed request is still in memory; failed assistant rows are excluded from later RAG history. Chat image input can now be submitted without typed text. The existing image upload and vision request path remains unchanged; `ChatInput` supplies `Analyze the attached image.` for image-only sends so the backend's required query field stays populated. System prompts overhauled for Nexus coding assistant persona. `CODE_ASSISTANT_SYSTEM_PROMPT` now instructs multi-chunk reasoning, integration signal extraction (Talend/Workato/MuleSoft/dbt), structured debugging (Finding/Evidence/Cause/Fix/Verify), and Deep Dive escalation. CSRF exempt list consolidated to `/api/v1/auth/` prefix — no more manual per-route additions. Backend container rebuilt and running.

**Status** (2026-05-19): Config cleanup complete. Admin email parameterized; `.env.production.template` aligned with Anthropic as default provider.

**Config cleanup done (2026-05-19)**:
- [x] `ADMIN_INITIAL_EMAIL` env var added to Settings + wired into bootstrap admin + migration seed
- [x] `.env.production.template` defaults to Anthropic (not DeepSeek); all provider API keys + model tiers documented
- [x] `docker-compose.yml` — mcp-server service added, Hindsight LLM model parameterized, MCP_JWT_SECRET_KEY removed from dashboard service
- [ ] Ansible Nexus-Infra repo still needs updating: `LLM_API_KEY` → `ANTHROPIC_API_KEY`, port name mismatches, `ADMIN_INITIAL_EMAIL` in env.j2

---

**Status** (2026-05-12): Sprint 4 complete. 279 tests passing.

**Sprint 2 done**:
- [x] Structured JSON logging + request ID middleware (`src/logging_config.py`, `src/api/middleware/request_id.py`, `main.py`)
- [x] Account lockout — migration 023, User model columns, `authenticate_user` rewrite, 403 route handler, 3 new tests
- [x] Soft deletes — migration 024, `deleted_at` on 3 models, 9 SELECT filters, cascade on conversation delete, 4 new tests
- [ ] Integration test suite (real Postgres + Qdrant) — deferred to CI setup

**Sprint 3 done**:
- [x] DB indexes — migration 025: `(project_id, created_at DESC)` on documents, `created_at` on audit_logs, `conversation_id` on conversation_messages
- [x] Error schema standardization — `src/api/errors.py` with `api_error()` helper, `ErrorDetail` in `schemas.py`, 12 priority raises converted in auth/documents/conversations/query
- [x] RAG chain extraction — `_recall_memories`, `_execute_search`, `_persist_query` helpers extracted; orchestrator reduced to ~100 lines
- [x] Prometheus metrics — `prometheus-client` dep, `src/api/metrics.py`, `src/api/middleware/metrics.py`, `GET /metrics` endpoint, `settings.metrics_enabled` gate

**Sprint 4 done**:
- [x] JWT key rotation — `jwt_previous_keys` env var, `decode_token()` tries all keys, `POST /admin/auth/rotate-key` advisory endpoint
- [x] CSRF protection — `src/api/middleware/csrf.py` double-submit cookie; `csrf_token` cookie set at login; disabled via `CSRF_ENABLED=false`
- [x] SSO/OIDC skeleton — migration 026 (`sso_provider`, `sso_subject`), `src/api/routes/sso.py`, `sso_enabled=False` default; no new library deps
- [x] OpenAPI docstrings — `summary=` on all 69 routes across 8 files; docstrings on 32 previously missing

---

**Previous status** (2026-05-11): Sprint 1 complete. Three blocking issues fixed:
1. **RBAC bug closed** — `DELETE /documents/{id}` now enforces project access check + logs `document_deleted` audit event. Test coverage added.
2. **Onboarding ingest wired** — `run_background_setup()` stub replaced: ZIP path marks complete immediately (ProvisioningStep/MCP already handled it); GitHub URL path downloads the archive and ingests text files via `ingest_text()`.
3. **EP-001 docs hardened** — RUNBOOK.md has Ollama model check + full Rollback Procedure section; SMOKE-TEST.md has explicit bootstrap-admin API test; `deploy/caddy-install.sh` moved to `deploy/legacy/`.

**Next up**:
- Wire `run_company_background_setup` in `system_config_service.py` (same stub pattern as the user-level one, now fixed)
- Test full flow: fresh DB → bootstrap → admin wizard → invite user → user welcome wizard
- Consider rendering `system_config.workspace_name` in app chrome (sidebar header)
- Sprint 2: integration tests (real Postgres + Qdrant), structured logging, soft deletes, account lockout

**Previous status** (2026-05-05): EP-002 is implemented as "secure existing/admin-created accounts" and passed Docker-stack API/BFF acceptance. MFA is mandatory for every active authenticated user before normal app/API use. Backend enforcement lives in `CurrentUser`: non-enrolled users can still call `/auth/me`, `/auth/logout`, `/auth/refresh`, `/auth/totp/setup`, and `/auth/totp/verify`, but normal protected APIs return `403` with `code: "mfa_required"`. Frontend auth guard redirects authenticated users with `mfa_enabled=false` to `/settings/security`; successful TOTP verify refreshes auth state so the app unlocks without a manual reload. Existing MFA-enabled users still see the password + MFA login screen, and backup-code login remains inside `/auth/verify-mfa`. Request Access, outbound email, invite tokens, questionnaire/self-service account creation, TOTP secret encryption, and strict TOTP replay tracking remain deferred. Acceptance uncovered and fixed two backend issues: backup-code JSONB mutation persistence now uses `flag_modified`, and refresh JWTs include a random `jti` so rapid repeated logins do not collide on `refresh_tokens.token_hash`.

**Previous status** (2026-04-21 cont.): Post-onboarding source upload landed. Wizard no longer blocks on "must upload a ZIP per selected platform" — users declare their stack at onboarding and upload ZIPs from the new `/settings/sources` page whenever they have them ready. Page derives Uploaded/Pending status by cross-referencing `UserOnboarding.selected_platforms` against rows in the MCP `projects` table; pending rows reuse `lib/mcpClient` (uploadZip + subscribeToLogs) to drive the same upload → scan → generate → push pipeline as the wizard. Sidebar now has a top-level "Sources" entry. Bug fix earlier in the session: `KB_API_URL` promoted to a Docker build ARG because `next.config.ts` rewrites are resolved at `npm run build` time in standalone mode, not per-request — the wizard was 500'ing with `ECONNREFUSED ::1:8002` until this was fixed.

**Earlier today (2026-04-21)**: Nexus per-client deployment track landed. Backend parameterized for per-client stacks, `POST /auth/bootstrap-admin` + `GET /auth/bootstrap-status` live, TOTP/MFA end-to-end (pyotp + 10 bcrypt-hashed backup codes, QR code via `qrcode.react` added this session), IP allowlist + ProxyHeaders middleware on both RAG backend and MCP, `deploy/deploy-client.sh` + nginx template + systemd template unit. Frontend onboarding wizard actually streams ZIPs to `/mcp/upload-zip` and drives the stepper from the `/logs/stream` SSE feed. Login page auto-detects empty users table and switches into bootstrap mode; `/settings/security` page for MFA setup.

**Key decisions today** (rationale in PROJECT_MEMORY.md):
- Production skips the default-admin seed; `POST /auth/bootstrap-admin` is the single first-admin path (returns 409 forever after first user). Dev mode still seeds admin/admin.
- `vector_store.COLLECTION_NAME` reads `settings.qdrant_collection` at import time — default `documents`, per-client override via env.
- `validate_production_secrets()` HARD-FAILS on default JWT secret, default admin password, empty `MCP_INGEST_API_KEY`.
- Middleware order: `IPAllowlist` registered BEFORE `ProxyHeaders` (stacks LIFO → dispatch is ProxyHeaders then IPAllowlist) so the allowlist sees real client IP.
- MCP `--host` default kept at `0.0.0.0` for Docker; production lockdown via `deploy/mcp-server@.service` which passes `--host 127.0.0.1`.

**Migrations pending**: `alembic upgrade head` applies `015_add_wizard_events.py`, `016_add_feedback.py`, AND `017_add_mfa_fields.py`. Without 017 the TOTP endpoints will 500 on first use.

**Next up**:
- Optional literal browser pass for EP-002 UI rendering/redirect polish. Functional Docker-stack API/BFF acceptance passed for the full MFA flow: password login for a non-enrolled user, protected API blocked with `403 mfa_required`, TOTP setup/verify, 10 backup codes returned once, same-session unlock, future TOTP login, backup-code login, and backup-code reuse rejection.
- Manual end-to-end verification of the new partial-upload flow: reset admin onboarding, pick multiple platforms, upload one ZIP, hit Continue, then visit `/settings/sources` and upload another.
- End-to-end deploy dry-run: `deploy/deploy-client.sh --client-name demo --port-offset 100 --no-certbot` against a clean host (still deferred from the Nexus track).
- Consider extracting the `RealProcessing` / `SourcesSettingsPage` upload queue into a shared component if a third call site appears (intentionally deferred — two sites isn't enough abstraction pressure).

**Older status** (Pre-Sales Audit Day 1, 2026-04-20): chat thumbs-up/down persisted end-to-end. Day 2 (atomic docs swap + staging GC) was the planned next item before today's Nexus track preempted it.

**Earlier today**: Admin project-assignment UX fix (dropdown by display name) — see PROGRESS_LOG entry.
**Before that**: Tenant identity surfaced in UI + enterprise onboarding wizard premium polish. Per-VPS branding now drives the sidebar wordmark, browser title, login page, and wizard chrome via `GET /api/v1/tenant/config` reading `TENANT_NAME` / `TENANT_SLUG` / `TENANT_ENVIRONMENT` env vars. Small environment pill (amber STAGING / violet PREVIEW / muted others) renders everywhere when not production. Same session shipped: 7-step onboarding wizard with an explicit "Ready" celebration step (fixed the auto-tour bug), UI primitive layer under `components/ui/` (Button/Input/Card/Badge/Label), elevation shadow tokens, branded driver.js popovers, and wizard telemetry.
**Prior session**: 2026-04-17
**Also this session (earlier from dev-B)**: Hindsight memory retention fix deployed. `decide_retention()` now allows FULL retention for metadata queries with search results, all retain call sites pass `action`/`query_type`/`project_ids`, distillation disabled (structured fallback), Hindsight timeout increased to 300s.

## What's Done

### Backend (216 tests passing)
- FastAPI app with CORS, lifespan events
- Database: SQLAlchemy async models (User, Role, Document, QueryLog, IngestionJob)
- Ingestion: PDF/DOCX/PPTX/XLSX parsing, **semantic chunking** (paragraph-aware, heading-aware, slide markers), **replace-on-re-upload**, **sentence-boundary chunking** (overlap=100)
- Retrieval:
  - **Hybrid search**: Dense (provider chain: Voyage AI → Ollama) + Sparse (hash-based, fixed TF-IDF bug) with RRF fusion
  - **Voyage AI reranking**: rerank-2.5 via API (replaced local cross-encoder ms-marco-MiniLM-L-12-v2)
  - **Query expansion**: LLM-generated alternatives via provider chain (more-specific + more-general)
  - **Chat-with-document**: Optional document_ids filter on all search functions
  - Named vectors in Qdrant (dense + sparse), payload indexes for RBAC + document_id
  - **Tuned parameters**: top_k=10, threshold=0.3, rerank_top_n=30, prefetch_limit=50, max_documents=6
- Generation: **Multi-provider LLM client** (Kimi K2.5 → Ollama fallback chain, sync + **streaming SSE**, Ollama num_ctx=16384), prompt builder (10000 token context with per-source truncation), RAG chain with intent routing + anti-hallucination prompts
- **Embedding provider chain**: Voyage AI (voyage-4-large, 1024-dim) → Ollama (nomic-embed-text, 768-dim) fallback
- **Intent classifier**: Zero-LLM heuristic router (keyword-based summary detection, all queries go through SEARCH)
- **Map-reduce summarization**: Full-document summaries via parallel map → reduce
- **Numbered citations**: LLM uses `[1]`, `[2]` format with `[Reference N: filename]` headers
- **Auth**: JWT httpOnly cookies (access 60min + refresh 7d rotation), bcrypt passwords, rate-limited login, admin panel for user management
- **RBAC**: Full permission system (9 codenames), Manager/Developer roles, project assignments, audit logging
- **Security**: Hardcoded secrets removed, localhost-only Docker ports, Caddy auto-TLS, UFW+fail2ban, security headers, CORS tightened, ingestion API key auth, extended rate limiting, daily PostgreSQL backups
- API routes: /health, /api/v1/query, /api/v1/query/debug, /api/v1/query/stream (SSE), /api/v1/documents, /api/v1/ingest
- Conversations: /api/v1/conversations with conversational RAG pipeline
- Feature flags: `hybrid_search_enabled`, `reranker_enabled`, `query_expansion_enabled`

### Frontend (build passing)
- Next.js 15 App Router with BFF pattern (route.ts → FastAPI)
- React Query for server state, Zod for validation
- Tailwind + lucide-react for styling
- **Markdown rendering**: `react-markdown` + `remark-gfm` for rich answer display
- **Clickable citations**: `[N]` in answers scroll to `#source-N` anchors
- Pages: / (chat — main interface), /chat (redirects to /), /documents (upload/list/delete), /settings (health)

## To Run

```bash
docker-compose up -d          # Qdrant + PostgreSQL
cd backend && alembic upgrade head && uvicorn src.api.main:app --reload
cd frontend && npm run dev    # http://localhost:3000
```

## Recent Changes

- **Constrained RAG agent layer** (2026-06-02): Added `backend/src/generation/agent_orchestrator.py` with a fixed read-only tool registry and 3-step trace cap. `conversational_rag.py` records steps for recall, previous-source reuse, metadata, summary, and search branches; streaming Chat emits optional `event: step`. Frontend parses `AgentStep` events in `useStreamMessage` and displays the latest step in the streaming assistant bubble. Conversation API tests now send CSRF headers for state-changing requests. Verification: 57 backend targeted tests passed, 5 frontend stream tests passed, frontend build passed.
- **Phase 5 Memory Dashboard** (2026-04-15): Admin dashboard at `/admin/hindsight` with Insight Pipeline, Memory Banks, and Reflect tabs. Backend: 4 GET endpoints + `get_bank_stats()` / `list_bank_memories()` helpers. Frontend: 4 BFF proxy routes, Sidebar Brain icon link, full dashboard page. Bug fix: `acreate_mental_model`. Files: `hindsight_client.py`, `memory.py`, `Sidebar.tsx`, 4 new route.ts, `hindsight/page.tsx`.
- **Enhanced Hindsight Memory Integration** (2026-04-14): Project-scoped banks, tags, intent-based gating, LLM distillation, observations, reflect, mental models. Admin API at `/memory/mental-models` and `/memory/reflect-patterns`. Files: `hindsight_client.py` (full rewrite), `conversational_rag.py`, `config.py`, `memory.py` (new), `main.py`.
- **Admin sidebar visibility & role separation** (2026-04-09): Login sets auth state instantly (no refresh needed), admin role restricted to read-only dashboard (usage/audit/benchmark), superadmin-only for user/role/project management. Migration 012 + frontend guards + backend AuditViewer dependency.
- **Post-stress-test improvements** (2026-04-03): 5 fixes — targeted enrichment query for project_detail (overview docs), reranker for metadata enrichment, document_sources init bug, METADATA_SYSTEM_PROMPT rewrite (doc content primary), classifier prompt refinement (content vs metadata). Files: `conversational_rag.py`, `prompt_builder.py`, `intent_classifier.py`.
- **RAG follow-up robustness** (2026-04-03): Streaming context-first enabled, `needs_new_search()` safety net, metadata enrichment for all content intents, cross-project source filter, prompt improvements (partial info + no retraction), temperatures lowered (kimi 0.45, ollama 0.45, classifier 0.0), reranker_min_score 0.05→0.15. Files: `conversational_rag.py`, `prompt_builder.py`, `config.py`, `llm_client.py`, `intent_classifier.py`.
- **RAG hallucination/recall fix** (2026-03-27): Increased retrieval depth (`top_k` 5→10, `max_result_documents` 3→6, `prefetch_limit` 30→50, `rerank_top_n` 20→30), `MAX_CONTEXT_TOKENS` 3000→8192 (chat) / 6144→10000 (search), strengthened anti-hallucination prompts, added project attribution to source headers, improved history truncation (anchors first exchange, 4000 tokens / 20 messages), added `num_ctx=16384` to Ollama calls, fixed hardcoded `top_k=3` in chat path. Files: `config.py`, `conversational_rag.py`, `prompt_builder.py`, `llm_client.py`.
- **Admin project-scoped search** (2026-03-24): Fixed `_build_search_filter()` in `search.py` to apply `allowed_groups` filter when admin selects a specific project. Without this fix, admins saw all documents regardless of project selection.
- **Conversation summary intent** (2026-03-18): LLM classifier now distinguishes "summarize our chat" from "summarize document X". Added `CONVERSATION_SUMMARY` action, conversation context passing, dedicated prompt builder. Fixed `using="dense"` Qdrant crash in `map_reduce.py` and `query.py`. Files: `intent_classifier.py`, `prompt_builder.py`, `conversational_rag.py`, `map_reduce.py`, `query.py`.
- **Auto-detect corrections** (2026-03-16): When a user corrects the chatbot ("no that's wrong, it's actually X"), the system detects it via keyword heuristics, extracts a structured correction via LLM, and stores it in a shared `rag-corrections` Hindsight bank. Corrections are recalled for ALL users on relevant queries and injected as "## Known corrections" in the LLM context. Zero latency impact (fire-and-forget). Files: `hindsight_client.py`, `prompt_builder.py`, `conversational_rag.py`, `rag_chain.py`, `query.py`.
- **Deploy Security + RBAC to VPS** (2026-03-09): Deployed all Phase 6.1/6.2 changes to running VPS. Reverted docker-compose ports to public (no Caddy yet). Added MCP_INGEST_API_KEY to .env and docker-compose.yml. Fixed migration 006 setval bug (`GREATEST(..., 1)` to avoid 0 on empty sequences). Migration 006 applied successfully — permissions, role_permissions, project_assignments, audit_logs tables created and seeded. All services healthy.
- **Fix MCP server DuplicateTableError** (2026-03-02): MCP server `create_all` now checks existing tables first before creating — fixes crash on restart when `projects` table already exists.
- **Fix Docker startup crash** (2026-03-02): Backend Dockerfile now runs `alembic upgrade head` before `uvicorn` startup. Fixes `relation "users" does not exist` crash when PostgreSQL has no tables.
- **Deep Dive Streaming Repair** (2026-03-02): Fixed three compounding issues preventing Deep Dive output: (1) concurrent stderr reading via `asyncio.create_task` to prevent pipe buffer deadlock, (2) restored `assistant` text as fallback when `content_block_delta` events don't arrive (tracked via `_text_streamed_via_delta` flag), (3) made `--session-id` conditional — only used for multi-turn continuations, not first queries from search page. Added debug logging for raw events and parsed event types.
- **Deep Dive Streaming Fix + Chat Integration** (2026-03-02): Fixed `_convert_event()` to handle `content_block_delta` events (streaming tokens were silently dropped). Added multi-turn session management (`--session-id`/`--continue`). Updated prompt for knowledge persistence (`insert_to_rag`). Integrated Deep Dive into `/chat` page with ModeToggle (Chat/Deep Dive), ProjectSelector, DeepDiveChatBubble (collapsible thinking, tool calls, streaming markdown). Build passing.
- **Deep Dive mode** (2026-03-01): Full-stack feature — DeepDiveService (subprocess + stream-json), API endpoints (POST /mcp/deep-dive SSE, GET /mcp/deep-dive/projects), BFF routes, useDeepDive + useDeepDiveProjects hooks, ModeToggle + ProjectSelector + DeepDiveDisplay components, search page integration. Build passing.
- **Fix markdown rendering** (2026-03-01): Installed `@tailwindcss/typography`, registered plugin, added prose CSS variable overrides for HSL theme integration, simplified `MarkdownContent.tsx` by delegating `code`/`pre`/`blockquote` styling to the plugin. Build passing.
- **Fix Hindsight retain blocking response** (2026-02-28): `retain_interaction()` was `await`-ed at all 4 call sites, blocking HTTP response until quality bank timed out (~60s). Changed to `asyncio.create_task()` (fire-and-forget), `asyncio.gather()` (concurrent bank retains), and reduced timeout 60→15s. Response now returns immediately after LLM completes.
- **Fix Voyage reranker 429 crash** (2026-02-28): Added retry with backoff tuned for 3 RPM free tier (`[2s, 5s, 15s]`, 3 attempts) and `Retry-After` header parsing (capped at 30s) for 429 rate limits. Added graceful fallback in `search.py` — on any reranker API failure, returns candidates by original RRF scores instead of crashing.
- **Replace local reranker with Voyage AI rerank-2.5** (2026-02-28): Local cross-encoder returned negative scores for all candidates → nothing reached LLM. Replaced with Voyage AI `rerank-2.5` API (async httpx, 0-1 calibrated scores, `min_score=0.05`). Lowered pre-filter threshold to 0.0 in `search_and_rerank()` so reranker is the sole quality gate. Removed `sentence-transformers` dep (~500MB). 174 tests passing.
- **Fix Hindsight memory not working on /query and /query/stream** (2026-02-28): Added Hindsight recall+retain to `rag_chain.py` (search bar queries) and `query.py` `/query/stream` endpoint. Added `memory_context` parameter to `build_messages()` in `prompt_builder.py`. Added INFO-level diagnostic logging to `hindsight_client.py`. All three query paths (search bar, streaming, conversations) now have full Hindsight integration.
- **Fix LLM timeouts on all endpoints** (2026-02-27): Increased timeouts (Kimi 15→60s, Ollama 30→120s), eliminated non-streaming `generate_answer()` calls from streaming path via `precompute=False` parameter in `_prepare_rag_context()`, improved error logging with exception types. Streaming endpoint no longer blocks on hidden non-streaming LLM calls.
- **Fix RBAC group mismatch — 0 search results** (2026-02-27): Admin users now bypass RBAC group filtering in `_build_search_filter()` (`backend/src/retrieval/search.py` line 35) and `query_debug()` (`backend/src/api/routes/query.py` line 68). Documents stored with `["public", project_id]` were invisible to users with `groups=["admin"]` because `"admin"` was never in `allowed_groups`.
- **Fix NO_RESULTS_PROMPT vs Hindsight memory** (2026-02-27): Added explicit trust declaration for memory context in `NO_RESULTS_PROMPT` (`backend/src/generation/prompt_builder.py` lines 33–43). LLM was ignoring injected Hindsight memories because "NEVER make up information" caused it to treat memory context as untrusted general knowledge. Prompt now distinguishes trusted memory context from general knowledge.
- **Fix config.py drift** (2026-02-24): Synced `config.py` with runtime expectations — removed dead groq/openrouter settings, added missing `kimi_timeout`, `kimi_temperature`, `ollama_timeout`, `reranker_min_score`, fixed `llm_providers` to `["kimi", "ollama"]`, set `query_expansion_enabled=False`. Fixes `AttributeError: 'Settings' object has no attribute 'kimi_timeout'` and `KeyError: 'groq'`.
- **Auto-detect Qdrant dimension mismatch** (2026-02-24): `ensure_collection()` now checks existing collection's dense vector dim against `get_embedding_dim()`. If they differ (e.g. 768→1024 after Voyage AI switch), auto-deletes and recreates. No manual collection deletion needed.
- **Fix SQLAlchemy engine log noise** (2026-02-24): Changed `echo=settings.is_dev` to `echo=False` in `backend/src/db/session.py` — SQLAlchemy's `echo=True` overrides logger level, causing INFO log flood on first request.
- **Fix production bugs + enable Voyage AI** (2026-02-24): Fixed reranker min-score filtering (chitchat no longer gets -9.13 scores sent to LLM), removed `dimensions` param from Voyage API calls (was causing 400 errors), added reranker warmup at startup (no more +100s first request), fixed `await` on sync `analyze_query()` in streaming endpoint, added `delete_collection()` helper for collection migration.
- **Eliminate intent classifier LLM call** (2026-02-23): Replaced LLM-based intent classifier with zero-cost heuristic. All queries → SEARCH. Summary detection via keyword regex. Disabled query expansion by default. QA: 3→1 LLM calls, Chitchat: 2→1 LLM calls.
- **Fix Kimi K2.5 temperature 400 error** (2026-02-23): Changed `kimi_temperature` from 0.4 to 0.6 — Kimi K2.5 instant mode (thinking disabled) requires exactly 0.6. Was causing all Kimi calls to fail with HTTP 400 and fall back to Ollama.
- **Fix RAG Query Latency** (2026-02-23): Disabled Kimi K2.5 thinking mode (`extra_body={"thinking": {"type": "disabled"}}`), set temperature to 0.6 (required by instant mode), added singleton HTTP clients with timeouts (15s Kimi, 30s Ollama). Expected ~27s → ~3-5s.
- **RAG Retrieval Quality Overhaul** (2026-02-23): 4-phase implementation:
  1. Quick wins: Kimi K2.5 provider, smarter context truncation (6144 tokens, per-source), tuned search params
  2. Fixed critical sparse embedding bug (TF-IDF vocabulary mismatch → deterministic hash-based approach)
  3. Voyage AI embedding provider chain (1024-dim), upgraded reranker (L-12-v2), dynamic embedding dimensions
  4. Improved chunking (overlap 32→100/128, sentence-boundary detection)
  - Intent classifier + query expander now use LLM provider chain instead of local Ollama
  - Fixed null-safety bug in intent classifier JSON parser
  - 193 tests passing

## Blockers

- No Docker on dev machine — can't test E2E locally, use SQLite for unit tests
- Kimi API key placeholder added to `.env` — needs real key
- Voyage AI API key placeholder added to `.env` — needs real key

## Next Steps

- [ ] Commit Layered RAG Slice 1.
- [ ] With the local stack running, re-ingest canonical docs for existing projects and record the starter retrieval eval baseline.
- [x] RBAC: Project-scoped document access (2026-03-12)
- [x] Metadata-aware RAG: "Ask Anything" mode (2026-03-12)
- [ ] Deploy onboarding: `docker compose up -d --build backend frontend` then `docker exec intodata-kb-v2-backend-1 alembic upgrade head` (applies 014 — creates user_onboarding + notifications tables)
- [ ] Wire `run_background_setup` in `onboarding_service.py` to the real ingest pipeline (currently stubbed with 3s sleep)
- [ ] Wire real file upload for wizard SourceUploadStep (`source_filename` is recorded but ZIP bytes are not yet uploaded)
- [ ] Flip coming-soon tools to `"available"` in `_TOOL_CATEGORIES` (api/routes/onboarding.py) as each integration lands
- [ ] Render workspace_name in the app chrome (sidebar header / page title) once onboarding ships
- [ ] Per-deployment env vars for tenant branding (subdomain, logo, color) — one VPS per client
- [ ] Run Qdrant migration: `python scripts/migrate_qdrant_remove_public.py --apply`
- [ ] Verify: new user login → wizard → tour; existing user → no wizard
- [ ] Phase 2: Workato scanner plugin
- [ ] Frontend: document selector for chat-with-doc

See `PROJECT_MEMORY.md` for architecture decisions and `PROGRESS_LOG.md` for session history.
