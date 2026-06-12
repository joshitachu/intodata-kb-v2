---
inspected_at: 2026-06-12
verdict: WARN
slice: 3 — Layered ingestion push with entity index
---

## Verdict Summary
Implementation matches the approved Slice 3 plan with no blocking defects; the
one substantive finding is an unescaped SQL LIKE wildcard in the internal-delete
guard (bounded by the project_id equality condition, but imprecise for a
destructive filter).

## Check Results
| Check              | Result | Notes |
|--------------------|--------|-------|
| Plan conformance   | PASS   | All 6 plan steps implemented: EntityAlias model + migration 028, internal delete + alias replace endpoints, RagBridge discovery/sync, CSV alias generation, both entry points wired, default retrieval untouched. |
| Root cause         | PASS   | Internal lifecycle is wholesale delete-and-repush — the correct answer to removed groups being invisible to the visible-doc diff. |
| Generalized        | PASS   | Alias cleanup added to BOTH the internal lifecycle and the full project delete (stale aliases cannot survive project deletion). Layer constants live in one module shared by builder and consumer. |
| Design patterns    | PASS   | Discovery/alias-building are pure filesystem functions in the service layer; RagBridge owns transport only; backend endpoints mirror existing ingest route idioms. |
| Contract respect   | PASS   | `default_source_layers` untouched; `_read_md_files`/`_diff_generated_docs` are top-level digit-prefixed only so canonical push/delete cannot touch `_internal/`; staging contract unaffected. |
| No special-casing  | PASS   | `_internal/` prefix is a single documented scoping convention, mirrored as constants on both sides with cross-references. |
| Edge cases         | WARN   | LIKE wildcard finding (1); internal docs appear in admin document lists (2). Tests cover delete-first ordering, delete-failure abort, partial push errors, empty-tree no-op, `Training|Jasmijn` encoding, CSV dedupe, missing-CSV no-op. |
| RBAC               | PASS   | New endpoints are `verify_ingest_api_key`-protected like every ingest route; internal chunks flow through the same `ingest_text` path and carry the same project scoping; retrieval-side RBAC filter unchanged. |
| Credential safety  | PASS   | No credentials in code; alias records carry names/paths/confidence only; pushed internal content is LLM output already covered by upstream credential redaction (same exposure class as canonical docs). |

## Findings

1. **WARN — unescaped LIKE wildcards in the internal-delete filter**
   `RAG_pipeline/backend/src/api/routes/ingest.py` (delete_project_internal_documents):
   `Document.filename.like(f"{project_id}/_internal/%")` — `_` is a single-char
   SQL wildcard, and `project_id` may itself contain `_`/`%` (e.g. `cdc_v2`).
   Blast radius is bounded: the query also requires `Document.project_id ==
   project_id`, and no current writer produces filenames like
   `proj/Xinternal/...`. Still, a destructive filter should be exact: use
   `Document.filename.startswith(f"{project_id}/_internal/", autoescape=True)`.

2. **WARN — internal docs appear in RAG document-list endpoints**
   `Document` rows named `{project}/_internal/...` will show up in admin
   document listings and counts. Retrieval-side invisibility is guaranteed by
   `source_layer`, and the plan scopes UI display out (Slice 4+), but operators
   may be surprised by the row count change. Optional: filter `_internal/` from
   user-facing document list queries in a later slice.

3. **Note — `/push-to-rag` `last_rag_push_at` gating is stricter**
   Previously updated when `pushed > 0` even with errors; now requires zero
   total errors. Consistent with the generation path (`rag_error_message is
   None`) and with the plan's "only after successful RAG push work". Intended.

4. **Note — partial internal state on mid-sync push failure**
   Delete succeeds, some pushes fail → internal layer is partially populated
   until the next push. Errors are surfaced into `rag_errors` (generation
   verdict becomes `published_with_rag_error`) and any re-push repairs the
   state. Acceptable lifecycle for non-user-facing layers.

## Required Actions

No blocking changes.

WARN (should address before commit, cheap):
- Finding 1: switch the internal-delete filter to
  `filename.startswith(..., autoescape=True)`.

WARN (optional/follow-up):
- Finding 2: consider excluding `_internal/` from document-list endpoints in a
  future slice; log as backlog.

## Resolution (post-inspection, same session)

- Finding 1: FIXED — filter switched to `startswith(..., autoescape=True)`;
  test updated to assert escaped compiled SQL.
- Finding 2: OPEN — logged as follow-up; retrieval invisibility already
  enforced by source_layer.
