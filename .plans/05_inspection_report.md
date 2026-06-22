---
inspected_at: 2026-06-19
verdict: PASS
---

# Inspection Report - Slice 5 End-to-End Smoke Path and Minimal Operator Visibility

Feature: Git webhook reingestion reliability and patch-first update flow
Slice: Slice 5
Sub-project: knowledge_base
Critical area: MCP API routes

## Verdict Summary

PASS. The Slice 5 status endpoint and smoke path now give operators a compact,
read-only way to confirm webhook receipt, matching, update decision, RAG result,
SHA baseline, remote hygiene, and cache readiness. No remaining blocking or
warning findings.

## Check Results

- Plan conformance: PASS
- Root cause: PASS
- Generalized fix: PASS
- Design patterns: PASS
- Contract respect: PASS
- No special-casing: PASS
- Edge cases: PASS
- RBAC: PASS
- Credential safety: PASS

## Findings

No remaining findings.

## Fixed During Inspection

1. Real GitHub signed deliveries would fail HMAC validation because the route and
   tests used `X-GitHub-Signature-256`. GitHub sends `X-Hub-Signature-256`.
   The route now accepts the standard header first and keeps the legacy header
   as a compatibility fallback (`knowledge_base/mcp-server/src/mcp_server/api/routes/webhook.py:51`).
   The smoke script now signs with `X-Hub-Signature-256`
   (`knowledge_base/mcp-server/scripts/smoke_reingestion_webhook.py:43`).

2. The smoke script tried to authenticate the final `/mcp/.../reingestion-status`
   GET with a bearer token, but MCP project routes use the `access_token` cookie.
   The script now sends `--auth-token` as `Cookie: access_token=...` and redacts
   it in dry-run output
   (`knowledge_base/mcp-server/scripts/smoke_reingestion_webhook.py:178`).

## Evidence

- `GET /mcp/projects/{project_id}/reingestion-status` is registered as a
  project-scoped response model and only calls `inspect_reingestion_readiness()`;
  it does not start scan, generation, patching, RAG sync, fetch, or pull work
  (`knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py:638`).
- The response builder sanitizes nested strings before returning webhook,
  update, RAG, regeneration, and error payloads
  (`knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py:133`).
- The status response includes project status, webhook registration and last
  observation, normalized last update fields, SHA baseline, remote hygiene, and
  cache/rebaseline readiness
  (`knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py:157`,
  `knowledge_base/mcp-server/src/mcp_server/schemas.py:138`).
- Focused tests prove status aggregation without workflow side effects and
  prove signed webhook receipt with the standard GitHub signature header
  (`knowledge_base/mcp-server/tests/test_api/test_projects.py:498`,
  `knowledge_base/mcp-server/tests/test_api/test_webhook.py:22`).
- `deploy/WEBHOOK-SETUP.md` now documents real GitHub smoke steps, local
  simulated smoke steps, expected outcome states, reason codes, and auth/header
  details.

## Required Actions

None.

## Validation Evidence

- `python3 -m py_compile src/mcp_server/api/routes/projects.py src/mcp_server/api/routes/webhook.py src/mcp_server/schemas.py tests/test_api/test_projects.py tests/test_api/test_webhook.py scripts/smoke_reingestion_webhook.py`: PASS.
- `python3 scripts/smoke_reingestion_webhook.py --help`: PASS.
- `python3 scripts/smoke_reingestion_webhook.py --dry-run --base-url http://127.0.0.1:8002 --project-id smoke-proj --repo-url https://github.com/acme/repo.git --secret test-secret --auth-token test-jwt`: PASS.
- `uv run pytest tests/test_api/test_projects.py tests/test_api/test_webhook.py -q`: 42 passed.
- `uv run pytest -q`: 658 passed.
- `git diff --check -- <Slice 5 files>`: PASS.

## Residual Risk

No live GitHub delivery or live MCP server smoke was run in this session. The
local smoke script help/dry-run and API tests cover request signing, status
shape, and side-effect boundaries; a real repository delivery remains an
operator release smoke step.
