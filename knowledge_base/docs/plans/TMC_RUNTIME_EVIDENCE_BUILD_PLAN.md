# TMC Runtime Evidence in Documentation — Build Plan

Merged decision record from a 4-way architecture debate (pipeline integration, evidence
value + live-data reality check, verification strategy, operator surface). This document
is the contract for the implementation agents. Where the debate disagreed, the ruling and
its reason are recorded here; do not re-litigate them in code.

## 1. What is true today (verified, not assumed)

- The RAG backend mirrors TMC into a runtime store. `GET /api/v1/runtime/doc-evidence`
  (`RAG_pipeline/backend/src/api/routes/runtime.py:289` → `store/repository.py:1794`)
  serves bounded, redacted aggregates. It works; a live call returns correct data.
- `knowledge_base/mcp-server/src/mcp_server/providers/custom/runtime_evidence.py` fetches
  and renders per-unit sections. Complete and unit-tested.
- **The lane is dead on default configuration.** `_load_runtime_evidence`
  (`harness.py:6781`) early-returns unless `_per_job_doc_units` is non-empty
  (`harness.py:6800`), and that map is only populated when
  `MCP_DOCGEN_UNIT_DOCS_ENABLED=1` (`config.py:509`, default **False**) or a pack declares
  `per_job: true`. Standard runs get nothing, silently.
- **OpenCode never fetches.** `opencode_cli.py` reuses the consumer
  (`harness._phase3_context_files`, `opencode_cli.py:1141`) but never the producer, so
  `runtime_store` is always `None` in that engine.
- Group/summary docs and Phase 2 receive no runtime data at all.

### Live-data findings that reorder the work

1. **Name matching is not the problem.** Where scan and runtime describe the same estate,
   exact name equality resolves 100% of runtime jobs. No env prefixes, no version
   suffixes, no case drift. **Do not build a normaliser or fuzzy matcher.**
   `reverse_engineer_VW` contains both `AssetCheck_GetLocation` and
   `AssetCheck_GetLocations` as separate scheduled tasks with separate histories — any
   similarity matcher cross-attributes them.
2. **The break is the `project_id` binding.** Scan dir `alitalend` holds the AssetCheck
   estate, whose 10 units match set-for-set the 10 runtime jobs filed under
   `reverse_engineer_VW`. Runtime `alitalend` holds a different 28-job workspace with no
   scan on disk. A doc run for `alitalend` today gets 1/10 coverage and is wrong about the
   one unit it matches.
3. **Low coverage is correct, not a defect.** 5 of 374 werkorder units have runtime data;
   the other 369 are `tRunJob` children and joblets TMC never schedules as tasks. A TMC
   task list is the deployed orchestration surface, not the artifact inventory.
4. **`MCP_INGEST_API_KEY` is empty in the mcp-server container.** Dev allows keyless;
   production requires it. `fetch_runtime_evidence` swallows every exception → silent,
   permanent, undetectable absence in production on a green run.
5. **Redaction gap.** 827 failure rows: zero credentials, zero IPs, zero emails — but
   customer-identifying GCS bucket names and an internal hosting FQDN pass through
   verbatim into LLM prompts.
6. **`runs_per_day` misleads.** 61.29 runs/day is rendered for a job that last ran 54 days
   ago (computed over the observed span). Duration percentiles are meaningless below a
   sample floor (one ESB route: p50 = 13.2 days over 5 successes).
7. **Plans are mostly unresolvable.** `_doc_evidence_plans` matches on `steps[].job_name`,
   which TMC leaves null for most steps; 4 of 5 werkorder units return `plans: []`.
8. `runtime_job_scan_maps` has 0 rows platform-wide.

## 2. Rulings

**R1 — Runtime evidence becomes a staging artifact, not provider state.**
Every other deterministic evidence tier (`_cross_cutting_facts.md`,
`_noncode_project_context.md`, `_unit_docs_digest.md`, `talend_esb_facts.json`) is a file
produced by a preparation step and consumed by name. Runtime evidence is the only one held
as an in-memory attribute (`harness.py:693`) populated inside one engine's phase driver.
That shape *caused* the OpenCode divergence and it will cause the next one. Producer
writes files; consumers read files.

**R2 — Source the unit list from the facts manifest, never from `_per_job_doc_units`.**
`state.facts_manifest.unit_names()` is already partition-filtered in both engines
(`harness.py:1694-1700`, `opencode_cli.py:343-346`) and is already the scoping authority
everywhere else. Inheriting scope beats reimplementing it. Delete the
`_per_job_doc_units` early return.

**R3 — Batch the fetch client-side at 200 units.** `_DOC_EVIDENCE_MAX_UNITS` is a
FastAPI `max_length`; exceeding it returns 422, which the bare `except Exception` converts
into "no runtime data for the entire project". Sourcing from the manifest makes >200 the
normal case. Do not raise the server cap.

**R4 — One project-level rollup file, attached to every Phase 3 document.**
`_analysis/_runtime_profile.md`, written once at Phase 3 entry, never mutated, placed
immediately after `_cross_cutting_facts.md`. Per-doc variants are rejected: they break
byte-stability and defeat provider prefix caching (the R2.6 audit measured a collapse to
0.7% cache hits when a prompt head moved).

**R5 — 12 KB ceiling, asserted at load time.** `plan_phase3_slices` classifies context
files as SHARED purely by size against `phase3_shared_context_file_bytes` (16,000). At
16,001 bytes the rollup gets bin-packed as evidence and slice 1 sees the failing jobs
while slice 3 does not. `phase3_runtime_profile_bytes` defaults to 12,000 and config load
**asserts** it is strictly less than the shared threshold. Overflow drops whole rows by
deterministic rank and says how many were omitted; it never truncates mid-table.

**R6 — No runtime data in Phase 2 claim extraction, and none in Phase 1.**
Phase 2's contract is claim extraction with verbatim code citations; `apply_claim_floors`
demotes any claim that cannot cite the code packet, so runtime-derived claims are paid for
and then discarded. Cost is multiplicative across bin-packed tasks (Assetcheck ran 73).
Phase 1 output feeds the allowed-evidence set for the component-echo check; adding non-code
facts there loosens a fabrication guard for no retrieval benefit. Runtime as a *depth
selector* for Phase 2 (zero prompt bytes, decided in Python) is deferred, not rejected.

**R7 — Absence is always stated, never silent.** Once Phase 3 starts,
`_analysis/_runtime_profile.md` exists and its content is exactly one of: data,
opted-out, unavailable, partial, or binding-suspect. `runtime_evidence_coverage.json` is
written in **all** cases including fetch failure. This is what makes "no document infers
'this job never runs' from silence" structural rather than a prompt-adherence hope.

**R8 — Binding validation, not fuzzy matching.** When the fetch returns zero matches and
the endpoint reports orphan runtime jobs, the run emits a BINDING SUSPECT warning and the
rollup says so explicitly. Never silently attach 4%-correct evidence. Candidate-binding
suggestion across other projects is deferred (it needs a cross-project machine read).

**R9 — Coverage is reframed, not maximised.** The rollup leads with a coverage statement
including the caveat that TMC covers scheduled tasks and that child jobs invoked via
`tRunJob` do not appear as tasks — their absence is not evidence they do not run. No
coverage floor suppresses the lane.

**R10 — Per-unit table content changes.** Lead with days-since-last-run. Gate duration
percentiles behind `success_runs >= 30` (else `insufficient sample (n=N)`). Replace the
`runs_per_day` float with a cadence classification plus an explicit staleness qualifier.
Drop `plans` from the per-unit table until plan resolution is fixed. Fold
`unfinished_runs` into a telemetry-gap note.

**R11 — Cadence is classified deterministically in Python, never derived by a model.**
Median inter-run gap snaps to exact cron intervals (verified: 3600s, 1801s, 900s). Rule:
median within ±10% of a common interval AND n >= 20 AND IQR/median < 0.5 → emit the
interval; else `ad hoc (N starts)`. A model would average, and the mean gap for
`Maximo2RHM_Workorders` is 2204s, which is not any schedule anyone configured.

**R12 — Runtime evidence is a legitimate component-echo source.** Real `error_summary`
text names components (`tMSSqlConnection_2`). The component-echo validator rejects
`tXxx_N` references absent from the agent's evidence. This holds today only by accident
(runtime and scan describe the same revision). A runtime-cited component missing from the
scan must be rendered as a *flagged fact* ("the deployed build may differ"), never
silently passed — that mismatch is itself a finding, and burying it converts a real signal
into a fabrication false-positive.

**R13 — Redaction extends to entity shapes, not just keywords.** Normalise URIs and hosts
(`gs://bucket/path` → `gs://<bucket>/<path>`, bare FQDNs → `<host>`), preserving the
scheme. Strip execution UUIDs from prompt text. **Preserve component names deliberately**
— they carry the diagnosis and R12 depends on them. Add a render-time assertion writing
violations to `_assessment/runtime_redaction_flags.json`, dropping the offending line
rather than the run.

**R14 — TMC is visible in the dashboard connector list but not connectable there.** Its
credential home is `:3000/admin/runtime`, which is complete and registry-driven. A second
connect form would let an operator deposit a credential in a store the scheduler never
reads. Add `managed_by` to the provider manifest (`"connector_store"` default,
`"runtime_sources"` for TMC) — one explicit field, not a three-field inference over
`kind`/`capabilities`/`setup_capabilities`.

**R15 — The dialog bug is fixed by the state shape, not by a conditional.**
`editing: ConnectorInstance | null | undefined` carries the instance but not the provider,
so the render site has nothing to dispatch on. Make it
`{provider, connector} | null` and dispatch through a `connectorDialogRegistry` keyed on
`provider_id`. No registry entry ⇒ no Connect action offered anywhere. A registry-coverage
test is the anti-recurrence guard. **Not** building a generic manifest-driven form this
session: `configuration_schema` is a flat dict of prose hints, and a generic renderer
would regress conditional auth, the Kibana companion, HTTPS validation, and the
create→rotate→discover→test sequence.

**R16 — Engine parity is enforced by a test, not by discipline.** A module-level
`DETERMINISTIC_PHASE3_ARTIFACTS` tuple names every artifact preparation must produce; both
engines iterate it; a test asserts both engines' preparation produces an identical artifact
set. A future tier added to one engine and not the other becomes a red test.

**R17 — Never a gate.** Runtime absence, binding suspicion, and redaction flags are all
advisory, consistent with the 2026-06-11 decision that Phase 3 final checks are warnings.
A missing runtime mirror is a normal deployment state.

## 3. Rollup content (`_analysis/_runtime_profile.md`)

All deterministic. Model may interpret; model may not recompute.

1. **Coverage and confidence** — scanned units, units with observed runtime, orphan
   runtime jobs, window, source key, last sync. Plus the `tRunJob` caveat (R9).
2. **Reliability ranking** — matched units by failure rate desc, then runs desc:
   unit, runs, failure %, streak, environments, days silent.
3. **Failure-mode census** — project-wide, grouped, sorted by occurrence, with a **share**
   column. This is the payload: it turns 785 failure rows into "one SQL Server connection
   is responsible for 89% of every failure in this project".
4. **Observed schedule** — cadence classification per unit (R11), coverage hours,
   environments.
5. **Dormant and dead** — two explicitly separated lists: *silent* (matched, no run in
   >=30 days) and *unobserved* (scanned, no runtime record, labelled "not scheduled as a
   TMC task"). Only the intersection of unobserved with "not referenced by any `tRunJob`
   edge" is a dead-code candidate. Publishing the raw unobserved list as dead code would
   be a serious, confident error.
6. **Orphan runtime** — runtime jobs with no scanned unit. This is the tripwire that
   catches a mis-bound project before anyone reads a wrong document.

## 4. Work packages and file ownership

Agents must not edit files outside their package. Cross-package needs go through the
interface contract in §5.

### P1 — Evidence core (owner: agent MCP-CORE)
- `knowledge_base/mcp-server/src/mcp_server/providers/custom/runtime_evidence.py`
- `knowledge_base/mcp-server/src/mcp_server/config.py`
- `knowledge_base/mcp-server/tests/test_providers/test_runtime_evidence.py`

Batched fetch with per-batch failure isolation; `partial` / `requested_units` /
`orphan_runtime_jobs` on the store; `to_json`/`from_json`; `project_rollup()` implementing
§3 with R5 ceiling behaviour and the five absence modes (R7); per-unit render changes
(R10); cadence classifier (R11); redaction assertion (R13, MCP side).

### P2 — Harness and engine wiring (owner: agent MCP-WIRING)
- `providers/custom/harness.py`, `providers/custom/target_evidence.py`
- `providers/custom/pipeline/helpers.py`, `providers/custom/pipeline/phases/phase3_docs.py`
- `providers/opencode_cli.py`
- `tests/test_providers/test_runtime_evidence_wiring.py` (new), `test_target_evidence.py`

`_ensure_runtime_evidence` replacing `_load_runtime_evidence` (R1, R2); artifact writes;
`TargetEvidenceBuilder` rehydrates from disk instead of constructor injection (closes the
stale-builder hazard where a builder cached before the fetch freezes `runtime_store=None`
for the whole run); rollup attached to all Phase 3 docs (R4); shared helper on the facade;
OpenCode calls it from `_prepare_run` (make async; single call site at `:3203`);
`DETERMINISTIC_PHASE3_ARTIFACTS` + parity test (R16); the three missing log lines.

### P3 — RAG backend (owner: agent RAG-BACKEND)
- `RAG_pipeline/backend/src/runtime/redaction.py`
- `RAG_pipeline/backend/src/runtime/store/repository.py`
- `RAG_pipeline/backend/src/api/routes/runtime.py`, `src/runtime/models.py`
- `RAG_pipeline/backend/tests/test_runtime/`

Entity-shape redaction (R13); `orphan_runtime_jobs` on the doc-evidence response (feeds
R8 and rollup §6); plan resolution via `steps[].job_id → runtime_jobs.job_id` (R10);
route-level API tests (auth, unit cap → 422, scope pinning) which do not exist today.

### P4 — Operator surface (owner: agent UI-OPS)
- `knowledge_base/dashboard/src/components/connectors/ConnectorsPage.tsx`,
  `GenerateDialog.tsx`, `RunHistory.tsx`, `src/api/client.ts`, `src/api/schemas.ts`,
  `src/types.ts`, `src/tests/`
- `knowledge_base/mcp-server/src/mcp_server/connectors/domain.py`,
  `connectors/providers/talend_tmc.py`, `api/routes/connectors.py`,
  `api/routes/generate_runs.py`, `mcp_server/schemas.py`

Dialog registry + typed state (R15); manifest-driven row presentation; `managed_by` (R14);
`GET /mcp/generate-runs/{run_id}/connector-evidence` (the client method, hook, zod schema
and popover already exist and call a route that was never implemented) returning a
`runtime` block from `_assessment/runtime_evidence_coverage.json`; move the runtime panel
in `GenerateDialog` off the OpenCode-only branch onto the legacy path that actually reads
`live_evidence`, and expose the `live_evidence` checkbox with a **narrowly-scoped label**
(it gates only the TMC lane; OpenCode connector calls ignore it).

## 5. Interface contract (frozen — agents code against this)

```python
# runtime_evidence.py — owned by MCP-CORE, consumed by MCP-WIRING

RUNTIME_EVIDENCE_JSON = "_evidence/runtime/runtime_evidence.json"
RUNTIME_PROFILE_MD    = "_analysis/_runtime_profile.md"

class RuntimeMode(str, Enum):
    DATA = "data"; OPTED_OUT = "opted_out"; UNAVAILABLE = "unavailable"
    PARTIAL = "partial"; BINDING_SUSPECT = "binding_suspect"

@dataclass(frozen=True)
class RuntimeEvidenceStore:
    window_days: int
    by_unit: Mapping[str, RuntimeUnitEvidence]
    unmatched_units: tuple[str, ...]
    orphan_runtime_jobs: tuple[str, ...] = ()
    requested_units: int = 0
    partial: bool = False
    mode: RuntimeMode = RuntimeMode.DATA

    def section_for(self, unit_name, *, is_esb_route=False, max_bytes) -> str: ...
    def to_json(self) -> dict: ...
    @classmethod
    def from_json(cls, payload: Mapping) -> "RuntimeEvidenceStore": ...

async def fetch_runtime_evidence(project_id, unit_names) -> RuntimeEvidenceStore | None: ...
def project_rollup(store: RuntimeEvidenceStore | None, *, mode: RuntimeMode,
                   max_bytes: int) -> str: ...   # never returns "" — always renders a mode
```

New config (owned by MCP-CORE): `phase3_runtime_profile_bytes: int = 12_000`
(`MCP_PHASE3_RUNTIME_PROFILE_BYTES`), asserted `< phase3_shared_context_file_bytes`.

Coverage JSON schema (written by MCP-WIRING, read by UI-OPS):
```json
{"mode": "data|opted_out|unavailable|partial|binding_suspect",
 "window_days": 90, "units_requested": 374, "units_matched": 5,
 "matched": [...], "unmatched": [...], "orphan_runtime_jobs": [...],
 "partial": false, "batches": 2, "source_key": "tmc"}
```

## 6. Constraints that bind every package

- ASCII-only in anything that reaches a generated document (Windows cp1252).
- No raw log lines cross the boundary. Do not widen the endpoint payload.
- No credentials to any LLM; R13 extends this to customer identifiers and internal hosts.
- Never raise on runtime absence (R17).
- MCP API route changes require `/inspect` before commit (`knowledge_base/CLAUDE.md` r13).
- Match existing test conventions: pytest both Python sides, Vitest frontend, HTTP faked
  by monkeypatching `httpx.AsyncClient` **at the consuming module** (the from-import at
  `harness.py:61-63` means wiring tests must patch
  `mcp_server.providers.custom.harness.fetch_runtime_evidence`, not the module of origin).

## 7. Verification

Regression set (must be green before and after):
```
cd knowledge_base/mcp-server && pytest tests/test_providers/ tests/test_connectors/
cd RAG_pipeline/backend && pytest tests/test_runtime/
cd knowledge_base/dashboard && npm test
```

Keystone tests (must fail before the fix, pass after):
- Custom harness delivers a sentinel runtime fact into the doc writer's context.
- OpenCode delivers the same sentinel.
- Fetch happens under default config with `_per_job_doc_units` empty.
- Rollup at exactly `phase3_runtime_profile_bytes` is classified SHARED by
  `plan_phase3_slices` and is byte-identical in every slice.
- Coverage JSON exists when the backend is unreachable.

End-to-end: `cdc_job_route` (2 units, 1 group, under the small-project threshold →
~12-16 model calls). Seed known execution counts for one unit, leave the other unseeded,
run both engines, and grep `output/cdc_job_route/docs/` for the exact seeded numbers, the
absent-unit notice, and `runtime_evidence_coverage.json`. You invented the numbers, so the
doc either echoes them verbatim or the chain is broken.

## 7b. AMENDMENTS after red-team review (these override §2-§5 where they conflict)

A red-team pass verified the plan against the live tree and database. Its finding "P3/P4
are already implemented" is discounted — it read the working tree while the P3 and P4
agents were writing to it. The following corrections stand and are binding.

**A1 (overrides R2) — request BOTH identifier keys.** `FactsManifest.unit_names()` returns
`{u.name}` where `name = str(job.get("job_id") or display_name)`
(`providers/custom/validators.py:184`). It is the **job_id**, not the display name TMC
schedules under. Verified: werkorder has 429 job rows / 374 distinct display names, of
which **79 have `job_id != name`**; a live call requesting manifest names matched 4 units
while display names matched 5 — the lost unit is `Maximo2Quintiq_Workorder_Route`, the ESB
route this plan cites in §1.6 and the reason `_ROUTE_DURATION_LABEL` exists.
Request the union of `unit.name` and `unit.display_name`, deduplicated, and reconcile on
the response's `unit_name` back to the manifest unit. This is not fuzzy matching — it is
sending the right exact strings. `build_unit_index` (`target_evidence.py:134-155`) already
does this two-key reconciliation on the scan side; reuse that pattern.
Neither `cdc_job_route` (no `job_id` at all) nor `alitalend` can detect this defect, which
is why it survived four architects.

**A2 (extends R3) — merge rules are part of the contract.** The endpoint computes orphans
per call, so for batches `i` with matched sets `M_i` over a fixed runtime vocabulary `A`,
each call returns `O_i = A - M_i` and the true orphan set is `intersection(O_i)`, NOT the
union. Union reports every runtime job as an orphan the moment the unit list is split —
which R3 makes the normal case. Merge: `by_unit` and `unmatched_units` by union,
`orphan_runtime_jobs` by **intersection**, and a **failed batch is excluded from the
intersection entirely** (it has no `M_i`; treating it as `M_i = {}` poisons the result).
Batch by **URL byte length as well as count**: 200 job names at ~30 chars is ~7 KB of query
string against a common 8 KB request-line limit, and a 414 currently degrades to "no
runtime data at all" through the bare `except`.

**A3 (replaces R8/BINDING_SUSPECT) — state the asymmetry, do not accuse.** R8's trigger
("zero matches plus orphans") does not fire on the one confirmed mis-binding: `alitalend`
returns 1 of 10 matched, not zero. A match-ratio rule flags healthy werkorder (5/374,
correct per R9) and misses alitalend. The discriminating signal is the **runtime-side
orphan ratio** (werkorder 1/6 = 17%; alitalend 91/92 = 99%) — but that alone cannot
distinguish a mis-binding from a deliberately partial scan. So: no `BINDING_SUSPECT` mode
and no accusation. Report both ratios as facts and offer both readings ("the runtime source
lists N jobs with no scanned counterpart; this scan may cover a subset of the deployed
estate, or the project binding may be wrong"). A confident false accusation on every
partially-scanned project fails this plan's own standard.

**A4 (replaces the §5 enum) — `mode` and completeness are different axes.**
`mode ∈ {data, opted_out, unavailable}` plus independent booleans `partial` (not every
batch retrieved) and `scope_mismatch` (high orphan ratio). A partial fetch with a high
orphan ratio has no valid single enum value.

**A5 (narrows R4) — the rollup goes to GROUP/SUMMARY docs only, never to unit docs.**
Three independent reasons converge:
- *Fabrication false positives.* Phase 3 component-echo validates a unit doc against its
  own target bundle only (`harness.py:6079-6082`). A project-wide failure-mode census names
  components belonging to other units; a unit-doc writer echoing one is rejected as
  fabrication though the component is real. That is the same defect class
  `job_scoped_component_reference_errors` was built to fix, and it burns repair budget and
  produces salvage stubs.
- *Cost.* With unit docs enabled, werkorder is 429 unit docs x ~3,000 tokens = ~1.3M added
  input tokens per run, up to ~2.6M under the 2-calls-per-unit-doc budget model. Group-only
  is ~33K.
- *The caching argument does not apply there anyway.* Unit docs lead with a per-doc
  `_analysis/_evidence_{doc}.md` bundle, so the prefix diverges at byte 0 and nothing placed
  after it is cacheable across docs. R4's byte-stability rationale holds only for
  group/summary docs, where the leading context files are shared.
Unit docs keep their existing per-unit runtime section, which is already correctly scoped.

**A6 (fixes R7) — three delivery paths defeat "structural".**
- `09_EXECUTIVE_SUMMARY` early-returns at `harness.py:6863-6874`, before the shared-facts
  append. Handle it explicitly or the one document most likely to make an unsupported
  reliability claim is the one document that never sees the rollup.
- The tool-enabled first pass sets `context_files = []` (`harness.py:4718`). Default off
  (`agent_phase3_first_pass_tools = False`), but state the limit rather than claim
  structure.
- `phase3_slices._trim_priority:339-355` gives an unrecognised file rank 2 and trims it at
  *section* granularity — the coverage header can survive while the unobserved-units list
  is cut. Add `_runtime_profile` to the rank-0 branch. **Do not put `_evidence_` in the
  filename**: that substring silently promotes to rank 0 at `:341` and would collide with
  the per-doc bundle convention.

**A7 (fixes R1 placement) — the cache must not be published.** Phase 4 cleanup is a
denylist (`_preprocess`, `_analysis`, `_transient` only) and the swap is a whole-directory
rename, so `_evidence/runtime/runtime_evidence.json` would ship into `output/{pid}/docs/`
and accumulate — carrying raw `error_summary` text, the field §1.5 says leaks customer
bucket names and an internal FQDN. Put the cache under `_transient/` and **redact before
persisting**, not only before rendering. `_evidence/` is also an existing namespace with a
different meaning (`harness.py:6453`).

**A8 (fixes R11/R10) — aggregate per environment.** `runtime_jobs` is unique on
`(project_id, source_key, job_id)`, not on `name`: alitalend has 124 rows / 92 distinct
names, werkorder 8 / 6. Cadence and last-run computed over a name therefore blend DEV, TEST
and PROD executions, and an hourly PROD schedule plus ad-hoc DEV runs will fail R11's
`IQR/median < 0.5` gate — degrading exactly the multi-environment jobs it most wants to
classify. Compute per environment, or state which environments a figure aggregates.

**A9 (replaces R5's assert) — clamp, do not assert.** `MCPServerConfig` is instantiated at
import with no validators; an unconditional assert crashes the MCP server at startup when
an operator lowers `MCP_PHASE3_SHARED_CONTEXT_FILE_BYTES`, and `assert` is stripped under
`python -O`. Clamp `phase3_runtime_profile_bytes` to `shared_context_file_bytes - 1` and
log once. Consistent with R17.

**A10 (fixes R7 on resume) — keep the once-per-run guard in memory or key it on `run_id`.**
Staging persists across resume. An on-disk existence check would make a resumed run adopt
the failed run's snapshot and pin `mode: unavailable` for every subsequent resume.

**A11 (replaces R16) — no general artifact registry.** Engine parity is already broken in
six other places (`write_talend_intelligence`, `_ensure_noncode_plan`,
`_materialize_cross_project_refs`, `_prepare_evidence_registry`, `_prepare_pack_artifacts`
all run in the custom engine and not in OpenCode), and roughly half of Phase 3's inputs are
lazy or conditional, so a static set-equality test passes on day one and catches nothing.
Ship the narrow, honest version instead: a test asserting **both engines produce the runtime
profile and the runtime cache** for the same fixture. Broader parity is a separate problem
and is now recorded in §8.

**A12 — shared coverage schema.** The writer and the already-built reader disagree
(`harness.py:6817-6828` emits five fields; `generate_runs.py:239-256` also expects `mode`,
`source_key`, `partial`, `batches`, `orphan_runtime_jobs`), which today yields the
contradictory `available=true, mode="unavailable"`. Define the schema once in
`runtime_evidence.py` and have both sides import it. Markdown prose in §5 is how this
happened.

**A13 — sequencing.** P1 then P2, sequentially; P2 imports P1's symbols and cannot compile
before it lands. P2 additionally owns `phase3_slices.py` (A6),
`pipeline/phases/phase4_cleanup.py` (A7), `services/partition_orchestrator.py` and
`api/routes/projects.py` (coverage survival, scoped/partition gating) — the plan left those
unowned. Scoped runs (`scoped_docs`) narrow `_required_docs` but not the unit set, and the
living-docs patch lane (`services/true_patch_executor.py`) never reads
`_phase3_context_files` at all, so patched documents never gain runtime prose while the
coverage artifact beside them claims to describe them. Both must be handled or explicitly
recorded as known limits.

**A14 — E2E must include the werkorder key assertion.** `cdc_job_route` has 2 units with no
`job_id`, sits under `small_project_threshold`, and its runtime data is filed under project
key `cdc` — so it cannot detect A1, A2, A5 or A6. Keep it as the cheap smoke test and add a
mandatory no-model dry run on werkorder comparing matched counts under both key strategies.
That single check catches A1.

## 8. Deferred (explicitly not this session)

Phase 2 depth selection from runtime; business-topology live-vs-declared edges;
`runtime_claim_errors` fabrication validator; generic manifest-driven connector form;
auto-population of `runtime_job_scan_maps`; the unmatched-job remediation dialog in the
RAG frontend; per-run coverage archival; candidate-binding suggestion across projects;
composite display-name collision (two sub-projects sharing a unit display name would both
claim the same executions — recorded as a known limitation).

### Known limitations shipped with P2 (recorded, not fixed)

1. **The living-docs patch lane gains no runtime prose.**
   `services/true_patch_executor.py` builds its own prompt context and never calls
   `harness._phase3_context_files`, so a document amended in place is never shown
   `_analysis/_runtime_profile.md` and never gains a runtime section — while
   `_assessment/runtime_evidence_coverage.json` sitting beside it in the published
   tree describes the whole project, patched documents included. The gap is stated
   in the docstring of `harness._ensure_runtime_evidence` (the artifact's producer,
   which is where a reader chasing the discrepancy arrives). Threading the runtime
   rollup into the patch lane is separate work, and it is the same thread as the
   already-open "the patch lane cannot see the project's selected pack" problem.
2. **The tool-enabled Phase 3 first pass sets `context_files = []`**
   (`harness.py:4718`), so on that path no deterministic context file reaches the
   writer, the rollup included. Default off (`agent_phase3_first_pass_tools = False`).
   Stated rather than claimed structural, per A6.
3. **Scoped runs publish a project-wide coverage record.** `_apply_scoped_docs`
   narrows `_required_docs` but not the unit set, so a 2-document scoped run measures
   the whole project. Rather than narrow the fetch — which would make the rollup's
   coverage denominator disagree with the sibling documents preserved byte-stable
   from the baseline — the record carries a `run_scope` block naming the discrepancy
   (`kind: scoped_docs`, `covers_all_documents: false`, the regenerated doc list).
4. **Engine parity beyond this lane is unverified.** Per A11 no general artifact
   registry was built; the shipped guard asserts only that both engines produce the
   runtime profile and the runtime cache for the same fixture. The six other
   preparation steps that run in the custom engine and not in OpenCode
   (`write_talend_intelligence`, `_ensure_noncode_plan`, `_materialize_cross_project_refs`,
   `_prepare_evidence_registry`, `_prepare_pack_artifacts`) remain divergent.
