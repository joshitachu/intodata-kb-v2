---
feature: Generalize and harden the runtime log intelligence mechanism
pipeline_step: 1_grill
date: 2026-06-17
---

## Feature
Improve the deterministic runtime log mechanism (parse -> enrich -> incident ->
signature -> evidence) so it works beyond Oracle/Talend, attributes causes by
causality rather than position, reports honest confidence, and never leaks
credentials. Ingestion transport (how logs arrive) is explicitly out of scope.

## Problem Statement
The mechanism (runtime/insights/parser.py + builder.py) has five concrete defects:
1. Vendor lock-in. `_ORA_CODE_RE` (parser.py:21) is the only error-code matcher;
   `_derive_root_cause` / `_build_next_checks` (builder.py:556,583) hardcode
   `ORA-12` branches. Every non-Oracle failure degrades to generic text. New
   failure types require new inline `if` branches in two files -- special-case
   accumulation barred by CLAUDE.md.
2. No causal extraction. Multi-line messages (seed.py:180) are parsed flat;
   `qualified[0]` (parser.py:90) returns the outermost exception, never the
   `Caused by:` root. `primary_error_code` / `failed_component` (builder.py:216,
   230) are first-seen, not terminal/causal -- a benign earlier warning
   misattributes both, which then poisons the signature.
3. Fragile signature. `error_code|exception_class|failed_component`
   (builder.py:332) inherits the misattribution above; the `unknown|{job_name}`
   fallback (builder.py:344) collapses all unrelated unknown failures of one job
   into a single "recurring" group, inflating occurrence_count.
4. Confidence theater. `_derive_confidence` (builder.py:574) counts present
   signals, not correctness; a wrong templated cause can ship as "high".
5. Credential leak + brittle severity. `_JDBC_URL_RE` (parser.py:27) stores the
   whole `jdbc:...user/pass@host` substring into `endpoint`; `redact_credentials`
   only catches `keyword=value`, so inline `user/pass@` creds survive into a
   surfaced field. Severity is matched literally (`== "error"`), missing
   SEVERE/FATAL/uppercase from real sources.

## Definition of Done
- Error-code + root-cause + next-checks knowledge lives in a data-driven failure
  pattern catalog (Strategy/Registry), not inline branches. Adding a platform or
  failure family = adding catalog data + tests, zero parser/builder edits.
- Multi-line messages resolved to the deepest `Caused by:` root exception.
- Causal selection: terminal/failure-attributed error and component, not first-seen.
- Signature stable and honest; unrelated unknown failures are not merged by job_name.
- Confidence reflects evidence quality; low-confidence is surfaced in the data, not
  only in UI copy.
- Optional, opt-in LLM escalation runs ONLY on low-confidence incidents, fed the
  already-redacted bounded evidence (never raw logs) -- provider-neutral.
- Inline JDBC credentials redacted before storage; severity matching is
  case/format-insensitive.
- Verify: parser/builder tests with MySQL/Postgres/HTTP/Mule fixtures, a
  Caused-by stack-trace fixture, a JDBC-credential-leak assertion, severity-variant
  fixtures, and a signature-stability/recurrence test.

## Out of Scope
- Ingestion transport (TMC sync, polling, webhooks, file drops) -- deferred.
- Write-back to source (retry/rerun) and operator state (ack/resolve/edit).
- Raw log windows to the LLM as the primary strategy -- LLM is bounded fallback only.
- Operational analytics dashboards and alert delivery.
- Document ingestion, embeddings, chunking, Qdrant retrieval (critical area, untouched).

## Solution Rationale
A catalog/Strategy table replaces inline `if startswith` chains -- directly
satisfies the CLAUDE.md design-pattern and no-special-case rules, and is the only
structure that scales to the platform-agnostic requirement. Deterministic-first
with a bounded LLM fallback preserves the EP-006 token-bloat thesis (the non-goal
bars raw-logs-as-main-strategy, not a redacted residual escalation). Provider
neutrality matches the platform-agnostic-model requirement.

## Assumptions and Risk
- Assume multi-line traces arrive within one message (confirmed seed.py:180), not
  split across rows. If a real source splits them, the parser needs cross-row
  grouping -- a larger change.
- Assume changing the signature algorithm is acceptable to break recurrence
  continuity for already-built incidents (requires rebuild/migration of
  RuntimeErrorSignatureRow).
- Assume an LLM provider is available + cost/latency acceptable for the fallback;
  must stay provider-neutral.
- Assume the source-provided `error_summary` (seed.py:248) is trustworthy enough to
  use as a cause anchor.

## Critical Areas Touched
Not one of the four named critical areas (harness pipeline / MCP routes / RAG
retrieval / auth+RBAC). However it is security-sensitive (credential redaction)
and feeds the chat LLM surface, so /inspect is recommended before commit even
though not strictly mandated.

## Risk if Wrong
- A confidently-wrong root cause is worse than none -- misleads operators.
- A credential leak into `endpoint`/evidence sends secrets to the UI and LLM.
- A signature regression fragments recurrence -> false "first occurrence" or false
  "recurring" badges, corrupting the slice-5 feature.
- An unbounded LLM fallback reintroduces the exact token bloat EP-006 was built to
  avoid.
