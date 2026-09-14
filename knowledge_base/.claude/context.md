# KB Sub-project Context

Live state only. Session history is in `.claude/PROGRESS_LOG.md`; durable decisions are in `.agent/decisions/`.

## What this sub-project is

`knowledge_base/` contains the intodoc scanner, MCP/REST orchestration service and React dashboard for integration-landscape intelligence.

## Current state

- `integration-intelligence-v1` is an independent goal-driven documentation pipeline. It does not call the legacy custom gather/analyse/write phases or their fixed unit/document batching.
- Every selected upload is sanitized and deterministically parsed in full. Parser products are normalized into a canonical fact graph with explicit exact, partial, dynamic, unresolved and unsupported states.
- A user goal creates an advisory scope seed. The coding agent may expand it as evidence reveals dependencies; whole-estate documentation exposes the complete graph without serializing a giant ID list into the prompt.
- OpenCode is the first registered coding-agent adapter. Provider-neutral contracts and registry boundaries exist for future Codex, Claude Code or Hermes adapters; unregistered providers fail closed.
- The goal-driven OpenCode adapter can use native delegation only when the investigation discovers genuinely independent work. The legacy OpenCode route continues to deny delegation.
- Methodology packs guide questions, concepts, evidence hints, outputs and sufficiency. They cannot redefine parser facts, select providers, impose phases/batches, or escape the configured pack root.
- Candidate knowledge is reconciled with immutable deterministic facts and must pass semantic, scope and client-readiness gates. Catalogue, integration dossiers, topology, RAG and provenance are deterministic projections of accepted canonical knowledge.
- Publication is atomic. Failure/cancellation retains the previous baseline and a resumable checkpoint. Reingestion uses content hashes plus graph dependency impact so unchanged parsing and unaffected accepted knowledge can be reused.
- Resource ceilings are circuit breakers, not evidence-reading or per-agent quotas. Usage separates input, output, cache, reasoning, tool and wall-time costs.
- The dashboard exposes the new profile and goal/provider controls. Legacy profiles remain available as separate compatibility paths.
- Existing composite projects, runtime evidence, topology APIs and pack infrastructure remain live.

## Verification

- Goal-driven tests: 44 passed.
- Goal-driven plus OpenCode provider tests: 116 passed.
- Broader affected MCP integration set: 252 passed before the final delegation-only change; the provider subset passed afterward.
- Full intodoc suite: 669 passed, 7 skipped.
- Dashboard: TypeScript clean; 159 Vitest tests passed.
- Full MCP suite is not green because the existing GitHub App test fixture contains a malformed PEM (expected 200, received 502 after 88 passes).
- The changed-file invisible-character scan is clean. The repository-wide scan still finds 46 pre-existing variation selectors under `knowledge_base/output/poly/`.

## Next step

With explicit approval for provider spend, run focused and whole-estate ETTM pilots through `integration-intelligence-v1`; compare representative mappings, journeys, scope expansion, topology and citations directly with sanitized source before customer publication.

## Unverified

- Production semantic depth, cost and wall time are not claimed until that paid estate-scale pilot and manual source comparison pass.
- Only OpenCode has a real coding-agent adapter today; other provider contracts deliberately fail closed.
