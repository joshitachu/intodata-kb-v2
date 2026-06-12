# Active Plan: Slice 3 - Layered Ingestion Push With Entity Index

verdict: PASS
approved_at: 2026-06-12
slice_id: 3
slice_name: Layered ingestion push with entity index
sub_project: both
critical_area: yes - MCP API routes and RAG ingestion/storage; /inspect required before validation/commit
depends_on:
- Slice 1: RAG layer-aware retrieval contract
- Slice 2: MCP _rag_support persistence and analysis_cache restore

## Goal

After each generation or manual push, RAG receives the internal support layers created by the MCP harness:

- `technical_analysis` from `docs/_rag_support/phase2_analysis/*.md`
- `support_maps` from `docs/_facts/*.md`
- `scorecard` from `docs/_assessment/scorecard.md`
- `evidence_slices` from `docs/_evidence_registry/doc_slices/*.md` when present
- Entity and usage CSV indexes as a per-project alias lookup, not vector corpus chunks

Default user retrieval must remain canonical-only. Internal artifacts are only available when callers explicitly request internal `source_layers`.

## Current State

- Slice 1 added layer metadata fields, default canonical retrieval, source-layer filters, and RAG ingestion metadata threading.
- Slice 2 persists `_rag_support/` after generation and restores `analysis_cache/`, but `RagBridge` still pushes only top-level visible docs.
- Visible doc deletion uses changed/removed final docs. Internal support artifacts need full delete-and-repush because removed groups are hidden from the visible diff.
- `POST /api/v1/ingest/md` already accepts metadata. The missing pieces are MCP internal push orchestration, internal-layer delete support, and entity alias ingestion/storage.

## Implementation Plan

1. Add RAG backend entity alias storage.
   - Add an `EntityAlias` model under `RAG_pipeline/backend/src/db/models/`.
   - Add an Alembic migration, likely `028_add_entity_aliases.py`.
   - Store at least: `project_id`, `alias`, `alias_normalized`, `canonical_name`, `entity_type`, `unit_name`, `source_path`, `source_artifact`, and `confidence`.
   - Add indexes for `(project_id, alias_normalized)` and `project_id`.
   - Import the model from `db/models/__init__.py` so tests and metadata creation include it.

2. Add RAG backend ingest endpoints for internal lifecycle.
   - Add an API-key-protected endpoint to delete all internal-layer docs for a project.
   - Delete only documents whose DB filename is scoped under the project and uses the internal prefix, for example `{project_id}/_internal/...`.
   - For each deleted document, call the existing Qdrant point delete path by `document_id`; do not delete canonical docs or project assignments.
   - Clear entity aliases for the project as part of internal lifecycle cleanup so stale aliases do not survive a failed or partial re-push.
   - Add an API-key-protected entity alias ingest endpoint that replaces aliases for one project in a single transaction.
   - Keep `/ingest/md` metadata handling compatible with Slice 1.

3. Extend MCP `RagBridge` with internal artifact discovery and sync.
   - Reuse Slice 2 `_rag_support` constants where possible.
   - Add a deterministic internal filename builder using layer-prefixed names under `_internal/`.
   - Percent-encode unsafe path components so names like `Training|Jasmijn.md` cannot leak raw separators or collide with canonical docs.
   - Build metadata for every internal Markdown artifact:
     - `source_layer`
     - `artifact_type`
     - `visibility: internal`
     - `artifact_path`
     - `doc_name`
     - `project_id`
     - `run_id` when available
     - `group`, `unit`, or `section` when available from manifests or path context
   - Push internal Markdown through the existing `push_markdown(..., metadata=...)` path.
   - Do not ingest `_facts/*.csv` or `_facts/*.json` as vector documents.

4. Add MCP entity alias payload generation.
   - Parse `docs/_facts/entity_index.csv` and `docs/_facts/usage_index.csv` with `csv.DictReader`.
   - Convert entity names and useful unit names into alias records.
   - Include usage rows as alias records with source metadata from `usage_index.csv`.
   - Normalize and dedupe aliases deterministically before posting to the RAG backend.
   - Treat missing CSV files as a successful no-op.

5. Wire both MCP push entry points through the same sync path.
   - In `_run_generate_background`, keep the current visible doc push behavior, then run internal sync for the final `docs/` directory.
   - In manual `/push-to-rag`, call the same internal sync method after the visible push.
   - Combine internal push and alias ingest errors into the existing RAG error handling so generation reports partial RAG sync correctly.
   - Preserve `last_rag_push_at` update behavior only after successful RAG push work.

6. Preserve default retrieval behavior.
   - Internal chunks must always carry a non-canonical `source_layer`.
   - Do not change `default_source_layers`.
   - Add or keep tests proving default search excludes internal layers unless explicitly requested.

## Expected File Changes

- `knowledge_base/mcp-server/src/mcp_server/services/rag_bridge.py`
- `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
- `knowledge_base/mcp-server/tests/...` for bridge and route coverage
- `RAG_pipeline/backend/src/api/routes/ingest.py`
- `RAG_pipeline/backend/src/schemas.py`
- `RAG_pipeline/backend/src/db/models/entity_alias.py`
- `RAG_pipeline/backend/src/db/models/__init__.py`
- `RAG_pipeline/backend/alembic/versions/028_add_entity_aliases.py`
- `RAG_pipeline/backend/tests/...` for ingest delete, alias replace, metadata threading, and retrieval default safety

## Test Plan

Targeted MCP tests:

```bash
cd knowledge_base/mcp-server
uv run pytest tests/test_services/test_rag_bridge.py tests/test_api/test_projects.py tests/test_services/test_rag_support_package.py
```

Targeted RAG backend tests:

```bash
cd RAG_pipeline/backend
uv run pytest tests/test_api/test_ingest_delete.py tests/test_api/test_entity_aliases.py tests/test_ingestion/test_pipeline.py tests/test_retrieval/test_search.py
```

Syntax checks for every changed Python file:

```bash
python3 -m py_compile <changed-file>
```

Full validation before commit:

```bash
cd knowledge_base/mcp-server && uv run pytest
cd RAG_pipeline/backend && uv run pytest
```

Required gate after implementation:

```text
/inspect
/validate
```

## Acceptance Criteria

- Generation and manual push both sync canonical docs plus internal layers.
- Internal-layer sync deletes the previous internal set for the project before pushing the new set.
- Canonical documents and project assignments are not removed by internal cleanup.
- Internal filenames are scoped, layer-prefixed, and sanitized; `Training|Jasmijn.md` is covered by a test.
- `_facts/*.md`, scorecard, evidence slices, and `_rag_support` technical analysis receive correct metadata.
- `_facts/*.csv` feeds the entity alias table and is not pushed as vector Markdown.
- Entity alias ingest replaces aliases per project and does not affect other projects.
- Default retrieval remains canonical-only.
- Explicit internal `source_layers` continue to work through the Slice 1 retrieval path.

## Out Of Scope

- Retrieval ladder routing across layers.
- Query-time entity/alias matching.
- Section-parent expansion.
- UI display of internal citations.
- `source_evidence` raw file ingestion.
- Bulk reindex of existing projects outside generation or manual push.
