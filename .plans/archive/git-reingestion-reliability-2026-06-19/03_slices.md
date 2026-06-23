---
feature: Git webhook reingestion reliability and patch-first update flow
pipeline_step: 3_slices
total_slices: 5
current_slice: 0
---

## Slice 1: Credential-safe Git operations and repair report

**Delivers**: Git project clone/fetch/diff paths no longer persist tokenized remotes, and operators can inspect/repair existing Git projects without running expensive generation.

**Expected files**:
- `knowledge_base/mcp-server/src/mcp_server/services/git_remote_service.py` or equivalent centralized helper
- `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
- `knowledge_base/mcp-server/src/mcp_server/services/project_registry.py`
- `knowledge_base/mcp-server/src/mcp_server/services/git_poller.py`
- `knowledge_base/mcp-server/src/mcp_server/services/git_diff_patcher.py`
- `knowledge_base/mcp-server/src/mcp_server/services/git_reingestion.py`
- `knowledge_base/mcp-server/src/mcp_server/services/auto_doc_update.py`
- `knowledge_base/mcp-server/tests/test_services/test_git_remote_service.py`
- Existing MCP tests for poller, patcher, reingestion, auto-doc update, and project routes

**Critical area**: yes - MCP API routes (`knowledge_base/mcp-server/src/mcp_server/api/`)

**Depends on**: none

**Acceptance checkpoint**:
- New clones store a clean `origin` URL.
- Fetch/diff/reingestion paths restore clean remotes on success and failure.
- Existing projects can be reported as clean, tokenized, auth-failing, missing baseline, or missing cache without exposing credentials.
- Repair does not trigger scan, patch, generation, or RAG sync.

## Slice 2: Webhook receipt, matching, and setup observability

**Delivers**: GitHub webhook `ping` and `push` deliveries become diagnosable from project state, with canonical repo matching and durable early failure evidence.

**Expected files**:
- `knowledge_base/mcp-server/src/mcp_server/api/routes/webhook.py`
- `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
- `knowledge_base/mcp-server/src/mcp_server/services/project_registry.py`
- `knowledge_base/mcp-server/src/mcp_server/services/auto_doc_update.py`
- `knowledge_base/mcp-server/tests/test_api/test_webhook.py`
- `knowledge_base/mcp-server/tests/test_api/test_projects.py` or nearest existing route tests
- `deploy/WEBHOOK-SETUP.md`

**Critical area**: yes - MCP API routes (`knowledge_base/mcp-server/src/mcp_server/api/`)

**Depends on**: Slice 1

**Acceptance checkpoint**:
- `ping` records matched/unmatched project evidence.
- `push` records `webhook_received`, `project_matched`, and early failure reason codes before long-running work starts.
- Repo matching handles clean URLs, `.git` suffix differences, and sanitized stored URLs.
- Unregistered repos do not trigger scan, generation, patching, or RAG sync.

## Slice 3: Patch-first decision state machine

**Delivers**: Every push resolves to one explicit outcome (`skip`, `bounded_patch`, `targeted_regen`, `full_regen`, `manual_review`, or `failed`) with reason codes, safe patch gating, RAG result, and SHA advancement only after success.

**Expected files**:
- `knowledge_base/mcp-server/src/mcp_server/services/auto_doc_update.py`
- `knowledge_base/mcp-server/src/mcp_server/services/git_diff_patcher.py`
- `knowledge_base/mcp-server/src/mcp_server/services/git_reingestion.py`
- `knowledge_base/mcp-server/src/mcp_server/services/rag_bridge.py`
- `knowledge_base/mcp-server/tests/test_services/test_auto_doc_update.py`
- `knowledge_base/mcp-server/tests/test_services/test_git_diff_patcher.py`
- `knowledge_base/mcp-server/tests/test_services/test_git_reingestion.py`
- `knowledge_base/mcp-server/tests/test_services/test_rag_bridge.py`

**Critical area**: no - MCP service decisioning and RAG bridge only

**Depends on**: Slice 1

**Acceptance checkpoint**:
- Small bounded doc-relevant diffs choose `bounded_patch` only when baseline, docs, mapping, thresholds, and validation pass.
- Unsafe, unmapped, missing-baseline, missing-doc, auth-failed, or validation-failed changes choose `manual_review` or explicit regeneration-required outcomes instead of silently regenerating.
- `last_ingested_commit_sha` advances only after patch/regen execution and required RAG sync succeed.
- RAG sync failure is recorded distinctly and does not masquerade as a fully ingested commit.

## Slice 4: Cache-aware targeted regeneration and safe rebaseline

**Delivers**: Regeneration is available as a controlled fallback with visible cache readiness, cost/fallback reasons, preserved staging swap behavior, and a safe path to rebaseline old projects.

**Expected files**:
- `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
- `knowledge_base/mcp-server/src/mcp_server/services/git_reingestion.py`
- `knowledge_base/mcp-server/src/mcp_server/services/rag_support_package.py`
- `knowledge_base/mcp-server/src/mcp_server/services/auto_doc_update.py`
- `knowledge_base/mcp-server/src/mcp_server/services/rag_bridge.py`
- `knowledge_base/mcp-server/tests/test_services/test_git_reingestion.py`
- `knowledge_base/mcp-server/tests/test_services/test_rag_support_package.py`
- `knowledge_base/mcp-server/tests/test_services/test_auto_doc_update.py`
- `knowledge_base/mcp-server/tests/test_api/test_projects.py` or nearest existing route tests

**Critical area**: yes - MCP API routes (`knowledge_base/mcp-server/src/mcp_server/api/`) and harness-adjacent generation state

**Depends on**: Slices 1 and 3

**Acceptance checkpoint**:
- Projects with missing `_rag_support` or `analysis_cache` are marked as not targeted-regen-ready.
- Targeted regeneration runs only when changed groups and cache/support artifacts are usable.
- Full regeneration records an expensive-fallback reason before running.
- Failed generation preserves old `docs/` through the existing staging behavior.
- Successful regeneration restores `analysis_cache`, syncs RAG, and advances baseline SHA after success.

## Slice 5: End-to-end smoke path and minimal operator visibility

**Delivers**: A maintainer can prove the full workflow with a real or simulated GitHub push and see current webhook/reingestion health without reading container internals first.

**Expected files**:
- `deploy/WEBHOOK-SETUP.md`
- `scripts/` or `knowledge_base/mcp-server/scripts/` smoke-test script, if a script is chosen
- `knowledge_base/dashboard/src/` status components or existing project detail components, if dashboard visibility is needed
- `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`, if a status endpoint needs additions
- `knowledge_base/mcp-server/tests/test_api/test_webhook.py`
- `knowledge_base/dashboard/src/**/*.test.tsx` or nearest existing dashboard tests, if dashboard UI changes are made

**Critical area**: yes - MCP API routes if status endpoint additions are required

**Depends on**: Slices 1, 2, 3, and 4

**Acceptance checkpoint**:
- Runbook or script covers commit/push to a test repo, webhook delivery, decision recording, patch or regeneration execution, RAG sync, and SHA advancement.
- Operator-visible status includes last ping, last push outcome, last error, last ingested SHA, remote hygiene, and cache readiness.
- Focused MCP/RAG tests and the documented smoke path pass before commit.
