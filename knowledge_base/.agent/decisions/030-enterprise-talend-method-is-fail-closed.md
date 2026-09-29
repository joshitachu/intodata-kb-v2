# 030: Enterprise Talend documentation is a fail-closed knowledge-model projection

## Status
Superseded by ADR-032 (contract retired; see RETIRED_CONTRACTS in mcp-server/src/mcp_server/services/generation_profile.py). Originally: Accepted 2026-09-09; implemented, awaiting a paid pilot

## Decision

Contract `talend-enterprise-v1` (OpenCode, Talend only) replaces the per-job analysis
sessions with a small, durable, resumable phase sequence: DEEP ANALYZE ESTATE ->
RECONSTRUCT END-TO-END INTEGRATIONS -> BUILD ENTERPRISE KNOWLEDGE MODEL -> RECONCILE
CODE / DESIGN / RUNTIME -> WRITE CATALOGUE AND DOSSIERS -> REVIEW -> TARGETED REPAIR ->
PUBLISH (`providers/custom/enterprise/`).

- **Exact facts are deterministic; models interpret.** intodoc writes the complete
  sanitized export (`scan/sanitized/`, digest manifest, redaction-verified), exact mapping
  accounting (offered / written / mapped / constant / lookup / dynamic / unresolved), and a
  capability ledger. The runner stages them as `_workspace/` and `_analysis/_exact/`; a scan
  without the package cannot start an enterprise run. A model claim that retypes a mapping
  table is rejected; it must reference the exact table, which Python embeds verbatim.
- **Sessions read the complete sanitized workspace** with native read/grep/glob/list.
  Assignments are scheduling and recovery records; they never limit access. The pinned
  Integration Suite Talend method (revision `0c68b22633a6d067fcc30e29dd18e86d9ea6d24f`,
  digest-verified) is staged read-only under `_method/talend/` with a Nexus adaptation
  guide. Writes go through one typed submission tool; no bash, web or direct edits.
- **Submissions accumulate in per-phase ledgers.** Each entry is validated (schema,
  canonical ids, evidence path inside the evidence roots, verbatim quote present) and
  persisted immediately. A rejected entry never discards its accepted siblings. Remaining
  keys drive recovery rounds; a validation failure of an accepted entry drives a REPAIR
  session naming exactly that entry; absent output drives a REGENERATE round.
- **The knowledge model is the product boundary.** Writing is blocked until the assembled
  model passes grounding and completeness: every design analysed for four audiences, every
  entry point in a journey (or excluded with a reason), every exact mapping table
  interpreted, every context group and inline context explained, every error-handling design
  with an error path, every design with an explicit runtime status, every journey with a
  business-meaning status, open contradictions and unknowns recorded. A frozen question set
  (developer / operator / architect / business owner) is answered from the model only;
  unsupported questions resolve to an explicit unknown.
- **Client documents are projections.** `INTEGRATION_CATALOGUE.md` lists every detected
  interface once; related paths group by supplied business capability, else by exact
  system pair and direction; material groups get a top-level `INTEGRATION__<slug>.md`
  dossier (Python renders structure and exact tables, the model writes the prose sections).
  Provenance lives in `_evidence_registry/enterprise/` sidecars, never in prose; internal
  paths, ids, registers and pipeline vocabulary in a client document are a blocking finding.
- **Publication is fail-closed.** `_assessment/publication_verdict.json` is evaluated over
  the final staged product and again by the publication task. A failing verdict holds the
  atomic swap and the RAG push (`run_verdict = held_client_readiness`), keeps
  `docs_staging/` as a labelled draft and leaves the last published baseline untouched.
  Budget ceilings (session and run wall time, processed tokens, cost dispatch ceiling) stop
  new dispatches and end the run as explicitly incomplete; they never narrow coverage.
- **Runtime is a separate authority.** TMC (and an optional normalised Elastic-style
  aggregate) are fused into deployed-set, observation, correlation-latency and limitation
  records with environment and window provenance. Missing runtime data is `unavailable`,
  never non-execution; routes carry the DI-only observability limitation.

## Deviation from the plan

The plan named `integrations/<slug>.md`. Every publication consumer (doc listing, doc
count, RAG push, PDF export, the viewer route, quality scoring) enumerates top-level
`docs/*.md`, and the viewer rejects path separators. Dossiers are therefore top-level
`INTEGRATION__<slug>.md` files. Nothing else about the dossier contract changed.

## Consequences

- Other platforms and the legacy / landscape OpenCode contracts are untouched; the lane is
  selected only by the profile contract.
- A rescan is required before the first enterprise run on an existing project.
- Semantic quality, wall time and spend are unmeasured until the paid pilot (plan slice 10).
