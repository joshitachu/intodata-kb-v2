---
feature: Generalize and harden the runtime log intelligence mechanism
pipeline_step: 3_slices
total_slices: 4
current_slice: 0
---

## Slice 1: Credential-redaction + severity-normalization hardening
**Delivers**: No credential pattern (keyword=value or inline `user:pass@host`)
survives into any persisted insight field, and severity matching is
case/alias-insensitive (ERROR/SEVERE/FATAL -> error, WARN/WARNING -> warning).
**Expected files**:
- `RAG_pipeline/backend/src/runtime/insights/parser.py` (extend `redact_credentials`
  to cover inline `user:pass@host`; apply before storing `endpoint`)
- `RAG_pipeline/backend/src/runtime/insights/builder.py` (route severity through a
  normalization helper before counting/selection)
- `RAG_pipeline/backend/tests/test_runtime/test_insight_parser.py` (credential-leak
  assertions)
- `RAG_pipeline/backend/tests/test_runtime/test_insight_builder.py` (severity-variant
  fixtures)
**Critical area**: no -- but security-sensitive (redaction); /inspect recommended.
**Depends on**: none

## Slice 2: FailurePatternCatalog + multi-platform generalization
**Delivers**: Error-code matching, root-cause text, and next-checks come from a
data-driven catalog instead of inline `ORA-`/`ORA-12` branches; MySQL/Postgres/HTTP/
Mule fixtures yield specific (non-generic) causes, and adding a family is catalog
data + a test with zero parser/builder control-flow edits.
**Expected files**:
- `RAG_pipeline/backend/src/runtime/insights/catalog.py` (NEW -- FailurePatternCatalog:
  data + match API)
- `RAG_pipeline/backend/src/runtime/insights/parser.py` (delegate code-family matching
  to the catalog)
- `RAG_pipeline/backend/src/runtime/insights/builder.py` (replace `_derive_root_cause`
  / `_build_next_checks` with catalog lookups)
- `RAG_pipeline/backend/tests/test_runtime/test_catalog.py` (NEW)
- `RAG_pipeline/backend/tests/test_runtime/test_insight_builder.py` (multi-platform
  fixtures; Oracle no-regression)
**Critical area**: no
**Depends on**: Slice 1

## Slice 3: Causal extraction + honest signature/confidence (schema migration)
**Delivers**: The parser resolves multi-line messages to the deepest `Caused by:`
root exception; the builder selects the terminal/causal error code and component (not
first-seen); the signature no longer merges unrelated unknown failures by job_name;
confidence reflects evidence quality and `low` is persisted; a `cause_origin` field is
added and existing insight rows rebuild cleanly.
**Expected files**:
- `RAG_pipeline/backend/src/runtime/insights/parser.py` (`Caused by:` resolution,
  most-specific exception)
- `RAG_pipeline/backend/src/runtime/insights/builder.py` (causal selection, signature
  recomputation, evidence-quality confidence)
- `RAG_pipeline/backend/src/db/models/runtime_insights.py` (add `cause_origin`; optional
  `pattern_id`)
- `RAG_pipeline/backend/src/runtime/insights/models.py` (expose `cause_origin`)
- `RAG_pipeline/backend/alembic/versions/034_*.py` (NEW -- migration + signature rebuild
  path)
- `RAG_pipeline/backend/tests/test_runtime/test_insight_builder.py`,
  `test_similar_failures.py` (stack-trace, misattribution, distinct-unknown-signature,
  recurrence-still-passes)
**Critical area**: no -- but signature change risks the slice-5 recurrence feature;
verify against `test_similar_failures.py`.
**Depends on**: Slice 2

## Slice 4: Bounded opt-in LLM fallback for low-confidence incidents
**Delivers**: An env-gated, off-by-default escalation that runs a single, redacted,
count/token-capped LLM call only on `low`-confidence incidents, records an
`llm_fallback` cause origin, never overwrites high/medium deterministic results, and
is byte-for-byte identical to Slice 3 when the flag is off.
**Expected files**:
- `RAG_pipeline/backend/src/runtime/insights/llm_fallback.py` (NEW -- bounded escalation,
  reuses `src/generation/llm_client.py`)
- `RAG_pipeline/backend/src/runtime/insights/builder.py` (invoke fallback on low
  confidence; propagate origin)
- `RAG_pipeline/backend/src/config.py` (feature flag, default off)
- `RAG_pipeline/backend/tests/test_runtime/test_llm_fallback.py` (NEW -- flag-off
  identical, flag-on low-confidence-only, redaction + cap)
**Critical area**: no -- but sends evidence to an LLM; /inspect recommended for the
redaction/cap guarantees.
**Depends on**: Slice 3
