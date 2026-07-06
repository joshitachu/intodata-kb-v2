---
name: lfe-scout
description: Lightweight LFE maintenance mode for minor fixes. Use for non-architectural changes across at most 3 files; reject and escalate file creation, root config edits, schemas, public APIs, or core logic changes.
---

# LFE Scout Mode

Use for small, non-architectural maintenance.

## Scope

Allowed:

- Typos.
- UI adjustments.
- Simple bug fixes in a well-understood area.
- At most 3 files changed.

Reject and escalate:

- File creation, deletion, or renaming.
- Root-level configuration changes.
- Core logic changes per `CONTEXT.md`.
- Schema, public API, auth, RAG retrieval, MCP route, or harness pipeline changes.

## Workflow

1. Analyze the exact lines requiring change.
2. Apply direct, scoped edits.
3. Report action, rationale, and integrity confirmation.
4. Log changes in `CHANGELOG.md` if project convention requires it.
5. Hand off to `lfe-archivist` if docs must be synced.

