# Shared-tenant runtime reads

Decouple "which project may READ a runtime mirror" from "which project OWNS the
credential and pays the poll", so two Nexus projects can document one Talend
tenant without duplicating the credential or the vendor API load.

Status: **built** (2026-08-26), local only — not deployed. Slices 1-4 all landed.

The two open questions were decided as recommended and implemented that way:

* **R1 (orphan tripwire)** — both halves. A grant carries its own environment
  scope, which holds the runtime vocabulary to what the reader documents; and
  `shared_read` suppresses the `scope_mismatch` verdict entirely
  (`runtime_evidence.py`), because under a shared read a sibling project's jobs
  are orphans by design. The orphan FIGURES are still published; only the
  mis-binding accusation is withheld, and the rollup states why.
* **Q4 (409 wording)** — the owning source and project are named only when the
  caller's scope already contains that project, else "in another project". A 409
  is reachable by anyone holding a working token for the tenant.

One thing the plan got wrong: the doc-evidence helpers grouped by
`(project_id, job_name)` while returning a dict keyed on `job_name` alone.
Harmless at one project, silent data loss at two. The fix was to drop
`project_id` from the doc lane's GROUP BY (it is constant within a
single-project read, so nothing changes there) and to merge reliability rollups
by name — see `_merge_reliability_by_job_name`.

---

## 1. Context and assumptions

### Symptom

Registering the same TMC token + base URL against a second project returns
409 `That physical runtime source is already registered`.

**Resolved.** The refusal is still a 409 — it is correct — but it is no longer a
dead end. `_registration_conflict` (`src/api/routes/runtime_sources.py`) now
names sharing as the way forward in `detail` and returns a `conflict` block
beside it (`kind`, `can_request_share`, and the owning source's id/label/project
when, and only when, the caller's scope already contains that project — the Q4
rule above). The dashboard turns that into a jump straight to the owning
source's sharing control with the refused project preselected.

The discoverability half mattered more than the wording: the grant mechanism
shipped 2026-08-26 and was invisible enough that it was reported as missing and
requested from scratch. Sharing state is now rendered at rest on the source row
in both the dashboard and the RAG app's `/admin/runtime` console, rather than
behind a collapsed toggle.

### Cause

Registration rejects any credential whose resolved **physical identity** already
exists anywhere in the install. The check is not project-scoped. For TMC the
identity is (`src/runtime/providers/talend_tmc.py:257-272`):

```
tmc:{base_url}#sha256(sorted list of ALL environment ids the token can see)[:16]
```

Built from `/orchestration/environments` — every environment the token can see,
not the ingest-scope selection. So narrowing the environment tick-boxes cannot
produce a distinct identity. The guard is correct and should stay: it stops two
mirrors of one tenant diverging and doubling the vendor poll.

### The actual gap

`runtime_sources.project_id` is a single column doing three unrelated jobs:

| Relation | Today | Should be |
|---|---|---|
| Credential ownership / poll binding | `runtime_sources.project_id` | 1:1, unchanged |
| Write attribution (which project a mirrored row is stamped with) | `resolve_project_id` via `runtime_workspace_project_map` (`repository.py:1128`) | already pluggable, unchanged |
| **Read entitlement** (which projects may read the mirror) | **implicit — equals ownership** | **explicit, many** |

There is also no route to rebind a source: `RuntimeSourceUpdate`
(`src/runtime/models.py:131`) exposes only `label`, `enabled`,
`sync_interval_seconds`.

### Assumptions

- One tenant stays one mirror and one poll. Sharing is a read-side grant, never
  a second ingest.
- The reading project has its own scan, so unit-name matching still applies.
- Grants are operator-configured. No inference from name similarity or overlap.
- Backwards compatible: a project with no grants behaves exactly as today,
  including query shape (see 3.2).

---

## 2. Target approach

### 2.1 New relation: read grants

```
runtime_source_reads
  id            PK
  source_id     FK -> runtime_sources.id  ON DELETE CASCADE
  project_id    str(255)      -- the READER
  environment_ids  JSON null  -- reader's own doc read scope; null = all mirrored
  window_days      int  null  -- reader's own history window; null = caller default
  created_at    tz-aware
  created_by    str null
  UNIQUE (source_id, project_id)
  INDEX (project_id)
```

Alembic revision `095_runtime_source_read_grants` (head is
`094_instance_settings_created_at`; deploy runs `alembic upgrade head` from
`backend/Dockerfile:18`).

**Why the grant carries its own scope.** Owner read scope lives in the
credential blob today (`doc_environment_ids` / `doc_window_days`, read by
`resolve_doc_read_scope`, `repository.py:813`). Two projects reading one tenant
will almost always want different environments — that is the whole point of the
split (PROD for one project, ACC for another). Scope therefore belongs on the
(source, project) pair, not on the credential. The owner path is left where it
is rather than migrated: it works, it is documented in `knowledge_base/CLAUDE.md`,
and moving live config is unnecessary risk. `resolve_doc_read_scope` becomes one
function over two scope stores.

### 2.2 Resolution

New repository method:

```python
async def resolve_runtime_read_projects(self, project_id: str) -> list[str]
```

Returns `[project_id]` plus, for every source granted to `project_id`, the
project ids that source's rows are actually stamped with — the source's own
`project_id` plus every `runtime_workspace_project_map.project_id` for that
source. Deduplicated, `project_id` first.

**Why project ids and not `source_key`.** Every table in
`db/models/runtime_data.py` carries both `project_id` and `source_key`, both
indexed — `source_key` would be the more precise filter. But the insights
tables (`runtime_incidents`, `runtime_enriched_events`,
`runtime_execution_summaries`, `runtime_error_signatures` in
`db/models/runtime_insights.py`) carry **`project_id` only**. A mixed predicate —
`source_key` where available, `project_id` elsewhere — would be two scoping
rules in one query lane, which is how one aggregate ends up describing a
different population than its neighbour. One rule, applied once.

The precision cost is real and bounded: granting B read on a source whose
workspace map also routes rows to C exposes C's rows to B. Documented as a
constraint on the grant UI (see 4.3), not hidden.

### 2.3 Widening the doc-evidence lane

Only the doc-evidence lane changes. The user-facing runtime dashboard queries
keep single-project semantics.

- `list_doc_evidence` (`repository.py:2390`) and its eight private helpers
  (`_doc_evidence_scan_map` / `_durations` / `_gap_quantiles` / `_environments`
  / `_plans` / `_failures` / `_log_quality` / `_incidents`) take
  `project_ids: Sequence[str]` in place of `project_id: str`.
- `ListRuntimeJobReliabilityInput` (`models.py:897`) gains
  `project_ids: list[str] | None = None`, taking precedence over `project_id`
  when set. Only the doc lane sets it.
- The two shared statement builders `_scoped_execution_base`
  (`repository.py:1859`) and `_environment_execution_ids`
  (`repository.py:1835`) gain an optional `project_ids` that wins over
  `project_id` when given. Existing dashboard callers are untouched.
- One helper does the predicate so the rule lives in one place:

```python
@staticmethod
def _apply_read_projects(stmt, column, project_ids: Sequence[str]):
    # A single project keeps the identical `==` predicate: same plan, same
    # index use, and existing tests compare unchanged SQL.
    if len(project_ids) == 1:
        return stmt.where(column == project_ids[0])
    return stmt.where(column.in_(list(project_ids)))
```

~12 literal `project_id ==` predicates in the lane change. The join-style
predicates (`log_counts.c.project_id == RuntimeExecutionRow.project_id`,
`diagnosed.c.project_id == ...`, `RuntimeIncidentRow.project_id ==
RuntimeExecutionRow.project_id`) **do not change** — they equate two columns and
already carry scope correctly under a widened population.

Two subqueries currently double-filter: `log_counts` and `diagnosed` in
`_doc_evidence_log_quality` (`repository.py:2915-2935`) apply a literal
`project_id == project_id` *and* join on project_id. Drop the literal and let
the join to the already-scoped execution population carry it. Same for the
incident/summary joins in `_doc_evidence_incidents`. This is what keeps the
insights tables correctly scoped despite having no `source_key`.

### 2.4 RBAC on the machine endpoint

`GET /runtime/doc-evidence` (`src/api/routes/runtime.py:315`) currently pins
scope to `[project_id]` — deliberately the narrowest possible, since it
authenticates with the MCP ingest key and has no user.

It now pins scope to `await repo.resolve_runtime_read_projects(project_id)`.

**Invariant: widening is only ever driven by stored grant rows, never by a
request parameter.** The endpoint signature does not change — no caller can ask
for a wider read. This must be stated in the route docstring and asserted by a
test that a forged `project_id` list cannot reach the scope.

`resolve_doc_read_scope` and `list_mirrored_environments` (`repository.py:758`)
widen the same way, so the environment picker offers a reader project the
environments of the tenant it was granted, and `unavailable_environment_ids`
keeps reporting selections that are not mirrored.

### 2.5 Freshness

`runtime_sync_state` is keyed `(source_key, project_id)`
(`runtime_data.py:111`). A reader project has no row, so freshness resolves to
unknown. `get_freshness` must resolve through the grant — read the sync state of
the granting source's own `(source_key, project_id)`. Otherwise a shared read
publishes evidence with no "as of" and the coverage artifact cannot say how
stale it is.

---

## 3. Slices

Each slice is independently shippable and independently demoable.

### Slice 1 — read grants, backend core

Table + migration 095; `resolve_runtime_read_projects`; widened
`resolve_doc_read_scope` / `list_mirrored_environments`; doc-evidence lane
widened per 2.3; `/doc-evidence` scope per 2.4; freshness per 2.5.

No API to create a grant yet — tests insert rows directly.

Tests:
- one project, no grants → SQL and results byte-identical to today (the
  regression floor)
- granted project sees the tenant's units; ungranted project sees nothing
- an aggregate computed over a shared read matches the same aggregate computed
  for the owner when scoped to the same environments (internal consistency —
  the pooled-environment defect class in `knowledge_base/CLAUDE.md`)
- insights joins return incidents only for in-scope executions after the
  literal filters are dropped
- forged wide `project_id` cannot widen scope

### Slice 2 — grant API

`GET/POST/DELETE /api/v1/runtime/sources/{source_id}/readers`, modelled on
`src/api/routes/runtime_workspace_map.py` (same shape, same audit pattern via
`log_runtime_source_event`, actions `reader_granted` / `reader_revoked`).

Authorisation for POST: caller must be a `RuntimeSourceWriter` **and** have the
source's project in `get_user_project_ids` **and** have the reader project in
it. A grant that widens a read must not be creatable by someone who could not
already see both sides.

Also in this slice: change the 409 in `runtime_sources.py:271-272` from a dead
end to a signpost — name the source that already holds the tenant and say the
supported action is a read grant. That is the fix for the reported symptom.
`promote_source_identity`'s 409 (`repository.py:954`) and the rotate-path 409
(`runtime_sources.py:410`) keep their current wording; they are different cases.

### Slice 3 — proxy + UI

The screenshot's dialog is the dashboard: `knowledge_base/dashboard/src/
components/connectors/ConnectorsPage.tsx` (1044 lines), which reaches the RAG
backend through `mcp-server`'s `api/routes/runtime_sources.py` +
`services/runtime_sources_bridge.py`. That bridge currently proxies six routes
and knows nothing of workspace-map or readers.

- mcp-server: bridge methods + proxy routes for `/readers` and the existing
  `/workspace-map` endpoints.
- dashboard: a "Shared with" panel on an existing source — list reader
  projects, add/remove, per-reader environment + window picker fed by
  `GET /runtime/doc-read-scope` for that reader.
- Same panel gets the workspace-map editor (container → project). It has had a
  working backend and no UI since it was built; it is the right control for the
  *different* case where two projects want disjoint slices of one tenant rather
  than a shared read.
- Present the two as what they are: **Share** (both projects read the same
  rows) vs **Split** (each project owns different workspaces' rows). Conflating
  them in the UI is how an operator ends up with the wrong one.

Whether `RAG_pipeline/frontend/app/settings/sources/ProjectConnectorBindings.tsx`
needs the same panel is deferred — the dashboard is where the failure was hit.

### Slice 4 — the run must state its scope

`knowledge_base/CLAUDE.md` already holds the rule: *"A run must state its scope.
Without it two runs of one project are not comparable and a narrowed read is
indistinguishable from a shrinking estate."* A shared read is exactly such a
case and currently has no way to declare itself.

- `RuntimeDocEvidenceResponse.read_scope` gains `shared_from_source: str | None`
  and `read_projects: list[str]`.
- `providers/custom/runtime_evidence.py` renders it into the
  `_analysis/_runtime_profile.md` coverage table and into
  `_assessment/runtime_evidence_coverage.json`.
- Watch the size clamp: `phase3_runtime_profile_bytes` (12,000) is clamped to
  `phase3_shared_context_file_bytes - 1` because `plan_phase3_slices`
  classifies context files as SHARED purely by size. Extra coverage prose must
  not push the rollup past that.

---

## 4. Key design decisions

| Decision | Chosen | Rejected alternative | Why |
|---|---|---|---|
| Where sharing lives | Read-side grant | Second `runtime_sources` row per project | A second row means a second credential, a second poll, two divergent mirrors of one tenant, and doubled vendor rate-limit load. The 409 exists to prevent exactly this. |
| Filter key | `project_id IN (...)` | `source_key IN (...)` | More precise, but four insights tables have no `source_key`. Two scoping rules in one lane is the defect class this codebase already documents. |
| Grant scope storage | On the grant row | Inherit the owner's credential-blob scope | Two projects reading one tenant will want different environments; that is the primary use case, not an edge case. |
| Owner scope storage | Left in the credential blob | Migrate everything onto grant rows | Cleaner end state, but migrating live config for no functional gain. Recorded as a follow-up. |
| Blast radius | Doc-evidence lane only | Sweep every repository query | The dashboard has no shared-read requirement yet. A sweep changes the query plan of every runtime page for a feature none of them use. |
| Single-project predicate | Keep `==` | Always `IN` | Identical SQL for the unshared case is the regression floor; it also keeps the existing index behaviour unchanged. |
| Rebinding a source | Not in scope | Add `project_id` to `RuntimeSourceUpdate` | A rebind orphans every already-mirrored row under the old `project_id`. It needs its own data-migration story; sharing does not. |

---

## 5. Risks and open questions

**R1 — the orphan tripwire inflates under sharing (must be handled in Slice 1).**
`orphan_runtime_jobs` is the mis-binding signal: runtime jobs in this project
and window that matched none of the requested units. Under a shared read the
runtime vocabulary becomes the whole tenant, so a project documenting 10 units
of a 400-job tenant reports ~390 orphans, and the generator is instructed to
treat that as a suspect binding. Left alone this turns a correct shared read
into a published accusation. Options: (a) restrict the vocabulary to the grant's
environment scope; (b) carry a `shared_read` flag so the generator reports the
orphan ratio as an observation rather than a binding fault — consistent with the
existing rule *"never accuse a project of a bad binding from match ratio alone"*.
Recommend (a)+(b). Needs a decision before Slice 1 is built.

**R2 — over-share via the workspace map.** Per 2.2, granting B read on a source
whose map routes rows to C also exposes C. Mitigation: the grant UI names every
project that will become readable, and the API returns that list on POST. Not
silently prevented — silently preventing it would make a grant do less than it
says.

**R3 — RBAC on a machine endpoint.** `/doc-evidence` is authenticated with the
ingest key. Widening its scope from stored state is the single most
security-relevant change here. Mitigated by 2.4's invariant plus an explicit
negative test.

**R4 — insights scoping after dropping the literal filters.** Correctness now
rests on the joins alone. Any future insights query that filters `project_id`
literally and does *not* join through the scoped execution population will
silently under-read for a reader project. Worth a comment at each site.

**R5 — TMC identity is environment-set derived.** If a token's visible
environment set changes at the vendor, `resolve_physical_identity` returns a
different hash. Unrelated to this work, but it means identity is not stable
forever; `list_unresolved_identity_source_ids` already exists to repair drift.
Grants key on `source_id`, not identity, so they survive it.

**Open questions**
1. R1: which orphan treatment?
2. Should a grant be revocable by the reader project's owner, or only by the
   source owner? (Recommend: either — revoking your own read is not a privilege
   escalation.)
3. Does the RAG frontend `settings/sources` page need the same panel, or is the
   dashboard sufficient?
4. Should Slice 2's 409 message name the owning source's project? It leaks one
   project name to an operator who may not have it in scope. (Recommend: name it
   only if the caller's `get_user_project_ids` includes it, else say "another
   project".)

---

## 6. Suggested next steps

1. Decide R1 (orphan treatment) and open question 4 — both change Slice 1/2 code.
2. Build Slice 1 behind no flag; the unshared path is byte-identical, so the
   regression floor is the test suite passing unchanged.
3. Slice 2, then verify on the VPS with the real second project that triggered
   this: grant, run doc generation, confirm `read_scope` in
   `_assessment/runtime_evidence_coverage.json` names the shared source.
4. Slices 3 and 4 can proceed in parallel once 1 and 2 are in.

Not in scope, recorded for later: unifying owner read scope onto grant rows;
rebinding a source to a different project; threading the selected pack into the
living-docs patch lane (unrelated, noted because it shares the "producer writes
files, consumers read files" seam).
