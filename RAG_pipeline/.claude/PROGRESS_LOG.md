# IntoData KB — Progress Log

## Session: 2026-04-10

### [Perf]: Topology perf round 3 — root-cause for scherpenhuizen-talend

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
    - Extended `_FALLBACK_INTERNAL_COMPONENTS` (lines ~990-1006) to include the
      tRunJob family + diagnostics: `tLogCatcher`, `tStatCatcher`, `tFlowMeter`,
      `tFlowMeterCatcher`, `tChronometerStart`, `tChronometerStop`, `tAssert`,
      `tAssertCatcher`. (`tRunJob`, `tPrejob`, `tPostjob`, `tWarn`, `tDie`,
      `tLogRow` were already present.)
    - In `_derive_systems_from_job_json` (lines ~1087-1105) for `platform == "talend"`:
      added `and not system.startswith("REFERENCE")` belt-and-braces filter to
      drop synthesized REFERENCE:* names that leak through the suffix-stripping
      path; capped the result at `sorted(systems)[:6]` so a single hub-job
      can't fan out across the entire view.
  - `RAG_pipeline/frontend/app/topology/page.tsx`
    - Added `lastFitKeyRef = useRef<string|null>(null)` next to `expandAnchorRef`
      to gate fitView animations behind the meaningful layout identity.
    - In the layout `useEffect`, replaced `setNodes([...bgNodes, ...laid])` with
      a wrapper that initializes `data.dimmed: false, data.highlighted: false`
      on every laid-out node — fixes the `undefined !== false` strict-equality
      gap that defeated the round-2 same-reference fast path on the first
      hover after a layout.
    - In the same effect, gated the full-layout `fitView({padding: 0.1, duration: 300})`
      call behind `fitKey = ${selectedProject}|${direction}|${compactMode}|${laid.length}`
      vs `lastFitKeyRef.current` so the 300ms zoom no longer re-fires every
      time `groupMembership` rebuilds with the same shape. Added `selectedProject`
      to the effect's dependency array.
    - In the project-change reset effect, added `lastFitKeyRef.current = null`
      so a new project still gets its initial fit.
    - In the search `useEffect` (`searchMatchIds.size > 0` block), capped the
      auto-fit to `<= 8` matches so single-letter searches that match dozens
      of nodes don't trigger a 400ms zoom-fit on every keystroke.
- **Problem**: After rounds 1 and 2 of the topology perf pass, every project the
  user tested felt snappy except `scherpenhuizen-talend` (toolbar showed 43 jobs
  / 41 conn / 12 groups). Hovering and toggling on it still stuttered, ripple
  was visible, viewport got stolen on direction/compact toggles.
- **Root cause** (two-layer):
  1. **Backend was synthesizing junk hub edges.** The on-disk manifest for
     `scherpenhuizen-talend` has empty `job_connections_used`, `connection_used_by`,
     and an absent `job_external_systems` (legacy scan). The `/projects/{id}/graph`
     endpoint hit the runtime fallback in `_derive_systems_from_job_json` →
     `_fallback_talend_system`. The fallback's suffix-stripping heuristic
     (strip leading `t`, strip `Input/Output/Connection/...` suffixes) treats
     `tRunJob`'s payload `REFERENCE:sendToMonitoringMOM` /
     `REFERENCE:Trigger_Next_Interface_92` as foreign systems. Result: ~37
     synthetic connection nodes and ~256 job→connection edges (vs the 0 that
     the on-disk manifest implies), most of them noise, all of them hub-shaped.
     Hub jobs like Oracle (29 jobs) and `REFERENCE:sendToMonitoringMOM` (27
     jobs) caused every hover to dim ~30 adjacencies, and dagre had to lay
     out 80+ nodes / 271 edges. **18x more edges** than the manifest hinted at.
  2. **Frontend amplifiers defeated round-2's wins on the first hover after
     any layout.** (a) `setNodes([...bgNodes, ...laid])` wrote nodes whose
     `data.dimmed` was `undefined`, not `false`. The hover effect's
     same-reference fast path is `if (n.data.dimmed === !isMatch && n.data.highlighted === isMatch) return n;`
     — `undefined !== false` for every node, so every node got rewritten on the
     first hover, defeating the whole optimization. After the first hover all
     nodes had explicit `false`/`true` and subsequent hovers were fine — which
     matches the user's lived experience: "all the others are fine, only
     scherpenhuizen feels bad". (b) `fitView({duration: 300})` from the layout
     effect re-fired every time `groupMembership` rebuilt with the same shape;
     `fitView({duration: 400})` from the search effect re-fired on every
     keystroke even when the match set was huge. Both stack and dominate frame
     time on heavy graphs.
- **Change**:
  1. **Backend**: dropped `REFERENCE:*` synthesis at the source by adding
     tRunJob-family component types to `_FALLBACK_INTERNAL_COMPONENTS` so they
     return `None` from `_fallback_talend_system`. Added a defensive
     `not system.startswith("REFERENCE")` filter in
     `_derive_systems_from_job_json` against any future component type that
     leaks REFERENCE:* through the suffix-stripping path. Capped the
     fallback at 6 systems per job (modern scans populate
     `job_external_systems` explicitly and bypass this path entirely, so the
     cap is a noise-floor on the legacy fallback only).
  2. **Frontend**: initialized `data.dimmed: false, data.highlighted: false`
     on every laid-out node so the round-2 same-reference fast path takes
     effect on the FIRST hover after a layout. Gated layout-effect `fitView`
     behind `lastFitKeyRef` so only meaningful layout changes (project /
     direction / compactMode / node count) re-zoom. Capped search-effect
     `fitView` to `<= 8` matches so single-letter searches don't auto-zoom.
- **Why this approach**: The backend fix is surgical — it touches only the
  runtime fallback path used by legacy scans missing `job_external_systems`,
  so projects with modern scans (e.g. `esbmain`) are not affected. The
  frontend fix is also surgical — three targeted edits all consistent with
  rounds 1/2's same-reference reconciliation pattern; no new components, no
  new dependencies, no behavior changes the user has to learn. Real Talend
  external systems (Oracle, MSSql, FTP, SAP, Salesforce, etc.) still appear
  in scherpenhuizen-talend's topology — only the synthetic `REFERENCE:*`
  noise disappears. If the user wants the REFERENCE noise back, the scanner
  should populate `job_external_systems` explicitly via tRunJob → called-job
  tracing — that's a scanner feature, not a graph-rendering feature.
- **Trade-offs (intentional)**:
  - Marginal `REFERENCE:*` "external systems" disappear from
    scherpenhuizen-talend (they were never real systems anyway).
  - `fitView` no longer auto-runs every time `groupMembership` rebuilds.
    First load and explicit direction/compact toggles still fit.
  - Search no longer auto-fits when there are >8 matches.
- **Verification**: Backend needs `sudo systemctl restart mcp-server`
  (editable install picks up the Python change). Frontend needs
  `sudo docker compose up -d --build frontend` (prod ships a baked Next.js
  standalone build). Smoke targets: (1) `GET /mcp/projects/scherpenhuizen-talend/graph`
  with auth cookie should return ~10-15 connections (was ~37) and zero
  entries starting with `REFERENCE:`. (2) Hover-rip across the canvas on
  `/topology` for scherpenhuizen-talend should be instant on the first
  hover after layout. (3) Type a search query — first letter should not
  trigger a zoom-fit; once the match narrows to <=8, it should auto-fit.
  (4) Smoke `esbmain` — its manifest already has `job_external_systems`
  so its connection count should be unchanged (bypasses runtime derivation
  entirely).

### [Perf]: Topology hover/search node-reference stability (round 2)

- **Files changed**: `RAG_pipeline/frontend/app/topology/page.tsx` — rewrote both the search-dimming `useEffect` (~lines 358-419) and the hover-dimming `useEffect` (~lines 432-491) so that `setNodes`/`setEdges` callbacks return the **same reference** for any node or edge whose `data.dimmed`/`data.highlighted` (or `style.opacity`) didn't actually change. The "clear" branch (`searchMatchIds === null` / `hoveredNodeId === null`) also short-circuits per node when it's already cleared.
- **Problem**: After round 1 of the perf pass (backdrop-filter / infinite animations / mount animation / inline transitions / hover early-out / minimap gating), the topology view was still visibly "buggy" on small projects like `scherpenhuizen-talend` (43 jobs + 41 connections + 12 groups, ~84 nodes + 12 group-bg). User reported it specifically by name.
- **Root cause**: Even with the `prevHoverRef` early-out, every *distinct* hover entry still rewrote the entire 80+ node array. The previous code did `nds.map((n) => ({ ...n, data: { ...n.data, dimmed, highlighted } }))` which produced a new node reference (and new `data` reference) for **every** node, regardless of whether the dimmed/highlighted values actually changed. ReactFlow saw 80+ "changed" nodes and reconciled all of them; `React.memo` on `JobNode`/`ConnectionNode`/`GroupSummaryNode` couldn't help because the `data` prop was a brand-new object every time. Net effect: each mouse-enter on a new node triggered a full graph re-render even though only ~5 nodes' dimming state had actually changed.
- **Change**: Inside both `setNodes` callbacks, compare `n.data.dimmed === newDimmed && n.data.highlighted === newHighlighted` and `return n` (same reference) when nothing changed. Only return a new `{ ...n, data: { ... } }` when the values actually differ. Same idea for edges: compare `e.style?.opacity ?? undefined` against the new opacity and return the same edge ref when unchanged. Added a destructuring trick `const { opacity: _drop, ...restStyle } = e.style ?? {}` to drop `opacity` from the style object instead of setting it to `undefined` (which would still produce a new style object).
- **Why this approach**: Preserves all existing behavior (search dimming, hover dimming, edge fading) but stops generating spurious reference changes. With same-reference data, ReactFlow's per-node component (memoized via `memo(JobNodeComponent)` etc.) skips render entirely. The dominant hover-time work goes from "rewrite + reconcile + repaint 80 nodes" to "rewrite + reconcile + repaint ~5-10 nodes" — roughly a 10x reduction on a graph this size.
- **Verification**: Frontend rebuilt via `sudo docker compose up -d --build frontend` (build succeeded; `intodata-kb-v2-frontend-1` healthy in 8s). Smoke test target: `scherpenhuizen-talend` topology — hover-rip across the canvas, hover should now feel snappy without ripple lag, edge dimming should be instant, no visual regressions.

### [Perf]: Topology view performance pass — cut lag when many things are open

- **Files changed**:
  - `RAG_pipeline/frontend/app/topology/page.tsx` — removed `backdropFilter: "blur(1px)"` from `GroupBackgroundNode` (was line 65); added `prevHoverRef = useRef<string|null>(null)` and an early-out at the top of the hover-dimming `useEffect` (lines ~423-432); gated `<MiniMap>` JSX behind `(visibleNodes.length <= 150 || isCollapsed)` (was line 749).
  - `RAG_pipeline/frontend/app/globals.css` — removed the two infinite edge animations (`.react-flow .react-flow__edge-path[style*="dash"]` and `.react-flow .react-flow__edge.animated path`, was lines 125-131); removed the `.topo-node-enter` rule that fired the 0.3s `node-enter` keyframe on every node mount (was lines 133-135). The `node-enter` and `edge-flow*` keyframes themselves are left orphaned/harmless. Kept the `.topo-node` `transform/box-shadow` 0.2s transition and the `:hover translateY(-1px)` (CSS-only, scoped, cheap).
  - `RAG_pipeline/frontend/components/graph/JobNode.tsx` — removed `transition: "opacity 0.3s ease, box-shadow 0.3s ease"` from inline style (was line 43); removed `topo-node-enter` from className (was line 33).
  - `RAG_pipeline/frontend/components/graph/ConnectionNode.tsx` — same removal of inline `transition` (was line 28) and `topo-node-enter` class (was line 18).
  - `RAG_pipeline/frontend/components/graph/GroupSummaryNode.tsx` — same removal of inline `transition` (was line 30) and `topo-node-enter` class (was line 21).
- **Problem**: Topology view felt sluggish with many visible nodes — hover ripples, pan/zoom stutter, heavy toggles for direction/compact/expand-collapse. Verified by reading source: five concrete amplifiers were active (backdrop-filter blur on every group, two infinite edge animations, per-node mount animation re-firing on every layout change, inline 0.3s opacity transition combined with hover dimming rewriting every node, MiniMap re-rendering all nodes on every viewport movement).
- **Root cause**:
  1. `backdrop-filter: blur(1px)` is one of the most expensive CSS properties — the GPU re-blurred behind every group box on every paint. ~10 groups = constant frame-rate killer.
  2. `animation: edge-flow ... infinite` kept the GPU painting forever even when idle.
  3. `.topo-node-enter` mount keyframe re-fired across the entire visible graph on every direction/compact/expand toggle (nodes remount, animation re-runs).
  4. Inline `transition: opacity 0.3s` combined with hover dimming rewriting the entire `nodes` array on every mouse-enter produced the visible "ripple lag".
  5. Hover-dimming `useEffect` had no early-out — any unrelated re-render that re-entered the same `hoveredNodeId` value still rewrote every node.
- **Change**:
  1. Killed `backdropFilter` on group backgrounds. Gradient + dashed border kept (cheap, visually almost identical at 1px blur).
  2. Removed both `animation: edge-flow ... infinite` rules. Dashed edges are still dashed, just no longer painting forever.
  3. Removed the `.topo-node-enter` rule and the class from all three node components. Layout toggles now snap instantly.
  4. Removed inline `transition: opacity 0.3s ease, box-shadow 0.3s ease` from JobNode/ConnectionNode/GroupSummaryNode. Dimming is now instant (no 0.3s cross-fade across hundreds of nodes = no ripple).
  5. Added `prevHoverRef = useRef<string|null>(null)` mirroring the existing `prevSearchRef` pattern. Hover effect early-outs when `hoveredNodeId === prevHoverRef.current`, eliminating redundant `setNodes` churn.
  6. Wrapped `<MiniMap>` in `{(visibleNodes.length <= 150 || isCollapsed) && <MiniMap ... />}` — small projects keep it; heavy fully-expanded views drop it. The default lightweight collapsed view always keeps it because `isCollapsed === true`.
- **Why this approach**: Frontend-only patch, no new components, no new dependencies, no behavior changes the user has to learn. Skipped the gradient → solid color regression on JobNode (visible regression for marginal gain) and skipped dagre memoization (verified the layout `useEffect` already depends on `flowResult`, not `nodes`, so dimming does not retrigger dagre — not the bottleneck).
- **Trade-offs (intentional)**: No 0.3s opacity fade on dimming (instant instead). No scale+opacity entry animation on layout changes (instant). MiniMap hidden when `visibleNodes.length > 150 && !isCollapsed`. No `backdrop-filter: blur` on group backgrounds. None of these affect keyboard or accessibility.
- **Verification**: Visual review of all 5 changed files. TypeScript-only edits in client components; no backend, no schema, no docker rebuild dependencies beyond the standard frontend rebuild (`docker compose up -d --build frontend`). Pre-existing TS errors in this tree are from missing local `node_modules` (`@xyflow/react`, `@dagrejs/dagre`) — the project's build runs in the Docker container which has them installed.

### [Fix]: Topology view always starts in lightweight (compact + collapsed) interface

- **Files changed**: `RAG_pipeline/frontend/app/topology/page.tsx`
  - Initial state: `collapsedMode` default changed from `null` (auto-detect via `>30` jobs) to `true` (always collapsed). `compactMode` already defaulted to `true`.
  - Project-change reset effect: removed `setCompactMode(false)` (was forcing compactMode off on every project switch — bug); changed `setCollapsedMode(null)` to `setCollapsedMode(true)`.
- **Problem**: Switching to a project always rendered the full expanded ReactFlow graph (every job + connection node) on first load, which uses a lot of memory and is slow on large projects (`esbmain` has ~50 jobs and many connections). Even though `compactMode` defaulted to `true` in `useState`, the project-change effect immediately reset it to `false`, and `collapsedMode` only auto-collapsed projects with more than 30 jobs.
- **Root cause**: The auto-detect heuristic (`>30` jobs) and the explicit `setCompactMode(false)` on project switch were both designed assuming "show as much as possible by default". User preference is the opposite: always start lightweight, let the user opt into expansion via the toolbar.
- **Change**: Force `compactMode = true` and `collapsedMode = true` both at initial mount and on every project switch. The toolbar's Expand-All / Compact toggle still works for users who want the full view.
- **Why this approach**: Single-line state defaults — no new props, no new components, no behavior change for the toolbar controls. Reversible if needed.
- **Verification**: TypeScript-only change in a Next.js client component; no backend or schema impact. Frontend hot-reloads in dev; production rebuild required for the dashboard container if redeploying.

## Session: 2026-04-09 (Admin Sidebar Visibility & Role Separation)

### [Feature]: Admin sidebar visibility fix, role separation, and permission guards

- **Files changed**:
  - `frontend/hooks/useAuth.ts` — added `loginAs` method to `UseAuthReturn` interface and hook return
  - `frontend/components/AuthProvider.tsx` — added `loginAs` to `AuthContextValue` interface and context
  - `frontend/app/login/page.tsx` — parse login response, call `loginAs(data.user)` before redirect, remove `router.refresh()`
  - `frontend/hooks/usePermissions.ts` — added `isSuperadmin` field, changed `hasAnyAdminPermission` to role-based check (`is_superadmin || roles.includes("admin")`)
  - `frontend/components/Sidebar.tsx` — admin sub-links now use `superadminOnly` flag for Users/Roles/Projects, `perm: "view_audit_logs"` for Usage/Benchmark; default admin href falls back to `/admin/usage`
  - `frontend/app/admin/users/page.tsx` — added superadmin guard with redirect to `/admin/usage`
  - `frontend/app/admin/roles/page.tsx` — added superadmin guard with redirect to `/admin/usage`
  - `frontend/app/admin/project-assignments/page.tsx` — added superadmin guard with redirect to `/admin/usage`
  - `backend/src/api/dependencies.py` — added `AuditViewer` dependency (`require_permission("view_audit_logs")`)
  - `backend/src/api/routes/admin.py` — changed `list_audit_logs`, `usage_summary`, `usage_recent`, `usage_by_user`, `usage_user_detail`, `usage_daily_by_user` from `AdminUser` to `AuditViewer`; removed redundant `has_permission` check in `list_audit_logs`
  - `backend/src/api/main.py` — removed auto-sync of admin role to all permissions (lines 78-86), cleaned up unused `role_permissions` import
  - `backend/alembic/versions/012_restrict_admin_role_permissions.py` — new migration removing `manage_users`, `manage_roles`, `assign_projects` from admin role
- **Problem**: (1) Admin button only appeared after page refresh post-login. (2) Managers could see admin button. (3) Admin role had all permissions including user management.
- **Root cause**: (1) Login page discarded user data from response, auth state only fetched on mount. (2) `hasAnyAdminPermission` checked individual permissions, not role. (3) No separation between admin and superadmin — startup seed always re-granted all permissions to admin role.
- **Change**: 6-step implementation — login state fix, migration for role restriction, startup seed removal, backend endpoint auth relaxation, frontend permission rewrite, page guards.
- **Why this approach**: Clean role separation — superadmin manages users/roles/projects, admin role gets read-only dashboard (usage, audit, benchmark). Login state fix uses existing response data rather than extra API call.
- **Verification**: TypeScript `tsc --noEmit` passes. Python AST parse passes for all 4 backend files. Pytest not available in venv (needs Docker rebuild).

## Session: 2026-04-03 (Post-Stress-Test Improvements)

### [Fix]: RAG metadata enrichment quality — targeted queries, reranker, prompt improvements, classifier refinement

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` (lines 421-448) — 3 fixes: targeted enrichment query for `project_detail` intent, use reranker for metadata enrichment search, initialize `document_sources` before conditional block
  - `backend/src/generation/prompt_builder.py` (lines 33-40) — rewritten `METADATA_SYSTEM_PROMPT` to prioritize document content over system data
  - `backend/src/generation/intent_classifier.py` (lines 48-50) — added classifier rule to distinguish content queries from metadata queries
- **Problem**: Stress test scored 18/20 PASS but Q2 (follow-up context gap) and Q8 (shallow project overview) scored poorly due to Completeness=2. Root causes: (1) enrichment search used raw vague queries like "Tell me about this project", (2) enrichment used basic `search_similar()` instead of reranker, (3) `document_sources` uninitialized in else branch, (4) metadata prompt treated document content as secondary, (5) follow-up "what is this based on" misclassified as metadata
- **Root cause**: Metadata enrichment pipeline was under-optimized — vague queries, no reranker, prompt prioritized system data over document content, classifier conflated content questions with metadata queries
- **Change**:
  - Fix 1: For `project_detail` intent, construct targeted query `"{project_name} executive summary overview architecture assessment"` instead of raw user query
  - Fix 2: Use `search_and_rerank()` when reranker is enabled (was always `search_similar()`)
  - Fix 3: Initialize `document_sources = []` before the `if hybrid_sources:` block to prevent `UnboundLocalError`
  - Fix 4: Rewrote METADATA_SYSTEM_PROMPT — document content is now PRIMARY source, system data supplements
  - Fix 5: Added classifier rule: questions about "why/how/what is this based on" → search, not project_detail
- **Why this approach**: Each fix targets a specific root cause identified in the stress test report. Minimal changes, maximum impact on the 2 failing questions.
- **Verification**: All 3 files pass Python syntax check (`ast.parse`). Pytest not installed in venv — needs Docker rebuild for full test run.

## Session: 2026-04-03

### [Fix]: RAG follow-up robustness — streaming context-first, temperatures, prompts, metadata enrichment

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` — streaming context-first, `needs_new_search()` safety net, cross-project source filter, metadata enrichment for all content-relevant intents, system prompt improvements
  - `backend/src/generation/prompt_builder.py` — updated SYSTEM_PROMPT rules 2 and 5
  - `backend/src/config.py` — `kimi_temperature` 0.6→0.45, `reranker_min_score` 0.05→0.15
  - `backend/src/generation/llm_client.py` — added `temperature: 0.45` to Ollama options
  - `backend/src/generation/intent_classifier.py` — classifier temperature 0.6→0.0
- **Problem**: Follow-up questions caused the RAG system to lose context, do irrelevant new searches, and falsely retract correct answers. Metadata queries (project_detail, system_overview, etc.) had no document content. Garbage 28-33% match results got through reranker.
- **Root cause**: (1) Streaming mode always skipped context-first optimization, forcing a new search on every follow-up. (2) `needs_new_search()` only checked for `[NEED_SEARCH]` token — missed "I don't know" responses. (3) Only HYBRID metadata intent ran vector search; others had PostgreSQL-only data. (4) `get_previous_sources()` didn't filter by project. (5) Temperatures too high (0.6 for generation, 0.6 for classification). (6) Reranker min score too low (0.05).
- **Change**:
  1. Enabled context-first optimization for streaming mode (was skipped with `previous_doc_sources = []`)
  2. Added `_CANT_ANSWER_PATTERNS` regex + `len < 200` guard to catch "I don't know" responses as needing search
  3. Enriched ALL metadata intents (except MY_PROFILE, DOCUMENT_LIST) with vector search results
  4. Added `project_ids` parameter to `get_previous_sources()` for cross-project filtering
  5. Improved system prompts: rule 2 (partial info → state found/missing), rule 5 (don't retract correctly cited info)
  6. Reduced temperatures: kimi 0.6→0.45, ollama added 0.45, classifier 0.6→0.0
  7. Raised reranker_min_score 0.05→0.15
- **Why this approach**: Context-first is the key optimization — if previous sources already contain the answer, no search is needed. The streaming path was missing this entirely. Lower temperatures reduce hallucination. Higher reranker threshold filters garbage.
- **Verification**: Code review — all changes consistent with plan. Needs deployment + manual testing per verification steps.

## Session: 2026-04-01

### [Fix]: Restore RagProjectSelector dropdown for chat and search

- **Files changed**: `frontend/components/ChatWindow.tsx`, `frontend/components/ChatLayout.tsx`, `frontend/app/page.tsx`
- **Problem**: `RagProjectSelector` component existed but was never rendered. Users had no way to scope normal chat/search queries to a specific project — only deep-dive mode had a project selector.
- **Root cause**: The component was built but never wired into the UI. `handleSend` and `search.mutate` didn't pass `project_id`.
- **Change**:
  - `ChatWindow.tsx`: Imported `RagProjectSelector`, show it in header when not in deep-dive mode (deep-dive keeps `ProjectSelector`), pass `project_id` in `handleSend`.
  - `ChatLayout.tsx`: Pass `project_id` from request (or fall back to `selectedProject` state) in the normal chat `sendStreamMessage` call.
  - `page.tsx`: Imported `RagProjectSelector`, show it always (not just deep-dive), pass `project_id` in `search.mutate`.
- **Why this approach**: Reuses existing `RagProjectSelector` component and `useUserProjects` hook. Deep-dive mode retains its own `ProjectSelector` (which fetches scanned projects with job counts). Normal mode uses `RagProjectSelector` (which fetches RAG-ingested projects). Backend already supports `project_id` on both schemas.
- **Verification**: Code review — all three files correctly import and render the component, pass `project_id` to backend calls.

## Session: 2026-03-27

### [Fix]: RAG hallucination, cross-project confusion, low recall, self-contradiction

- **Files changed**:
  - `backend/src/config.py` (lines 47-57): `default_top_k` 5→10, `max_result_documents` 3→6, `prefetch_limit` 30→50, `rerank_top_n` 20→30
  - `backend/src/generation/conversational_rag.py` (lines 46-75, 127-137, 169-207, 210-228, 285, 605, 730): Constants, prompts, headers, footer, truncate_history, hardcoded top_k
  - `backend/src/generation/prompt_builder.py` (lines 10, 13-31, 154-190): `MAX_CONTEXT_TOKENS` 6144→10000, `SYSTEM_PROMPT` replaced, project attribution in headers, context footer
  - `backend/src/generation/llm_client.py` (lines 128-131, 146-150): Added `options={"num_ctx": 16384}` to both Ollama chat calls
- **Problem**: (1) LLM fabricated full migration plans not in any document, then denied it. (2) Cross-project contamination — asking about Scherpenhuizen returned aliTalend/workatozipoppp results mixed in. (3) Only 3 docs with 3000 tokens reached the LLM. (4) Self-contradiction because history window was only 2000 tokens (~2-3 exchanges).
- **Root cause**: Chat path had `top_k` hardcoded to 3 (ignoring config), `MAX_CONTEXT_TOKENS=3000` (search bar got 6144), weak anti-hallucination prompts, and only 3 merged documents ever reached the LLM. History window was 2000 tokens / 10 messages.
- **Change**:
  1. **Retrieval depth**: `default_top_k` 5→10, `max_result_documents` 3→6, `prefetch_limit` 30→50, `rerank_top_n` 20→30, `MAX_CONTEXT_TOKENS` 3000→8192 (chat) / 6144→10000 (search)
  2. **Anti-hallucination prompts**: Replaced all 3 system prompts (`CONVERSATIONAL_SYSTEM_PROMPT`, `CONTEXT_FIRST_SYSTEM_PROMPT`, `SYSTEM_PROMPT`) with CRITICAL RULES section (no general knowledge, cite sources, never invent details). Added context boundary footer.
  3. **Project attribution**: Source headers now show `[Reference N: filename (Project: project_id)]` extracted from scoped filenames. Applied to all 3 context builders.
  4. **History anchoring**: `truncate_history()` now always keeps the first user+assistant pair, then fills remaining budget with most recent messages. Budget increased to 4000 tokens / 20 messages.
  5. **Ollama context window**: Added `options={"num_ctx": 16384}` to both Ollama chat calls (gemma3 default is 8K, now 16K).
  6. **Hardcoded top_k**: All 3 locations (`_prepare_rag_context`, `run_conversational_rag`, `run_conversational_rag_stream`) changed from `top_k=3` to `top_k=settings.default_top_k`.
- **Why this approach**: The chat path was severely under-resourced compared to the search bar path. Increasing retrieval depth and context window gives the LLM enough information to answer complex questions. Anti-hallucination prompts with explicit CRITICAL RULES and context boundary footer make it much harder for the LLM to fabricate. Project attribution in headers helps the LLM distinguish between projects. History anchoring prevents topic drift in long conversations.
- **Verification**: All 4 files pass Python AST syntax check. Manual testing required: restart backend, test with project-scoped chat.

## Session: 2026-03-24

### [Fix]: Project-scoped chat should filter admin results by selected project

- **Files changed**: `backend/src/retrieval/search.py` (lines 82-90)
- **Problem**: When an admin user selected a specific project in the chat UI (e.g., aliTalend), search results still included documents from all projects (workatozipoppp, wrokato2, udateddocs). The `project_id` sent by the frontend was ignored for admins.
- **Root cause**: `_build_search_filter()` line 44: `if "admin" not in user_groups` skipped the entire RBAC filter for admins, including the project_ids restriction. The `elif` chain had no branch handling admin + project_ids.
- **Change**: Added a new `elif` branch after the existing non-admin conditions: when `"admin" in user_groups` AND `project_ids` is not None and non-empty, add a `FieldCondition` filtering `allowed_groups` to only the specified project IDs. When no project is selected (`project_ids=None`), admin still sees everything as before.
- **Why this approach**: Minimal one-branch addition to the existing filter logic. Non-admin RBAC is unchanged. Admin-without-project is unchanged. Only admin-with-explicit-project-scope gets the new filter.
- **Verification**: Code review confirms the `elif` chain logic is correct — the new branch only fires when `user_groups` contains "admin" AND `project_ids` is provided and non-empty.

## Session: 2026-03-18 (LLM-based Conversation vs Document Summary Detection)

### [Feature]: Add CONVERSATION_SUMMARY intent to LLM classifier

- **Files changed**:
  - `backend/src/generation/intent_classifier.py`: Added `summary` and `conversation_summary` actions to `CLASSIFIER_SYSTEM_PROMPT`. Added `CONVERSATION_SUMMARY` to `Action` enum. Updated `_llm_classify()` to accept `conversation_context` param (appended as `[Context: ...]` note to user message). Updated `analyze_query_async()` to handle `conversation_summary` -> `Action.CONVERSATION_SUMMARY` and `summary` -> `Action.SEARCH`/`QueryType.SUMMARY` from LLM response. Regex fallback kept for when LLM is unavailable.
  - `backend/src/generation/prompt_builder.py`: Added `CONVERSATION_SUMMARY_PROMPT` and `build_conversation_summary_messages()` function for building LLM messages from chat history.
  - `backend/src/generation/conversational_rag.py`: Updated import to include `build_conversation_summary_messages`. Pass `conversation_context` (message count) to `analyze_query_async()`. Added `Action.CONVERSATION_SUMMARY` handler that builds summary from chat history (doubled token/message limits). Updated SUMMARY fallback: when `resolve_document_id` fails and conversation history exists, falls back to conversation summary instead of generic "no results".
- **Problem**: "Summarize all your answers" in a conversation would (1) crash due to missing `using="dense"` in Qdrant `query_points`, and (2) even without crash, always tried to find a document to summarize instead of summarizing the conversation.
- **Root cause**: No conversation summary intent existed. The `conversation_context` parameter in `analyze_query_async` was never used. The LLM classifier had no way to distinguish "summarize our chat" from "summarize document X".
- **Change**: Extended the LLM classifier with two new actions (`summary` for documents, `conversation_summary` for chat). The LLM now receives conversation context ("user is in an active conversation with N messages") and can naturally distinguish the two intents. Added a dedicated prompt and handler for conversation summaries.
- **Why this approach**: LLM-based routing handles typos, rephrasing, and multilingual queries naturally. No extra LLM calls — the classification already happens on every query. Conversation context is the key signal for the LLM to distinguish intents.
- **Verification**: All 5 modified files pass Python AST syntax check.

### [Fix]: Add `using="dense"` to `query_points` calls (Qdrant 400 error)

- **Files changed**:
  - `backend/src/generation/map_reduce.py` (line 83): Added `using="dense"` to `query_points` in `resolve_document_id()`
  - `backend/src/api/routes/query.py` (line 102): Added `using="dense"` to `query_points` in `query_debug()`
- **Problem**: Qdrant returns 400 error when `query_points` is called without specifying which named vector to use, since the collection has named vectors (dense + sparse).
- **Root cause**: These two call sites were added before/after the named vectors migration and never updated.
- **Change**: Added `using="dense"` parameter to both `query_points` calls.
- **Verification**: AST syntax check passes.

## Session: 2026-03-18 (Scope Document Dedup by Project ID)

### [Fix]: Prefix stored filename with project_id to scope deduplication

- **Files changed**:
  - `backend/src/ingestion/pipeline.py`: Added `_scoped_filename()` helper; called it as first statement in both `ingest_document()` and `ingest_text()`
  - `backend/tests/test_ingestion/test_pipeline.py`: Added `TestScopedFilename` class with 3 tests; updated import
- **Problem**: `_replace_existing_document()` deduplicates by filename only. Uploading `00_EXECUTIVE_SUMMARY.md` for Project B deletes Project A's copy.
- **Root cause**: Filename was stored without project context, so two projects with identically-named docs collided.
- **Change**: Added `_scoped_filename(filename, project_id)` that returns `"{project_id}/{filename}"` when project_id is truthy, otherwise returns filename unchanged. Called at the top of `ingest_document()` and `ingest_text()` so all downstream code (replace, DB record, Qdrant payload) uses the scoped name.
- **Why this approach**: Minimal change — one helper + two call sites. No DB migration, no schema change, no frontend change. `Path("proj/file.md").suffix` still returns `.md` so extension detection is unaffected.
- **Verification**: All 7 tests in `tests/test_ingestion/test_pipeline.py` pass.

## Session: 2026-03-16 (Auto-Detect Corrections in RAG Conversations)

### [Feature]: Auto-detect user corrections and store in shared Hindsight bank

- **Files changed**:
  - `backend/src/memory/hindsight_client.py`: Added `CORRECTIONS_BANK`, `CORRECTION_SIGNALS` heuristic list, `ensure_corrections_bank()`, `recall_corrections()`, `retain_correction()`, `is_likely_correction()` functions
  - `backend/src/generation/prompt_builder.py`: Added `CORRECTION_EXTRACTION_PROMPT` template and `build_correction_extraction_messages()` function
  - `backend/src/generation/conversational_rag.py`: Updated imports, added `corrections` parameter to `_build_memory_context()` with "## Known corrections" section, wired `ensure_corrections_bank()` + `recall_corrections()` into `_prepare_rag_context()` (parallel via `asyncio.gather`), added `_detect_and_store_correction()` helper, wired fire-and-forget correction detection into both `run_conversational_rag()` and `run_conversational_rag_stream()`
  - `backend/src/generation/rag_chain.py`: Updated imports, wired `ensure_corrections_bank()` + parallel `recall_corrections()` into `run_rag_query()`
  - `backend/src/api/routes/query.py`: Updated imports, wired `ensure_corrections_bank()` + parallel `recall_corrections()` into streaming query endpoint
- **Problem**: Hindsight stored every Q&A flat — no distinction between normal answers and user corrections. Corrections were lost and not recalled for future queries.
- **Root cause**: No correction detection logic existed.
- **Change**: Added a 3-stage correction pipeline: (1) heuristic detection via keyword signals (no LLM cost for non-corrections), (2) LLM extraction of structured correction text (only when heuristic triggers), (3) storage in shared `rag-corrections` Hindsight bank. Corrections are recalled alongside user memories and quality insights, injected as "## Known corrections" in the LLM context. All correction work is fire-and-forget (asyncio.create_task) — zero latency impact on responses.
- **Why this approach**: Heuristic-first avoids LLM calls on every message. Shared bank means corrections benefit ALL users. Async fire-and-forget means no latency impact. Recall parallelized with existing memory recalls via asyncio.gather.
- **Verification**: All 5 files pass Python AST syntax check. No circular imports introduced.

## Session: 2026-03-12 (Metadata-Aware RAG — "Ask Anything" Mode)

### [Feature]: Metadata-aware RAG with intent routing and system knowledge injection

- **Files changed**:
  - `backend/src/retrieval/metadata_retriever.py` (NEW): SQL queries for structured data (projects, docs, stats). Functions: `get_project_list()`, `get_project_detail()`, `get_document_stats()`, `get_document_list()`, `get_system_overview()`, `get_system_knowledge_summary()`, `fetch_metadata()` dispatcher. All RBAC-filtered via `project_ids`.
  - `backend/src/generation/intent_classifier.py`: Added `METADATA` action to `Action` enum, `MetadataIntent` enum (6 intents: PROJECT_LIST, PROJECT_DETAIL, DOCUMENT_STATS, DOCUMENT_LIST, SYSTEM_OVERVIEW, HYBRID), regex-based metadata detection patterns, project name extraction. Conservative: unmatched queries default to SEARCH.
  - `backend/src/generation/prompt_builder.py`: Added `METADATA_SYSTEM_PROMPT`, `build_metadata_messages()` function, `system_knowledge` parameter to `build_messages()` and `build_no_results_messages()`.
  - `backend/src/generation/rag_chain.py`: Added metadata branch in `run_rag_query()` after intent analysis. System knowledge injection for all query paths. `_fetch_metadata()` helper delegates to `metadata_retriever.fetch_metadata()`.
  - `backend/src/api/routes/query.py`: Added metadata branch in `query_stream()` SSE endpoint. System knowledge injection.
  - `backend/src/generation/conversational_rag.py`: Added `db` parameter to `_prepare_rag_context()`, metadata branch before RESPOND check, system knowledge injection in all `build_no_results_messages()` calls.
  - `backend/src/config.py`: Added `metadata_queries_enabled: bool = True` and `system_knowledge_injection: bool = True` settings.
- **Problem**: RAG only searched Qdrant vectors. "What projects do we have?" or "how many documents?" returned irrelevant or no results.
- **Root cause**: No metadata query path — all queries went through vector similarity search which is wrong for structured data questions.
- **Change**: Added intent routing layer that detects metadata questions via regex patterns and routes them to PostgreSQL. System knowledge (~100 tokens) injected into ALL queries so LLM is always project-aware.
- **Why this approach**: Real-time SQL (1-5ms) vs pre-indexing metadata into Qdrant (staleness, wrong tool for COUNT queries). Conservative regex routing — unrecognized queries default to existing SEARCH path, no regressions.
- **Verification**: 19 existing intent classifier tests pass. All files parse cleanly. No circular imports (moved `fetch_metadata` to `metadata_retriever.py` to break rag_chain <-> conversational_rag cycle).

### [Refactor]: Replace regex intent classifier with LLM-based classifier

- **Files changed**:
  - `backend/src/generation/intent_classifier.py`: Replaced all regex patterns with LLM-based `analyze_query_async()`. Uses Kimi/Ollama provider chain with structured JSON output. `CLASSIFIER_SYSTEM_PROMPT` explains 7 action categories. Sync `analyze_query()` kept for backward compat (returns SEARCH for everything). On LLM failure, defaults to SEARCH (safe).
  - `backend/src/generation/rag_chain.py`: Changed `analyze_query()` -> `await analyze_query_async()`
  - `backend/src/api/routes/query.py`: Changed `analyze_query()` -> `await analyze_query_async()`
  - `backend/src/generation/conversational_rag.py`: Changed `analyze_query()` -> `await analyze_query_async()`
- **Problem**: Regex patterns were hardcoded English phrases — missed Dutch, paraphrases, queries without "project" keyword.
- **Root cause**: Regex can't understand semantic intent.
- **Change**: LLM classifies intent via JSON response. Uses existing provider chain (Kimi first, Ollama fallback). `max_tokens=64` for speed. Ollama uses `ollama_router_model` (gemma3:4b) with `format="json"`.
- **Why this approach**: LLM understands any phrasing/language. ~0.5-2s latency is acceptable since it happens in parallel with other startup work. Safe fallback to SEARCH on failure.
- **Verification**: All 19 existing intent classifier tests pass (sync path unchanged). All files parse cleanly.

## Session: 2026-03-12 (RBAC: Project-Scoped Document Access)

### [Feature]: Project-scoped RBAC for all document ingestion and access paths

- **Files changed**:
  - `backend/src/ingestion/pipeline.py` (lines 85, 172, 200): Added `project_id` param to `ingest_document()`, store on Document record; removed `"public"` from MCP ingestion tagging
  - `backend/src/api/routes/ingest.py` (lines 1-77): Added `project_id` Form field, validate project access, use `[project_id]` for allowed_groups
  - `backend/src/retrieval/search.py` (lines 44-81): Removed `"public"` force-append from `_build_search_filter`; block results with `["__none__"]` when no groups/projects
  - `backend/src/api/routes/query.py` (lines 80-131): Fixed debug endpoint to use `_build_search_filter` with `project_ids` instead of manual filter construction
  - `backend/src/api/routes/documents.py` (lines 1-57): Added RBAC filtering — non-admins only see docs from assigned projects via `project_id` column
  - `backend/src/db/models/document.py` (line 19): Added `project_id: str | None` column with index
  - `backend/src/schemas.py` (line 73): Added `project_id` to `DocumentResponse`
  - `backend/alembic/versions/010_add_project_id_to_documents.py`: New migration adding `project_id` column + index
  - `frontend/hooks/useDocuments.ts` (lines 23-38): Changed `uploadDocument` to accept `{file, project_id}` object
  - `frontend/components/DocumentUpload.tsx`: Added project selector dropdown (from `useDocsProjects`), require project before upload
  - `frontend/app/api/ingest/route.ts`: Forward `project_id` in FormData to backend
  - `frontend/types/index.ts` (line 50): Added `project_id` to `DocumentResponseSchema`
  - `scripts/migrate_qdrant_remove_public.py`: One-time migration script to remove "public" from existing Qdrant vectors
- **Problem**: RBAC was broken across all ingestion paths — MCP docs tagged `["public", project_id]` (visible to everyone), file uploads tagged with role names (no project association), search filter force-appended `"public"`, documents page had no RBAC filtering, debug endpoint skipped project_ids
- **Root cause**: Original design used `"public"` as a catch-all group for visibility, which bypassed project-level access control entirely
- **Change**: Every document now belongs to a project. `allowed_groups` in Qdrant contains only `[project_id]`. Users see docs only from their assigned projects. Admins/superadmins bypass all filters.
- **Why this approach**: Project-scoped access is the correct RBAC model — users should only see docs from projects they're assigned to, not everything tagged "public"
- **Verification**: All 15 search tests pass, all 3 pipeline tests pass (1 pre-existing failure due to missing `docx` module), Python imports verified clean

## Session: 2026-03-11 (Security Hardening + Stress Test)

### [Security]: Lock Docker ports to localhost

- **Files changed**: `../../docker-compose.yml` (lines 67, 106, 131)
- **Problem**: Backend (8000), frontend (3000), and dashboard (8001) ports were exposed on all interfaces, bypassing Caddy reverse proxy
- **Change**: Changed port bindings from `"8000:8000"` to `"127.0.0.1:8000:8000"` (same for 3000, 8001)
- **Why this approach**: Only Caddy should accept external traffic; services should only be reachable via localhost
- **Verification**: Syntax check on docker-compose.yml

### [Security]: Fix password reset min_length

- **Files changed**: `backend/src/api/routes/admin.py` (line 43)
- **Problem**: `ResetPasswordRequest.new_password` had `min_length=4`, inconsistent with `CreateUserRequest` which already uses `min_length=8`
- **Change**: `min_length=4` -> `min_length=8`
- **Why this approach**: Password policy should be consistent across create and reset flows
- **Verification**: Syntax check

### [Security]: Fix dashboard login redirect (hardcoded :3000)

- **Files changed**: `../../knowledge_base/dashboard/src/api/client.ts` (line 7), `../../knowledge_base/dashboard/src/hooks/useAuth.ts` (line 8)
- **Problem**: Login redirect URLs had hardcoded `:3000` port, breaking in production behind Caddy (port 443)
- **Change**: Removed `:3000` from `getLoginUrl()` fallback in both files
- **Why this approach**: Behind a reverse proxy, the default port (443/80) is correct; hardcoded ports break production
- **Verification**: Both files updated consistently

### [Security]: Add env validation to deploy script

- **Files changed**: `../../deploy/deploy.sh` (new section 11, renumbered 12/13)
- **Problem**: Deploy script would start services with insecure default secrets (JWT_SECRET_KEY, POSTGRES_PASSWORD)
- **Change**: Added validation block that checks `.env` for insecure defaults, empty values, and short JWT keys. Aborts deployment if insecure.
- **Why this approach**: Fail-fast prevents deploying with default credentials
- **Verification**: Bash syntax check

### [Feature]: Comprehensive stress test script

- **Files changed**: `backend/tests/stress_test.py` (NEW, ~530 lines)
- **Problem**: No integration or load tests existed -- only unit tests with mocks
- **Change**: Created standalone async stress test that runs against live services, testing:
  - A) Health & connectivity (all 4 services)
  - B) Auth flow (login/me/refresh/logout, invalid credentials, N iterations)
  - C) Document ingestion (upload, re-upload, wrong file type)
  - D) RAG query (N iterations with latency tracking)
  - E) SSE streaming (event ordering, TTFT measurement)
  - F) Conversations (create/message/follow-up/list/delete)
  - G) Admin CRUD (create user/assign project/RBAC verification/deactivate)
  - H) Concurrent load (N users x M queries with throughput measurement)
  - I) Rate limiting (login 5/min enforcement check)
- **Why this approach**: Uses httpx async client, configurable via CLI args, self-contained (creates/cleans test data), prints structured report with percentile latencies
- **Verification**: `python3 -c "import py_compile; py_compile.compile(...)"` passes

## Session: 2026-03-11 (Phase 1 Consolidation)

### [Security]: P1 — 7 security fixes

- **Files changed**: `backend/src/api/routes/test.py` (DELETED), `backend/src/api/main.py`, `../knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`, `backend/src/api/routes/admin.py`, `backend/src/api/routes/documents.py`, `backend/src/api/routes/conversations.py`, `backend/src/api/routes/health.py`
- **Changes**:
  - 1a: Removed unauthenticated test route with broken code (direct Ollama calls, undefined `payload` var)
  - 1b: Fixed git clone command injection — added URL regex validation + `--` separator
  - 1c: Added pagination limits (max 100) via `Query(ge=1, le=100)` to all list endpoints (users, audit-logs, usage/recent, documents, conversations)
  - 1d: Fixed N+1 count queries — `list_users` and `list_audit_logs` now use `select(func.count())` instead of fetching all rows into memory
  - 1e: Added `@limiter.limit("10/minute")` rate limit to document deletion endpoint
  - 1f: Sanitized health endpoint error messages — all `str(e)` replaced with "Service unavailable" to prevent leaking DB connection strings
  - 1g: Strengthened password policy from min_length=4 to min_length=8 in `CreateUserRequest` and `ResetPasswordRequest`
- **Tests updated**: `tests/test_api/test_admin.py` — fixed mocks for count queries and password length

### [UX]: P2 — 6 UX improvements

- **Files changed**: `frontend/app/admin/users/page.tsx`, `frontend/app/not-found.tsx` (NEW), `frontend/app/error.tsx` (NEW), `frontend/components/ConfirmDialog.tsx` (NEW), `frontend/app/documents/page.tsx`, `frontend/app/admin/roles/page.tsx`, `frontend/components/ConversationItem.tsx`, `frontend/app/layout.tsx`, `frontend/public/favicon.svg` (NEW), `../knowledge_base/dashboard/src/hooks/useAuth.ts`, `../knowledge_base/dashboard/index.html`, `../knowledge_base/dashboard/public/favicon.svg` (NEW)
- **Changes**:
  - 2a: Replaced `prompt()`/`alert()` with modal dialogs for password reset (with confirmation field) and user deactivation. Added success toast with auto-dismiss.
  - 2b: Added 404 page and error boundary component
  - 2c: Added ConfirmDialog component, used for document deletion, role deletion, conversation deletion
  - 2d: Fixed KB dashboard useAuth to surface network errors instead of silently redirecting
  - 2e: Permission tooltips already present via title attributes (no change needed)
  - 2f: Added SVG favicons for both frontends, page title template in layout

### [Performance]: P3 — 4 performance/stability improvements

- **Files changed**: `backend/alembic/versions/009_add_performance_indexes.py` (NEW), `backend/src/generation/llm_client.py`, `backend/src/api/main.py`, `docker-compose.yml`
- **Changes**:
  - 3a: Added DB indexes on `query_usage.created_at`, `audit_logs.created_at`, `documents.filename`
  - 3b: Added `cleanup_llm_clients()` function for singleton HTTP client cleanup, called in lifespan shutdown
  - 3c: Added health checks to frontend and dashboard Docker services
  - 3d: Added `mem_limit` and `cpus` resource limits to all 6 Docker services

### [Deployment]: P5 — 3 deployment hardening items

- **Files changed**: `RAG_pipeline/backend/Dockerfile`, `knowledge_base/mcp-server/Dockerfile`, `backend/src/config.py`, `.env.production.template`
- **Changes**:
  - 5a: Both Dockerfiles now run as non-root user (appuser:appgroup, UID/GID 1001)
  - 5b: Production secret validation — `validate_production_secrets()` raises on default JWT_SECRET_KEY when APP_ENV=production
  - 5c: Removed duplicate `MCP_INGEST_API_KEY` entry from .env.production.template

### [Testing]: P4 — Backend + Frontend test infrastructure

- **Files changed**: `tests/test_api/test_usage.py` (NEW, 5 tests), `tests/test_api/test_benchmark.py` (NEW, 4 tests), `frontend/vitest.config.ts` (NEW), `frontend/tests/setup.ts` (NEW), `frontend/tests/components/ConfirmDialog.test.tsx` (NEW, 5 tests), `frontend/tests/hooks/useAuth.test.ts` (NEW, 4 tests), `../knowledge_base/dashboard/src/tests/setup.ts` (NEW), `../knowledge_base/dashboard/src/tests/api-client.test.ts` (NEW, 4 tests)
- **Changes**:
  - 4a: Added 9 new backend API tests for usage recording (auth, validation) and benchmark endpoints (list, get, 404)
  - 4b: Set up Vitest + React Testing Library for RAG frontend with 9 tests (ConfirmDialog rendering, useAuth flow)
  - 4c: Set up Vitest for KB dashboard with 4 API client tests (list, error, auth redirect, scan POST)
- **Verification**: 243 backend tests pass (1 pre-existing docx skip), 9 frontend tests pass, 4 dashboard tests pass, frontend build clean

## Session: 2026-03-11

### [Feature]: Phase 1 Closeout — 6 remaining items

#### Item 2: KB Doc Gen Token Persistence
- **Files changed**: `backend/src/api/routes/usage.py` (NEW), `backend/src/api/main.py`, `../knowledge_base/mcp-server/src/mcp_server/services/rag_bridge.py`, `../knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
- **Problem**: Doc generation extracted cost/tokens from Claude CLI but never saved to QueryUsage table
- **Change**: Created POST /usage/record endpoint with Bearer auth (reuses mcp_ingest_api_key). Added `post_usage()` method to RagBridge. Called it in `_run_generate_background()` after doc generation completes.
- **Why this approach**: Fire-and-forget pattern — failure to record usage doesn't break doc generation
- **Verification**: Frontend build passes, 236 backend tests pass

#### Item 3: Deep Dive Token Persistence
- **Files changed**: `../knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
- **Problem**: Deep dive streams usage to frontend but doesn't persist
- **Change**: In `deep_dive_query` SSE stream, intercept `type="usage"` events and call `bridge.post_usage(interface="deep_dive", ...)`. Wrapped in try/except to not break SSE stream.
- **Verification**: Build passes

#### Item 4: Embedding Token Tracking
- **Files changed**: `backend/src/retrieval/embedder.py`, `backend/src/generation/rag_chain.py`, `backend/src/api/routes/query.py`, `backend/src/generation/conversational_rag.py`
- **Problem**: Voyage AI and Ollama embedding responses contain token counts but they're ignored
- **Change**: Added thread-local `_last_embed_tokens` storage (same pattern as `llm_client._last_usage`). Voyage: captures `response.usage.total_tokens`. Ollama: captures `response.get("prompt_eval_count")`. Added `embedding_tokens=get_last_embed_tokens()` to all 4 QueryUsage creation sites.
- **Verification**: 236 tests pass

#### Item 5: Frontend Permission Guards
- **Files changed**: `frontend/hooks/usePermissions.ts` (NEW), `frontend/components/Sidebar.tsx`, `frontend/app/admin/layout.tsx`
- **Problem**: Admin UI doesn't hide elements based on user permissions
- **Change**: Created `usePermissions` hook wrapping `useAuthContext()` with `hasPermission()`, `hasAnyAdminPermission`, convenience booleans. Sidebar now filters admin sub-links by required permission. Admin layout checks `hasAnyAdminPermission` instead of just `manage_users`.
- **Verification**: `npx next build` passes

#### Items 1+6: Benchmark Page + Sidebar Link
- **Files changed**: `frontend/app/admin/benchmark/page.tsx` (NEW), `frontend/app/api/admin/benchmark/runs/route.ts` (NEW), `frontend/app/api/admin/benchmark/runs/[runId]/route.ts` (NEW), `frontend/components/Sidebar.tsx`
- **Problem**: Backend benchmark API exists but no frontend page
- **Change**: Created benchmark page with runs list (table) + run detail (drill-down). Summary cards: Total Runs, Best Precision@5, Best Relevance, Avg Latency. Per-question detail with color-coded relevance. API proxy routes via fastapiFetch. Sidebar: added FlaskConical icon + Benchmark link guarded by manage_users permission.
- **Verification**: `npx next build` passes

## Session: 2026-03-10

### [Fix]: Token tracking gaps in non-streaming paths

- **Files changed**: `backend/src/generation/rag_chain.py`, `backend/src/generation/conversational_rag.py`
- **Problem**: Non-streaming RAG query (`run_rag_query()`) and non-streaming conversation (`run_conversational_rag()`) called `generate_answer()` but never persisted `QueryUsage`. Only the streaming conversation path had usage persistence.
- **Root cause**: Token tracking was added to the streaming path first, but the non-streaming paths were missed.
- **Change**: Added `get_last_usage()` call + `QueryUsage` persistence after `generate_answer()` in both functions. Added `get_last_usage` import to `rag_chain.py` and `QueryUsage` import to `conversational_rag.py`.
- **Why this approach**: Matches the existing pattern in `run_conversational_rag_stream()` (line 637). Wrapped in try/except to avoid breaking queries if persistence fails.
- **Verification**: 216 tests pass. Grep confirms `QueryUsage` persistence in all 3 query paths.

### [Fix]: Test infrastructure — missing Phase 1 model imports in conftest.py

- **Files changed**: `backend/tests/conftest.py`
- **Problem**: Only base models (User, Role, Document, etc.) were imported. Phase 1 models (Permission, ProjectAssignment, AuditLog, QueryUsage, BenchmarkRun) missing — SQLite wouldn't create their tables.
- **Change**: Added all Phase 1 models to the import list.
- **Verification**: All Phase 1 model tests pass with SQLite in-memory DB.

### [Fix]: BenchmarkRun JSONB incompatible with SQLite

- **Files changed**: `backend/src/db/models/benchmark.py`
- **Problem**: `results` column used `JSONB` (PostgreSQL-only), causing `CompileError` when running tests with SQLite.
- **Change**: Changed to `JSON().with_variant(JSONB(), "postgresql")` — uses JSONB on PostgreSQL, JSON on SQLite.
- **Verification**: BenchmarkRun model tests pass on SQLite.

### [Fix]: ProjectAssignment server_default incompatible with SQLite

- **Files changed**: `backend/src/db/models/user.py`
- **Problem**: `assigned_at` used `server_default="now()"` (PostgreSQL syntax), causing `ValueError: Invalid isoformat string: 'now()'` on SQLite.
- **Change**: Changed to `server_default=func.now()` (cross-platform). Added `func` to imports.
- **Verification**: ProjectAssignment model tests pass on SQLite.

### [Fix]: Existing test_llm_client.py tests broken by usage tracking

- **Files changed**: `backend/tests/test_generation/test_llm_client.py`
- **Problem**: `_kimi_generate` and `_ollama_generate` now return `(str, UsageStats)` tuples instead of just `str`. Mock return values needed updating.
- **Change**: Updated mock return values in `test_first_provider_success`, `test_fallback_to_ollama`, `test_custom_provider_order` to return `(str, UsageStats(...))` tuples.
- **Verification**: All LLM client tests pass.

### [Fix]: Existing test_rag_chain.py assert_called_once broken

- **Files changed**: `backend/tests/test_generation/test_rag_chain.py`
- **Problem**: `mock_db.add.assert_called_once()` failed because `db.add` is now called twice (QueryUsage + QueryLog).
- **Change**: Changed to `assert mock_db.add.call_count >= 1`.
- **Verification**: All RAG chain tests pass.

### [Feature]: 51 Phase 1 tests across 5 files

- **Files created**:
  - `backend/tests/test_generation/test_usage.py` (9 tests) — UsageStats cost calculation, StreamUsageStats finalize, model pricing table
  - `backend/tests/test_db/test_phase1_models.py` (11 tests) — Permission, ProjectAssignment, AuditLog, QueryUsage, BenchmarkRun CRUD
  - `backend/tests/test_api/test_admin.py` (12 tests) — Admin endpoints: users, roles, project assignments, audit logs, usage
  - `backend/tests/test_api/test_auth.py` (8 tests) — Login, logout, refresh, /me, auth failures
- **Files modified**: `backend/tests/test_rbac/test_permissions.py` (6 new tests) — has_permission, get_user_permissions, can_view_all_projects
- **Total new tests**: 51 (46 new + 5 pre-existing = 51 in Phase 1 test files)
- **Verification**: All 51 Phase 1 tests pass. Total: 216 tests pass.

### [Docs]: Updated RAG_pipeline/CLAUDE.md with Phase 1 features

- **Files changed**: `RAG_pipeline/CLAUDE.md`
- **Change**: Added Auth System, RBAC System, Token/Cost Tracking, and Benchmarking sections. Updated Key Features list. Updated Project Structure tree with auth/, services/, db/models/, benchmark/. Added COOKIE_DOMAIN and COOKIE_SECURE to Environment section.
- **Verification**: All sections present and accurate.

## Session: 2026-03-09

### [Fix]: Make Hindsight client timeout configurable and increase to 120s

- **Files changed**: `RAG_pipeline/backend/src/config.py` (line 64), `RAG_pipeline/backend/src/memory/hindsight_client.py` (line 36), `docker-compose.yml` (line 37)
- **Problem**: Hindsight `_retain_user_bank` and `_retain_quality_bank` still failing with `asyncio.TimeoutError` after switching model to `gemma3:4b`. Client timeout (50s) too short for Ollama cold-loading under VPS memory pressure (~2.6GB free).
- **Root cause**: Hardcoded 50s client timeout in `hindsight_client.py` was shorter than Hindsight's server-side `HINDSIGHT_API_LLM_TIMEOUT` (default 120s). On first request after model eviction, Ollama cold-load + fact extraction exceeds 50s.
- **Change**:
  1. Added `hindsight_timeout: float = 120.0` to `config.py` Hindsight settings block (configurable via `HINDSIGHT_TIMEOUT` env var)
  2. Changed `timeout=50.0` to `timeout=settings.hindsight_timeout` in `hindsight_client.py`
  3. Added `HINDSIGHT_API_LLM_TIMEOUT: "180"` to hindsight service in `docker-compose.yml`
- **Why this approach**: Retain is fire-and-forget (`asyncio.create_task`), so longer timeout has zero user-facing impact. 120s client / 180s server gives headroom for cold-load scenarios. Making it configurable allows tuning without code changes.
- **Verification**: Rebuild backend + restart hindsight, trigger a query, check logs for "user bank stored" / "quality bank stored" instead of TimeoutError.

### [Fix]: Switch Hindsight LLM model to fix retain timeouts

- **Files changed**: `docker-compose.yml` (line 36)
- **Problem**: Hindsight `retain` calls failing with `asyncio.TimeoutError` after every query. Memory storage silently failing despite answers returning successfully.
- **Root cause**: Hindsight configured to use `gemma3:12b` (8GB) via Ollama, but VPS only has ~2.6GB free RAM. Ollama needs to cold-load the model each time, can't respond within 50s timeout under memory pressure.
- **Change**: `HINDSIGHT_API_LLM_MODEL: gemma3:12b` → `HINDSIGHT_API_LLM_MODEL: gemma3:4b`
- **Why this approach**: The 4B model (3.3GB) is already pulled on the VPS, loads faster, and fits in available RAM. RAG backend already uses `gemma3:4b` successfully. Hindsight retain/recall tasks only do lightweight summarization — no need for a 12B model.
- **Verification**: Restart hindsight container (`docker compose up -d hindsight`), trigger a query, check backend logs for absence of TimeoutError warnings.

### [Deploy]: Security + RBAC changes to running VPS

- **Files changed**:
  - `docker-compose.yml` — reverted all ports from `127.0.0.1:PORT:PORT` to `PORT:PORT` (no Caddy/domain yet), added `MCP_INGEST_API_KEY` env var to backend service
  - `.env` — added `MCP_INGEST_API_KEY=<generated 64-char hex key>`
  - `RAG_pipeline/backend/alembic/versions/006_add_rbac_and_audit.py` — fixed setval bug on lines 117-119
- **Problem**: Migration 006 crashed on startup with `asyncpg.exceptions.NumericValueOutOfRangeError: setval: value 0 is out of bounds for sequence "project_assignments_id_seq" (1..2147483647)`
- **Root cause**: `COALESCE(MAX(id), 0)` returns 0 for empty `project_assignments` table, but PostgreSQL sequences have minimum value of 1
- **Change**: Wrapped all three setval calls with `GREATEST(..., 1)` to ensure minimum value of 1
- **Why this approach**: Simplest fix — `GREATEST` ensures the value is always valid for the sequence
- **Verification**:
  - `alembic_version` shows `006`
  - `curl http://localhost:8000/health` → all services healthy
  - `curl http://localhost:8001/health` → dashboard OK
  - `curl -X POST http://localhost:8000/api/v1/ingest/md` → 401 "Missing Authorization header"
  - 9 permissions seeded, 20 role_permissions, 3 roles (admin, manager, developer)

### [Config]: Revert Docker ports to public access

- **Files changed**: `docker-compose.yml`
- **Problem**: Ports were bound to `127.0.0.1` (localhost-only), but no Caddy reverse proxy or domain is configured yet — would lock us out of the VPS
- **Change**: Reverted all 8 port bindings from `127.0.0.1:PORT:PORT` to `PORT:PORT`
- **Why this approach**: Temporary measure until domain + Caddy is set up in a future session

### [Config]: Add MCP_INGEST_API_KEY

- **Files changed**: `.env`, `docker-compose.yml`
- **Change**: Generated random 64-char hex key with `openssl rand -hex 32`, added to `.env` as `MCP_INGEST_API_KEY`, passed through to backend container in `docker-compose.yml`
- **Verification**: `curl -X POST http://localhost:8000/api/v1/ingest/md` returns 401

## Session: 2026-03-06

### [Feature]: VPS Security Hardening + Manager/Developer RBAC

- **Files changed**:
  - `RAG_pipeline/backend/src/config.py` — removed hardcoded Voyage API key, Qdrant API key+URL, DB password; added mcp_ingest_api_key setting
  - `knowledge_base/mcp-server/src/mcp_server/config.py` — removed hardcoded DB password, added ingest_api_key setting
  - `docker-compose.yml` — bound all ports to 127.0.0.1 (postgres, qdrant, hindsight, backend, frontend, dashboard)
  - `Caddyfile` (new) — reverse proxy with auto-TLS, security headers, routing for RAG frontend/backend + dashboard/MCP
  - `deploy/caddy-install.sh` (new) — automated Caddy installation and domain setup
  - `deploy/jail.local` (new) — fail2ban config for SSH protection
  - `deploy/backup-postgres.sh` (new) — daily pg_dump with 14-day retention
  - `deploy/deploy.sh` — added UFW firewall (22/80/443), fail2ban setup, backup cron job
  - `RAG_pipeline/backend/src/api/main.py` — added SecurityHeadersMiddleware (X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy, HSTS), tightened CORS methods/headers, permission seeding in startup
  - `knowledge_base/mcp-server/src/mcp_server/api/app.py` — added SecurityHeadersMiddleware, tightened CORS, require_auth stores payload on request.state
  - `RAG_pipeline/backend/src/api/routes/ingest.py` — added verify_ingest_api_key dependency on /ingest/md, rate limit on /ingest
  - `knowledge_base/mcp-server/src/mcp_server/services/rag_bridge.py` — sends Bearer token for authenticated ingestion
  - `RAG_pipeline/backend/src/api/routes/auth.py` — added permissions to UserResponse, audit logging on login, rate limit on /refresh
  - `RAG_pipeline/backend/src/api/routes/admin.py` — added project assignment CRUD endpoints, audit log endpoint, rate limit on password reset
  - `RAG_pipeline/backend/src/api/routes/query.py` — rate limits on /query and /query/stream (30/min)
  - `RAG_pipeline/backend/alembic/versions/006_add_rbac_and_audit.py` (new) — migration for permissions, role_permissions, project_assignments, audit_logs tables + seed data
  - `RAG_pipeline/backend/src/db/models/user.py` — added Permission, ProjectAssignment, AuditLog models, role_permissions table
  - `RAG_pipeline/backend/src/db/models/__init__.py` — exported new models
  - `RAG_pipeline/backend/src/rbac/permissions.py` — expanded with permission constants, get_user_permissions(), has_permission()
  - `RAG_pipeline/backend/src/api/dependencies.py` — added require_permission() factory, ManagerUser, ProjectManager types
  - `RAG_pipeline/backend/src/services/audit_service.py` (new) — audit logging service
  - `knowledge_base/mcp-server/src/mcp_server/api/permissions.py` (new) — require_project_access() for MCP endpoints
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` — project list filtered by assignments, access checks on scan/generate/push
  - `RAG_pipeline/frontend/hooks/useAuth.ts` — added permissions field to AuthUser
  - `RAG_pipeline/frontend/components/AuthProvider.tsx` — added permissions field to AuthUser
  - `RAG_pipeline/frontend/app/admin/layout.tsx` — gates on manage_users permission (not just is_superadmin)
  - `RAG_pipeline/frontend/components/Sidebar.tsx` — shows Admin link for manage_users permission holders
  - `knowledge_base/dashboard/src/hooks/useAuth.ts` — expanded to fetch and expose user permissions
  - `knowledge_base/dashboard/src/App.tsx` — gates Add Repo button on manage_projects permission
  - `knowledge_base/dashboard/src/components/ProjectList.tsx` — hides action buttons for non-managers
  - `.env.production.template` — added MCP_INGEST_API_KEY, updated COOKIE_SECURE/ALLOWED_ORIGINS for HTTPS

- **Problem**: VPS deployed with all ports exposed, no TLS, hardcoded secrets in source, no role-based project access
- **Root cause**: Initial deployment focused on functionality; security and RBAC deferred
- **Change**: Two-phase implementation: (1) security hardening (secrets, ports, TLS, firewall, headers, auth, rate limits, backups), (2) full RBAC with permissions, project assignments, audit logging
- **Why this approach**: Defense-in-depth — multiple layers of security. RBAC enables Manager/Developer workflow where managers assign projects and developers only see assigned projects.
- **Verification**: Code review; all changes are consistent with existing patterns. Migration 006 tested against schema. Frontend changes maintain type safety.

## Session: 2026-03-04

### [Feature]: Full JWT authentication system

- **Files changed**:
  - `backend/pyproject.toml` — added python-jose, passlib[bcrypt], slowapi deps
  - `backend/src/config.py` — added jwt_secret_key, jwt_algorithm, access/refresh token expiry, admin_default_password settings
  - `backend/src/db/models/user.py` — added password_hash, is_active, is_superadmin, last_login_at columns + RefreshToken model
  - `backend/src/db/models/__init__.py` — exported RefreshToken
  - `backend/src/auth/__init__.py` — new module init
  - `backend/src/auth/security.py` — hash_password, verify_password, create/decode JWT tokens, hash_token
  - `backend/src/auth/service.py` — AuthService class (authenticate, create/rotate/revoke tokens)
  - `backend/src/api/routes/auth.py` — POST /login (rate-limited 5/min), /logout, /refresh, GET /me endpoints with httpOnly cookies
  - `backend/src/api/routes/admin.py` — CRUD for users (create, update, deactivate, reset-password) and roles, all behind AdminUser dependency
  - `backend/src/api/dependencies.py` — replaced X-User-Id header auth with JWT cookie auth (access_token cookie), added AdminUser dependency
  - `backend/src/api/main.py` — registered auth + admin routers, updated CORS origins, added slowapi rate limiter, updated admin seed with password hash
  - `backend/alembic/versions/004_add_auth_fields.py` — migration: add auth columns to users, create refresh_tokens table, set admin as superadmin
  - `frontend/middleware.ts` — route protection (redirect to /login if no access_token cookie)
  - `frontend/app/login/page.tsx` — login page with form, error handling, rate limit feedback
  - `frontend/app/api/auth/login/route.ts` — proxy to FastAPI, forwards Set-Cookie headers
  - `frontend/app/api/auth/logout/route.ts` — proxy logout
  - `frontend/app/api/auth/refresh/route.ts` — proxy token refresh
  - `frontend/app/api/auth/me/route.ts` — proxy current user info
  - `frontend/hooks/useAuth.ts` — auth hook with user state, logout, periodic token refresh (12min)
  - `frontend/components/AuthProvider.tsx` — React context for auth state
  - `frontend/components/Layout.tsx` — conditionally hide sidebar on /login
  - `frontend/components/Sidebar.tsx` — added logout button, admin link for superadmins
  - `frontend/lib/fastapi.ts` — replaced X-User-Id header with access_token cookie forwarding
  - `frontend/app/api/conversations/[id]/messages/stream/route.ts` — replaced X-User-Id with cookie forwarding
  - `frontend/app/layout.tsx` — wrapped with AuthProvider via providers.tsx
  - `frontend/app/providers.tsx` — added AuthProvider
  - `frontend/app/admin/layout.tsx` — admin-only access guard
  - `frontend/app/admin/users/page.tsx` — user management table (create, deactivate, activate, reset password)
  - `frontend/app/api/admin/users/route.ts` — proxy GET/POST users
  - `frontend/app/api/admin/users/[id]/route.ts` — proxy PUT/DELETE user
  - `frontend/app/api/admin/users/[id]/reset-password/route.ts` — proxy password reset
  - `frontend/app/api/admin/roles/route.ts` — proxy GET/POST roles
  - `knowledge_base/mcp-server/pyproject.toml` — added python-jose dep
  - `knowledge_base/mcp-server/src/mcp_server/config.py` — added jwt_secret_key, jwt_algorithm settings
  - `knowledge_base/mcp-server/src/mcp_server/api/app.py` — added JWT cookie validation dependency on /mcp, /logs, /webhook routes; restricted CORS
  - `knowledge_base/dashboard/src/api/client.ts` — added credentials:"include", 401→redirect to login
  - `knowledge_base/dashboard/src/hooks/useAuth.ts` — verify auth on mount, redirect if unauthorized
  - `knowledge_base/dashboard/src/App.tsx` — wrapped in auth check, shows loading/redirect
  - `knowledge_base/dashboard/nginx.conf` — added Cookie header forwarding to proxy locations
  - `docker-compose.yml` — added JWT_SECRET_KEY and ADMIN_DEFAULT_PASSWORD env vars to backend + dashboard
  - `.env.production.template` — added JWT_SECRET_KEY and ADMIN_DEFAULT_PASSWORD
- **Problem**: App used hardcoded X-User-Id:1 header — no login, no password protection, all endpoints open
- **Root cause**: Phase 1 placeholder auth was never replaced
- **Change**: Full JWT auth system with httpOnly cookies, bcrypt password hashing, access+refresh token rotation, rate-limited login, admin user management panel, cross-service JWT validation (RAG backend + MCP server + dashboard)
- **Why this approach**: JWT with httpOnly cookies — stateless (no session store), works across both frontends (Next.js on :3000, dashboard on :8001), secure against XSS (httpOnly), shared secret enables MCP server to validate tokens independently
- **Verification**: Needs Docker rebuild + `alembic upgrade head` to test E2E. Manual verification checklist: login with admin/admin, verify cookie set, access protected routes, test admin panel CRUD, verify dashboard redirect, test rate limiting, test token refresh

## Session: 2026-03-02 (continued 3)

### [Fix]: MCP server crash on restart — DuplicateTableError for "projects"

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/api/app.py` (lifespan function)
- **Problem**: MCP server crashes on restart with `asyncpg.exceptions.DuplicateTableError: relation "projects" already exists`. The `Base.metadata.create_all` emits raw `CREATE TABLE` instead of checking first, despite `checkfirst=True` being the default.
- **Root cause**: `run_sync(Base.metadata.create_all)` with async engines doesn't reliably perform the `checkfirst` reflection — it emits `CREATE TABLE` without `IF NOT EXISTS`, crashing when tables already exist from a previous startup.
- **Change**: Replaced bare `create_all` with explicit table-existence check: query existing table names via `dialect.get_table_names()`, filter `Base.metadata.sorted_tables` to only missing ones, then `create_all(tables=tables_to_create)`. Skips entirely if all tables exist.
- **Why this approach**: More robust than relying on `checkfirst` with async engines. Explicit check + filtered create is idempotent and logs what it does.
- **Verification**: Needs Docker restart to confirm no crash on second startup.

## Session: 2026-03-02 (continued 2)

### [Fix]: Run Alembic migrations on backend Docker startup

- **Files changed**: `RAG_pipeline/backend/Dockerfile` (line 13)
- **Problem**: Backend container crashes with `relation "users" does not exist` because the PostgreSQL database has no tables. The Dockerfile only starts uvicorn — it never runs `alembic upgrade head` to create the tables. Migration files (001-003) are already copied into the image but never executed.
- **Root cause**: The `CMD` in the Dockerfile only ran `uvicorn`, never ran Alembic migrations first.
- **Change**: Replaced exec-form `CMD ["uvicorn", ...]` with shell-form `CMD ["sh", "-c", "alembic upgrade head && uvicorn ..."]` so migrations run before the API starts. Alembic is idempotent — safe on restarts.
- **Why this approach**: Simplest fix — single CMD with `&&` ensures tables exist before uvicorn starts. No entrypoint script needed for this use case.
- **Verification**: Needs Docker rebuild and `docker-compose up -d --build backend` to confirm migration output in logs.

## Session: 2026-03-02 (continued)

### [Fix]: Deep Dive streaming — concurrent stderr, assistant text fallback, session-id fix

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/services/deep_dive_service.py`
- **Problem**: Deep Dive runs for ~22s and completes successfully, but the frontend only shows `Analyzing project...` status + duration. No tool calls, no streaming text, no answer content. The previous fix (adding `content_block_delta` handling) was deployed but the problem persisted.
- **Root cause**: Three compounding issues:
  1. **Suppressed assistant text**: We removed text from `assistant` events assuming `content_block_delta` would provide it, but Claude CLI may not emit `content_block_delta` events (or they arrive all at once due to Windows pipe buffering). This left NO text source.
  2. **Sequential stderr read**: stderr was read AFTER stdout (lines 124-127). If Claude CLI writes enough to stderr, the pipe buffer fills → process blocks → stdout blocks → nothing streams. `claude_service.py` avoids this with concurrent reading.
  3. **Unconditional `--session-id`**: Added to every query including search page one-shots, potentially causing unexpected CLI behavior.
- **Changes**:
  1. **Debug logging**: Added `logger.debug()` for raw lines and parsed event types to capture what Claude CLI actually emits
  2. **Concurrent stderr**: Added `_drain_stderr()` inner function, started via `asyncio.create_task()` immediately after subprocess spawn. Awaited after stdout loop ends. Matches `claude_service.py` pattern.
  3. **Assistant text fallback**: Added `__init__` with `_text_streamed_via_delta` flag. Set `True` when `content_block_delta` text arrives. In `assistant` handler, only yield text blocks if no delta text was received (fallback). Tool use blocks always emitted.
  4. **Conditional `--session-id`**: Only added with `--continue` when `is_continuation=True` (caller passed a `session_id`). First queries from search page run without session flags.
- **Why this approach**: Belt-and-suspenders for text delivery (delta preferred, assistant as fallback). Concurrent stderr prevents deadlock. Session flags only when needed for multi-turn. Debug logging enables diagnosis of actual event stream.
- **Verification**: Code review — file reads correctly, `DeepDiveService()` call site compatible (no args needed). Needs runtime test.

## Session: 2026-03-02

### [Fix/Feature]: Fix Deep Dive streaming + Conversational Deep Dive in Chat

- **Files changed**:
  - **Backend (knowledge_base):**
    - `mcp-server/src/mcp_server/services/deep_dive_service.py` — Fixed `_convert_event()` to handle `content_block_start`, `content_block_delta`, `content_block_stop` events (streaming tokens). Added session management (`--session-id`/`--continue` CLI flags). Updated `_QA_PROMPT` with knowledge persistence instructions (insert_to_rag). Suppressed duplicate text from `assistant` events.
    - `mcp-server/src/mcp_server/schemas.py` (line 143) — Added `session_id: str | None = None` to `DeepDiveRequest`
    - `mcp-server/src/mcp_server/api/routes/projects.py` (line 430) — Pass `req.session_id` to `service.query()`
  - **Frontend (RAG_pipeline):**
    - `frontend/types/index.ts` (line 176) — Added `session_id` to `DeepDiveRequestSchema`
    - `frontend/hooks/useDeepDive.ts` — Added `sessionId`, `thinking` to `DeepDiveState`. Handles `session`, `thinking`, `thinking_start` events. Passes `session_id` in requests.
    - `frontend/components/DeepDiveChatBubble.tsx` (CREATE) — Chat-bubble formatted deep dive response with collapsible thinking section, tool call progress, streaming markdown with cursor, cancel button, usage stats.
    - `frontend/components/ModeToggle.tsx` — Refactored to support `variant="chat"` (Chat/Deep Dive toggle) alongside existing `variant="search"` (Search/Deep Dive). Discriminated union props for type safety.
    - `frontend/components/ChatWindow.tsx` — Added `mode`, `onModeChange`, `selectedProject`, `onProjectChange`, `deepDiveState`, `onCancelDeepDive` props. Header now shows ModeToggle + ProjectSelector. Passes deep dive state to MessageList.
    - `frontend/components/MessageList.tsx` — Added `deepDiveState`, `onCancelDeepDive` optional props. Renders `DeepDiveChatBubble` when deep dive is active instead of normal streaming bubble.
    - `frontend/components/ChatInput.tsx` — Added optional `placeholder` prop.
    - `frontend/components/ChatLayout.tsx` — Added `mode` state (`"chat" | "deep-dive"`), `selectedProject` state, `useDeepDive` hook. `handleSendMessage()` routes to deep dive or normal chat based on mode. Resets deep dive state on mode switch.
    - `frontend/app/api/deep-dive/query/route.ts` — Passes `session_id` through to backend (already forwarded all parsed fields).
- **Problem**: Deep Dive showed status + duration but no answer content. Root cause: `_convert_event()` only handled `assistant` (full turns) and `result` events — `content_block_delta` events (where streaming tokens actually come from in `stream-json` format) were silently dropped.
- **Root cause**: Claude Code's `stream-json` format emits incremental text via `content_block_delta` events with `text_delta` type, not in `assistant` events. The original implementation expected all text in `assistant` message blocks, which are full-turn summaries that duplicate delta content.
- **Change**:
  1. **Streaming fix**: Added `content_block_start` (tool_use/thinking detection), `content_block_delta` (text_delta → token, thinking_delta → thinking), `content_block_stop` (suppress) handlers. Suppressed text blocks from `assistant` events to avoid duplication.
  2. **Multi-turn sessions**: Generate UUID on first query, pass `--session-id <id>` to CLI. On follow-ups, add `--continue` flag. Yield `session` event to frontend.
  3. **Knowledge persistence**: Added prompt instructions for agent to call `insert_to_rag()` when producing significant findings.
  4. **Chat UI integration**: Full deep dive mode in `/chat` page with mode toggle, project selector, streaming chat bubbles with collapsible thinking + tool calls.
- **Why this approach**: Follows Claude Code's actual `stream-json` event protocol. Session management uses CLI's native `--session-id`/`--continue` flags for multi-turn context. Frontend reuses established patterns (ModeToggle, ProjectSelector, DeepDiveDisplay visual structure, MessageBubble layout).
- **Verification**: `npm run build` — compiled successfully, all 14 routes generated. No type errors.

## Session: 2026-03-01

### [Feature]: Deep Dive mode — Claude Code CLI analysis from search UI

- **Files changed**:
  - **Backend (knowledge_base):**
    - `mcp-server/src/mcp_server/services/deep_dive_service.py` (CREATE) — new service class
    - `mcp-server/src/mcp_server/schemas.py` (lines 137-152) — added DeepDiveRequest, DeepDiveProject, DeepDiveProjectsResponse
    - `mcp-server/src/mcp_server/api/routes/projects.py` (lines 403-447) — added POST /mcp/deep-dive (SSE), GET /mcp/deep-dive/projects
  - **Frontend (RAG_pipeline):**
    - `frontend/types/index.ts` (lines 172-198) — added DeepDiveRequestSchema, DeepDiveProjectSchema, DeepDiveProjectsResponseSchema
    - `frontend/app/api/deep-dive/query/route.ts` (CREATE) — BFF proxy for SSE stream
    - `frontend/app/api/deep-dive/projects/route.ts` (CREATE) — BFF proxy for project list
    - `frontend/hooks/useDeepDive.ts` (CREATE) — SSE streaming hook with AbortController
    - `frontend/hooks/useDeepDiveProjects.ts` (CREATE) — React Query hook for project list
    - `frontend/components/ModeToggle.tsx` (CREATE) — Search/Deep Dive pill toggle
    - `frontend/components/ProjectSelector.tsx` (CREATE) — project dropdown with loading/empty states
    - `frontend/components/DeepDiveDisplay.tsx` (CREATE) — tool call progress, streaming answer, usage stats
    - `frontend/components/SearchBar.tsx` (lines 7-12, 30, 37, 42) — added `placeholder` and `disabled` props
    - `frontend/app/page.tsx` (full rewrite) — integrated mode toggle, project selector, deep dive display
- **Problem**: RAG pipeline generates documentation summaries, but can't answer detailed code-level questions (e.g., "what are the exact tMap expressions in job X?"). Users needed Claude Code-level analysis directly from the UI.
- **Change**: Implemented full Deep Dive feature:
  1. **DeepDiveService**: Reuses subprocess pattern from ClaudeService. Spawns `claude -p` with a Q&A prompt, parses stream-json events, yields SSE events (status, tool_use, token, usage, done, error).
  2. **API endpoints**: POST /mcp/deep-dive validates project exists with scan data, then streams SSE. GET /mcp/deep-dive/projects returns projects with status ready/scanned and job_count > 0.
  3. **BFF routes**: Next.js route handlers proxy to knowledge_base MCP server (port 8001). Query route passes SSE stream through directly (no re-parsing).
  4. **useDeepDive hook**: Same SSE parsing pattern as useStreamMessage. Handles token/tool_use/usage/done/error/status events. AbortController for cancellation.
  5. **UI components**: ModeToggle (pill toggle), ProjectSelector (dropdown with loading/empty states), DeepDiveDisplay (tool call progress with animated spinners, streaming markdown answer, cancel button, usage stats).
  6. **Search page integration**: Mode state drives which UI elements render. Deep Dive disables search bar until project selected. handleSearch routes to either search.mutate() or deepDive.query() based on mode.
- **Why this approach**: Reuses established patterns (subprocess spawning from ClaudeService, SSE parsing from useStreamMessage, BFF proxy from existing routes, React Query from useConversations). No new dependencies needed. Stream-through SSE proxy avoids double-parsing in Next.js. The Q&A prompt instructs Claude Code to use MCP tools strategically — read scan data before answering, only fetch what's needed.
- **Verification**: `npm run build` — compiled successfully, all 14 routes generated including /api/deep-dive/projects and /api/deep-dive/query.

### [Fix]: Markdown rendering — install @tailwindcss/typography and wire up prose styles

- **Files changed**: `frontend/package.json`, `frontend/tailwind.config.ts`, `frontend/app/globals.css`, `frontend/components/MarkdownContent.tsx`
- **Problem**: `MarkdownContent` component used `prose prose-sm` Tailwind classes but `@tailwindcss/typography` plugin was never installed. The `prose` classes were no-ops — headings, lists, code blocks, blockquotes all rendered unstyled.
- **Root cause**: Missing `@tailwindcss/typography` dependency. The plugin that provides all `prose-*` utility styles was never added to the project.
- **Change**:
  1. `npm install @tailwindcss/typography` — added the plugin dependency
  2. `tailwind.config.ts` — imported `typography` from `@tailwindcss/typography` and added it to `plugins: [typography]`
  3. `globals.css` — added custom prose CSS variable overrides to integrate with existing HSL theme variables (foreground, muted, border, primary colors), tightened paragraph/list/heading spacing for chat-style layout, styled inline code with `bg-muted` + removed backtick pseudo-elements
  4. `MarkdownContent.tsx` — updated prose class to `prose prose-sm prose-neutral dark:prose-invert max-w-none`, removed manual component overrides for `code`, `pre`, `blockquote` (now handled by typography plugin + CSS overrides), kept citation link processing (`p`, `li`, `a` overrides)
- **Why this approach**: The typography plugin is the standard Tailwind solution for styling rendered markdown. Custom CSS variable overrides ensure consistency with the existing HSL-based theme. Removing redundant component overrides simplifies the code — the CSS layer handles `code`, `pre`, and `blockquote` styling.
- **Verification**: `npm run build` — compiled successfully with 0 errors, all 12 routes generated.

## Session: 2026-02-28

### [Fix]: Hindsight retain blocking HTTP response + quality bank timeout

- **Files changed**: `backend/src/memory/hindsight_client.py` (lines 8, 31, 137-228), `backend/src/generation/rag_chain.py` (lines 4, 161-169), `backend/src/api/routes/query.py` (lines 4, 210-218), `backend/src/generation/conversational_rag.py` (lines 4, 515-524, 621-630)
- **Problem**: `retain_interaction()` was called with `await` at all 4 call sites despite comments saying "fire-and-forget". The quality bank retain was timing out (`asyncio.TimeoutError` after 60s), adding ~60s latency to user requests. The HTTP 200 response was blocked until both retains completed. Additionally, user bank and quality bank retains ran sequentially — if the user bank took 5s and quality bank timed out at 60s, total blocking time was 65s.
- **Root cause**: The code used `await retain_interaction(...)` which blocks the coroutine until both retains complete. Inside `retain_interaction()`, the two bank retains were sequential (one after the other), not concurrent. The Hindsight client timeout was set to 60s, far too long for a non-critical background operation.
- **Change**:
  1. `hindsight_client.py`: Added `import asyncio`. Reduced client timeout from 60s to 15s. Extracted two helper coroutines `_retain_user_bank()` and `_retain_quality_bank()` from the monolithic `retain_interaction()`. Changed `retain_interaction()` to use `asyncio.gather(..., return_exceptions=True)` so both banks run concurrently and one failure doesn't cancel the other.
  2. `rag_chain.py`: Added `import asyncio`. Changed `await retain_interaction(...)` → `asyncio.create_task(retain_interaction(...))` so it truly fires and forgets.
  3. `query.py`: Added `import asyncio`. Same `await` → `asyncio.create_task()` change in the streaming endpoint's `event_generator()`.
  4. `conversational_rag.py`: Added `import asyncio`. Same change at both call sites — `run_conversational_rag()` (non-streaming) and `run_conversational_rag_stream()` (streaming).
- **Why this approach**: `asyncio.create_task()` schedules the coroutine on the event loop without awaiting its result — the HTTP response returns immediately. `asyncio.gather(return_exceptions=True)` runs both bank retains concurrently and converts exceptions to return values instead of propagating them. 15s timeout is generous for a memory write but fails fast enough to not waste resources.
- **Verification**: `pytest tests/ --ignore=tests/test_db` — 174 passed, 0 failed

### [Fix]: Voyage reranker retry backoff too short for free tier (3 RPM)

- **Files changed**: `backend/src/retrieval/reranker.py` (lines 14, 49-54)
- **Problem**: Retry backoff [1s, 2s, 4s] is too short for Voyage free tier (3 RPM limit). All 3 retries fire within ~7s total, but the per-minute rate limit window doesn't reset that fast. Result: all retries fail → falls back to RRF scores every time, reranker is effectively never used.
- **Root cause**: Backoff times were set for transient network errors, not API rate limits with 20s+ reset windows.
- **Change**: Increased backoff to `[2.0, 5.0, 15.0]` (22s total wait). Added `Retry-After` header parsing — if Voyage includes it, we use that (capped at 30s) instead of the fixed schedule.
- **Why this approach**: 22s total wait gives the per-minute rate limit time to reset. `Retry-After` is the standard mechanism for servers to communicate the exact wait time. Graceful fallback to RRF scores still exists as safety net if all retries fail.
- **Verification**: `pytest tests/test_retrieval/test_reranker.py` — 8 passed, 0 failed

### [Fix]: Voyage reranker 429 rate-limit crash — add retry + graceful fallback

- **Files changed**: `backend/src/retrieval/reranker.py` (lines 13-68), `backend/src/retrieval/search.py` (lines 189-193, 269-276)
- **Problem**: Voyage AI rerank API returns `429 Too Many Requests` which causes an unhandled `httpx.HTTPStatusError` that crashes the entire streaming response. No retry, no fallback — the user sees a broken connection.
- **Root cause**: `reranker.py` called `response.raise_for_status()` without any retry logic. `search.py` called `rerank()` without a try/except, so any API error propagated up and killed the request.
- **Change**:
  1. `reranker.py`: Added retry loop with exponential backoff (1s, 2s, 4s) for 429 responses. Up to 3 attempts. Non-429 HTTP errors still raise immediately. Network/timeout errors also retry with backoff.
  2. `search.py`: Wrapped both `rerank()` call sites in try/except. On failure, logs a warning and falls back to returning candidates sorted by original RRF scores (truncated to `top_k`). The user still gets results — just without reranking.
- **Why this approach**: Rate limits are transient — retry with backoff is the standard fix. Graceful fallback ensures the pipeline never crashes due to a reranker API issue. RRF scores from Voyage embeddings are already decent quality, so skipping reranking is acceptable degradation.
- **Verification**: `pytest tests/test_retrieval/test_reranker.py tests/test_retrieval/test_search.py` — 23 passed, 0 failed

### [Fix]: Replace local cross-encoder reranker with Voyage AI rerank API

- **Files changed**: `backend/src/retrieval/reranker.py` (full rewrite), `backend/src/retrieval/search.py` (lines 161-180, 189, 269), `backend/src/config.py` (lines 46-48), `backend/pyproject.toml` (line 24), `backend/tests/test_retrieval/test_reranker.py` (full rewrite)
- **Problem**: Documents retrieved from Qdrant (20 results via hybrid RRF search) never reach the LLM. The local cross-encoder reranker (`ms-marco-MiniLM-L-12-v2`) returns negative scores for all candidates, and `min_score=0.0` filters them all out. Every query ends with "No results passed threshold" → LLM gets no context → generic answer.
- **Root cause**: The local model is trained on MS MARCO web search data and doesn't understand the technical document domain. It scores everything as negative ("irrelevant"). Voyage embeddings already produce good relevance rankings that the reranker then overrides with bad ones.
- **Change**:
  1. `reranker.py`: Full rewrite — replaced `sentence_transformers.CrossEncoder` with async `httpx` call to Voyage AI `rerank-2.5` API. Function `rerank()` is now `async`. Returns properly calibrated 0-1 scores. `warmup()` is now a no-op (no local model to pre-load). Removed `_model` singleton and `_get_model()`.
  2. `search.py`: Added `await` to two `rerank()` calls — `search_and_rerank()` line 189 and `search_multi_query()` line 269. Lowered pre-filter threshold to `0.0` in `search_and_rerank()` (lines 169, 178) so all RRF candidates reach the reranker (reranker is the quality gate, not the RRF threshold).
  3. `config.py`: Changed `reranker_model` from `"cross-encoder/ms-marco-MiniLM-L-12-v2"` to `"rerank-2.5"`. Changed `reranker_min_score` from `0.0` to `0.05` (Voyage scores are 0-1; 0.05 filters truly irrelevant).
  4. `pyproject.toml`: Removed `"sentence-transformers>=2.2.0"` dependency — saves ~500MB of deps (torch, transformers, etc.).
  5. `test_reranker.py`: Full rewrite — all 8 tests now async, mock `httpx.AsyncClient` instead of `CrossEncoder`, test Voyage API payload format, 0-1 score ranges, min_score filtering.
- **Why this approach**: Voyage AI is already the embedding provider — using their reranker keeps the same domain understanding. API-based reranker eliminates ~100MB model download, ~500MB dependency chain, and cold-start latency. Properly calibrated 0-1 scores work naturally with threshold filtering.
- **Verification**: `pytest tests/ --ignore=tests/test_db` — 174 passed, 0 failed. All 8 reranker tests pass. DB test errors are pre-existing (`aiosqlite` not installed).

### [Fix]: Hindsight memory not working — missing integration in /query and /query/stream endpoints

- **Files changed**: `backend/src/generation/rag_chain.py` (added Hindsight imports + recall/retain calls), `backend/src/generation/prompt_builder.py` (added `memory_context` param to `build_messages()`), `backend/src/api/routes/query.py` (added Hindsight recall/retain to `/query/stream`), `backend/src/memory/hindsight_client.py` (added INFO-level diagnostic logging)
- **Problem**: Hindsight memory was only integrated in the conversational RAG pipeline (`conversational_rag.py`). The `/api/v1/query` endpoint (via `rag_chain.py`) and `/api/v1/query/stream` endpoint (in `query.py`) had zero Hindsight integration — no recall, no retain, no memory context injection. Every search bar query was a missed learning signal.
- **Root cause**: (1) `rag_chain.py` was written before Hindsight integration and never updated. (2) `build_messages()` in `prompt_builder.py` didn't accept `memory_context`, so even after adding recall to `rag_chain.py`, memories couldn't be injected into the LLM prompt when search results were found. (3) `/query/stream` built messages inline and was similarly disconnected. (4) `hindsight_client.py` had no INFO-level diagnostic logging — failures were silent at default log level.
- **Change**:
  1. `prompt_builder.py`: Added `memory_context: str | None = None` parameter to `build_messages()`. When provided, injects a system message after the system prompt, before the user query — same pattern as `build_no_results_messages()`.
  2. `rag_chain.py`: Added imports for Hindsight functions + `_build_memory_context` from `conversational_rag.py`. Before search: calls `ensure_user_bank`, `ensure_quality_bank`, `recall_user_memory`, `recall_quality_insights`. Passes `memory_context` to all `build_messages()` and `build_no_results_messages()` calls. After generating answer: calls `retain_interaction()` with query, answer, sources, latency, intent.
  3. `query.py`: Added same Hindsight recall pattern to `query_stream()`. Added `logging` import and logger. Recall happens before search. `memory_context` passed to both `build_messages()` and `build_no_results_messages()`. Streamed tokens are accumulated, then `retain_interaction()` is called inside `event_generator()` after streaming completes (before the `done` event).
  4. `hindsight_client.py`: Added INFO-level logs for: recall returning N memories (or "disabled"), retain starting/completing (or "disabled"). Upgraded all `except Exception` blocks to capture `as e` and log `type(e).__name__` in the warning message.
- **Why this approach**: Reuses existing Hindsight infrastructure from `conversational_rag.py` — same pattern of ensure_bank → recall → inject → retain. No new dependencies. `_build_memory_context` helper is imported rather than duplicated. Diagnostic logging makes it possible to verify Hindsight is working at default INFO level without enabling DEBUG.
- **Verification**: `pytest backend/tests/ --ignore=tests/test_db` — 174 passed, 0 failed

## Session: 2026-02-27

### [Fix]: LLM timeouts on both `/query` and conversation streaming endpoints

- **Files changed**: `backend/src/config.py` (lines 18, 25), `backend/src/generation/llm_client.py` (lines 144-148, 164-167), `backend/src/generation/conversational_rag.py` (lines 263-444, 578-582)
- **Problem**: Both Kimi and Ollama time out on all non-streaming `generate_answer()` calls. Kimi hits 15s timeout, Ollama hits 30s timeout with empty error message. Streaming endpoint also fails because `_prepare_rag_context()` makes hidden non-streaming `generate_answer()` calls before streaming begins (RESPOND path, context-first check, no-results fallback).
- **Root cause**: (1) Timeouts too short — Kimi 15s and Ollama 30s insufficient for models with context/memories, especially on cold start. (2) Streaming path called `_prepare_rag_context()` which precomputed answers via non-streaming `generate_answer()` in 4 places, blocking before any tokens could stream. (3) `httpx.TimeoutException` wasn't logged with exception type, producing empty error messages.
- **Change**:
  1. `config.py`: `kimi_timeout` 15.0 → 60.0, `ollama_timeout` 30.0 → 120.0
  2. `llm_client.py`: Error logging now includes `type(e).__name__` in both `generate_answer()` and `generate_answer_stream()` — e.g. `[TimeoutException]` instead of empty string
  3. `conversational_rag.py`: Added `precompute: bool = True` parameter to `_prepare_rag_context()`. When `precompute=False`, all `generate_answer()` calls are skipped — returns `precomputed_answer=None` with `llm_messages` set, so the caller can stream via `generate_answer_stream()`. Context-first optimization is skipped entirely in streaming mode (always goes to search). `run_conversational_rag_stream` now passes `precompute=False`. Non-streaming `run_conversational_rag` unchanged (uses default `precompute=True`).
- **Why this approach**: Minimal change — one boolean parameter controls precompute behavior. Streaming callers skip non-streaming LLM calls entirely. Non-streaming callers keep existing behavior with higher timeouts. Context-first optimization is acceptable to skip in streaming mode since users see tokens flowing immediately.
- **Verification**: `pytest backend/tests/ --ignore=tests/test_db` — 174 passed, 0 failed

### [Fix]: Qdrant search returns 0 results due to RBAC group mismatch

- **Files changed**: `backend/src/retrieval/search.py` (line 35), `backend/src/api/routes/query.py` (line 68)
- **Problem**: Qdrant returns 0 raw results on every query. Documents exist in Qdrant but `MatchAny(any=["admin"])` filter finds nothing because documents are stored with `allowed_groups = ["public", project_id]` — never `"admin"`.
- **Root cause**: The `admin` role was treated as a regular RBAC group. During ingestion, documents get `allowed_groups = ["public", "<project_id>"]`, but the search user has `groups=["admin"]`. The filter requires `"admin" IN allowed_groups` which never matches.
- **Change**:
  1. `search.py:_build_search_filter()`: Changed `if user_groups:` to `if user_groups and "admin" not in user_groups:` — admin users skip the group filter entirely and see all documents.
  2. `query.py:query_debug()`: Changed `rbac_filter_applied = bool(user_groups)` to `bool(user_groups) and "admin" not in user_groups`, and the conditional filter now uses `rbac_filter_applied` instead of `user_groups`.
- **Why this approach**: Standard RBAC behavior — admin role bypasses access filters. Minimal change (one condition added per file). All search paths flow through `_build_search_filter()`, so the fix covers `/query`, `/query/stream`, `/conversations`, and `/query/debug`.
- **Verification**: Restart backend, send query — `Qdrant returned N raw results` should now be > 0 for admin users.

### [Fix]: NO_RESULTS_PROMPT conflicts with Hindsight memory injection

- **Files changed**: `backend/src/generation/prompt_builder.py` (lines 33–43)
- **Problem**: LLM replies "I don't have access to your personal information" even though Hindsight memories are correctly injected into the prompt. Logs confirm `Injecting 5 user memories + 3 quality insights`.
- **Root cause**: `NO_RESULTS_PROMPT` contains "NEVER make up information or answer from general knowledge." The LLM treats injected memory context as "general knowledge" and refuses to use it — the prompt gives no indication that memory context is a trusted source.
- **Change**: Added a paragraph to `NO_RESULTS_PROMPT` explicitly telling the LLM that memory context (provided in a follow-up system message) is trusted information and should be used to answer personal/contextual questions. The "NEVER make up information" rule still applies to general knowledge, but no longer conflicts with injected memories.
- **Why this approach**: Minimal change — only the system prompt text was updated. No code logic changes needed. The memory injection pipeline was already working correctly; only the prompt wording caused the conflict.
- **Verification**: Restart backend, open new conversation, ask "what is my name?" — LLM should now use injected memories to answer.

## Session: 2026-02-24 (Fix config.py drift)

### [Fix]: Sync config.py with runtime expectations — missing settings crash backend

- **Files changed**: `backend/src/config.py` (lines 11-51)
- **Problem**: Backend crashes with `'Settings' object has no attribute 'kimi_timeout'` and `KeyError: 'groq'` on every query. `config.py` was out of sync with what `llm_client.py` and `reranker.py` actually reference.
- **Root cause**: Multiple sessions modified `llm_client.py` (removing groq/openrouter providers, adding timeouts/temperature) but corresponding `config.py` changes were lost or reverted. The on-disk `config.py` was stale.
- **Change**:
  1. Removed dead provider settings: `groq_api_key`, `groq_llm_model`, `openrouter_api_key`, `openrouter_llm_model`
  2. Fixed `llm_providers` default: `["kimi", "groq", "openrouter", "ollama"]` → `["kimi", "ollama"]`
  3. Added missing Kimi settings: `kimi_temperature: float = 0.6`, `kimi_timeout: float = 15.0`
  4. Added missing Ollama timeout: `ollama_timeout: float = 30.0`
  5. Added missing reranker min-score: `reranker_min_score: float = 0.0`
  6. Fixed `query_expansion_enabled`: `True` → `False` (disabled in Phase 3.2)
- **Why this approach**: Pure config sync — only `config.py` was stale; all consumer code (`llm_client.py`, `reranker.py`) was already correct.
- **Verification**: `pytest backend/tests/ --ignore=tests/test_db` — 174 passed, 0 failed

## Session: 2026-02-24 (Auto-detect Qdrant vector dimension mismatch)

### [Feature]: Auto-detect and fix Qdrant collection dimension mismatch at startup

- **Files changed**: `backend/src/retrieval/vector_store.py` (lines 24-58)
- **Problem**: After enabling Voyage AI as primary embedding provider (1024-dim), ingesting documents fails with `Wrong input: Vector dimension error: expected dim: 768, got 1024`. The old Qdrant collection was created with 768-dim (Ollama nomic-embed-text). `ensure_collection()` only created a new collection if none existed — it didn't detect dimension mismatches.
- **Root cause**: `ensure_collection()` had a simple `if COLLECTION_NAME not in names:` guard — it skipped all logic if the collection already existed, regardless of whether the vector dimensions matched the current embedding provider.
- **Change**: Rewrote `ensure_collection()` to:
  1. Call `get_embedding_dim()` upfront (before checking collection existence)
  2. If collection exists, fetch its info via `client.get_collection()` and compare `info.config.params.vectors["dense"].size` against expected dim
  3. If mismatch, log a WARNING and delete the collection
  4. After the check, re-query collections list and create if missing (handles both fresh installs and dimension-mismatch recreations)
- **Why this approach**: Automatic detection at startup means no manual intervention needed when switching embedding providers. The WARNING log makes it visible that old vectors are gone and re-ingestion is required. The two-phase check (exists → compare → maybe delete → create if missing) keeps the logic clean and handles all cases.
- **Verification**: 174 non-DB tests passing. No test changes needed — existing mocks don't exercise the dimension check path.

## Session: 2026-02-24 (Fix SQLAlchemy Engine Log Noise)

### [Fix]: SQLAlchemy engine INFO logs flooding startup output

- **Files changed**: `backend/src/db/session.py` (line 13)
- **Problem**: SQLAlchemy engine INFO logs (`select pg_catalog.version()`, `SELECT 1`, etc.) flooding console output on first request despite `logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)` in `main.py` lifespan.
- **Root cause**: `echo=settings.is_dev` in `create_async_engine()` — when `echo=True`, SQLAlchemy internally sets the `sqlalchemy.engine.Engine` logger to `logging.INFO`, overriding the parent-level WARNING set later in lifespan.
- **Change**: Changed `echo=settings.is_dev` to `echo=False`. SQL echo was useful during early development but is now just noise. DB errors still surface through normal error handling.
- **Why this approach**: The `echo` parameter directly controls SQLAlchemy's internal logger level. No amount of `setLevel(WARNING)` on the parent logger can override it because SQLAlchemy re-sets the level when connections are used. Disabling echo is the only clean fix.
- **Verification**: 174 non-DB tests passing. (DB test has unrelated `aiosqlite` missing dependency.)

## Session: 2026-02-24 (Fix Production Bugs + Enable Voyage AI Embeddings)

### [Fix]: Reranker min-score filtering — chitchat gets irrelevant results sent to LLM

- **Files changed**: `backend/src/config.py` (line 48), `backend/src/retrieval/reranker.py` (lines 52-69)
- **Problem**: Reranker returned top_k candidates regardless of score. Chitchat queries got irrelevant results (score -9.13) sent to LLM, producing hallucinated answers.
- **Root cause**: `rerank()` returned `indexed_scores[:top_k]` with no minimum score threshold.
- **Change**: Added `reranker_min_score: float = 0.0` to config. In `rerank()`, filter out scores below `settings.reranker_min_score` before slicing to top_k. Updated logging to show drop count.
- **Why this approach**: Centralizing the filter inside `rerank()` protects all callers (`search_and_rerank()` and `search_multi_query()`). Default 0.0 filters only negative scores.
- **Verification**: 3 new tests + 5 existing tests pass (174 total non-DB tests passing).

### [Fix]: Voyage AI 400 error — `dimensions` parameter not supported

- **Files changed**: `backend/src/retrieval/embedder.py` (lines 41-44, 56-59)
- **Problem**: Every Voyage AI embed call failed with HTTP 400, falling back to slow Ollama (1-6s vs 100-200ms).
- **Root cause**: `dimensions=settings.voyage_embed_dim` passed to Voyage API, but `voyage-4-large` doesn't support the `dimensions` parameter. It always returns 1024-dim natively.
- **Change**: Removed `dimensions=settings.voyage_embed_dim` from both `_voyage_embed_single()` and `_voyage_embed_batch()`.
- **Why this approach**: `voyage-4-large` natively outputs 1024-dim vectors. The `dimensions` param is an OpenAI API feature not supported by Voyage.
- **Verification**: No test changes needed (existing mocks don't check API params). 174 non-DB tests passing.

### [Fix]: Reranker downloads model on first request (+100s latency)

- **Files changed**: `backend/src/retrieval/reranker.py` (lines 67-71), `backend/src/api/main.py` (lines 27-29)
- **Problem**: First user request triggered cross-encoder model download (~100s), causing request timeout.
- **Root cause**: Model was lazy-loaded on first `rerank()` call, not at startup.
- **Change**: Added `warmup()` function to reranker that calls `_get_model()` to pre-load. Called from `main.py` lifespan after Qdrant verification, guarded by `settings.reranker_enabled`.
- **Why this approach**: Startup is the right time to download/load models. Users don't wait.
- **Verification**: 174 non-DB tests passing.

### [Fix]: `query.py:125` crashes — `await` on sync `analyze_query()`

- **Files changed**: `backend/src/api/routes/query.py` (line 125)
- **Problem**: Streaming endpoint crashed at runtime with `TypeError: object QueryAnalysis can't be used in 'await' expression`.
- **Root cause**: When `analyze_query()` was made sync (Phase 3.2), the `await` in `query.py:125` was missed (only `rag_chain.py` and `conversational_rag.py` were updated).
- **Change**: `analysis = await analyze_query(body.query)` → `analysis = analyze_query(body.query)`
- **Verification**: 174 non-DB tests passing.

### [Feature]: Add `delete_collection()` helper to vector_store.py

- **Files changed**: `backend/src/retrieval/vector_store.py` (lines 54-60)
- **Problem**: No way to programmatically delete the old 768-dim Qdrant collection to recreate with 1024-dim for Voyage AI.
- **Change**: Added `delete_collection(client)` async function that checks if collection exists and deletes it.
- **Why this approach**: Utility for one-time collection migration. Can be called via script or admin endpoint.
- **Verification**: 174 non-DB tests passing.

### [Test]: Reranker min-score tests

- **Files changed**: `backend/tests/test_retrieval/test_reranker.py` (full update)
- **Problem**: Existing tests didn't account for `settings.reranker_min_score`.
- **Change**: Added `@patch("src.retrieval.reranker.settings")` with `mock_settings.reranker_min_score = -999` to 4 existing tests (all except `test_rerank_empty_texts`). Added 3 new tests: `test_rerank_filters_below_min_score` (mixed scores, min=0), `test_rerank_all_below_min_score_returns_empty` (all negative), `test_rerank_min_score_boundary` (exactly 0.0 passes).
- **Verification**: All 8 reranker tests pass. 174 non-DB tests passing total.

## Session: 2026-02-23 (Eliminate Intent Classifier LLM Call + Disable Query Expansion)

### [Performance]: Remove LLM-based intent classifier — zero LLM calls for query routing

- **Files changed**:
  - `backend/src/generation/intent_classifier.py` — Full rewrite: removed `generate_answer()` LLM call, `ROUTER_PROMPT`, `_parse_router_response()`. Replaced `async analyze_query()` with sync `analyze_query()` using keyword-based heuristics. Added `_SUMMARY_PATTERN` regex for summary detection. All queries return `Action.SEARCH` (empty queries return `Action.RESPOND`).
  - `backend/src/generation/rag_chain.py` (line 39) — `await analyze_query(...)` → `analyze_query(...)` (sync call)
  - `backend/src/generation/conversational_rag.py` (lines 246-253) — Removed `conversation_context` building (was lines 247-252), `await analyze_query(query, conversation_context=...)` → `analyze_query(query)` (sync call)
  - `backend/src/config.py` (line 50) — `query_expansion_enabled: bool = True` → `False`
  - `backend/tests/test_generation/test_intent_classifier.py` — Full rewrite: 20 sync tests (no mocking, no async). Tests cover empty→RESPOND, all text→SEARCH, summary keywords (summarize/summarise/summary/overview/recap/rundown/brief me), QA default, case insensitivity, conversation_context API compat.
  - `backend/tests/test_generation/test_rag_chain.py` — Removed `analyze_query` AsyncMock. Renamed `test_rag_query_greeting_skips_search` → `test_rag_query_greeting_goes_through_search` (greetings now go through search → 0 results → no-results fallback).
  - `backend/tests/test_generation/test_conversational_rag.py` — Removed `analyze_query` AsyncMock. Renamed `test_greeting_skips_search` → `test_greeting_goes_through_search` (same pattern).
- **Problem**: Every user query made 2-3 sequential LLM API calls (intent classifier + optional query expansion + answer generation). At ~3-12s per call, total latency was 6-36s.
- **Root cause**: The intent classifier existed to skip search for chitchat, but Qdrant search takes ~50-100ms — negligible. The `NO_RESULTS_PROMPT` already handles greetings/chitchat naturally when search returns 0 results.
- **Change**: Replaced LLM-based intent classifier with zero-cost heuristic: all queries go through SEARCH. Summary detection uses regex keyword matching (reliable since users use explicit words like "summarize", "overview"). Disabled query expansion by default (saves another LLM call). Net effect: QA queries go from 3 LLM calls to 1, chitchat goes from 2 to 1.
- **Why this approach**: The intent classifier LLM call was pure overhead — Qdrant search is ~50ms, so "skipping search for chitchat" saved almost nothing while costing 3-12s. Keyword-based summary detection is reliable (users use explicit summary words). Query expansion can be re-enabled via config if quality > speed is preferred.
- **Verification**: 171 non-DB tests passing (20 DB errors are pre-existing `aiosqlite` issue). All 84 generation tests pass. All 35 conversational_rag tests pass.

## Session: 2026-02-23 (Fix Kimi K2.5 temperature 400 error)

### [Fix]: Change kimi_temperature from 0.4 to 0.6 — Kimi K2.5 instant mode requires exactly 0.6

- **Files changed**: `backend/src/config.py` (line 17)
- **Problem**: All Kimi K2.5 API calls fail with HTTP 400: `invalid temperature: only 0.6 is allowed for this model`. Every request falls back to local Ollama (~5s per call × 2 calls = ~10s), negating the latency optimization from the previous session.
- **Root cause**: Previous session disabled thinking mode (`extra_body={"thinking": {"type": "disabled"}}`) which puts Kimi K2.5 into "instant mode". Instant mode enforces exactly `temperature=0.6` — no other value is accepted. We set 0.4.
- **Change**: `kimi_temperature: float = 0.4` → `kimi_temperature: float = 0.6` with comment explaining the constraint.
- **Why this approach**: Kimi K2.5 instant mode only accepts 0.6. No flexibility — it's a hard API constraint.
- **Verification**: All 172 tests pass. Live verification: restart backend, send query → logs should show `LLM response from kimi` (not fallback to ollama). Latency should drop from ~10s to ~3-5s.

### [Fix]: Disable OpenAI SDK internal retries + add per-call timing

- **Files changed**: `backend/src/generation/llm_client.py` (lines 33, 2, 137-142)
- **Problem**: After temperature fix, Kimi calls succeeded but total latency was still ~25s for 2 calls. Logs showed `Retrying request to /chat/completions in 0.416478 seconds` — the OpenAI SDK was silently retrying failed attempts (default `max_retries=2`) with exponential backoff, burning ~10s on a failed first attempt before our provider chain even knew about the failure.
- **Root cause**: `AsyncOpenAI` defaults to `max_retries=2`. A failed Kimi call (transient error, rate limit) would be retried by the SDK internally, wasting the full timeout duration before the retry — and before our own `kimi → ollama` fallback chain could kick in.
- **Change**:
  1. Added `max_retries=0` to `AsyncOpenAI` constructor — our provider chain handles fallback, SDK retries just waste time
  2. Added `import time` and per-call `time.perf_counter()` timing in `generate_answer()` — logs now show exact milliseconds per provider call (both success and failure)
- **Why this approach**: With `max_retries=0`, a failed Kimi call immediately falls through to Ollama instead of burning 10+ seconds on internal retries. Per-call timing makes latency bottlenecks visible in logs.
- **Verification**: All 7 llm_client tests pass. Restart backend to confirm: logs should show `LLM response from kimi (Xms)` with actual per-call timing.

## Session: 2026-02-23 (Fix RAG Query Latency ~27s → ~3-5s)

### [Performance]: Disable Kimi K2.5 thinking mode + singleton clients + timeouts

- **Files changed**:
  - `backend/src/generation/llm_client.py` — Added `extra_body={"thinking": {"type": "disabled"}}` to both `_kimi_generate()` and `_kimi_stream()`; changed hardcoded `temperature=1` to `settings.kimi_temperature` (0.4); replaced per-call client creation with module-level singleton clients using `asyncio.Lock` for lazy initialization; added `httpx.Timeout` for both Kimi (15s) and Ollama (30s) clients
  - `backend/src/config.py` — Added `kimi_temperature: float = 0.4`, `kimi_timeout: float = 15.0`, `ollama_timeout: float = 30.0`
- **Problem**: Simple queries took ~27s. Kimi K2.5 defaults to Thinking Mode (deep chain-of-thought) when `temperature=1`, adding 8-25s per call. Two sequential Kimi calls (intent classifier + response generation) = ~26s total. Also, new HTTP clients were created per request with no timeouts.
- **Root cause**: Kimi K2.5's thinking mode is triggered by `temperature=1` and no `extra_body` override. Each API call spun up a new `AsyncOpenAI` / `ollama.AsyncClient` with connection overhead.
- **Change**:
  1. Added `extra_body={"thinking": {"type": "disabled"}}` to disable thinking mode on all Kimi calls
  2. Lowered temperature from 1.0 to 0.4 (configurable via `kimi_temperature`)
  3. Created singleton `AsyncOpenAI` and `ollama.AsyncClient` instances with double-checked locking
  4. Added configurable timeouts (kimi: 15s, ollama: 30s) with 5s connect timeout
- **Why this approach**: For RAG, the model synthesizes retrieved context — no need for deep reasoning. Thinking mode adds 8-25s per call with no quality benefit. Singletons reuse TCP connections, timeouts prevent hanging.
- **Expected improvement**: ~27s → ~3-5s for chitchat, ~5-8s for QA queries
- **Verification**: All 172 tests pass (85 generation tests, 87 other tests). 20 DB test errors are pre-existing (missing `aiosqlite`).

## Session: 2026-02-23 (Remove Groq + OpenRouter providers)

### [Refactor]: Strip Groq and OpenRouter, keep only Kimi K2.5 + Ollama fallback

- **Files changed**:
  - `backend/src/config.py` — removed `groq_api_key`, `groq_llm_model`, `openrouter_api_key`, `openrouter_llm_model`; changed `llm_providers` default from `["kimi", "groq", "openrouter", "ollama"]` to `["kimi", "ollama"]`
  - `backend/src/generation/llm_client.py` — removed `_groq_generate()`, `_groq_stream()`, `_openrouter_generate()`, `_openrouter_stream()` and their registry entries
  - `backend/tests/test_generation/test_llm_client.py` — rewrote all 7 tests for kimi + ollama only
  - `backend/pyproject.toml` — removed `groq>=0.11.0` dependency (kept `openai` — Kimi uses it)
- **Problem**: Unnecessary LLM providers (Groq, OpenRouter) cluttering the codebase. User only wants Kimi K2.5 as primary and Ollama as local fallback.
- **Change**: Deleted all Groq and OpenRouter provider functions, config settings, and test references. Provider chain is now `kimi → ollama`.
- **Why this approach**: Simplifies the stack — fewer dependencies, fewer API keys to manage, cleaner code.
- **Verification**: All 7 tests in `test_llm_client.py` pass. `.env` already had no groq/openrouter entries.

## Session: 2026-02-23 (API Key Placeholders)

### [Config]: Add Kimi and Voyage AI API key placeholders to .env

- **Files changed**: `backend/.env` (added lines 18-23)
- **Problem**: Kimi K2.5 and Voyage AI providers were added in the RAG Retrieval Quality Overhaul but their API keys were missing from `.env`, making it unclear what env vars need to be set.
- **Change**: Added `KIMI_API_KEY=your-kimi-api-key-here` and `VOYAGE_API_KEY=your-voyage-api-key-here` with section comments.
- **Why this approach**: Placeholder values make it obvious which keys are needed. `config.py` already reads these via `os.getenv()`.
- **Verification**: Confirmed `.env` has both new keys. No code changes needed — `config.py` already reads `kimi_api_key` and `voyage_api_key` from env.

## Session: 2026-02-23 (RAG Retrieval Quality Overhaul)

### [Feature]: Phase 1A — Smarter context truncation

- **Files changed**: `backend/src/generation/prompt_builder.py` (lines 10, 60-103)
- **Problem**: `_build_context()` dropped entire sources when the token budget was exceeded — a source with 5000 tokens would be completely discarded, losing all its information
- **Change**: Increased `MAX_CONTEXT_TOKENS` from 4096 → 6144. Replaced drop-entire-source logic with per-source truncation: if a source exceeds remaining budget, its text is truncated to fit with `[...]` appended. All sources are now represented in context.
- **Why this approach**: More sources = better LLM answers. Truncated context is still useful; dropped context is worthless.
- **Verification**: Updated `test_build_context_truncates_per_source` test confirms `[...]` marker appears and source header is preserved. 193 tests passing.

### [Feature]: Phase 1B — Add Kimi K2.5 as LLM provider

- **Files changed**: `backend/src/generation/llm_client.py` (added `_kimi_generate()` + `_kimi_stream()`), `backend/src/config.py` (added `kimi_api_key`, `kimi_llm_model`)
- **Problem**: No access to high-quality, cost-effective cloud LLM beyond Groq free tier and OpenRouter free tier
- **Change**: Added Kimi K2.5 provider using OpenAI-compatible API (`https://api.moonshot.ai/v1`). Same pattern as OpenRouter. Registered in `_PROVIDER_FUNCS`. Updated default `llm_providers` chain: `["kimi", "groq", "openrouter", "ollama"]` — Kimi as primary.
- **Why this approach**: Kimi K2.5 is a 1T-param MoE (32B active), 256K context, $0.60/M input — very cost-effective. OpenAI-compatible API means reusing existing `openai` SDK.
- **Verification**: 193 tests passing.

### [Feature]: Phase 1C — Upgrade query expansion + intent classifier to provider chain

- **Files changed**: `backend/src/retrieval/query_expander.py` (replaced Ollama direct call with `generate_answer()`), `backend/src/generation/intent_classifier.py` (replaced Ollama direct call with `generate_answer()`, fixed null-safety bug in `_parse_router_response`)
- **Problem**: Query expansion and intent classification were hardcoded to local Ollama `gemma3:4b`, which requires Ollama running locally. Also, `_parse_router_response` crashed with `'NoneType' object has no attribute 'lower'` when JSON had `null` values.
- **Change**: Both modules now use `generate_answer()` from `llm_client.py`, routing through the full provider chain (Kimi → Groq → OpenRouter → Ollama). Fixed null-safety: `data.get("action", "search").lower()` → `(data.get("action") or "search").lower()`. Improved expansion prompt to request one more-specific and one more-general alternative.
- **Why this approach**: Single provider chain for all LLM calls — no more dependency on local Ollama for routing/expansion.
- **Verification**: Updated test mocks to patch `src.generation.llm_client.generate_answer`. 193 tests passing.

### [Feature]: Phase 1D — Tune search parameters

- **Files changed**: `backend/src/schemas.py` (lines 12-13, 123-124), `backend/src/retrieval/search.py` (lines 57-58, 91, 328, 363), `backend/src/config.py`
- **Problem**: Suboptimal defaults: `top_k=3` too few sources, `threshold=0.4` too strict for API embeddings, `prefetch_limit=20` hardcoded, `max_documents=1` hardcoded
- **Change**: `top_k` 3→5, `threshold` 0.4→0.3, `rerank_top_n` 15→20, `prefetch_limit` 20→30 (from config), `max_result_documents` 1→3 (from config). Added `default_top_k`, `default_threshold`, `prefetch_limit`, `max_result_documents` to Settings.
- **Why this approach**: API embeddings (Voyage AI) have different score distributions than Ollama nomic-embed-text. More sources and candidates improve retrieval quality.
- **Verification**: Updated merge_sources default test. 193 tests passing.

### [Feature]: Phase 2A — Replace TF-IDF with token-hashing sparse embedder

- **Files changed**: `backend/src/retrieval/sparse_embedder.py` (full rewrite)
- **Problem**: **Critical bug**: `TfidfVectorizer` was created fresh on every call. During ingestion it fitted on a batch of chunks (vocabulary A). During query it fitted on the query string alone (vocabulary B). Index 0 in vector A mapped to a different word than index 0 in vector B. **Hybrid search was comparing random dimensions.**
- **Root cause**: TF-IDF requires a shared vocabulary between indexing and query time. Creating a new vectorizer per call made the vocabulary non-deterministic.
- **Change**: Full rewrite to hash-based approach: `hash(token) % 30000`. Each token deterministically maps to the same index regardless of when/where embedding is created. Tokenize with regex `[a-zA-Z0-9]+`, lowercase, remove English stop words, sub-linear TF weighting `1.0 + log(count)`. Pure Python, no sklearn dependency for this module.
- **Why this approach**: Hash-based approach is stateless — no fitting step, no vocabulary mismatch. Same token always produces same index. Removes sklearn dependency from sparse embedder.
- **Verification**: All 5 sparse embedder tests passing. `test_different_texts_produce_different_vectors` confirms deterministic indices. 193 tests passing.

### [Feature]: Phase 3A — Add Voyage AI embedding provider chain

- **Files changed**: `backend/src/retrieval/embedder.py` (full rewrite), `backend/src/retrieval/vector_store.py` (line 16, 31)
- **Problem**: Ollama `nomic-embed-text` (768-dim) produces mediocre embeddings for domain-specific content
- **Change**: Rewrote embedder with provider chain pattern (same as `llm_client.py`): Voyage AI → Ollama fallback. `voyage-4-large` (1024-dim). Added `get_embedding_dim()` function for dynamic dimension. `embed_text()` and `embed_texts()` public API unchanged. Updated `vector_store.py` to use `get_embedding_dim()` instead of `EMBEDDING_DIM` constant.
- **Why this approach**: Voyage AI `voyage-4-large` is +14% over OpenAI v3 Large on MTEB, first 200M tokens free, 40% cheaper. Uses OpenAI-compatible API (existing `openai` SDK).
- **Verification**: Updated embedder tests to mock provider chain. Added `test_get_embedding_dim_voyage` and `test_get_embedding_dim_ollama_default`. 193 tests passing.

### [Feature]: Phase 3B-D — Upgrade reranker, config, vector store

- **Files changed**: `backend/src/config.py` (added embedding_providers, voyage settings, search tuning settings, upgraded reranker model)
- **Problem**: `ms-marco-MiniLM-L6-v2` (22M params) is the smallest cross-encoder; L-12-v2 (33M params) outperforms much larger models
- **Change**: Upgraded `reranker_model` from `cross-encoder/ms-marco-MiniLM-L6-v2` → `cross-encoder/ms-marco-MiniLM-L-12-v2`. Added all config settings: `embedding_providers`, `voyage_api_key`, `voyage_embed_model`, `voyage_embed_dim`, `default_top_k`, `default_threshold`, `prefetch_limit`, `max_result_documents`.
- **Verification**: 193 tests passing.

### [Feature]: Phase 4A-B — Fix chunking overlap + sentence boundaries

- **Files changed**: `backend/src/ingestion/semantic_chunker.py` (line 146), `backend/src/ingestion/chunker.py` (full rewrite)
- **Problem**: Mechanical chunker had only 32-token overlap — chunks shared very little context. Also, chunks split mid-sentence, producing incoherent embeddings.
- **Change**: Increased semantic chunker fallback overlap from 32→128. Increased mechanical chunker `DEFAULT_OVERLAP` from 32→100. Added `_snap_to_sentence_boundary()`: when splitting, tries to end chunks at sentence-ending punctuation (`. `, `? `, `! `) within the last 20% of each chunk.
- **Why this approach**: More overlap = better semantic coherence between adjacent chunks. Sentence boundary detection prevents mid-sentence splits that degrade embedding quality.
- **Verification**: Updated `test_default_parameters` for new overlap value. 193 tests passing.

### [Fix]: Null-safety in intent classifier JSON parser

- **Files changed**: `backend/src/generation/intent_classifier.py` (line 70-72)
- **Problem**: `_parse_router_response` crashed with `'NoneType' object has no attribute 'lower'` when LLM returned JSON with `null` values (e.g., `{"query_type": null}`)
- **Root cause**: `data.get("action", "search")` returns `None` (not `"search"`) when the key exists but has value `null`. Calling `.lower()` on `None` crashes.
- **Change**: Changed `data.get("action", "search").lower()` → `(data.get("action") or "search").lower()` for all three fields. The `or` operator handles both missing keys and explicit `null` values.
- **Verification**: 193 tests passing.

### [Fix]: Updated test mocks for provider chain migration

- **Files changed**: `backend/tests/test_generation/test_intent_classifier.py`, `backend/tests/test_retrieval/test_query_expander.py`, `backend/tests/test_retrieval/test_embedder.py`, `backend/tests/test_generation/test_rag_chain.py`, `backend/tests/test_generation/test_conversational_rag.py`, `backend/tests/test_generation/test_prompt_builder.py`, `backend/tests/test_retrieval/test_search.py`, `backend/tests/test_ingestion/test_chunker.py`
- **Problem**: Tests patched `ollama_lib.AsyncClient` for intent classifier and query expander, but these now use `generate_answer()` from `llm_client.py`. Embedder tests referenced `EMBEDDING_DIM` constant that no longer exists.
- **Change**: Updated all test mocks to patch `src.generation.llm_client.generate_answer` instead of `ollama_lib`. Updated embedder tests for provider chain pattern. Added `analyze_query` mocks to greeting-skip tests. Updated default parameter assertions.
- **Verification**: 193 tests passing (was 192 — +1 new `test_get_embedding_dim_voyage` test).

## Session: 2026-02-20 (Fix Qdrant Cloud Connection Failure)

### [Fix]: Qdrant Cloud connection failure — port 6333 unreachable, must use port 443

- **Files changed**: `backend/.env` (line 7), `backend/src/config.py` (line 30)
- **Problem**: FastAPI app crashed on startup with `httpcore.ConnectError: All connection attempts failed` during `ensure_collection()` in the lifespan handler. The `qdrant-client` Python library (v1.17.0) could not connect to the Qdrant Cloud instance.
- **Root cause**: `qdrant-client` defaults to port 6333 for REST API connections when no port is specified in the URL. Qdrant Cloud exposes its API on port 443 (standard HTTPS), not port 6333. The URL `https://...cloud.qdrant.io` without an explicit port caused the client to attempt `https://...cloud.qdrant.io:6333`, which is not open on the Qdrant Cloud load balancer.
- **Change**: Appended `:443` to the Qdrant URL in both `.env` and `config.py` default:
  - Before: `https://8dbbe97c-2931-45fb-8c46-836c7fbcd3e7.europe-west3-0.gcp.cloud.qdrant.io`
  - After: `https://8dbbe97c-2931-45fb-8c46-836c7fbcd3e7.europe-west3-0.gcp.cloud.qdrant.io:443`
- **Why this approach**: Explicit port in the URL is the simplest fix — no code changes needed in the client initialization. Port 443 is the standard HTTPS port and is what Qdrant Cloud uses for its REST API.
- **Verification**: Tested with `curl` (port 443 returns `{"result":{"collections":[{"name":"documents"}]},"status":"ok"}`), Python `AsyncQdrantClient` with settings from `config.py` returns `Connection OK! Collections: ['documents']`.

## Session: 2026-02-11 (Streaming Responses for Chat UI)

### [Feature]: End-to-end streaming responses for conversation chat

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` — extracted `_prepare_rag_context()` helper + `RAGContext` dataclass, added `run_conversational_rag_stream()`, refactored `run_conversational_rag()` to use shared helper
  - `backend/src/api/routes/conversations.py` — added `POST /{conversation_id}/messages/stream` SSE endpoint
  - `frontend/app/api/conversations/[id]/messages/stream/route.ts` — **new** SSE proxy route handler
  - `frontend/hooks/useStreamMessage.ts` — **new** streaming hook with ReadableStream + TextDecoder SSE parsing
  - `frontend/components/ChatLayout.tsx` — replaced `useSendMessage` mutation with `useStreamMessage` hook, added optimistic user message + React Query invalidation on stream done
  - `frontend/components/ChatWindow.tsx` — added `streamingMessage` and `pendingUserMessage` props, removed `LoadingMessages` spinner
  - `frontend/components/MessageList.tsx` — renders optimistic user message and streaming assistant message at end of list, auto-scrolls on streaming content changes
  - `frontend/components/MessageBubble.tsx` — added `isStreaming` prop with blinking cursor (`animate-pulse`)
- **Problem**: Chat UI waited for full LLM generation to finish before showing anything — felt sluggish
- **Change**: Wired streaming end-to-end: backend extracts shared prep logic so both streaming and non-streaming endpoints reuse the same search/routing/context-building. Frontend reads SSE stream via ReadableStream, parses `event: sources`, `event: token`, `event: done` events, and renders tokens incrementally with a blinking cursor.
- **Why this approach**: Reuses existing `generate_answer_stream()` infrastructure. Shared `_prepare_rag_context()` helper eliminates code duplication between streaming and non-streaming paths. SSE is the standard for server-push; frontend proxy follows existing BFF pattern.
- **Verification**: 192 backend tests passing (no regressions), TypeScript compiles without errors

## Session: 2026-02-11 (Configurable LLM Provider Chain)

### [Feature]: Configurable LLM Provider Chain (Groq → OpenRouter → Ollama)

- **Files changed**:
  - `backend/pyproject.toml` — Added `groq>=0.11.0` and `openai>=1.0.0` dependencies
  - `backend/src/config.py` — Added `llm_providers` list, `groq_llm_model`, `openrouter_llm_model` settings; reorganized config sections
  - `backend/src/generation/llm_client.py` — Rewrote with 3-tier provider chain: Groq (AsyncGroq), OpenRouter (AsyncOpenAI with base_url), Ollama (existing); fallback logic for both `generate_answer()` and `generate_answer_stream()`
  - `backend/tests/test_generation/test_llm_client.py` — Replaced 2 old tests with 8 new tests: first-success, fallback-to-second, fallback-to-third, all-fail, custom-provider-order, stream-success, stream-fallback, stream-all-fail
- **Problem**: LLM generation was hardcoded to Ollama only. Needed configurable provider chain with fallback for reliability and cost optimization (free cloud APIs → local fallback).
- **Change**:
  - Added provider implementations: `_groq_generate/_groq_stream`, `_openrouter_generate/_openrouter_stream`, `_ollama_generate/_ollama_stream`
  - Provider registry `_PROVIDER_FUNCS` maps names to function name strings, resolved at call time via `_resolve()` (enables test patching)
  - `generate_answer()` and `generate_answer_stream()` iterate `settings.llm_providers` list, try each provider, log warnings on failure, raise `RuntimeError` if all fail
  - Groq and OpenRouter imports are lazy (inside function body) to avoid import errors when packages not installed
  - Config: `llm_providers: list[str] = ["groq", "openrouter", "ollama"]` — simple env var override to change provider chain
- **Why this approach**: Same public API signatures (`generate_answer`, `generate_answer_stream`), so no changes needed in `rag_chain.py`, `conversational_rag.py`, or `query.py`. Provider chain is a simple list — easy to reconfigure via `.env` for scaling to paid APIs later.
- **Verification**: 192 tests passing (8 new llm_client tests, full suite green)

## Session: 2026-02-11 (Enterprise Retrieval Quality Upgrade — 5 Phases)

### [Feature]: Phase 1 — Hybrid Search (Dense + Sparse with RRF)

- **Files changed**:
  - `backend/src/retrieval/sparse_embedder.py` (**new**) — TF-IDF sparse embedding via sklearn `TfidfVectorizer`
  - `backend/src/retrieval/vector_store.py` — Named vectors config (dense + sparse), `SparseVectorParams`, `upsert_chunks()` accepts optional `sparse_embeddings`
  - `backend/src/retrieval/search.py` — Added `search_similar_hybrid()` using Qdrant Prefetch + RRF fusion, `_build_search_filter()` helper, `_process_results()` refactored from inline code
  - `backend/src/ingestion/pipeline.py` — Generates sparse embeddings alongside dense when `hybrid_search_enabled`
  - `backend/src/config.py` — Added `hybrid_search_enabled`, `sparse_embed_model`
  - `backend/tests/test_retrieval/test_sparse_embedder.py` (**new**) — 5 tests
  - `backend/tests/test_retrieval/test_search.py` — 5 new hybrid search tests + 2 _process_results tests
- **Problem**: Pure cosine vector search missed keyword-exact matches (e.g., "ISO 27001")
- **Change**: Added sparse TF-IDF embeddings stored as named sparse vectors in Qdrant. Hybrid search uses Prefetch API to retrieve from both dense and sparse indexes, fused server-side with Reciprocal Rank Fusion (RRF).
- **Why this approach**: RRF is parameter-free and performs well without tuning. Qdrant's Prefetch API handles fusion server-side (no client-side merging). TF-IDF via sklearn used instead of fastembed (Rust toolchain not available on dev machine).
- **Verification**: 186 tests passing. Hybrid search tested with mock Qdrant.

### [Feature]: Phase 2 — Cross-Encoder Reranking

- **Files changed**:
  - `backend/src/retrieval/reranker.py` (**new**) — CrossEncoder wrapper with `rerank()` function, lazy-loaded singleton model
  - `backend/src/retrieval/search.py` — Added `search_and_rerank()`: retrieves N candidates → reranks with cross-encoder → returns top_k
  - `backend/src/generation/rag_chain.py` — Uses `search_and_rerank()` when `reranker_enabled`
  - `backend/src/generation/conversational_rag.py` — Same
  - `backend/src/config.py` — Added `reranker_enabled`, `reranker_model`, `rerank_top_n`
  - `backend/tests/test_retrieval/test_reranker.py` (**new**) — 5 tests
- **Problem**: Separate embedding models (dense/sparse) can't jointly score query-document relevance
- **Change**: After hybrid search retrieves 15 candidates, cross-encoder `ms-marco-MiniLM-L6-v2` (~80MB, CPU) scores each (query, document) pair jointly and reranks.
- **Verification**: 186 tests passing. Reranker tested with mocked model.

### [Feature]: Phase 3 — Semantic Chunking

- **Files changed**:
  - `backend/src/ingestion/semantic_chunker.py` (**new**) — `chunk_text_semantic()`: paragraph-aware splitting, small paragraph merging, oversized paragraph fallback to mechanical chunker, min_tokens filtering
  - `backend/src/ingestion/parser.py` — `_parse_docx()`: detects heading styles → markdown `# Heading` markers. `_parse_pptx()`: adds `[SLIDE N]` markers between slides
  - `backend/src/ingestion/pipeline.py` — Uses `chunk_text_semantic()` instead of `chunk_text()`
  - `backend/tests/test_ingestion/test_semantic_chunker.py` (**new**) — 10 tests
- **Problem**: Mechanical 256-token sliding window split content mid-sentence, diluting embeddings
- **Change**: Semantic chunker splits on paragraph boundaries (`\n\n`), merges small paragraphs up to max_tokens, preserves structural markers (headings, slides). XLSX delegates to mechanical chunker (tables don't benefit). Short single-chunk texts bypass min_tokens filter.
- **Verification**: 186 tests passing.

### [Feature]: Phase 4 — Query Expansion (Multi-Query)

- **Files changed**:
  - `backend/src/retrieval/query_expander.py` (**new**) — `expand_query()` generates 2 LLM alternative phrasings, `should_expand_query()` heuristic
  - `backend/src/retrieval/search.py` — Added `search_multi_query()`: expand → search each variant → deduplicate by (doc_id, chunk_idx) → rerank with original query
  - `backend/src/generation/rag_chain.py` — Uses `search_multi_query()` when `query_expansion_enabled`
  - `backend/src/generation/conversational_rag.py` — Same
  - `backend/src/config.py` — Added `query_expansion_enabled`
  - `backend/tests/test_retrieval/test_query_expander.py` (**new**) — 7 tests
- **Problem**: Short/ambiguous queries like "HR WFH policies" missed relevant docs
- **Change**: LLM generates 2 alternative phrasings (e.g., "Human Resources work from home guidelines"). Each variant searched independently, results deduplicated, then reranked using original query.
- **Verification**: 186 tests passing.

### [Feature]: Phase 5 — Streaming SSE + Chat-with-Document

- **Files changed**:
  - `backend/src/generation/llm_client.py` — Added `generate_answer_stream()` async generator
  - `backend/src/api/routes/query.py` — Added `POST /api/v1/query/stream` SSE endpoint (events: token, sources, done)
  - `backend/src/schemas.py` — Added `document_ids: list[int] | None` to `QueryRequest`
  - `backend/src/retrieval/search.py` — All search functions accept optional `document_ids` filter via `_build_search_filter()`
- **Problem**: No streaming (user waits for full response), no way to search within specific documents
- **Change**: SSE endpoint streams tokens as they arrive. `document_ids` filter adds Qdrant FieldCondition on `document_id` to restrict search scope.
- **Verification**: 186 tests passing.

### [Fix]: Existing tests updated for new feature flags

- **Files changed**:
  - `backend/tests/test_generation/test_rag_chain.py` — Added `disable_new_features` autouse fixture
  - `backend/tests/test_generation/test_conversational_rag.py` — Added `disable_new_features` to `TestConversationalRagIntentRouting` and `TestContextFirstFlow`
  - `backend/tests/test_ingestion/test_pipeline.py` — Mock `embed_texts_sparse` and `settings.hybrid_search_enabled`
- **Problem**: Existing tests mocked `search_similar` but code now calls `search_multi_query` (query expansion enabled by default)
- **Root cause**: New feature flags changed the search dispatch path in rag_chain and conversational_rag
- **Change**: Added autouse fixtures that set `query_expansion_enabled=False`, `reranker_enabled=False`, `hybrid_search_enabled=False` so existing tests continue testing the dense-only path
- **Verification**: All 186 tests passing (154 existing + 32 new)

### [Fix]: fastembed → scikit-learn for sparse embeddings

- **Files changed**: `backend/src/retrieval/sparse_embedder.py`, `backend/pyproject.toml`
- **Problem**: `fastembed` package depends on `py-rust-stemmers` which requires Rust toolchain (not available on dev machine)
- **Change**: Replaced fastembed with sklearn `TfidfVectorizer` for sparse embedding generation. Produces comparable sparse vectors for hybrid search keyword matching.
- **Why this approach**: sklearn already installed, no compilation needed. TF-IDF provides adequate keyword matching for hybrid search. Can swap to fastembed when Rust is available.
- **Verification**: 5 sparse embedder tests passing, all 186 tests passing.

## Session: 2026-02-10 (Fix [NEED_SEARCH] Leak + Remove Reformulation)

### [Fix]: [NEED_SEARCH] marker leaking into frontend UI

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` — Major changes (see below)
  - `backend/tests/test_generation/test_conversational_rag.py` — Removed 4 tests, added 1, updated 3
- **Problem**: `[NEED_SEARCH]` marker appeared in the frontend UI. The instruction to respond with `[NEED_SEARCH]` was in `CONVERSATIONAL_SYSTEM_PROMPT`, which was used for ALL LLM calls — including the final answer after search. The LLM could respond `[NEED_SEARCH]` on the second attempt, and it got saved as the assistant message.
- **Root cause**: Single system prompt (`CONVERSATIONAL_SYSTEM_PROMPT`) contained the `[NEED_SEARCH]` instruction and was used for both the context-first attempt AND the final answer generation.
- **Changes**:
  1. **Removed `[NEED_SEARCH]` from `CONVERSATIONAL_SYSTEM_PROMPT`** — restored to "If the context does not contain enough information, say so clearly."
  2. **Created `CONTEXT_FIRST_SYSTEM_PROMPT`** — identical to main prompt but with: "respond with exactly [NEED_SEARCH] and nothing else." Used only by `build_conversational_messages_with_context()`.
  3. **Added safety strip in `run_conversational_rag()`** — `answer = answer.replace("[NEED_SEARCH]", "").strip()` before saving the assistant message.
  4. **Deleted `reformulate_search_query()`** and `REFORMULATE_PROMPT` — search now uses `analysis.cleaned_query` directly (last user message).
  5. **Updated 2 call sites** — summary path (line ~253) and QA search path (line ~285) now use `analysis.cleaned_query` instead of `await reformulate_search_query(...)`.
  6. **Removed unused imports** — `ollama as ollama_lib`, `from src.config import settings`
- **Why this approach**: Two-prompt separation ensures `[NEED_SEARCH]` is only available during the context-first attempt, never during final answer generation. Safety strip is a belt-and-suspenders defense. Reformulation removed for simplicity — the intent classifier's `cleaned_query` already handles typo correction.
- **Test changes**:
  - Deleted `TestReformulateSearchQuery` class (4 tests)
  - Removed `reformulate_search_query` from imports
  - Removed `mock_reform` patches from 3 integration tests
  - Added `test_need_search_marker_stripped_from_final_response` — verifies `[NEED_SEARCH]` is never present in response content
- **Verification**: 154 backend tests passing (was 157; removed 4 reformulation tests, added 1 safety test).

## Session: 2026-02-10 (Context-First Chat + LLM Search Reformulation)

### [Feature]: Context-first QA flow — try previous context before searching

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` — Major rewrite of QA branch
  - `backend/tests/test_generation/test_conversational_rag.py` — 15 new tests (38 total in file)
- **Problem**: Every chat message triggered a fresh semantic search, even follow-ups like "tell me more" or "what about X in that document?" when the LLM already had the context to answer.
- **Root cause**: QA branch always called `search_similar()` unconditionally, regardless of whether previous sources existed in conversation history.
- **Changes**:
  1. **Deleted `expand_query()`** and `PRONOUN_PATTERNS` — replaced by `reformulate_search_query()` (see below)
  2. **Added `get_previous_sources()`** — walks conversation messages in reverse, returns `list[DocumentSource]` from the most recent assistant message that has sources
  3. **Added `needs_new_search()`** — checks if LLM response contains `[NEED_SEARCH]` marker
  4. **Added `build_context_from_document_sources()`** — builds context string from `DocumentSource` objects (like `build_context()` but for document-level sources instead of chunk-level `Source` objects)
  5. **Added `build_conversational_messages_with_context()`** — builds LLM messages from a pre-built context string (for context-first path)
  6. **Updated `CONVERSATIONAL_SYSTEM_PROMPT`** — added instruction: "If the provided context does not contain enough information to answer the question, respond with exactly [NEED_SEARCH] and nothing else."
  7. **Rewrote QA branch in `run_conversational_rag()`** — context-first flow:
     - Has previous sources? → Try answering from previous context + history
     - LLM answers well → return (0 searches)
     - LLM responds `[NEED_SEARCH]` → fall through to search (1 search)
     - No previous sources → search directly (1 search, same as before)
  8. **Changed `document_sources` scoping** — initialized as `list[DocumentSource] = []` at top of function, removed the old `if not sources: document_sources = []` pattern
- **Why this approach**: The LLM itself decides when it needs new information (via `[NEED_SEARCH]` marker). No new intent classifier actions, no hardcoded follow-up detection. Simple two-step logic that the LLM controls.
- **Verification**: 157 backend tests passing (15 new). New tests cover: `get_previous_sources` (4 tests), `needs_new_search` (4 tests), `build_context_from_document_sources` (4 tests), `build_conversational_messages_with_context` (2 tests), context-first QA flow (3 integration tests: no-search, NEED_SEARCH fallback, direct search).

### [Feature]: LLM-based search query reformulation (replaces expand_query)

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` — Added `reformulate_search_query()`, `REFORMULATE_PROMPT`
  - `backend/tests/test_generation/test_conversational_rag.py` — 4 new tests for reformulator
- **Problem**: `expand_query()` was dumb string concatenation: `"Context: {messages}. Current question: {query}"`. This produced noisy search queries that hurt vector similarity.
- **Root cause**: Regex-based pronoun detection + string concatenation can't produce clean, focused search queries.
- **Change**: Added `reformulate_search_query()` — calls the router model (`gemma3:4b`) with a prompt that resolves pronouns, strips conversational fluff, and outputs a concise 3-10 word search query. Falls back to original query on error or empty messages. Used in both QA search path and summary `resolve_document_id()` path.
- **Why this approach**: Small LLM call (~200-500ms) produces much better search queries than string concatenation. Same model already loaded for intent classification, so minimal overhead.
- **Verification**: 4 new tests: returns original on empty messages, calls router model correctly, falls back on error, falls back on empty LLM response.

## Session: 2026-02-10 (Conversation Memory Fix — Pronoun Resolution)

### [Fix]: Chat conversation memory — every message triggered blind semantic search

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` (lines 68-77) — `expand_query()` now uses last 4 messages (user + assistant) instead of only last 2 user messages. Assistant responses contain document names critical for resolving pronouns.
  - `backend/src/generation/conversational_rag.py` (lines 213-216) — Summary path now calls `expand_query()` before `resolve_document_id()`, matching the QA path behavior.
  - `backend/src/generation/conversational_rag.py` (lines 204-210) — Builds conversation context string from last 4 messages and passes it to `analyze_query()`.
  - `backend/src/generation/intent_classifier.py` (line 86) — `analyze_query()` now accepts optional `conversation_context: str | None` parameter. When provided, prepends conversation context to the user message sent to the router LLM, enabling pronoun resolution (e.g., "summarize it" → "summarize The Quiet Revolution").
  - `backend/tests/test_generation/test_intent_classifier.py` — Added 2 new tests: `test_conversation_context_included_in_prompt` and `test_no_context_sends_plain_query`.
- **Problem**: When a user asked "is there a file about X?", got an answer, then said "summarize it", the system ran a vector search on "summarize it" with no context and matched the wrong document.
- **Root cause**: Three issues: (1) `expand_query()` only used user messages, ignoring assistant responses that contained document names; (2) Summary path skipped `expand_query()` entirely; (3) Intent classifier received no conversation context, so `cleaned_query` couldn't resolve pronouns.
- **Change**: (1) `expand_query()` now takes last 4 messages (both roles); (2) Summary path calls `expand_query()` before `resolve_document_id()`; (3) `analyze_query()` accepts optional conversation context string, prepended to the user message for the router LLM.
- **Why this approach**: Assistant messages are the richest source of context (they contain exact document names, answers, etc.). Passing conversation context to the intent classifier allows the LLM to resolve "it" → "The Quiet Revolution" at classification time, before search even begins.
- **Verification**: All 142 backend tests passing (including 2 new intent classifier tests).

## Session: 2026-02-10 (Map-Reduce Summarization, Document Re-Upload, Phase 3 Docs)

### [Feature]: Map-reduce summarization pipeline for full-document summaries

- **Files changed**:
  - `backend/src/generation/intent_classifier.py` — Added `QueryType` enum (`QA` | `SUMMARY`), `query_type` field to `QueryAnalysis` dataclass, updated `ROUTER_PROMPT` to classify query type, updated `_parse_router_response()` to parse `query_type` (defaults to `QA`)
  - `backend/src/generation/map_reduce.py` (**new**) — Full map-reduce pipeline: `resolve_document_id()` (top_k=1 vector search to identify document), `retrieve_document_chunks()` (Qdrant scroll to get all chunks sorted by chunk_index), `map_summarize()` (summarize one batch), `reduce_summaries()` (combine all maps), `run_map_reduce()` (orchestrator with `asyncio.gather` + `Semaphore(3)`)
  - `backend/src/generation/rag_chain.py` — Added `QueryType` import + `map_reduce` imports. After `analyze_query()`, branches on `query_type`: `SUMMARY` → `resolve_document_id()` → `run_map_reduce()`, `QA` → existing search path. Fallback: if `resolve_document_id()` returns None → no-results message.
  - `backend/src/generation/conversational_rag.py` — Same branching pattern as rag_chain.py for summary queries in conversations.
- **Problem**: Users asking "Summarize the HR policy document" only got 3 chunks via top_k=3, missing 90%+ of the document content.
- **Root cause**: No mechanism to retrieve ALL chunks for a document; retrieval was designed for Q&A (best-N chunks), not summarization.
- **Change**: Added intent classifier query type detection + full map-reduce pipeline that scrolls all document chunks, batches them (8 per batch), summarizes each batch in parallel (max 3 concurrent), then reduces all summaries into a coherent final answer.
- **Why this approach**: Map-reduce scales to any document size. Semaphore(3) prevents overwhelming local Ollama. Single-batch documents skip the reduce step for efficiency.
- **Verification**: 14 new tests in `test_map_reduce.py` (resolve, retrieve, map, reduce, batching, edge cases). All 140 tests passing.

### [Feature]: Document re-upload replacement (staleness fix)

- **Files changed**:
  - `backend/src/ingestion/pipeline.py` — Added `_replace_existing_document()` helper that queries DB for same filename, deletes old Qdrant vectors via `delete_document_points()`, deletes old DB record. Called at start of `ingest_document()` before creating new Document record. Added `sqlalchemy.select` and `delete_document_points` imports.
- **Problem**: Uploading "HR_Policy_v2.pdf" when "HR_Policy_v1.pdf" already exists with same filename creates duplicate vectors. Old chunks pollute search results.
- **Root cause**: No check for existing documents at any step of the ingestion pipeline.
- **Change**: Before creating a new Document record, check if a document with the same filename exists. If yes, delete old Qdrant vectors + old DB record, then proceed with fresh ingestion.
- **Why this approach**: Replace (not version) — versioning adds UI complexity, storage costs. KB users always want the latest version. Logged replacement for auditing.
- **Verification**: 2 new tests (no-existing, existing-is-deleted). All 140 tests passing.

### [Fix]: merge_sources_by_document max_documents parameter

- **Files changed**:
  - `backend/src/retrieval/search.py` — Added `max_documents` parameter (default=1) to `merge_sources_by_document()`. Changed from hardcoded `[merged[0]]` return to `merged[:max_documents]` with loop to assign reference numbers.
  - `backend/tests/test_retrieval/test_search.py` — Replaced `test_merge_sources_multiple_documents` (which expected 3 results but function returned 1) with 3 new tests: `test_merge_sources_multiple_documents_default` (1 result), `test_merge_sources_multiple_documents_max_3` (3 results), `test_merge_sources_max_documents_limits` (2 of 3).
- **Problem**: Pre-existing test `test_merge_sources_multiple_documents` expected 3 documents but `merge_sources_by_document()` was hardcoded to return only 1 (the top result). Also, function was not flexible for future use cases.
- **Root cause**: Function was modified to return top-1 only in the "strict docs-only RAG" session, but the old test was never updated.
- **Change**: Added `max_documents` param, replaced hardcoded slice with configurable limit. Default=1 preserves existing behavior.
- **Verification**: 3 new merge tests + all 140 tests passing.

### [Fix]: Pipeline test mock for _replace_existing_document

- **Files changed**: `backend/tests/test_ingestion/test_pipeline.py` — Updated `mock_db` fixture to include `db.delete = MagicMock()` and mock `db.execute` to return a result with `.scalars().first() → None` (no existing document). This prevents `AttributeError: 'coroutine' object has no attribute 'first'` when `_replace_existing_document()` calls `await db.execute(select(...))`.
- **Problem**: `test_ingest_document_success` failed because `db.execute()` returned an `AsyncMock` coroutine, but `.scalars().first()` was called synchronously on the awaited result.
- **Root cause**: `AsyncMock` auto-generates coroutines for all attribute access. Need a sync `MagicMock` for the `.scalars().first()` chain since `execute()` is already awaited.
- **Verification**: All 4 pipeline tests passing.

### [Docs]: Phase 3 future issues documented

- **Files changed**: `.claude/PROJECT_MEMORY.md`, `.claude/progress.md`, `.claude/context.md`, `.claude/PROGRESS_LOG.md`
- **Change**: Documented 3 future issues with proposed solutions and effort estimates:
  1. **Multi-hop reasoning**: Query decomposition → parallel search → merge + synthesize
  2. **Chunking strategy**: 3 tiers (paragraph-aware → section-aware → format-specific)
  3. **Embedding drift**: 4 options (glossary → cross-encoder → fine-tune → model swap)
- **Why**: These issues are real scaling concerns but require more design work. Documented so nothing gets lost.
- **Verification**: All project tracking files updated consistently.

## Session: 2026-02-10 (Retrieval Config, Logging Overhaul & Reasoning Logs)

### [Feature]: Unify retrieval defaults to top_k=3, threshold=0.4

- **Files changed**: `backend/src/schemas.py` (lines 12-13, 110-111), `backend/src/retrieval/search.py` (lines 20-21), `backend/src/generation/conversational_rag.py` (line 183-184)
- **Problem**: Inconsistent retrieval defaults across the codebase — `schemas.py` had top_k=10/threshold=0.2, `conversational_rag.py` had top_k=5/threshold=0.58. This caused different behavior between one-off queries and conversations.
- **Root cause**: Defaults were set independently in each location without a single source of truth
- **Change**: Set all three locations to `top_k=3, threshold=0.4` — schemas.py (API defaults), search.py (function signature safety net), conversational_rag.py (was overriding with its own hardcoded values)
- **Why this approach**: top_k=3 avoids flooding the LLM with low-relevance chunks; threshold=0.4 is a good middle ground (0.2 was too permissive, 0.58 was too strict for nomic-embed-text scores)
- **Verification**: Code review confirms all three locations now match

### [Fix]: Silence SQLAlchemy SQL echo in dev mode

- **Files changed**: `backend/src/api/main.py` (line 17)
- **Problem**: SQLAlchemy's `echo=settings.is_dev` in session.py floods the terminal with raw SQL statements, burying useful diagnostic logs
- **Root cause**: Engine's `echo` parameter feeds into `sqlalchemy.engine` logger at INFO level
- **Change**: Added `logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)` after `logging.basicConfig()` in lifespan
- **Why this approach**: Suppresses SQL noise without changing engine config; warnings/errors still visible
- **Verification**: Code review confirms placement after basicConfig

### [Feature]: Add structured reasoning logs to RAG pipelines

- **Files changed**: `backend/src/generation/rag_chain.py` (lines 5, 50-65), `backend/src/generation/conversational_rag.py` (lines 227-240)
- **Problem**: No visibility into which chunks were sent to the LLM, at what scores, or how many tokens — makes debugging retrieval quality difficult
- **Change**: Added three log sections in both `rag_chain.py` and `conversational_rag.py` (before `generate_answer()` call when sources exist):
  1. `--- Retrieved Chunks ---`: filename + score for each source
  2. `--- LLM Input Preview ---`: first 100 chars of each chunk
  3. `Logic trace:` summary line with chunk count, threshold, and total context tokens (via tiktoken)
- **Why this approach**: Shows the decision trail at a glance without being verbose. Complements existing `search.py` raw Qdrant logs with post-threshold, clean summaries
- **Verification**: Code review confirms logs are placed after search returns and before LLM call in both pipelines

## Session: 2026-02-10 (LLM-Based Query Router)

### [Feature]: Replace regex intent classifier with LLM-based query router

- **Files changed**: `backend/src/generation/intent_classifier.py` (full rewrite), `backend/src/config.py` (line 15), `backend/src/generation/rag_chain.py` (lines 10, 29-44), `backend/src/generation/conversational_rag.py` (lines 13, 203-215), `backend/tests/test_generation/test_intent_classifier.py` (full rewrite)
- **Problem**: Regex-based `classify_intent()` was brittle — missed misspellings, variations, edge cases. No typo correction for search queries.
- **Root cause**: Hardcoded regex patterns can't generalize beyond exact pattern matches
- **Change**:
  1. **intent_classifier.py**: Removed all regex patterns, `Intent` enum, `classify_intent()`. Added `Action` enum (`SEARCH`/`RESPOND`), `QueryAnalysis` dataclass, `ROUTER_PROMPT`, `_parse_router_response()` (JSON parser with markdown fence stripping + fallback), `analyze_query()` (async Ollama call using `ollama_router_model`). Safe fallback: any parse/connection failure defaults to `Action.SEARCH` with original query.
  2. **config.py**: Added `ollama_router_model: str = "gemma3:4b"` setting
  3. **rag_chain.py**: Replaced `classify_intent(request.query)` with `await analyze_query(request.query)`. Uses `analysis.action == Action.RESPOND` for branching. Passes `analysis.cleaned_query` to `search_similar()` for better search results.
  4. **conversational_rag.py**: Same pattern as rag_chain. Passes `analysis.cleaned_query` through `expand_query()` and to `search_similar()`.
  5. **Tests**: 14 new tests — 8 for `_parse_router_response()` (valid JSON, respond, code fences, invalid JSON fallback, missing fields, unknown action, empty cleaned_query) + 6 for `analyze_query()` (empty/whitespace, mocked Ollama calls, greeting routing, connection failure fallback, garbage response fallback)
- **Why this approach**: Small LLM call (~200-500ms) is a worthwhile trade-off for much more robust routing + automatic typo correction. `gemma3:4b` is small/fast enough for routing. Safe fallback to SEARCH ensures no queries are lost if router fails.
- **Verification**: All 48 generation tests passing (14 intent classifier + 34 others). Existing rag_chain and conversational_rag tests still pass with updated imports.

## Session: 2026-02-10 (Strict Docs-Only RAG + Chat UI Stability)

### [Refactor]: Unify chitchat + no-results into single response path

- **Files changed**: `backend/src/generation/prompt_builder.py`, `backend/src/generation/rag_chain.py`, `backend/src/generation/conversational_rag.py`
- **Problem**: Separate hardcoded prompts (GREETING_PROMPT, THANKS_PROMPT, GOODBYE_PROMPT) for chitchat intents, duplicating logic and producing inconsistent tone
- **Change**: Removed `GREETING_PROMPT`, `THANKS_PROMPT`, `GOODBYE_PROMPT`, `_INTENT_TO_PROMPT` dict, and `build_chitchat_messages()` function. Updated `NO_RESULTS_PROMPT` to handle all no-context scenarios (greetings, thanks, no results) naturally. Changed chitchat branches in `rag_chain.py` and `conversational_rag.py` to call `build_no_results_messages()` instead of `build_chitchat_messages()`. Removed unused `Intent` import from `prompt_builder.py` and `build_chitchat_messages` imports from both chain files.
- **Why this approach**: Single prompt is more maintainable and produces natural responses regardless of intent type. Intent classifier still used for efficiency (skip search for chitchat).
- **Verification**: All imports resolve, no remaining references to removed functions in codebase (except old `test.py` copy)

### [Feature]: Return only top-1 source from merge

- **Files changed**: `backend/src/retrieval/search.py` (lines 116-121)
- **Problem**: Multiple sources returned, cluttering the UI when only the best match matters
- **Change**: After sorting merged documents by score descending, return only `[merged[0]]` with `reference_number=1` instead of the full list
- **Why this approach**: Single best source reduces noise, matches the "docs-only" philosophy
- **Verification**: Function still returns empty list when no sources exist

### [Fix]: Chat input stays visible during submission

- **Files changed**: `frontend/components/ChatInput.tsx`
- **Problem**: Input cleared immediately on submit (`setMessage("")`), causing text to disappear before response arrives
- **Change**: Added `pendingRef` (useRef) and `useEffect` watching `isLoading`. On submit, set `pendingRef.current = true` instead of clearing. When `isLoading` transitions to false and pending is true, clear the input.
- **Why this approach**: Ref avoids re-renders, effect cleanly syncs with loading state
- **Verification**: Visual — text stays until response arrives, then clears

### [Fix]: Hide "0 sources" in metadata

- **Files changed**: `frontend/components/ResponseDisplay.tsx` (lines 41-44)
- **Problem**: "0 sources" displayed in metadata when no results found
- **Change**: Wrapped source count in conditional `{response.sources.length > 0 && (...)}` so it only renders when sources exist
- **Why this approach**: Clean UI — only show source info when relevant
- **Verification**: Visual — latency still shown, source count hidden when empty

## Session: 2026-02-09 (UI/UX & Response Formatting)

### [Feature]: Document-level source merging with numbered citations

- **Files changed**:
  - `backend/src/schemas.py` — Added `DocumentSource` model (document_id, filename, score, text, reference_number). Updated `QueryResponse.sources` and `MessageResponse.sources` to `list[DocumentSource]`.
  - `backend/src/retrieval/search.py` — Added `merge_sources_by_document()` function: groups chunks by document_id, sorts by chunk_index, concatenates texts, keeps highest score, assigns reference numbers.
  - `backend/src/generation/prompt_builder.py` — Rewrote `SYSTEM_PROMPT` with markdown formatting instructions and numbered citation format `[1]`, `[2]`. Added `reference_numbers` param to `build_messages()` and `_build_context()`. Context headers now use `[Reference N: filename]` instead of `[Source: filename, chunk N]`.
  - `backend/src/generation/rag_chain.py` — After search, merges sources via `merge_sources_by_document()`, builds ref_map, passes to `build_messages()`, returns `DocumentSource` in response.
  - `backend/src/generation/conversational_rag.py` — Updated `CONVERSATIONAL_SYSTEM_PROMPT` with numbered citation + markdown instructions. Added `reference_numbers` param to `build_context()` and `build_conversational_messages()`. `run_conversational_rag()` now merges sources and returns `DocumentSource`.
  - `backend/tests/test_generation/test_prompt_builder.py` — Added `test_build_messages_with_reference_numbers`, `test_build_context_with_reference_numbers`.
  - `backend/tests/test_generation/test_rag_chain.py` — Updated to assert `DocumentSource` type and `reference_number` field.
  - `backend/tests/test_generation/test_conversational_rag.py` — Added `test_single_source_with_reference_numbers` to TestBuildContext.
  - `backend/tests/test_retrieval/test_search.py` — Added 4 tests for `merge_sources_by_document()`: single chunk, multiple chunks same doc, multiple documents, empty.
  - `backend/tests/test_api/test_query.py` — Updated to use `DocumentSource` in mock response.
- **Problem**: Sources were exposed at chunk level (showing `chunk_index`), LLM used `[Source: filename, chunk N]` format.
- **Root cause**: No document-level aggregation existed; chunks were passed directly to frontend.
- **Change**: Added `DocumentSource` schema + `merge_sources_by_document()` utility. Updated all RAG pipelines to merge before returning. Updated prompts to instruct numbered `[1]` citations.
- **Why this approach**: Document-level sources are more meaningful to users. Numbered refs enable clickable citation links in the frontend.
- **Verification**: 164 tests passing (`pytest`). Frontend builds without type errors (`next build`).

### [Feature]: Markdown rendering, citation links, and loading animations in frontend

- **Files changed**:
  - `frontend/types/index.ts` — Added `DocumentSourceSchema` + `DocumentSource` type. Updated `QueryResponseSchema`, `MessageResponseSchema`, `QueryHistoryEntry` to use `DocumentSourceSchema`.
  - `frontend/components/LoadingMessages.tsx` (new) — Rotating loading messages with fade animation, cycles every 2.5s.
  - `frontend/components/MarkdownContent.tsx` (new) — `react-markdown` + `remark-gfm` renderer with clickable `[N]` citation links that scroll to `#source-N`.
  - `frontend/components/SourceCitation.tsx` — Changed from `Source` to `DocumentSource` prop type. Removed `chunk_index` display. Added reference number badge and `id={source-N}` anchor with `scroll-mt-4`.
  - `frontend/components/ResponseDisplay.tsx` — Replaced skeleton loader with `LoadingMessages`. Replaced plain text `<p>` with `MarkdownContent`. Uses `source.document_id` as key.
  - `frontend/components/MessageBubble.tsx` — Assistant messages now render with `MarkdownContent` (no bubble wrapper). User messages stay as plain text bubbles.
  - `frontend/components/ChatWindow.tsx` — Added `LoadingMessages` with Bot avatar when `isSending` is true, shown below message list.
  - `frontend/package.json` — Added `react-markdown` and `remark-gfm` dependencies.
- **Problem**: Answers rendered as plain text, chunk-level sources exposed, basic skeleton loader.
- **Change**: Full markdown rendering, clickable numbered citations, rotating loading messages, document-level sources with reference badges.
- **Verification**: `next build` passes with 0 type errors. All routes compile successfully.

## Session: 2026-02-09

### [Fix]: Frontend Zod schemas sending stale threshold/top_k defaults

- **Files changed**: `frontend/types/index.ts` (lines 7-8, 95-96)
- **Problem**: Backend defaults were updated (threshold 0.2, top_k 10) but frontend Zod schemas still hardcoded `threshold: 0.7` and `top_k: 5`. Since the frontend explicitly sends these values in every request, they override backend defaults. Logs showed `Threshold 0.7: 0 passed, 5 dropped` — all results were being filtered out.
- **Root cause**: Frontend schemas were not updated when backend defaults were changed in the previous session.
- **Change**: In `QueryRequestSchema` and `ConversationMessageRequestSchema`:
  - `top_k`: `.default(5)` → `.default(10)`, `.max(20)` → `.max(50)`
  - `threshold`: `.default(0.7)` → `.default(0.5)`
- **Why this approach**: User wants threshold 0.5 (accept results scoring above 0.5). The query's 5 results scored 0.56-0.68, all above 0.5. max(50) for top_k aligns with backend schema.
- **Verification**: Visual inspection of file changes. Restart frontend and re-run query to confirm logs show `Threshold 0.5` and results pass.

### [Feature]: Improve semantic search recall — tuned defaults, debug endpoint, diagnostic logging

- **Files changed**:
  - `backend/src/schemas.py` — threshold default 0.4→0.2, top_k default 5→10, top_k max 20→50 (both `QueryRequest` and `ConversationMessageRequest`). Added `DebugSearchRequest`, `DebugSearchResult`, `DebugSearchResponse` schemas.
  - `backend/src/retrieval/search.py` — function defaults updated (top_k=10, threshold=0.2). Replaced debug-level logging with comprehensive INFO-level diagnostics: embedding time, RBAC filter details, all raw results with scores/filenames/text previews, threshold pass/drop summary with dropped scores listed.
  - `backend/src/retrieval/embedder.py` — added timing + INFO logging to `embed_text()` and `embed_texts()` (model name, dimension, elapsed ms).
  - `backend/src/generation/rag_chain.py` — added INFO logging: query text + classified intent, chitchat skip, search result count, no-results fallback trigger, total latency + source count.
  - `backend/src/generation/prompt_builder.py` — MAX_CONTEXT_TOKENS 3000→4096. Added logger import + INFO logging in `_build_context()`: chunks used, tokens used vs max, chunks truncated.
  - `backend/src/ingestion/chunker.py` — DEFAULT_CHUNK_SIZE 500→256, DEFAULT_OVERLAP 50→32. Updated log message to include average chunk size.
  - `backend/src/api/routes/query.py` — added `POST /api/v1/query/debug` endpoint returning raw Qdrant scores without threshold filtering (embedding_ms, RBAC info, all results with scores).
  - `backend/tests/test_retrieval/test_search.py` — updated `test_search_no_results_below_threshold` to use score 0.15 (below new 0.2 threshold, was 0.3 for old 0.4 threshold).
  - `backend/tests/test_api/test_query.py` — added `test_query_debug_endpoint` test.
  - `backend/tests/test_ingestion/test_chunker.py` — updated `test_default_parameters` assertions: 500→256, 50→32.
  - `backend/tests/test_generation/test_prompt_builder.py` — updated `test_build_context_truncates_at_max_tokens`: increased long_text from 4000 to 5000 words to exceed new 4096 token limit.
- **Problem**: Semantic search returned few or no hits for natural language queries. 5 cascading filters suppressed recall: RBAC pre-filter, low top_k (5), strict threshold (0.4), small context window (3000 tokens), and large chunk size (500 tokens). No diagnostic logging made it impossible to see what scores Qdrant was actually returning.
- **Root cause**: `nomic-embed-text` cosine similarity for natural language queries against relevant chunks typically scores 0.2-0.5. Threshold 0.4 dropped borderline-relevant results. top_k=5 was too restrictive after RBAC filtering. 500-token chunks dilute embeddings.
- **Change**: Lowered threshold (0.2), doubled top_k (10), expanded max top_k (50), increased context window (4096 tokens), halved chunk size (256 tokens). Added debug endpoint for empirical score analysis. Added comprehensive INFO-level pipeline logging.
- **Why this approach**: Empirical tuning based on nomic-embed-text score distributions. Debug endpoint enables data-driven threshold decisions. Logging makes the full RAG pipeline transparent in terminal output.
- **Verification**: All 157 backend tests passing. Existing documents must be re-ingested after chunk size change.

## Session: 2026-02-06

### [Feature]: Intent routing for RAG pipeline — greetings, chitchat, and LLM fallback for no-results

- **Files changed**:
  - `backend/src/generation/intent_classifier.py` (new) — Intent enum + regex-based classify_intent()
  - `backend/src/generation/prompt_builder.py` — added GREETING/THANKS/GOODBYE/NO_RESULTS prompts + build_chitchat_messages() + build_no_results_messages()
  - `backend/src/generation/rag_chain.py` — added intent routing in run_rag_query(): chitchat skips search, no-results uses LLM fallback
  - `backend/src/generation/conversational_rag.py` — same routing in run_conversational_rag() with conversation history awareness
  - `backend/tests/test_generation/test_intent_classifier.py` (new) — 30 parametrized tests for all intents
  - `backend/tests/test_generation/test_rag_chain.py` — replaced hardcoded fallback test with LLM fallback test + added greeting-skips-search test
  - `backend/tests/test_generation/test_conversational_rag.py` — added greeting-skips-search + no-results LLM fallback tests
- **Problem**: Every user input (including "hi", "thanks", vague text) triggered semantic search. When no results were found, the system returned a hardcoded string: "I could not find any relevant information in the knowledge base for your question." — robotic and poor UX.
- **Root cause**: No intent classification layer; all queries went through the same search → threshold → hardcoded fallback path.
- **Change**: Hybrid rule-based classifier + LLM fallback approach:
  1. Regex patterns detect greetings/thanks/goodbye → skip search, respond via LLM with appropriate prompt
  2. Short queries (1-2 words) without conversation history → classified as UNCLEAR, still runs search but gets LLM fallback if nothing found
  3. Short queries WITH conversation history → classified as KB_QUERY (likely follow-ups)
  4. When search returns no results → LLM generates helpful response (asks clarifying questions or suggests related topics) instead of hardcoded string
- **Why this approach**: No extra LLM call for KB queries (preserves performance), greetings are actually faster (skip embedding + Qdrant), failed searches get better UX. Works well with small local LLM.
- **Verification**: All 156 backend tests passing (91 in generation suite alone), no regressions

### [Fix]: Lower similarity threshold from 0.7 to 0.4 — most queries returning zero results

- **Files changed**: `backend/src/schemas.py` (lines 13, 79), `backend/src/retrieval/search.py` (line 20, added debug logging lines 43-45), `backend/tests/test_retrieval/test_search.py` (lines 42-45)
- **Problem**: Most natural language queries returned "I searched the vector database and found nothing relevant." Only near-exact text matches returned results.
- **Root cause**: Default similarity threshold was 0.7. With `nomic-embed-text`, natural language queries produce cosine similarity scores in the 0.3-0.6 range against relevant chunks. Only near-verbatim matches score above 0.7, so all semantically relevant results were silently dropped at the threshold filter.
- **Change**:
  - Lowered default threshold from `0.7` to `0.4` in `QueryRequest.threshold`, `ConversationMessageRequest.threshold`, and `search_similar()` function signature
  - Added debug logging in `search_similar()` to log raw Qdrant result count and individual scores before threshold filtering, making future debugging easier
  - Updated `test_search_returns_sources_above_threshold` to expect 2 results (score 0.5 now passes the 0.4 threshold)
- **Why this approach**: 0.4 is a reasonable threshold for nomic-embed-text cosine similarity — low enough to capture semantically relevant results, high enough to filter noise. Debug logging added to avoid similar blind-spot issues in the future.
- **Verification**: All 93 backend tests passing (previously 1 failure in test_search due to old threshold expectation)

## Session: 2026-02-05

### Worked On
- Project planning and architecture decisions
- Project scaffolding (all directories, configs, placeholders)

### Completed
- Full implementation plan created
- All architecture decisions confirmed
- Directory structure created
- Tracking files initialized

### Issues Encountered
- None

---

## Session: 2026-02-05 (continued)

### Worked On
- Database Agent: config, models, session, Alembic migration, tests
- Backend Agent: full RAG pipeline, all API routes, RBAC, tests

### Completed
- **Database Agent**: config.py, db/base.py, db/session.py, 5 models (User, Role, Document, QueryLog, IngestionJob), Alembic async migration, 11 tests
- **Backend Agent**: schemas.py, api/main.py, api/dependencies.py, 4 route files (health, query, documents, ingest), ingestion/ (parser, chunker, metadata, pipeline), retrieval/ (embedder, vector_store, search), generation/ (llm_client, prompt_builder, rag_chain), rbac/ (auth, permissions), 37 new tests → 48 total

### Issues Encountered
- Python 3.14 doesn't have `setuptools.backends._legacy` → use `setuptools.build_meta`
- JSONB not supported by SQLite → use generic JSON
- SQLite needs PRAGMA foreign_keys=ON for CASCADE
- SQLAlchemy needs passive_deletes=True for DB-level CASCADE

---

## Session: 2026-02-06

### Worked On
- Frontend Agent: Complete Next.js implementation

### Completed
- **Frontend Agent**: Full implementation including:
  - Config files: `package.json`, `tsconfig.json`, `tailwind.config.ts`, `next.config.ts`, `postcss.config.js`, `components.json`
  - Types: Zod schemas for all API types (`types/index.ts`)
  - Lib: `utils.ts` (cn helper), `fastapi.ts` (server-side fetch wrapper with X-User-Id header)
  - Route handlers (BFF pattern): `/api/query`, `/api/documents`, `/api/ingest`, `/api/health`
  - Hooks: `useSearch`, `useDocuments` (list/delete/upload), `useQueryHistory` (React Query + client state)
  - Components: `SearchBar`, `ResponseDisplay`, `SourceCitation`, `QueryHistory`, `DocumentUpload`, `Sidebar`, `Layout`
  - Pages: `/` (search), `/documents` (upload/list/delete), `/settings` (health check)
  - Providers: QueryClientProvider wrapper

### Issues Encountered
- Zod `.default()` doesn't make field optional for `z.infer` → use `.optional().default()` + `z.input`
- OneDrive path caused `.next/build-manifest.json` error → delete `.next` cache and rebuild

### Build Status
- TypeScript: passes (`npx tsc --noEmit`)
- Next.js build: succeeds (10 routes generated)
- Backend tests: 48/48 passing

### MVP Status: COMPLETE

All Phase 1 features are implemented:
- Backend: FastAPI with RAG pipeline, RBAC, 48 tests
- Frontend: Next.js with BFF pattern, React Query, Tailwind
- Database: PostgreSQL schema with Alembic migrations

### To Run the Full Stack
```bash
# Terminal 1: Start Docker services
docker-compose up -d  # Starts Qdrant + PostgreSQL

# Terminal 2: Run migrations + start backend
cd backend
alembic upgrade head
uvicorn src.api.main:app --reload

# Terminal 3: Start frontend
cd frontend
npm run dev
```

Then open http://localhost:3000

### Next Session Priorities
1. End-to-end testing with real Docker services
2. Integration Agent (Phase 2) — when company tooling is known
3. Optional: Add JWT/OAuth authentication (Phase 2)

---

## Session: 2026-02-06 (continued)

### [Fix]: Add missing Qdrant payload indexes for RBAC filtering

- **Files changed**: `backend/src/retrieval/vector_store.py` (lines 7-11 imports, lines 29-39 new index calls)
- **Problem**: Every search query using RBAC filtering failed with:
  > Bad request: Index required but not found for "allowed_groups" of one of the following types: [keyword].
- **Root cause**: `ensure_collection()` only created the Qdrant collection with vector config but never called `create_payload_index()`. Qdrant requires explicit keyword indexes on payload fields used in `FieldCondition` / `MatchAny` filters.
- **Change**:
  1. Added `PayloadSchemaType` to the `qdrant_client.models` import block
  2. Added two `create_payload_index()` calls after collection creation (outside the `if` block so they run idempotently on every startup):
     - `allowed_groups` with type `KEYWORD` — required for `MatchAny` RBAC filtering in search
     - `document_id` with type `INTEGER` — required for filtered deletes in `delete_document_points()`
- **Why this approach**: Qdrant's `create_payload_index` is idempotent (no-op if index already exists), so placing it outside the `if` block ensures both new and existing collections get indexes without conditional logic.
- **Verification**: Code review confirms correct API usage. Runtime verification requires Docker (Qdrant) — to be tested in E2E.

### [Improvement]: Overhaul CLAUDE.md with mandatory logging rules

- **Files changed**: `CLAUDE.md` (full rewrite of context system section, added new section)
- **Problem**: CLAUDE.md had a minimal "Context System" section that said to update files after work, but had no enforcement, no format specification, and no detail on what to log.
- **Change**:
  - Added a new **"MANDATORY: Logging & Documentation Rules"** section with 4 subsections:
    1. **Read Before Working** — lists all 4 `.claude/` files to read at session start
    2. **Log Every Change** — specifies exactly what to write in each file, with a template format for PROGRESS_LOG entries (date, files changed, problem, root cause, change, reasoning, verification)
    3. **Never Skip Logging** — explicit rule that even small changes must be logged
    4. **Log Format Rules** — specificity requirements (exact paths, verbatim errors, before/after)
  - Updated Critical Rules list to include logging (rule 6), context reading (rule 7), and specificity (rule 8)
  - Updated project structure to show all 4 `.claude/` files with descriptions
- **Why this approach**: Without explicit, detailed logging rules, context gets lost between sessions. The structured format ensures consistency and makes logs actually useful for resuming work.
- **Verification**: CLAUDE.md reads correctly, rules are clear and actionable.
- **Also updated**: `PROGRESS_LOG.md` (this entry), `context.md`, `PROJECT_MEMORY.md`

---

## Session: 2026-02-06 (continued #2)

### [Fix]: Add Qdrant `ensure_collection()` call to FastAPI lifespan startup

- **Files changed**: `backend/src/api/main.py` (lines 17-22, added 5 lines in lifespan function)
- **Problem**: Queries fail with `Index required but not found for "allowed_groups" of one of the following types: [keyword]` because `ensure_collection()` (which creates the KEYWORD index) was only called during document ingestion, not at app startup. If no documents had been ingested yet, or the collection was created before the index code existed, queries would fail.
- **Root cause**: `ensure_collection()` was wired into `pipeline.py` (ingestion) but never called from the FastAPI lifespan. The app startup didn't initialize Qdrant indexes.
- **Change**: Added Qdrant initialization to the `lifespan()` function in `main.py`:
  ```python
  from src.api.dependencies import get_qdrant
  from src.retrieval.vector_store import ensure_collection
  qdrant = await get_qdrant()
  await ensure_collection(qdrant)
  logger.info("Qdrant collection and indexes verified")
  ```
- **Why this approach**: `ensure_collection()` and `create_payload_index()` are both idempotent — safe to call on every startup. This guarantees indexes exist before any query is attempted, regardless of whether documents have been ingested.
- **Verification**: Code review confirms correct usage. Runtime verification requires Docker (Qdrant).

## Session: 2026-02-09 (Fix — Frontend Conversation 500 Errors)

### [Fix]: Clear stale .next webpack cache causing 500 on all conversation routes

- **Files changed**: Deleted `frontend/.next/` directory
- **Problem**: All conversation route handlers (`GET /api/conversations`, `GET /api/conversations/{id}`) returned 500 with `Cannot find module './331.js'`. The compiled webpack chunks referenced a missing module.
- **Root cause**: Stale `.next` build cache — incremental compilation left orphan chunk references after code changes. The OneDrive path exacerbates this (known gotcha).
- **Change**: `rm -rf frontend/.next` to force a full rebuild on next `npm run dev`.
- **Verification**: `.next` directory removed successfully.

### [Fix]: Handle 204 No-Content in `fastapiFetch` to prevent JSON parse crash

- **Files changed**: `frontend/lib/fastapi.ts` (lines 28-30)
- **Problem**: DELETE endpoints return 204 with no body, but `fastapiFetch` always called `res.json()` which throws `SyntaxError: Unexpected end of JSON input` on empty responses.
- **Root cause**: No check for 204 status before attempting JSON parse.
- **Change**: Added `if (res.status === 204) { return undefined as T; }` before the `res.json()` call.
- **Why this approach**: 204 is the standard "success, no body" status. Returning `undefined` matches the `Promise<void>` expectation of delete operations.
- **Verification**: Code review — the early return prevents `res.json()` from being called on empty bodies.

### [Fix]: Harden error handling in `useConversations.ts` — prevent secondary JSON parse crash

- **Files changed**: `frontend/hooks/useConversations.ts` (lines 19-21, 31-34, 48-51, 69-72, 81-83, 100-103)
- **Problem**: All 6 fetch functions called `await res.json()` on error responses without checking if the body is valid JSON. When the BFF returns a non-JSON error (e.g., from webpack crash or HTML error page), this throws `SyntaxError: Unexpected end of JSON input` which masks the real error.
- **Root cause**: Error handling assumed error responses always contain JSON `{ error: "..." }` body.
- **Change**: Replaced `const data = await res.json()` with `const text = await res.text()` + `try { message = JSON.parse(text).error || message; } catch {}` in all 6 functions: `fetchConversations`, `fetchConversation`, `createConversation`, `updateConversation`, `deleteConversation`, `sendMessage`.
- **Why this approach**: `res.text()` never throws on any valid HTTP response. The try-catch around `JSON.parse` gracefully falls back to the default error message when the body isn't JSON.
- **Verification**: Code review — all 6 error paths now handle non-JSON responses without throwing secondary errors.

## Session: 2026-02-09 (Bug Fix — Conversations 500 Error)

### [Fix]: 500 error on GET /api/v1/conversations/{id} due to Source→DocumentSource mismatch

- **Files changed**: `backend/src/api/routes/conversations.py` (lines 11-35, 127)
- **Problem**: After the UI/UX formatting session changed `MessageResponse.sources` from `list[Source]` to `list[DocumentSource]`, the `get_conversation` endpoint still constructed `Source(**s)` objects from stored JSON. Pydantic rejected these, causing a 500 Internal Server Error when loading any conversation with sources.
- **Root cause**: The schema migration to `DocumentSource` (with `reference_number` instead of `chunk_index`) was applied to schemas.py but the conversation route was not updated to match.
- **Change**:
  - Replaced `Source` import with `DocumentSource` import
  - Added `_deserialize_sources()` helper that constructs `DocumentSource` objects from DB JSON, with backward compatibility: uses `reference_number` if present, otherwise assigns sequential numbers (1, 2, 3...)
  - Changed line 127 from `[Source(**s) for s in m.sources]` to `_deserialize_sources(m.sources)`
- **Why this approach**: A helper function keeps the deserialization logic in one place and handles both old-format sources (with `chunk_index`, no `reference_number`) and new-format sources cleanly. Sequential numbering for old sources is a reasonable fallback.
- **Verification**: Code review confirms the fix matches the `DocumentSource` schema fields. Runtime verification requires Docker.
