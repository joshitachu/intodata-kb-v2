---
feature: Git webhook reingestion reliability and patch-first update flow
slice: Slice 5 - End-to-end smoke path and minimal operator visibility
sub_project: knowledge_base
critical_area: yes - MCP API route/status additions
verdict: PASS
approved_at: 2026-06-19
---

## Problem Statement

Slices 1-4 make webhook receipt, patch-first decisions, credential-safe Git, and
cache-aware regeneration safer, but maintainers still need one repeatable way to
prove the workflow and one compact status surface that shows the current
webhook/reingestion health without reading DB JSON or container logs first.

## Proposed Solution

Add a minimal operator status path and smoke procedure:

- Expose a project-scoped reingestion status response that aggregates webhook
  observation, last Git update outcome/error, last ingested SHA, webhook
  registration status, remote hygiene, and cache readiness.
- Add a local smoke script or runbook steps that can simulate GitHub `ping` and
  `push` payloads against the existing webhook endpoint, using HMAC signing when
  a secret is provided.
- Update `deploy/WEBHOOK-SETUP.md` so operators can verify: project registered,
  ping matched, push queued/processed, decision recorded, RAG sync result
  visible, and SHA advancement or explicit manual/rebaseline reason visible.

## Affected Files

- `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
- `knowledge_base/mcp-server/src/mcp_server/schemas.py`
- `knowledge_base/mcp-server/tests/test_api/test_projects.py`
- `knowledge_base/mcp-server/tests/test_api/test_webhook.py`
- `deploy/WEBHOOK-SETUP.md`
- `knowledge_base/mcp-server/scripts/smoke_reingestion_webhook.py` or equivalent

## Architecture Contracts

- The status endpoint must not trigger scan, generation, patch, Git network, or
  RAG sync work beyond the existing readiness inspection.
- The smoke script must not require secrets in source control and must not print
  raw PATs or webhook secrets.
- Webhook handling remains asynchronous for long-running work.
- Existing webhook route HMAC behavior must remain unchanged.
- Dashboard UI is out of scope unless API/project response visibility is not
  enough.

## Implementation Steps

1. Add a Pydantic response model for reingestion status that composes:
   project ID/status, webhook registration/status, last webhook observation,
   top-level update action/outcome/reason/error, last ingested SHA, readiness
   report, and cache readiness fields.
2. Add `GET /mcp/projects/{project_id}/reingestion-status` using
   `inspect_reingestion_readiness(project)` and existing `git_update_summary`.
3. Add focused API tests proving the status response includes last ping/push
   evidence, last outcome/error, last ingested SHA, remote hygiene status, and
   cache readiness without workflow side effects.
4. Add a smoke script that posts signed GitHub-style `ping`/`push` payloads to a
   target MCP base URL and prints the resulting status endpoint summary.
5. Update `deploy/WEBHOOK-SETUP.md` with real and simulated smoke paths, expected
   statuses, and troubleshooting notes for manual review, full rebaseline, and
   RAG sync failure.
6. Run py_compile, focused MCP route/webhook tests, and full MCP pytest.

## Verification Strategy

- `python3 -m py_compile` on touched Python files/tests/script.
- Focused tests:
  - `uv run pytest tests/test_api/test_projects.py tests/test_api/test_webhook.py -q`
- Full MCP server pytest after the slice.
- Run smoke script help or dry-run mode if available without requiring a live
  server.
- Independent inspection before final summary because MCP API routes are in
  scope.

## Out of Scope

- Dashboard UI changes unless the API status endpoint cannot satisfy visibility.
- Real GitHub network delivery; the runbook may document real delivery while the
  automated smoke path can be local/simulated.
- Any change to RAG retrieval behavior.
