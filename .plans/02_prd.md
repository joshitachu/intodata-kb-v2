---
feature: Generalize and harden the runtime log intelligence mechanism
pipeline_step: 2_prd
---

## Executive Summary
The runtime log mechanism (parser.py + builder.py) turns raw log events into
incidents, signatures, and evidence, but its extraction and cause logic are
hardcoded to Oracle/Talend, attribute causes by position rather than causality,
report confidence as a signal-count, and leak inline JDBC credentials. This work
replaces the inline logic with a data-driven failure-pattern catalog
(Strategy/Registry), fixes causal extraction and signature stability, makes
confidence honest, and adds an opt-in, bounded LLM fallback only for low-confidence
incidents. Done means a new platform or failure family is added as catalog data
plus tests -- with zero edits to parser.py or builder.py control flow.

## Problem
parser.py and builder.py carry five defects. (1) `_ORA_CODE_RE` is the only
error-code matcher and `_derive_root_cause` / `_build_next_checks` hardcode
`ORA-12` branches, so every non-Oracle failure degrades to generic text and each
new failure type means another inline branch -- the special-case accumulation
CLAUDE.md forbids. (2) Multi-line messages are parsed flat: `qualified[0]` returns
the outermost exception, never the `Caused by:` root, and `primary_error_code` /
`failed_component` are first-seen rather than causal, so a benign earlier event
misattributes the cause and poisons the signature. (3) The signature
`error_code|exception_class|failed_component` inherits that misattribution, and the
`unknown|{job_name}` fallback collapses unrelated unknown failures of one job into
a single recurring group. (4) Confidence counts present signals, not correctness,
so a wrong templated cause can ship as "high". (5) `_JDBC_URL_RE` stores the whole
`jdbc:...user/pass@host` substring into `endpoint`, and `redact_credentials` only
catches `keyword=value`, so inline credentials survive into a surfaced field;
severity is matched literally and misses SEVERE/FATAL/uppercase.

## Goals
1. All error-code, root-cause, and next-check knowledge is expressed as catalog
   data; adding a platform/failure family requires no parser/builder control-flow
   edits.
2. The parser resolves multi-line messages to the deepest `Caused by:` root
   exception and selects the terminal/causal error code and component, not the
   first-seen one.
3. The failure signature is stable under noise and never merges unrelated unknown
   failures by job_name; recurrence counts reflect genuinely identical failures.
4. Confidence reflects evidence quality and is surfaced honestly in the data
   (not only in UI copy).
5. An opt-in, off-by-default LLM fallback improves only low-confidence incidents,
   using bounded redacted evidence (never raw logs), via the existing
   provider-neutral client.
6. No credential pattern -- keyword form or inline `user:pass@host` -- appears in any
   persisted insight field; severity matching is case/format-insensitive.

## Non-Goals
- Ingestion transport (TMC sync, polling, webhooks, file drops).
- Write-back to source (retry/rerun) and operator state (ack/resolve/edit).
- Raw log windows to the LLM as the primary strategy -- LLM is a bounded fallback only.
- Operational analytics dashboards and alert delivery.
- Document ingestion, embeddings, chunking, Qdrant retrieval.

## User Stories
- As an operator triaging a non-Oracle (MySQL/Postgres/HTTP/Mule) failure, I want a
  specific root cause and next checks, so that I am not handed generic
  "review the log" text.
- As an operator reading a wrapped Java stack trace, I want the reported exception
  to be the `Caused by:` root, so that I chase the real fault, not a wrapper.
- As an operator relying on the recurring badge, I want "recurring" to mean the same
  failure actually happened before, so that I trust the count.
- As a security reviewer, I want no database credentials to ever appear in stored
  insight fields or LLM input, so that secrets never leak through diagnostics.
- As a maintainer adding support for a new platform, I want to add a catalog entry
  and a test, so that I never touch parser/builder logic.

## Functional Requirements
1. A `FailurePatternCatalog` maps a matched signal set (error code family,
   exception class, message family) to: a human root-cause template, an ordered
   next-checks list, and a stable pattern id. Catalog entries are data, loaded once.
2. The error-code matcher recognizes multiple families (at minimum: Oracle `ORA-`,
   generic SQLSTATE, MySQL `ER_`/numeric, Postgres SQLSTATE, HTTP status, and a
   generic `[A-Z]{2,}-\d+` form) via the catalog, not inline regex in builder.
3. Exception extraction parses multi-line messages and returns the deepest
   `Caused by:` exception when present; otherwise the most specific single exception.
4. `primary_error_code` and `failed_component` are selected from the terminal/causal
   error event (the failure-attributed line), with a defined, tested tie-break, not
   the first matching row.
5. The signature is computed from the resolved causal signals; the `unknown` branch
   does not merge unrelated failures -- distinct unknown failures get distinct
   signatures (job_name alone is insufficient).
6. Confidence is derived from evidence quality (catalog match strength + signal
   agreement), and a `low` confidence incident is explicitly marked as such in the
   persisted incident row.
7. An opt-in LLM fallback, gated by an env flag (default off), runs only when an
   incident's deterministic confidence is `low`; it receives only the already-
   redacted, count-capped evidence excerpts and produces a candidate root cause /
   next checks. Its output is marked with a distinct cause origin and never
   overwrites a high/medium deterministic result.
8. Credential redaction covers inline `user:pass@host` JDBC forms in addition to
   `keyword=value`; redaction is applied before any value is persisted or sent to
   the LLM.
9. Severity classification normalizes case and common aliases (ERROR/SEVERE/FATAL ->
   error; WARN/WARNING -> warning) before counting and selection.

## Non-Functional Requirements
- Deterministic path makes no network/LLM call and stays linear in log-row count;
  per-execution build remains synchronous.
- LLM fallback: single call per low-confidence incident, <= 5 evidence excerpts,
  each <= 300 chars, with a hard input-token cap; off by default; provider-neutral
  via `src/generation/llm_client.py` (no new provider, no Claude CLI dependency).
- Security: a test asserts no credential pattern (keyword or inline form) appears in
  `endpoint`, `excerpt`, `summary_text`, or LLM input for credential-bearing fixtures.
- RBAC: every new/changed query carries `project_id` scope; signature lookup is never
  driven by arbitrary external input.
- Rebuildability: insight tables remain fully rebuildable from the raw runtime store;
  a signature-algorithm change ships with a rebuild path for existing rows.

## Architecture Impact
- New module `src/runtime/insights/catalog.py` (FailurePatternCatalog: data + match
  API). parser.py and builder.py call into it; their control flow becomes
  pattern-agnostic. This matches the CLAUDE.md design-pattern / no-special-case rules.
- parser.py: multi-line/`Caused by:` handling, multi-family code matching delegated
  to the catalog, severity normalization, inline-credential redaction fix.
- builder.py: causal selection for primary code/component, catalog-driven cause and
  next-checks, evidence-quality confidence, signature recomputation.
- Schema (runtime_insights.py): add a cause-origin marker to `RuntimeIncidentRow`
  (deterministic vs llm_fallback) and optionally a `pattern_id` on
  `RuntimeEnrichedEventRow`/`RuntimeIncidentRow`. Requires an Alembic migration.
  Because the signature algorithm changes, existing `RuntimeErrorSignatureRow` /
  incident signatures must be rebuilt (data migration or documented re-seed).
- LLM fallback reuses `src/generation/llm_client.py`; no change to the RAG retrieval
  chain or auth/RBAC layers.
- Read-layer models (insights/models.py) gain the cause-origin field so the UI and
  runtime tools can distinguish deterministic from LLM-assisted causes.

## Implementation Phases
### Phase A -- Catalog + generalization (no behavior regression)
Goal: replace inline Oracle/ORA-12 logic with a data-driven catalog; add multi-family
matching, severity normalization, and the inline-credential redaction fix.
Deliverables: `catalog.py`; parser/builder delegate to it; severity + redaction fixes.
Validation: existing Oracle fixtures still produce the same incident; new
MySQL/Postgres/HTTP/Mule fixtures produce specific (non-generic) causes; credential-
leak and severity-variant tests pass.

### Phase B -- Causal correctness + honest signature/confidence
Goal: multi-line `Caused by:` resolution, terminal/causal selection, signature
stabilization, evidence-quality confidence, cause-origin schema field + migration.
Deliverables: parser/builder changes; Alembic migration; signature rebuild path.
Validation: stack-trace fixture reports the root exception; misattribution fixture
selects the causal component; two unrelated unknown failures get distinct signatures;
recurrence counts only identical failures; low confidence persisted as `low`.

### Phase C -- Bounded opt-in LLM fallback
Goal: env-gated escalation for low-confidence incidents only, redacted bounded
evidence, provider-neutral, marked cause origin, never overwriting deterministic
high/medium results.
Deliverables: fallback hook in builder; flag wiring; cause-origin propagation to
read models.
Validation: with flag off, behavior is identical to Phase B; with flag on, only
low-confidence incidents call the LLM, input is redacted and capped, and origin is
recorded; a token/evidence-cap test passes.

## Risks and Open Questions
- Multi-line traces are assumed to arrive within one message (confirmed seed.py:180).
  If a real source splits a trace across rows, cross-row grouping is a larger,
  separate change.
- Changing the signature algorithm breaks recurrence continuity for already-built
  incidents; a rebuild/migration is required and must be validated against the
  slice-5 recurrence feature.
- LLM fallback cost/latency and provider availability; must stay provider-neutral and
  default off.
- Open: should the source-provided `error_summary` (seed.py:248) anchor the cause when
  present, or only the parsed log lines? Default assumption: use it as a corroborating
  signal, never as the sole source.
- Open: confidence taxonomy stays {high, medium, low} vs adding `unknown`. Default:
  keep three, treat absent-signal as `low`.

## Acceptance Criteria
- [ ] Adding a new platform/failure family requires only a catalog entry + test;
      parser.py and builder.py control flow are unchanged by it.
- [ ] MySQL, Postgres, HTTP, and MuleSoft failure fixtures each yield a specific root
      cause and tailored next checks (not the generic fallback text).
- [ ] A wrapped-exception fixture reports the `Caused by:` root exception class.
- [ ] A misattribution fixture (benign earlier event) selects the causal
      component/code, not the first-seen one.
- [ ] Two unrelated unknown-signal failures in the same job produce distinct
      signatures and do not inflate each other's occurrence_count.
- [ ] A low-confidence incident is persisted with confidence `low` and a deterministic
      cause origin.
- [ ] With the fallback flag off, output is identical to the deterministic path; with
      it on, only low-confidence incidents trigger a single, redacted, capped LLM call,
      recorded with an llm_fallback origin, never overwriting high/medium results.
- [ ] No credential pattern (keyword=value or inline user:pass@host) appears in
      `endpoint`, `excerpt`, `summary_text`, or any LLM input across credential-bearing
      fixtures.
- [ ] Severity variants (ERROR/SEVERE/FATAL/WARN, mixed case) are counted and selected
      correctly.
- [ ] Existing insight tables rebuild cleanly from the raw store after the signature
      change; the slice-5 recurrence feature still passes its tests.
