# ADR-005: Core Generation Writes From Bounded Evidence; Retrieval May Be Agentic But Must Be Budgeted

## Status
Accepted 2026-05-28; still current

## Context

The `Steven_inventory` incident: generic staging tools (`list_artifacts`,
`read_artifact`) were exposed to Phase 2 core generation, and the model browsed staging
until the accumulated input tokens ran away.

## Decision

Phase 2 and Phase 3 core generation write from bounded `context_files`. Generic staging
tools may be exposed only to explicitly named phases (`phase1.investigate`,
`phase2.deep_dive`, repair, critic, consistency) and only with hard byte caps and
accumulated input-token ceilings.

## Rationale

"As of 2026-05-28, the `Steven_inventory` incident established that generic staging tools
(`list_artifacts`, `read_artifact`) must not be exposed to Phase 2/3 core generation
without hard byte caps and accumulated input-token caps. Phase 2 core generation uses
bounded `context_files` by default; pack-specific depth should come from curated evidence
bundles, not unbounded staging browsing."

"Python may construct contracts, constrain context/tools, validate coverage, trigger
retries/repairs, and persist exact LLM output. Python must not synthesize business
explanations or fill final documentation prose itself."

## Consequences

- `_tool_policy` is the single explicit per-phase tool contract. Its repair check must run
  BEFORE the phase-prefix checks, because `"phase2.repair".startswith("phase2")` is True
  and the wrong order silently disables tools for every Phase 2 repair.
- Depth is an evidence-engineering problem, not a model-spend problem: bundles push
  verbatim code/tMap/SQL into the prompt rather than hoping the model pulls it.
- Pack-specific depth comes from curated evidence bundles, never from broader tool access.

## Source
Promoted from `knowledge_base/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
