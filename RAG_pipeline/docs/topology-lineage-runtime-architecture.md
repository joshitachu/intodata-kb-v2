# Topology Drill-Down, Field Lineage, and Runtime Health Overlay — Architecture Plan

> Standalone planning doc (not the LFE pipeline). Implementation-ready architecture for
> GitHub issue IntoDataBV/Nexus#27 ("Traceability impact, downstream impact visualisation").
> Chat-on-topology is explicitly deferred by the requester ("maybe first the topology") —
> it is noted as a later phase only, not designed here.

Status: **proposed — not yet implemented.** Last updated 2026-07-16.

---

## 1. Context and assumptions

### 1.1 The ask, restated

Four capabilities on the existing topology view (`/topology` in the RAG frontend):

1. **Recursive drill-down** — from the project-level job graph, double-click a job to open
   a job-level graph of its internal components, and go "as deep as possible": drill into a
   component when it has internal structure (tMap column mappings). Zoom in/out, not only
   click-through navigation.
2. **Field/element lineage ("heritage")** — follow a specific field/column through the graph:
   origin, transformations, destinations — potentially across job boundaries.
3. **Live runtime health overlay** — reflect real execution state (healthy / degraded / down)
   on the topology itself, not only on the separate logs pages.
4. **Dependency failure propagation** — if job X structurally depends on job Y and Y's live
   status is down, flag the edge and X visually ("red line"), i.e. propagate live health across
   the static dependency graph.

### 1.2 Current state (verified against the codebase)

| Layer | What exists today | Where |
|---|---|---|
| Project/job topology | Job + connection + group-summary nodes, dagre layout, expand/collapse groups, job detail side panel (flat component list) | `RAG_pipeline/frontend/app/topology/page.tsx`, `frontend/lib/graphTransform.ts`, `frontend/components/graph/*` (React Flow: `@xyflow/react` 12.10, layout: `@dagrejs/dagre` 3.0) |
| Graph API | `GET /mcp/projects/{id}/graph` → `ProjectGraphResponse` (manifest + connections + per-job aggregates only: `component_count`, deduped `component_types`) | `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py:3051`, `schemas.py:640`; proxied by `frontend/app/api/projects/[id]/graph/route.ts` → `KB_API_URL` |
| Job detail API | `GET /mcp/projects/{id}/jobs/{job_name}/detail` — full scan data + doc sections (already reads the per-job JSON) | `projects.py:3196` |
| Scan data | One JSON per job: `components` (~34/job in real samples), `connections` (source/target/connector/line_style), `tmap_data` (per output column: table, column, raw `expression` string), `trunjob_refs` (job→job calls), plus project `manifest.json` (`job_calls`, `job_called_by`, `job_connections_used`, ...) | `knowledge_base/intodoc-output/<project>/scan/`, models in `knowledge_base/intodoc/src/intodoc/models.py` (flat file; the `ir/` package and `normalize`/`analyze` CLI described in `intodoc/CLAUDE.md` do not exist — that doc is stale) |
| Sub-component data | **tMap only** (`TMapData`/`TMapEntry`, `models.py:82/76`). No structured source-column references — `expression` is a raw Java-ish string. No sub-component capture for tFilterRow, tAggregateRow, tXMLMap, etc. **No schema/column lists captured anywhere** (no `scan_result.json` persisted per project; component params carry no column definitions) |
| Lineage schema | `LineageMatrix`/`LineageEntry`/`ColumnTransformation` (`models.py:638-659`) — **dead code** from the abandoned 21-pass CLI analyzer; nothing references it today |
| Runtime health | `RuntimeJobHealth` (`RAG_pipeline/backend/src/runtime/models.py:264`): per job — `total_runs`, `failed_runs`, `failure_rate`, `current_failure_streak`, `latest_run_at`, `latest_state` (`failed`/`running`/`succeeded`/`unknown`). Served RBAC-scoped by `GET /runtime/job-health` (`api/routes/runtime.py:594`) |
| Runtime dependencies | `RuntimeJobDependencyRow` (`db/models/runtime_data.py:413`): job→**artifact/connection** edges only (`dependency_type` constrained `^(artifact\|connection)$`, `runtime/models.py:598`), populated from live TMC telemetry. **No job→job dependency exists on the runtime side**; job→job structure lives only in scanner `trunjob_refs`/`manifest.job_calls` |
| Failure diagnostics | `runtime/insights/builder.py` — per-execution root cause / incident signatures. **No dependency-aware rollup or propagation exists anywhere** |

Verified sample of real tMap expressions (`reverse_engineer_VW/.../Maximo2AssetCheck_SendLocation.json`):
column references appear as `row17.message`, `Var.Message`; dynamic references as
`((String)globalMap.get("tFileOutputDelimited_1_FILE_NAME"))`; plus nested ternaries and
string literals. Also verified: `Component.incoming_connections`/`outgoing_connections` can be
empty even when the job's `connections` array is complete — **component wiring must be built
from `connections`, not from the per-component lists**.

### 1.3 Assumptions

- **"As deep as possible" bottoms out at four levels.** Level 0: project (group summaries) —
  exists. Level 1: job graph — exists. Level 2: job-internal component graph — new, fully
  supported by existing scan data. Level 3: intra-component graph — **tMap only** today; the
  floor with current data is "output column + raw expression string". With the deterministic
  expression tokenizer proposed in §2.2, the floor becomes real column→column edges for
  statically resolvable references, with dynamic references (`globalMap`, `context.*`, routine
  calls) shown but flagged unresolvable. All other component types get a structured **inspector
  panel** (params, code, connectors) at level 3, not a fake graph, until the scanner captures
  sub-component structure for them (see §3, D4).
- **Talend first.** Workato/Lobster/Generic get level 2 for free (components + connections are
  platform-agnostic) but no level 3 and no column lineage initially.
- **Runtime↔scan job identity is name-based with an override escape hatch.** TMC task names
  generally mirror Talend job names, but this must be confirmed on the live `cdc` data (spike,
  §5 S0). Unmatched jobs render with health "unknown" — never fabricated.
- **"Live" means sync-cadence-fresh.** Health reflects the last runtime sync
  (`runtime/sync/scheduler.py`), surfaced with a freshness timestamp. True push/streaming is
  out of scope; shortening the sync interval or a TMC webhook is a later optimization.
- **Scale envelope:** ~100–200 jobs/project, ~34 components/job, tMap entry counts in the tens.
  Everything below is sized for that; nothing precludes 10x.

---

## 2. Target architecture

```
intodoc scanner ──(scan)──> scan cache ──> mcp-server REST ──> Next.js proxy ──> topology UI
  + new lineage pass          + lineage.json   + /jobs/{j}/graph                  (React Flow)
                                               + /lineage/field                       ▲
                                                                                      │ merge client-side
TMC ──(sync)──> runtime store (Postgres) ──> RAG backend /runtime/job-health ─────────┘
                 + scan-derived job→job edges (S6)   + /runtime/topology-health (S6)
```

Structure (scan-derived, versioned with the scan) and health (runtime store, sync-cadence)
remain owned by their existing services. The topology UI joins them; a server-side join is
added later for agent/alerting reuse (§3, D1).

### 2.1 Recursive drill-down (levels, contracts, rendering)

**Data contract per level — lazy-loaded, one endpoint per level.** No nested mega-response:
payloads stay bounded and levels cache independently (React Query keyed by
`[project, job?, component?]`).

- **Level 0/1 (exists, unchanged):** `GET /mcp/projects/{id}/graph` → `ProjectGraphResponse`.
- **Level 2 (new):** `GET /mcp/projects/{id}/jobs/{job_name}/graph` → `JobGraphResponse`:

  ```
  JobGraphResponse:
    job_name, group
    nodes: [ { unique_name, component_type, role,            # role: input|transform|output|orchestration|logging (derived from type)
               has_subgraph: bool,                           # true for tMap (later: other captured types)
               subjob_target: str | null,                    # tRunJob → child job name (portal node)
               connection_ref: str,                          # metadata_connection_ref for system badge
               param_summary: {k: v},                        # bounded, credential-filtered high-signal params
               has_code: bool } ]
    edges: [ { name, source, target, connector_name, line_style } ]   # straight from ParsedJob.connections
    tmap_tables: { component → {input_tables, output_tables} }        # for badges before drilling
  ```

  Implementation: new handler in `mcp-server/src/mcp_server/api/routes/projects.py` reusing the
  per-job JSON loading already done by `get_job_detail` (`projects.py:3196`); schema additions in
  `mcp_server/schemas.py`. Edges come from the job's `connections` array (component-level
  `incoming/outgoing_connections` are unreliable, §1.2). `require_project_access`
  (`api/permissions.py:15`) guards it like every project route. Trigger links (`line_style`
  != 0 / `connector_name` like `COMPONENT_OK`, `RUN_IF`) are rendered as dashed control-flow
  edges, distinct from solid data-flow rows — this distinction matters for both readability
  and lineage (only FLOW/LOOKUP edges carry data).

- **Level 3 (new, tMap):** `GET /mcp/projects/{id}/jobs/{job_name}/components/{unique_name}/graph`
  → `ComponentGraphResponse`: input tables (row names resolved to upstream components via the
  job's connections), the `Var` table, output tables; each table carries its columns (from
  `tmap_data.entries`/`var_entries`); mapping edges carry `{expression, resolved_refs[],
  confidence}` once S4 lands (before S4: raw expression only). Non-tMap components return
  `404 { fallback: "inspector" }` and the UI opens the inspector panel instead.

**Frontend rendering — keep React Flow + dagre; add a level-aware navigation stack.**

- Refactor `app/topology/page.tsx` into a container holding a breadcrumb stack
  (`Project ▸ Job ▸ Component`). Double-click descends (job node → level 2; tMap node →
  level 3; tRunJob portal node → sibling job's level 2). Breadcrumb/Escape ascends. Each
  descent is a `fitView`-animated transition into a freshly laid-out React Flow canvas —
  levels are separate graphs, not one canvas with 10k nodes.
- New files: `frontend/lib/jobGraphTransform.ts` (level-2 transform + dagre sizing),
  `frontend/components/graph/ComponentNode.tsx`, `frontend/components/graph/TableNode.tsx`
  (level-3 table node with per-column React Flow handles — the standard schema-viz pattern,
  so column→column edges attach to the exact row), `frontend/components/graph/ComponentInspector.tsx`
  (level-3 fallback), plus proxy routes under `frontend/app/api/projects/[id]/jobs/...`.
- "Zoom in/out, not just click-through": within a level, React Flow zoom/pan already works;
  across levels, semantic zoom (auto-descend past a zoom threshold) is a polish item —
  the core contract is the stacked levels. Existing group expand/collapse at level 0/1 is
  untouched; double-click is a new gesture only on nodes that have a deeper level.

### 2.2 Field/column lineage ("heritage")

**Derivation: a new deterministic scanner pass — no LLM, no runtime cost.**

New module `knowledge_base/intodoc/src/intodoc/lineage.py`, invoked at the end of
`intodoc scan`, emitting `scan/lineage.json` per project. Steps:

1. **Expression tokenization.** For each `TMapEntry.expression`, extract candidate
   `identifier.field` pairs (`\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b` over a comment/string-literal-
   stripped copy of the expression) and keep only identifiers that are (a) the tMap's declared
   `input_tables` (row names like `row17`), or (b) `Var`. Everything else — `globalMap.get(...)`,
   `context.*`, routine calls, casts — is recorded as an **unresolved dynamic reference**, never
   dropped silently. Verified against real expressions (§1.2): this tokenizer covers the
   dominant patterns (direct maps, ternaries over row fields, Var chaining).
2. **Var chaining.** `Var.X` in an output expression is resolved through `var_entries` so the
   final edge points at the true source columns (`row17.message → Var.Message → logVariables.Message`
   collapses to `row17.message → logVariables.Message` with the Var hop recorded in
   `transformations`).
3. **Row → component resolution.** Input table names are connection row names; join against the
   job's `connections` to find the upstream component (and onward to its
   `metadata_connection_ref` + table/query params for source-system attribution).
4. **Classification per edge:** `direct_map` (expression is exactly one reference),
   `expression` (multiple refs / operators), `constant` (literal only), `lookup` (ref into a
   LOOKUP-style input), `dynamic` (contains unresolved refs) — with
   `confidence: certain | likely | dynamic | opaque`.
5. **Cross-job stitching (second increment):** (a) `trunjob_refs` context-param passing, and
   (b) table identity — job A's output component writes table T (connection ref + table param),
   job B's input reads T → an `inferred`-confidence job-boundary edge. Requires no new scan
   data, only param inspection.

**Schema: reuse and extend the dead `LineageMatrix` code** (`models.py:638-659`) rather than
invent a parallel model (§3, D5): add `source_component`, `target_component`, `resolved: bool`,
`dynamic_refs: list[str]`, and widen `confidence` to the enum above. Population moves from the
abandoned LLM pass to this deterministic pass. The credential filter
(`intodoc/src/intodoc/scanner/`) must run over `lineage.json` exactly as it does over scan
output — expressions can embed secrets (the VW sample contains password-handling logic).

**Query API:** `GET /mcp/projects/{id}/lineage/field?job=&component=&table=&column=&direction=up|down|both&max_depth=`
→ BFS over the lineage graph (cycle-safe, depth-capped), returning path nodes/edges plus each
edge's expression + confidence. New `mcp-server/src/mcp_server/services/lineage_service.py`
(loads + indexes `lineage.json`, cached per project mtime) and a route in `routes/projects.py`.
The UI consumes it as a **highlight overlay**: clicking a column in a level-3 table node (or a
field chip in the job inspector) dims the canvas and paints the upstream/downstream path across
whatever level is visible — job-boundary hops render on the level-1 graph, intra-job hops on
level 2/3.

**How far this extends beyond tMap:** pass-through components (tFilterRow, tSortRow, tUniqRow,
tReplicate) preserve schema 1:1 and can be modeled as identity edges *once column names are
known on their flows* — which today they are not (no schema capture, §1.2). Until the scanner
extracts schema column lists from Talend `.item` metadata (real new scanner work in
`platforms/talend/parser.py`), lineage exists **only where tMap declares columns**, and
pass-through components appear as component-level (not column-level) hops. tAggregateRow
(GROUPBYS/OPERATIONS params) is parseable next; tXMLMap/tJavaRow remain `opaque` —
column lineage legitimately breaks there and the UI must say so rather than pretend.

### 2.3 Live runtime health on the static graph

**Health source:** the existing `RuntimeJobHealth` aggregate via `GET /runtime/job-health`
(RBAC-scoped server-side, `get_user_project_ids` + `_intersect_scope`). Derived node status:

| Status | Rule (evaluated in that order) |
|---|---|
| `down` | `latest_state == failed` |
| `degraded` | `failure_rate` over the health window ≥ threshold (default 0.3) but latest run succeeded, or `current_failure_streak > 0` with a non-failed latest state |
| `running` | `latest_state == running` |
| `healthy` | latest run succeeded and below degraded threshold |
| `unknown` | no runtime match for this scan job |

**Identity join (the real work):** scanner job name ↔ runtime `job_name`. Normalized matching
(case-insensitive, trim whitespace/version suffixes) plus an explicit per-project override map
for exceptions — modeled after `RuntimeWorkspaceProjectMapRow` (`runtime_data.py:200`), i.e. a
small `runtime_job_scan_map` table + admin endpoint in `api/routes/runtime_sources.py`, added
only if the S0 spike shows real-world mismatches. Never guess: ambiguous or missing matches are
`unknown`.

**Join location:** client-side in the topology page for the UI slice (both datasets are already
in the browser: manifest from the graph endpoint, health from a new `useRuntimeLogs.ts`-style
hook polling `/runtime/job-health?project_id=` every 30–60s with React Query `refetchInterval`).
A server-side equivalent (`/runtime/topology-health`) arrives in S6 for agent/alerting reuse —
see §3, D1 for why this split is deliberate and not a workaround.

### 2.4 Dependency failure propagation ("Y down → flag X")

**Structural edges:** scanner `manifest.job_calls` (populated from `trunjob_refs`) is the source
of truth for job→job dependency. `job_calls[X] = [Y]` means X calls Y — the parent depends on
its child, so a down child impacts every transitive ancestor. Data-flow impact (Y writes table
T, Z reads T) is a second edge class, available once lineage stitching (§2.2 step 5) lands;
propagation treats both edge classes uniformly.

**Algorithm:** pure function, one implementation shared by UI and (later) backend:

```
propagateHealth(edges: {parent→child}[], status: Map<job, Status>) -> Map<job, EffectiveStatus>
  seeds = jobs with status down
  reverse-BFS from seeds over child→parent edges, visited-set (cycle-safe —
  tRunJob cycles are possible), no depth cap (O(V+E), graphs are ~200 nodes)
  a non-down reached job gets effective status "impacted" (its own status preserved as ownStatus)
  every edge on a seed→impacted path is marked "failing-path"
```

Fan-out is **fully transitive** — a one-hop flag understates blast radius and the user asked for
lifecycle-level traceability; the UI differentiates severity by hop distance (tooltip: "impacted
via Y → Z, 2 hops").

**Frontend:** `frontend/lib/healthPropagation.ts` (pure, unit-testable with Vitest) +
status ring/badge on `JobNode`/`GroupSummaryNode` (a collapsed group shows the worst member
status), red animated edges (`animated: true`, red stroke) on failing paths, an "impacted"
amber treatment distinct from "down" red, a legend entry in `GraphLegend.tsx`, a freshness
chip ("runtime data as of 12:03"), and a toggle in `GraphToolbar.tsx` to switch the overlay off.
At level 2, a down job's failing component (from `runtime/insights` root-cause / failure
`component metrics` where available) gets the red marker — best-effort, `unknown` otherwise.

**Server-side (S6):** persist scan-derived job→job edges into the runtime store and expose
`GET /runtime/topology-health?project_id=` returning `{job, own_status, effective_status,
failing_paths[]}` — the contract the future chat-on-topology phase and alerting will consume.
See §3, D1 for the schema decision.

---

## 3. Key design decisions

### D1 — Job→job dependency data model

| Option | Pros | Cons |
|---|---|---|
| A. Derive from `manifest.job_calls` at read time only (client-side join) | Zero schema change; structure stays versioned with the scan; ships fastest | Not reachable by the agent/alerts; two consumers would re-implement the join |
| B. New dedicated `runtime_scan_job_edges` table | Clean separation of scan-derived vs telemetry edges | Second dependency table to keep in sync; every consumer must union two tables |
| C. Extend `RuntimeJobDependencyRow`: allow `dependency_type = "job"`, add `origin: "scan" \| "runtime"` column | One dependency table for all edge kinds; agent tools (`runtime/registry.py`) and RBAC scoping work unchanged; provenance explicit | Touches the `^(artifact\|connection)$` contract (`runtime/models.py:598`) + a migration; scan-publish must upsert edges (staleness window between rescan and sync) |

**Decision: A now (S3), C as the durable model (S6).** This is phasing, not a workaround: the
propagation algorithm is written once as a pure function; S3 runs it in the browser over data
both services already serve, S6 moves the same function server-side once a second consumer
(agent/alerting) actually exists. Population for C happens at scan publish — the natural hook is
`_run_generate_background` / `RagBridge` push in `mcp-server` calling a small RAG-backend
ingest endpoint, or the RAG backend's existing sync service reading the manifest; decide in the
S6 plan. Migration numbering: **061+** (060 is taken by the runtime-logs work).

### D2 — Expression parsing strategy for lineage

| Option | Pros | Cons |
|---|---|---|
| Deterministic tokenizer (regex over literal-stripped text, scoped to declared input tables + Var) | Fast, testable, zero cost, confidence is honest by construction; verified viable against real expressions | Cannot resolve dynamic refs (`globalMap`, `context`, routines); ternary *branch* semantics ignored (all referenced columns become sources) |
| Full Java AST parse (javalang / tree-sitter) | Precise reference extraction, branch awareness | Heavy dependency; tMap snippets are fragments, not compilation units (`\r\n`, no class context) — fragile; still can't resolve `globalMap` (that's runtime state, not syntax) |
| LLM extraction | Handles anything textual | Non-deterministic lineage is worse than none; cost; violates "credential-safe, evidence-first" posture |

**Decision: deterministic tokenizer with confidence tiers.** Dynamic refs are first-class
*unresolved* entries, rendered as such. LLM involvement is limited to optional
`human_explanation` enrichment of already-extracted edges, later, if ever.

### D3 — Health propagation: compute on read vs precompute

**Decision: on read.** The graph is small (O(200) nodes), the algorithm is O(V+E), and health
changes at sync cadence — precomputing at sync time couples the sync pipeline to topology
semantics and creates invalidation questions for zero measured benefit. Server-side (S6) may add
a short (~30s) in-process cache per project. Revisit only if estates grow 100x.

### D4 — How deep recursion goes before falling back to a flat view

**Decision: a level is a graph only where the scan cache has real edges for it.** Level 3 graph
= tMap (only sub-component structure captured today). Every other component type opens the
structured inspector (params, code, connectors, linked docs) — an honest floor, no synthesized
graphs. The level-3 contract (`ComponentGraphResponse`) is deliberately component-type-agnostic
so that when the scanner later captures tAggregateRow/tXMLMap internals, those types join level
3 without API changes. Alternative rejected: rendering pseudo-graphs from param heuristics —
would fabricate structure the evidence doesn't support, the same failure class the fabrication
validators exist to prevent on the docs side.

### D5 — Reuse `LineageMatrix` dead code vs new schema

**Decision: reuse and extend** (`models.py:638-659`): the shape (source/target
system+table+column, transformation list, confidence) is right; extending keeps one canonical
lineage vocabulary shared with the doc-generation side if Pass-20-style docs ever return.
Cost: the extension fields in §2.2 and moving population to the deterministic pass. Rejected:
a fresh schema — parallel near-identical models violate the "don't invent bespoke structures"
rule and the dead code would still linger.

### D6 — Where lineage is computed

**Decision: scan-time, persisted as `scan/lineage.json`,** served by mcp-server with an
in-memory index. Lineage only changes when code changes (i.e., on rescan), so computing it in
the scanner keeps it versioned with the scan, cacheable, and free at query time. Rejected:
on-demand parsing in mcp-server (repeated cost, and the scanner already owns the
credential-filter stage the lineage output must pass through).

---

## 4. Risks and open questions

- **Runtime Logs Redesign coordination (real, current).** The working tree carries large
  uncommitted changes across `runtime/store/repository.py`, `api/routes/runtime.py`,
  `runtime/models.py`, `db/models/runtime_data.py`, `runtime/semantic/`, migration
  `060_runtime_logs_storage` (see `RAG_pipeline/docs/runtime-logs-redesign.md`, status
  "implemented and validated locally"). This plan builds directly on `RuntimeJobHealth`,
  `/runtime/job-health`, and the repository — **do not start S3/S6 until that work is committed
  and its migration landed.** Also inherit its outcomes: the `cdc`-survives dedupe determines
  which `project_id`/`source_key` values the health join sees; new migrations start at 061.
- **Expression parsing is not data-flow analysis.** `globalMap`/`context` refs, routine calls,
  and tJava code are runtime-dynamic; ternary branches are over-approximated (all referenced
  columns count as sources). The UI and API must always carry confidence; "certain" is reserved
  for direct static references. Sell this internally as *static lineage with explicit gaps*,
  not complete lineage.
- **Schema capture gap (biggest lineage limitation).** No column lists exist in scan output —
  column-level lineage starts and ends at tMap boundaries. Making pass-through components
  column-aware requires new Talend parser work (extract flow schemas from `.item` metadata),
  which is its own slice with its own token-bloat pitfall (schemas are large; keep them out of
  LLM chunking paths — `base_distiller.py` must not pick them up).
- **Runtime↔scan name matching is unproven.** Verify on live `cdc` data before building UI on
  it (S0). If TMC task names diverge from Studio job names materially, the override-map table
  moves from "if needed" to required scope.
- **Performance.** Level-2/3 graphs are small; the risks are (a) level-1 with the health
  overlay re-rendering on every poll — memoize node data, only patch changed statuses;
  (b) dagre layout cost on fully expanded large projects — already mitigated by
  collapsed-by-default + compact mode; keep those defaults; (c) lineage BFS payloads — depth
  cap + node-count cap on `/lineage/field`.
- **RBAC / exposure surface.** All new mcp routes sit behind `require_project_access`; all
  runtime data behind `get_user_project_ids` scoping — no LLM-supplied scope, ever (matches
  NFR2 of the logs redesign). Field-level detail widens what a project member can see
  (expressions, params): the scanner's credential filter must demonstrably cover
  `lineage.json` and level-2 `param_summary` (params like passwords must never reach the
  API even filtered-out client-side). Note: runtime health/log data originates from customer
  production systems — retention and data-handling posture should be validated with the
  compliance owner (technical guidance only; not legal advice), same caveat as the logs
  redesign.
- **Multi-platform degradation.** Workato/Lobster/Generic projects must not error: level 2
  renders from generic components/connections; level 3 and lineage return an explicit
  "not available for this platform" rather than 500s or empty graphs.
- **Open questions.** (1) Does the double-click gesture conflict with users' existing
  expand/collapse habits at level 0? Usability check during S1 demo. (2) Group-level health
  rollup semantics: worst-of vs failure-count badge — decide at S3 demo. (3) Should `unknown`
  health (no runtime source configured) hide the overlay entirely per project? Default: yes,
  overlay auto-hides when `/runtime/job-health` returns nothing for the project. (4) For the
  deferred chat phase: the harness capability registry (`RAG_pipeline/backend/src/harness/`)
  can consume `/lineage/field` and `/runtime/topology-health` as capabilities as-is — the S4/S6
  API contracts are deliberately agent-friendly (bounded JSON, project-scoped), so nothing in
  this design makes that phase harder.

---

## 5. Suggested next steps (slice-able build order)

Each slice is independently demoable and small enough for a future `/plan`. Critical-area note:
S1/S2/S4 touch MCP API routes → `/inspect` required before commit (root `CLAUDE.md`).

### S0 — Spike (timeboxed, no product code)
Verify on live data: (a) TMC `job_name` vs scanner job-name match rate on `cdc`;
(b) tokenizer hit rate over all tMap expressions in `reverse_engineer_VW` + `cdc`
(script only, in scratch); (c) confirm which params on input/output components identify
tables/connections for future stitching. Output: go/no-go notes attached to this doc.

### S1 — Job-level drill-down (level 2)
- New `GET /mcp/projects/{id}/jobs/{job_name}/graph` + `JobGraphResponse` schema; Next proxy route.
- `frontend/lib/jobGraphTransform.ts`, `ComponentNode.tsx`, breadcrumb navigation stack in
  `app/topology/page.tsx`; double-click job → component graph; tRunJob portal nodes navigate
  to the child job; control-flow vs data-flow edge styling.
- **Touches:** `mcp-server/api/routes/projects.py`, `mcp-server/schemas.py`, frontend topology stack.
- **Acceptance:** any job in a scanned project opens a laid-out component graph matching its
  scan JSON (node count == component count); back navigation restores the prior level's viewport.

### S2 — Component-level depth (level 3: tMap graph + inspector fallback)
- `GET .../components/{unique_name}/graph` (tMap: tables/columns/expressions — raw strings,
  no parsing yet); `TableNode.tsx` with per-column handles; `ComponentInspector.tsx` for all
  other types (params, code, connectors).
- **Acceptance:** double-click a tMap → column mapping graph with expressions on edge hover;
  double-click any other component → inspector; no dead ends.

### S3 — Runtime health overlay + failure propagation (client-side)
- Health hook polling `/runtime/job-health?project_id=`; name-based identity join;
  `frontend/lib/healthPropagation.ts` (pure, Vitest-covered incl. cycle case);
  node status rings, red failing-path edges, amber "impacted", legend, freshness chip,
  overlay toggle; group nodes roll up worst status.
- **Precondition:** Runtime Logs Redesign committed.
- **Acceptance:** with mock provider data (`runtime/providers/mock.py`) forcing job Y failed,
  Y renders down and every transitive caller of Y renders impacted with red path edges;
  jobs without runtime data render unchanged with health "unknown".

### S4 — Lineage extraction + query API
- `intodoc/src/intodoc/lineage.py` deterministic pass (tokenizer, Var chaining, row→component
  resolution, classification); extend `LineageMatrix`/`LineageEntry` (`models.py`); emit
  credential-filtered `scan/lineage.json`; `lineage_service.py` + `/lineage/field` route.
- **Acceptance:** for a known VW job, `/lineage/field` returns the verified expected edges
  (golden-file pytest against the real scan fixture); dynamic refs appear as unresolved with
  `confidence: dynamic`; no credentials in `lineage.json`.

### S5 — Lineage UI ("heritage" mode) + cross-job stitching
- Column-click → dim canvas + highlight upstream/downstream path overlay across levels 1–3;
  stitching increment in the scanner pass (trunjob context params + table identity) so paths
  cross job boundaries with `inferred` confidence badges.
- **Acceptance:** following a field from a source component shows its full visible path,
  including at least one cross-job hop on a project where table identity holds, with
  per-hop confidence displayed.

### S6 — Durable job→job dependencies + server-side propagation
- Migration 061+: `dependency_type` gains `"job"` + `origin` column on
  `runtime_job_dependencies`; scan-publish population path; `GET /runtime/topology-health`
  running the same propagation function server-side (port of `healthPropagation.ts` semantics,
  shared test vectors); optional `runtime_job_scan_map` override table if S0 demands it.
- This is the enabler for the deferred **chat-on-topology phase** (agent capabilities over
  `/lineage/field` + `/runtime/topology-health`) and for alerting — both explicitly out of
  scope here.

### Later (explicitly deferred per requester)
Chat-on-topology: flip the narrative — chat panel scoped to the visible graph selection,
answered by the harness with lineage + health + docs as capabilities. Not designed in this
document; §4 confirms the S4/S6 contracts keep it cheap.
