---
name: session-start
description: Load session context, check protocol debt, recover pipeline state, and classify work before coding. Use at the start of a repo session with optional scope kb, rag, or both.
---

# Session Start

Prepare the session before coding.

## Argument

Accepted scopes: `kb`, `rag`, or empty for both.

## Steps

1. Check `PROTOCOL_DEBT.md` or `.docs/quality/PROTOCOL_DEBT.md` if present. Print unresolved entries first.
2. Run `git status --short` and identify active sub-projects. Flag if status contradicts the requested scope.
3. Load scope context:
   - `kb`: `knowledge_base/.claude/context.md`, last 3 entries of `knowledge_base/.claude/PROGRESS_LOG.md`, and `knowledge_base/.agent/README.md` if present.
   - `rag`: `RAG_pipeline/.claude/context.md` and last 3 entries of `RAG_pipeline/.claude/PROGRESS_LOG.md`.
   - both: read both sets.
4. List `.plans/` files and determine pipeline position:
   - Inspection PASS: run validation and commit.
   - Inspection BLOCK/WARN: fix findings and rerun inspection.
   - Active plan PASS without report: build or inspect if already built.
   - Active plan pending/block: revise or approve plan.
   - Slices only: create active plan.
   - PRD only: create slices.
   - Grill only: create PRD.
   - Nothing: start grill or classify as Scout.
5. If no pipeline is active, classify work:
   - Scout: fewer than 3 files, no new abstractions, no schemas or public APIs, no core harness/MCP/RAG/auth changes.
   - Full pipeline: new feature, schema/API changes, sensitive subsystem changes, or more than 3 files expected.

## Output

Keep each active sub-project summary under 20 lines:

- Protocol debt.
- Pipeline state and next action.
- Mode.
- Last completed status.
- Last 3 changes.
- Blockers.
- Next step.
- Modified files.

