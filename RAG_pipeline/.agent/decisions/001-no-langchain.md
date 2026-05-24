# ADR-001: Direct Implementation over LangChain

## Status
Accepted (Feb 2026)

## Context

When building the RAG pipeline, we evaluated LangChain as the framework for orchestrating
the retrieval and generation pipeline. LangChain was the dominant choice for RAG applications
at the time and offered pre-built components for document loading, splitting, embedding,
retrieval, and chain composition.

## Decision

Build the RAG pipeline with direct implementation: no LangChain dependency.

## Rationale

- **Debugging opacity**: LangChain abstracts away the actual HTTP calls, prompt construction,
  and retrieval logic. When something fails (wrong results, hallucinations, poor retrieval),
  the stack traces point into LangChain internals, not our code.
- **Version instability**: LangChain had breaking API changes frequently in 2024-2025.
  Pinning versions meant missing improvements; upgrading meant rework.
- **Over-engineered for our scope**: We have one embedding model (Ollama), one vector DB
  (Qdrant), and one LLM (Ollama). LangChain's abstraction adds cost for a fixed topology.
- **Token and cost visibility**: Direct implementation means we control exactly what gets
  sent to the LLM, can track token counts precisely, and can log the exact prompt and
  context at every step.
- **Performance**: Fewer abstraction layers, no serialization overhead, async all the way.
- **Team understanding**: Every developer on the team can read and trace the entire
  pipeline in under 30 minutes. With LangChain, that requires reading the framework source.

## Consequences

- We own the full implementation of: document parsing, chunking, embedding, Qdrant
  client calls, RBAC filters, prompt building, and LLM streaming.
- New team members must learn our conventions (documented in CLAUDE.md and this `.agent/`)
  rather than LangChain docs.
- Extending the pipeline (e.g., adding a reranker, query expander, new retrieval strategy)
  requires writing code, not configuring a LangChain chain — which is more work but
  also more transparent.
- If LangChain ever solves the opacity and stability problems, this decision can be revisited.
  The modular architecture (separate embedder, vector_store, search, llm_client modules)
  makes it straightforward to swap implementations.

## Alternatives Considered

- **Keep LangChain**: Rejected due to debugging friction and version instability
- **LlamaIndex**: Similar concerns; also not needed for our fixed topology
- **Haystack**: Mature framework but same opacity trade-off
