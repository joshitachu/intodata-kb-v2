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

The KB MCP server posts usage records for doc generation to the RAG backend:

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

## Onboarding and Walkthrough System

Persona-aware first-run experience. Three moving parts: role assignment at
access approval, a first-run wizard, and a spotlight walkthrough. All three key
off one server-resolved persona so they cannot disagree about who the user is.

### Persona resolution (single source of truth)

`src/rbac/persona.py` -> `resolve_persona(user, owns_projects=...)` returns
`owner | admin | project_owner | developer`, derived from persisted authority:

- `owner` -- `users.is_superadmin`
- `admin` -- holds `manage_users` or `assign_projects` (permission-based, so a
  renamed admin role still resolves)
- `project_owner` -- owns a project, or holds `manage_projects` / `create_projects`
- `developer` -- everything else

Exposed on `GET /api/v1/onboarding/status` as `persona`. The browser must NOT
re-derive it: the old guard branched on `is_superadmin` alone, so a non-Owner
Admin received the read-only member welcome.

Persona is a *teaching* concept only. Never use it as an authorization check --
that stays with `src/rbac/permissions.py`.

### Role assignment at access approval

`POST /api/v1/admin/access-requests/{id}/approve` takes an optional body:
`{role_id, project_ids, welcome_note}`. The choice is stored on the invite
(`user_invites.assigned_role_id`, `.assigned_project_ids`, `.welcome_note`) and
applied by `accept_invite`.

- Owner-named roles are refused, reusing `_load_assignable_roles`.
- `project_ids` additionally requires `assign_projects`.
- The grant is written to the audit log as `access_request_approved`.
- A missing/NULL role falls back to `DEFAULT_MEMBER_ROLE` (`src/rbac/permissions.py`),
  which is also what SSO first-login uses. Do not re-hardcode `"developer"`.
- A role deleted between approval and acceptance is `ON DELETE SET NULL`, and
  acceptance degrades to the default role rather than failing signup.

Before this existed, `accept_invite` hardcoded `developer`, so role was decided
*after* onboarding rendered and admins had to promote people from a second screen.

### Tier 1: Provisioning wizard (`AdminOnboardingWizard.tsx`)

Shown only when deployment setup is incomplete AND the user can provision
(`persona === "owner"`). 8 steps: Welcome, Workspace, Platforms, Tools, Source,
Setup, **Authority**, Ready. The Authority step is `CapabilityStep` -- the Owner
finishing setup was previously never told what their own role covers.

Backend: `POST /api/v1/company-onboarding/step/{step}`, persists to `system_config`.

### Tier 2: Member walkthrough (`UserWelcomeWizard.tsx`)

Everyone else, including Admins. 4 steps:

1. `MemberWelcomeStep` -- names the approver and echoes their welcome note
   (from `invite_context` on the status response, only while `wizard_completed`
   is false).
2. `CapabilityStep` -- persona capabilities AND the boundary, from
   `components/onboarding/personaContent.ts`.
3. `GroundingStep` -- walkable 5-stage retrieval explainer.
4. `MemberReadyStep` -- hands off to the walkthrough or to a first action.

Copy lives in `personaContent.ts` as data so the wizard and `/guide` cannot drift.

### Walkthrough engine (`components/tour/`)

Custom spotlight overlay; **driver.js was removed**. Files:

- `types.ts` -- `TourStep`, permission guards, `applicableSteps()`
- `tours.ts` -- per-persona step composition (shared chapters + role chapters)
- `geometry.ts` -- shared `TargetRect`/`Viewport`, `sameRect`, `collapse`,
  `overlapArea`, `isOversize`. Three components were each about to derive their
  own idea of the target box; they now agree on one.
- `useTargetRect.ts` -- resolves and tracks the target element
- `useTourStage.ts` -- presentation phase machine, separate from resolution
- `useEnvironment.ts` -- viewport size and `prefers-reduced-motion`
- `placement.ts` -- pure `place()`, side selection scored by room AND occlusion
- `SpotlightOverlay.tsx` -- SVG-mask cutout, click-through over the target
- `TourCard.tsx` -- theme-token popover
- `TourProgress.tsx` -- top-of-viewport indeterminate bar while waiting
- `ProductTour.tsx` -- orchestration, persistence, keyboard control

**Resolution is separate from presentation.** This is the central design point.
`useTargetRect` answers "where is this step's anchor"; `useTourStage` answers
"what should the overlay be doing right now". The engine used to render `null`
while the next anchor resolved, so the overlay unmounted, the page flashed back
to full brightness and the card reappeared elsewhere -- reported by the user as
"it just jumps from page to page".

Phases: `idle -> hold -> closing -> shut -> open`.

- Same-page moves usually resolve inside the 140ms `hold` grace and glide
  straight to the new box -- no close, no blink.
- Cross-page moves iris the cutout shut, then do navigation, mount and
  scroll-into-view under a still-painted dim layer, then iris open.
- The overlay node is never remounted and the scrim is never removed. There is a
  DOM-identity assertion in the tests; do not "simplify" it back to conditional
  rendering.

Behaviour that matters:

- **Steps are filtered by permission** before starting, so admin steps are never
  shown to a member. Codenames must exist in `ALL_PERMISSIONS` -- a typo silently
  hides the step from everyone. Guards should track what the UI actually shows:
  `admin.roles` is guarded on `manage_roles`, which no seeded role holds, so it
  resolves only for the Owner -- matching the Roles tab's own `visible: isOwner`.
- **Waits for the target element** via MutationObserver instead of the old fixed
  `setTimeout(350)` after `router.push()`.
- **`target` is a comma-separated preference list**, resolved left to right by
  `resolveTarget()`, preferring a candidate that actually has a box. Steps used
  to point at page-root containers, so the cutout was the whole page and
  highlighted nothing. Each step now names the specific control and falls back
  to a coarser ancestor for the empty-deployment case.
- **`isOversize` drops the cutout** when the anchor covers >55% of the viewport.
  A centred card over an even scrim is honest; a cutout around 90% of the screen
  is a fake highlight.
- **Card placement avoids its own subject.** Sides are scored by room *and* by
  how much of the target they would cover. Clamping a card back into the viewport
  can otherwise park it on the very element the step describes -- a card
  explaining the cost tile landing on the cost tile.
- **A step whose anchor never mounts is dropped** (2.5s timeout) rather than
  spotlighting an empty page. Two steps (`evidence.docs-pdf`,
  `runtime.evidence`) intentionally have no fallback because their subject does
  not exist without data; on an empty deployment they cost that 2.5s and are
  then dropped.
- **`TargetState` is tagged with its selector.** Callers MUST check
  `targetState.selector === step.target` before acting on `status`. State lands a
  render after the selector changes, and reading `status` alone attributed the
  previous step's verdict to the current step -- which cascaded one missing anchor
  into skipping the entire tour.
- **The card renders the *committed* step**, which lags the requested one during
  a handoff, so its controls and `aria-live` text always match what is on
  screen. Advancing works off the committed index; a click mid-handoff
  re-requests rather than skips.
- **`scrollIntoView` is instant (`behavior: "auto"`) and only when off-screen**,
  and it happens while the aperture is shut. Smooth scrolling fought the rAF
  rect tracking and produced a cutout that chased the element down the page.
- **Progress persists by step id**, not index: each persona's tour is a different
  length, so an integer cursor cannot resume. Resume is skipped when
  `tour_variant !== persona` (after a promotion the step list changed shape).
- **Progress is chapter-based**, one proportional segment per chapter -- 27 dots
  was unreadable. "Skip section" jumps to the next chapter's first step, fires
  `tour_skipped`, and does not persist the skipped steps as seen.
- **Reduced motion is handled in JS as well as CSS.** With transitions disabled
  the fade choreography would degrade into a blink, so the engine skips the hold
  and iris entirely rather than relying on `[data-spotlight-animated]` alone.

Step counts: developer 10, project_owner 15, admin 19, owner 27, in 7 chapters.

Anchors are `data-tour="..."` attributes, 35 of them across pages and
components. `admin-{tab}` on the Organization page is a template literal
(`admin-members`/`access`/`roles`/`projects`), so grepping for the literal
string will not find it. Removing an anchor silently drops its step -- grep
before deleting a `data-tour`.

**Copy must match the page.** Two content bugs were shipped and then fixed here:
a step titled "The audit trail" pointed at `/admin/logs`, which is the runtime
execution console and not an audit trail (there is no audit-log UI in the app at
all), and the Project Docs step claimed documents are grouped into
architecture/component/data-flow categories, which does not exist -- grouping is
by sub-project. Verify a claim against the component before writing it.

### Guard (`OnboardingGuard.tsx`)

Branches on persona, not `is_superadmin`. Skips `/login`, `/onboarding`,
`/settings/security`, `/guide` -- security setup is itself a mandatory first-run
step and must not be covered. Renders the app (not a blank screen) when the
status fetch fails. Latches the chosen wizard for the session so it does not
vanish when a completion flag flips mid-flow.

### Product guide (`/guide`)

`components/guide/ProductGuide.tsx` plus `ArchitectureExplorer.tsx` (zoomable
platform -> subsystem -> mechanism model). Uses theme tokens throughout, so dark
mode works -- it previously had 184 hardcoded `slate-*` classes. `LAST_VERIFIED`
is one constant; update it on content review.

**Two editions.** `Edition = "using" | "running"`:

- `using` -- how to work in the product. Everyone gets this.
- `running` -- everything in `using`, plus Sources and pipeline, Organization,
  Observe and diagnose, and the Admin runbook.

The page used to render every chapter to every reader, so a member looking up
how to ask a question scrolled past four chapters about controls they cannot
open. Rules that keep that from coming back:

- A chapter marked `runningOnly` needs BOTH the edition AND its `permission`.
  Edition alone is not enough -- an admin who cannot open Organization should
  not be taught the Organization chapter. `visibleGroups()` enforces both.
- The switcher renders only for a reader with administrative access. A member
  gets no switcher: a door into chapters they cannot act on is a dead end.
- Search is filtered to the rendered set. Returning a hidden chapter would
  scroll to an element that does not exist.
- `?edition=` persists the choice for sharing, but `?edition=running` is ignored
  for a reader without access, so an admin's link cannot leak the admin edition.
- A `#hash` link to a hidden chapter switches edition for a reader who is
  allowed to see it, rather than silently doing nothing.
- The IntersectionObserver must re-bind when the edition changes; an observer
  bound to the old section set leaves the nav highlight stuck.

Both switcher buttons carry an explicit `aria-label`. Without it the accessible
name is the label plus the blurb, and the `running` blurb contains the words
"Using the app", so both buttons announced nearly the same thing.

The role quickstart tabs are a SEPARATE control from the edition. The guide
auto-selects the reader's real persona there; an explicit `?role=` still wins.

### Telemetry

`trackWizardEvent` types in `frontend/lib/telemetry.ts` must stay in sync with
`ALLOWED_EVENT_TYPES` in `src/api/routes/telemetry.py` -- an unlisted event is
dropped server-side, silently. Walkthrough events: `tour_started`,
`tour_skipped`, `tour_completed`, `tour_dismissed`, `tour_step_unavailable`.

### Key files

- `src/rbac/persona.py` -- persona resolution
- `src/rbac/permissions.py` -- `DEFAULT_MEMBER_ROLE`, permission constants
- `src/api/routes/onboarding.py` -- per-user state, persona, invite context
- `src/api/routes/company_onboarding.py` -- deployment setup endpoints
- `src/api/routes/admin.py` -- `approve_access_request` (role decision)
- `src/api/routes/auth.py` -- `accept_invite` (applies the recorded role)
- `src/db/models/access.py` -- `UserInvite.assigned_role_id` and friends
- `src/db/models/onboarding.py` -- `tour_variant`, `tour_seen_steps`
- `src/services/onboarding_service.py` -- step progression, tour progress
- `alembic/versions/097_invite_role_and_tour_variant.py`
- `frontend/components/onboarding/` -- wizards, steps, `personaContent.ts`
- `frontend/components/tour/` -- walkthrough engine and content
- `frontend/components/admin/org/ApproveAccessDialog.tsx` -- role picker
- `frontend/components/guide/` -- product guide and architecture explorer
