---
inspected_at: 2026-06-16
verdict: WARN
---

## Verdict Summary
The Talend TMC adapter conforms to the approved mocked-transport scope and all runtime/credential contracts; findings are limited to commit hygiene and an operational token-cache note.

## Check Results
| Check              | Result | Notes |
|--------------------|--------|-------|
| Plan conformance   | PASS   | Adapter, registry entry, settings, tests, Compose env hooks, env template, RUNBOOK/SMOKE-TEST sections all implemented. No real TMC service added (image unavailable) — matches the plan's "env/secret hooks only" branch. |
| Root cause         | PASS   | `talend_tmc` previously resolved as unknown; now a real adapter behind the generic protocol. No symptom-patching. |
| Generalized        | PASS   | Provider keys exist only in the single `_PROVIDER_MODULE_PATHS` registry + doc comments; status route is fully provider-agnostic (reads settings, never the registry). Adding a provider is one-line and consistent with `mock`. |
| Design patterns    | PASS   | Adapter (TMC ops behind generic `RuntimeProvider`) + Strategy/registry + module-singleton, mirroring `mock.py`. Transport injected for testability. |
| Contract respect   | PASS   | Returns normalized models only; client.py remains the sanitize/timeout/size/error boundary; status response shape unchanged; no changes to retrieval/auth/RBAC/MCP/harness/LLM loop/frontend. |
| No special-casing  | PASS   | No flags or shims. Talend op names confined to the adapter. |
| Edge cases         | PASS   | Tests cover all four mappings, normalization x4, unknown-state fallback, missing/empty token, missing base URL, HTTP 401/403/500, timeout, malformed + missing-list payloads, no-token-in-errors, and client integration (success + provider_error). Verification run: 29 focused, 61 runtime/gen, 475 full suite, `docker compose config` exit 0, secret-hygiene grep clean. |
| RBAC               | N/A    | Outbound HTTP adapter, not a DB/vector query. The chat route that triggers runtime already enforces conversation ownership upstream; no new route added. |
| Credential safety  | PASS   | Token read from mounted file, never env; not logged (logs carry op name + "2xx" only); never in exception messages (status code / exception type only); test asserts the fixture token never appears in errors. Bearer header not logged. |

## Findings

1. WARN — Token cache lifetime: `TalendTmcRuntimeProvider._token` caches the token on the process-lifetime module singleton (`talend_tmc.py`). A rotated token requires a backend restart to take effect. This is documented implicitly (RUNBOOK says restart backend to apply), but operators should be aware. Acceptable for this slice; revisit if hot rotation is ever required.

2. WARN — Commit hygiene (carried over): `RAG_pipeline/frontend/tsconfig.tsbuildinfo` (build artifact), the `knowledge_base/pack-repos/table-inventory-pack` `-dirty` submodule pointer, and untracked root junk (`.mavis/`, `=`, stray `.txt`) are unrelated to this slice and must be excluded from the commit.

3. INFO — Assumed wire format: the adapter's upstream operation names and request/response shapes are the plan's documented assumptions, not a confirmed contract (external TMC service unavailable). This was an explicit, approved downscope to a mocked-transport scaffold; the module docstring marks it and defers real-data reconciliation to Slice 6. Not a defect.

4. INFO — Plan listed `tests/test_api/test_runtime_status.py` and `deploy/deploy-client.sh` as affected files; both were intentionally left unchanged. The status route is provider-agnostic so its tests stay valid (61 pass), and the deploy script change was conditional in the plan — secret creation is documented in RUNBOOK section 13 instead. Defensible reduction in scope; contracts intact.

5. INFO — The production `httpx.AsyncClient(transport=None, ...)` construction line is not directly exercised (unit tests inject `MockTransport`; the client integration test patches the method). The same code path runs under MockTransport, so coverage is effectively equivalent; real-network construction is standard httpx. Slice 6 exercises it for real.

## Required Actions

- BLOCK: None.
- WARN: When committing, stage only the slice files:
  `RAG_pipeline/backend/src/config.py`, `RAG_pipeline/backend/src/runtime/client.py`,
  `RAG_pipeline/backend/src/runtime/providers/talend_tmc.py`,
  `RAG_pipeline/backend/tests/test_runtime/test_client.py`,
  `RAG_pipeline/backend/tests/test_runtime/test_talend_tmc_provider.py`,
  `docker-compose.yml`, `.env.production.template`, `deploy/RUNBOOK.md`, `deploy/SMOKE-TEST.md`, and `.plans/`.
  Do not stage `tsconfig.tsbuildinfo`, the submodule pointer, or untracked root junk.
- WARN: Carry findings 1, 3, 4, 5 forward as known context for Slice 6 (confirm real wire format; verify token rotation/restart expectation; exercise the live transport).
