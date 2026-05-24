# RAG Pipeline -- Subsystem Reference

Read the relevant section when working on that area. Not loaded at session start.

---

## Auth System

JWT httpOnly cookie authentication with token rotation:

- **Access token**: 60min expiry, stored in httpOnly cookie on `/`
- **Refresh token**: 7-day expiry, stored in httpOnly cookie on `/api/v1/auth/refresh`, rotated on use
- **Endpoints**: `POST /auth/login`, `POST /auth/logout`, `POST /auth/refresh`, `GET /auth/me`
- **Dependencies**: `CurrentUser`, `AdminUser`, `ProjectManager`, `ManagerUser` (in `src/api/dependencies.py`)
- **Cookie settings**: `COOKIE_DOMAIN`, `COOKIE_SECURE`, `samesite=lax`
- **Rate limiting**: 5/min login, 10/min refresh (via slowapi)
- **Password hashing**: bcrypt
- **Token storage**: Refresh token SHA-256 hashes in `refresh_tokens` table

---

## RBAC System

Role-based access control with 3 default roles and 9 permissions:

- **Roles**: admin (all perms), manager (project management), developer (query + view)
- **Permissions**: `manage_users`, `manage_roles`, `assign_projects`, `view_all_projects`, `manage_projects`, `view_assigned_projects`, `query_rag`, `ingest_documents`, `view_audit_logs`
- **Project assignments**: Filter Qdrant queries via `_build_search_filter()` in `src/retrieval/search.py`
- **Admin endpoints**: User CRUD (`/admin/users`), Role CRUD (`/admin/roles`), project assignments (`/admin/project-assignments`), audit logs (`/admin/audit-logs`), permissions listing (`/admin/permissions`)
- **Permission checks**: `has_permission()`, `get_user_permissions()`, `can_view_all_projects()` in `src/rbac/permissions.py`
- **Superadmin**: Bypasses all permission checks

---

## Token/Cost Tracking

Automatic token usage and cost tracking for all LLM interactions:

- **Model**: `QueryUsage` in `src/db/models/query_usage.py` (15 fields: user_id, interface, query_text, model_name, provider, input/output/embedding tokens, cost_usd, latency/embedding/search/llm ms, project_id, conversation_id)
- **Persistence**: Automatic on all query paths:
  - Non-streaming RAG query (`rag_chain.py:run_rag_query()`)
  - Non-streaming conversation (`conversational_rag.py:run_conversational_rag()`)
  - Streaming conversation (`conversational_rag.py:run_conversational_rag_stream()`)
- **Usage tracking**: `UsageStats` / `StreamUsageStats` dataclasses in `src/generation/usage.py`
- **Cost calculation**: `MODEL_PRICING` table with per-million-token pricing (Kimi, Ollama, Voyage)
- **SSE event**: `event: usage` emitted after stream completes (input_tokens, output_tokens, model, provider, cost_usd, latency_ms)
- **Admin endpoints**: `GET /admin/usage/summary` (aggregated by interface/model/day), `GET /admin/usage/recent` (paginated list)
- **Thread-local storage**: `get_last_usage()` in `llm_client.py` returns stats from the most recent LLM call

---

## External Usage Recording

The KB MCP server posts usage records for doc generation and deep dive to the RAG backend:

- **Endpoint**: `POST /api/v1/usage/record` (Bearer auth via `MCP_INGEST_API_KEY`)
- **Accepts**: `interface`, `model_name`, `provider`, `input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`, `project_id`, `query_text`
- **Route**: `src/api/routes/usage.py`
- **Called by**: `RagBridge.post_usage()` in `knowledge_base/mcp-server`

---

## Embedding Token Tracking

- **Storage**: Thread-local `_last_embed_tokens` in `src/retrieval/embedder.py`
- **Access**: `get_last_embed_tokens()` — returns token count from most recent embedding call
- **Populated**: All 4 `QueryUsage` creation sites include `embedding_tokens=get_last_embed_tokens()`

---

## Benchmarking

Automated RAG quality benchmarking framework:

- **CLI**: `python -m benchmark run|list|show|compare` (in `backend/benchmark/`)
- **Model**: `BenchmarkRun` in `src/db/models/benchmark.py` with JSONB results field
- **Metrics**: precision@5, LLM-as-judge relevance (1-5 scale), latency, tokens, cost
- **Test corpus**: `benchmark/corpus/default.json` (10 Q&A pairs)
- **API**: `GET /benchmark/runs` (list all), `GET /benchmark/runs/{id}` (details with per-question results)
- **Frontend**: `/admin/benchmark` page with runs list + detail drill-down, summary cards, color-coded relevance

---

## Frontend Permission Guards

Admin UI elements are filtered by user permissions:

- **Hook**: `usePermissions()` in `frontend/hooks/usePermissions.ts` — wraps `useAuthContext()`, provides `hasPermission(codename)`, `hasAnyAdminPermission`, convenience booleans
- **Sidebar**: Admin sub-links filtered by required permission (Users→`manage_users`, Roles→`manage_roles`, Projects→`assign_projects`, Usage→`manage_users`, Benchmark→`manage_users`)
- **Admin layout**: Checks `hasAnyAdminPermission` (any admin permission grants access, not just `manage_users`)

---

## Onboarding System

Two-tier onboarding guards app access. Both wizards must complete before the main app is accessible.

**Flow**: Cold start -> `OnboardingGuard` checks backend state -> routes to appropriate wizard -> app unlocks

### Tier 1: Admin/Company Wizard (`AdminOnboardingWizard.tsx`)
- Triggered on first boot when no system config exists
- Steps: Welcome -> Platform select -> Source upload -> Workspace -> Communication tools -> Provisioning -> Complete
- Backend: `POST /api/v1/company-onboarding/step/{step}`, `GET /api/v1/company-onboarding/status`
- Persists to `system_config` table via `system_config_service.py`
- Frontend API routes: `app/api/onboarding/company/` (complete, status, setup-status, step)

### Tier 2: User Welcome Wizard (`UserWelcomeWizard.tsx`)
- Triggered for each new user after company setup completes
- Steps: UserWelcomeStep (intro, tour, preferences)
- Backend: `POST /api/v1/onboarding/step/{step}`, `GET /api/v1/onboarding/status`
- Persists to `onboarding` table per user

### Guard (`OnboardingGuard.tsx`)
- Wraps the entire app
- Checks `/api/onboarding/setup-status` -> if company not set up, shows `AdminOnboardingWizard`
- Checks `/api/onboarding/status` -> if user not onboarded, shows `UserWelcomeWizard`
- Hook: `useSystemConfig.ts` for reading/updating system-wide config state

### Key files
- `src/api/routes/company_onboarding.py` -- company setup endpoints
- `src/api/routes/onboarding.py` -- per-user onboarding endpoints
- `src/db/models/system_config.py` -- system-wide key/value config store
- `src/db/models/onboarding.py` -- per-user onboarding state
- `src/services/system_config_service.py` -- CRUD for system_config
- `src/services/onboarding_service.py` -- onboarding step progression logic
- `frontend/components/onboarding/AdminOnboardingWizard.tsx`
- `frontend/components/onboarding/UserWelcomeWizard.tsx`
- `frontend/hooks/useSystemConfig.ts`
