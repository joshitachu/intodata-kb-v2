# ADR-022: No LangChain; A Configurable Provider Chain Instead

## Status
Accepted 2026-02-05; provider chain formalized 2026-02-23

## Context

A framework would have supplied retrieval and generation plumbing at the cost of control
over prompts, timeouts, streaming, tool-call wire format and failure classification -- all
of which turned out to be where the real defects lived.

## Decision

Direct provider clients, no LangChain. Embeddings, reranking and generation each run a
configurable provider chain with an explicit fallback order.

## Rationale

"No LangChain -- Direct Ollama client. Full control, fewer deps."

"Embeddings: Voyage AI voyage-4-large (1024-dim) -> Ollama fallback."

"No Claude CLI dependency; all features must work with deepseek/kimi/Anthropic/Ollama
swappable at runtime. Anthropic partnership (token resale) requires Claude support but
architecture must not be Claude-only; tool-use implementation uses OpenAI function-calling
format which all three cloud providers support."

## Consequences

- Client-facing material must describe the models as a configurable provider chain with a
  data-residency note, and must NEVER claim "no external APIs / all data stays local" --
  that is false for the default chain and is a data-governance misstatement. Any
  "stays local" guarantee must be gated on the actual configuration.
- Provider-specific wire quirks are handled explicitly rather than hidden: for example
  every `tool_call_id` in an assistant message must be answered with a tool message or the
  next request 400s, so a parallel-call cap must ANSWER overflow calls, never drop them.
- There is currently no cross-provider fallback in the V2 runtime; a provider capacity
  error surfaces as a 429. This is a documented, deliberate omission.

## Source
Promoted from `RAG_pipeline/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
