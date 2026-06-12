---
feature: Layered RAG Over Final Docs and Phase 2 Analysis
pipeline_step: 1_grill
date: 2026-06-11
---

## Feature

Change generated-document RAG so final Phase 3 docs remain the primary visual and
canonical documentation layer, while richer Phase 2 analysis artifacts are also
indexed and used as a hidden fallback/deep-answer source.

## Problem Statement

Nothing is broken in the current RAG flow in a strict failure sense. The current
answers are generally good, and the generated final docs are useful as the
visible documentation product.

The missing capability is depth. Today RAG primarily uses the final Phase 3
documentation, but Phase 3 docs are necessarily compressed and can omit details
because the upstream evidence context is large. The richer technical detail
often lives in Phase 2 analysis outputs, findings, group analyses, and related
evidence artifacts. Those artifacts should be available to retrieval when the
final docs do not contain enough information to answer a specific technical
question.

## Definition of Done

Done means the RAG answer flow can prefer final Phase 3 docs first, then fall
back to Phase 2 analysis when the final docs are not answerable enough.

The intended behavior:

- Final docs remain the user-facing visual/canonical documentation.
- Phase 2 analysis is indexed as an internal, richer technical source.
- A query is first answered from final docs when those docs contain sufficient
  supporting evidence.
- If final docs do not answer the question, the system can retrieve from Phase 2
  analysis and answer from that deeper context.
- The system should distinguish internally between answers supported by final
  docs and answers supported by Phase 2 fallback.
- Phase 2 fallback hits should be usable as documentation-gap signals, because
  they indicate information that exists in analysis but not in final docs.

Verification still needs to be defined in the PRD. The target is not literally
"any question can be answered"; the measurable goal should be that representative
technical questions answer correctly when the answer exists in either Phase 3
docs or Phase 2 analysis, and that unsupported questions fail cleanly instead of
hallucinating.

## Out of Scope

No explicit exclusions were identified yet.

Candidate scope boundaries to decide in PRD:

- Whether Phase 1 source evidence is also indexed immediately, or added later as
  a citation/proof layer.
- Whether Phase 2 artifacts are visible in the UI, or remain fully hidden.
- Whether this change should modify documentation generation itself, or only
  ingestion/retrieval after generation.
- Whether all Phase 2 artifacts are indexed, or only selected analysis files to
  avoid duplicate/noisy retrieval.

## Solution Rationale

The observed generation runs show that Phase 2 contains substantially more
technical detail than the final Phase 3 docs. In the Lobster run, Phase 2
analysis artifacts were much larger and richer than the final staged docs. The
final docs are useful for readable documentation, but they are not guaranteed to
contain every fact needed for deep technical Q&A.

Using Phase 2 as a fallback RAG corpus should improve answer depth without
changing the visual documentation product. This approach keeps the final docs as
the first answer source while allowing the system to recover details that were
captured upstream but compressed out of Phase 3.

This is preferred over simply making final docs larger because:

- Final docs should stay readable and navigable.
- Phase 2 already contains the richer technical analysis.
- Retrieval can use different source layers for different query types.
- Fallback usage can generate concrete documentation-gap signals.

## Assumptions and Risk

Assumptions that need validation:

- Phase 2 analysis reliably contains the missing technical facts.
- Phase 2 analysis contains materially different and richer content than Phase 3
  docs.
- Phase 2 can be chunked/indexed without flooding retrieval with duplicates from
  bundles, doc packs, and repeated analysis.
- The system can make a reliable answerability decision before falling back from
  final docs to Phase 2.
- Retrieval can prefer final docs without letting the much larger Phase 2 corpus
  overpower them.
- Users will accept answers sourced from internal analysis when final docs are
  incomplete, as long as the answer remains correct and clear.

Risks:

- The system may fall back too aggressively and stop treating final docs as the
  canonical source.
- The system may fail to fall back when final docs are incomplete.
- Phase 2 may contain noisy, duplicated, or intermediate analysis that hurts RAG
  precision.
- A weak answerability threshold may cause hallucinated "not found" results or
  overconfident answers from insufficient context.
- Indexing too much Phase 2 output may increase cost, latency, and retrieval
  ambiguity.

## Critical Areas Touched

- Harness pipeline: generated artifact selection and/or ingestion handoff may
  need to include Phase 2 analysis artifacts in addition to final docs.
- RAG retrieval: query routing, corpus partitioning, answerability gating, and
  fallback logic need to change.

Potentially touched depending on implementation:

- MCP API routes if generation-to-ingestion payloads or ingestion metadata are
  changed.
- RAG backend ingestion schema if new artifact metadata is needed.

## Risk if Wrong

If implemented wrong, RAG may retrieve the wrong source layer, answer from noisy
or stale Phase 2 analysis, fail to show the correct output, or miss information
that exists in the generated artifacts.

The main user-facing failure modes are:

- Final docs contain the answer but the system incorrectly falls back or says it
  cannot find it.
- Final docs do not contain the answer but Phase 2 does, and the system fails to
  retrieve it.
- Retrieval returns plausible but unsupported answers.
- Phase 2 overwhelms final docs, making the visible documentation feel less
  canonical.
- Documentation-gap signals become noisy and stop being useful.
