---
name: topology-wave-build-protocol
description: How the org-topology backend build is coordinated across parallel agents — pinned contract file, strict per-agent file ownership, report-don't-touch
metadata:
  type: project
---

The org-topology / page-embedded-copilot build runs as parallel agents per wave, coordinated by
two rules rather than by conversation:

1. **A contract file is pinned before implementation.** The orchestrator writes the module
   docstring, function signatures and payload shape first (e.g. `src/topology/client.py` with
   `NotImplementedError` stubs), then hands it out. Signatures and payload shape are frozen; if a
   contract genuinely cannot be honoured, stop and report instead of improvising.
2. **Strict file ownership per agent, with no shared edits.** Wave 1: `topology-backend` owned
   `src/topology/**` + `tests/test_topology/**` + additive-only `src/config.py`; a parallel agent
   owned `src/schemas.py`, `src/db/models/conversation.py`, `src/api/routes/conversations.py`,
   `src/generation/agent_loop.py`, and the alembic migration. Needing a change in someone else's
   file means reporting it, not editing it.

3. **Mid-task schema handoff.** When a sibling finishes a schema the other agents must mirror,
   the coordinator forwards the exact final field list as a message mid-task, including changes
   the original brief did not mention. Treat it as authoritative but still verify against the
   real Pydantic models (`model_json_schema()` / `model_fields`) before mirroring — the message
   is a summary and has been off by a bound (e.g. `job` 1..500 for lineage vs 1..200 for
   component detail).

**Why:** the waves are built simultaneously against the same seam, so a shared edit or a
signature drift breaks the other agent's already-written code and tests. The pinned contract is
what makes independent implementation possible at all.

**Useful when mirroring a topology tool schema in the harness capability:** the tool input models
use Pydantic's default `extra="ignore"`, so a shared optional property that a given operation does
not take is silently dropped rather than rejected — one flat property set for all operations is
safe. The reverse is not: `additionalProperties: False` on the capability means a field the tool
accepts but the capability omits is unreachable to the model, and that failure is silent.

**How to apply:** when picking up topology work, look for the pinned contract docstring before
designing anything — it is the agreement, not a suggestion. Expect the full backend suite to show
failures owned by the parallel agent (e.g. an alembic head assertion moving) and do not "fix"
them. Verify with the owned test package only: `cd RAG_pipeline/backend && .venv/bin/pytest
tests/test_topology -q` (there is no `python` on PATH and no system pytest).
