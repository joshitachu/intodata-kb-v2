# KB Sub-project Context

Live state only. Session history is in `.claude/PROGRESS_LOG.md`; durable decisions are in `.agent/decisions/`.

## What this sub-project is

`knowledge_base/` contains the intodoc scanner, MCP/REST orchestration service, and React dashboard for integration-landscape intelligence.

## Current state

- OpenCode documentation generation is a transport adapter for the canonical Claude Code CLI mechanism.
- `OpenCodeCliProvider` sends the byte-identical `ClaudeCliProvider._build_prompt` once to one `opencode run --format json` session over the complete estate.
- `ClaudeService` owns the shared preprocessing, quick scan, resume state, staging lifecycle, validation, atomic publication, and RAG push for both CLI transports.
- The OpenCode-specific Gather/Analyze/Produce scheduler, goal-driven pipeline, agent-workspace pipeline, connector runtime, and reingestion worker are removed.
- OpenCode bypasses the composite partition orchestrator and publishes one root document tree; Claude CLI and custom providers still support partitioned generation.
- Historical generation-profile JSON remains readable, but every profile is rejected for a new or resumed run. The dashboard no longer exposes profile, goal, or agentic controls.
- OpenCode runtime differences are limited to executable arguments, pinned-version preflight, isolated HOME/XDG/auth state, provider configuration, JSON-event parsing, usage accounting, and process cleanup.
- OpenCode Go keys use a temporary 0600 auth file. Known credentials from environment and OpenCode JSON configuration are redacted from event and stderr diagnostics.
- Shared business-topology services and publication paths remain live; deleting `providers/agent_workspace/topology.py` removed only the retired workspace projection.
- Newline-delimited streams are read through `utils/stream_frames.py`, never `readline()`: the OpenCode event/stderr readers and both ends of the connector-facade relay. Frame size there is model- or payload-controlled, so the stream buffer must not be the ceiling.

## Verification

- Routing/provider backend tests: 103 passed.
- Backend excluding four unrelated failing fixture files: 2424 passed, 5 warnings.
- Dashboard: TypeScript clean; 158 Vitest tests passed.
- Python compilation and `git diff --check`: passed.
- Installed OpenCode version: 1.18.7, matching the configured pin.
- Full backend still has nine unrelated failures: malformed GitHub test PEM, curated VW count drift, model catalogue mismatches, and missing real component-description fixtures.
- Repository scan finds 83 pre-existing variation selectors under generated `knowledge_base/output/` trees.
- Stream-frame fix: `pytest tests/test_providers tests/test_utils tests/test_connectors` -> 940 passed; the regression test was confirmed to fail against the old `readline()` reader.

## Next step

Rebuild the `mcp-server` image (source is baked in, not bind-mounted) and re-run `ettm_TEST_Claude` with OpenCode -- staging from failed run 257 is preserved, so it resumes from checkpoint. Then, with explicit approval for model spend, compare one representative project through OpenCode and Claude against the same sanitized scan: all nine documents, assessment artifacts, cost, and wall time, before adding any workspace mechanism.

## Unverified

- Production document-quality parity, model cost, and wall time. The first isolated OpenCode smoke produced no output and was terminated rather than allowed to spend or hang indefinitely.
