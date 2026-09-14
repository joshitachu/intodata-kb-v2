# ADR-021: Capability Questions Answer From The Live Registry, Never From Retrieved Documentation

## Status
Accepted 2026-07-10; still current

## Context

Asking the assistant what it can do is a retrieval question only by accident. Answering it
from indexed project documentation means a stale document decides the product's
self-description -- for example claiming the assistant is read-only after workflow
capabilities exist.

## Decision

Capability and self-awareness questions answer from the live harness capability registry.
The chat planner may choose `answer_direct`, `ask_clarification`, `call_capability` or
`use_rag`; only normal documentation Q&A falls through to RAG.

## Rationale

"This prevents stale docs from claiming Nexus is read-only after workflow capabilities
exist. The registry is the source of truth for what the assistant can do; RAG is one
evidence/retrieval capability, not the assistant identity. Current planner is deterministic
but intentionally shaped like the future model-backed planner schema; backend execution
guards remain authoritative."

## Consequences

- Backend execution guards stay authoritative regardless of what the planner chooses.
- Connectors and capabilities are registered behind provider/capability contracts rather
  than hardcoded into any one integration.

## Source
Promoted from `RAG_pipeline/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
