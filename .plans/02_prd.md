---
feature: Git webhook reingestion reliability and patch-first update flow
pipeline_step: 2_prd
---

## Executive Summary

Make GitHub push-triggered reingestion deterministic, observable, secure, and
patch-first. A push to a registered GitHub project must leave enough durable
evidence to answer: did the webhook arrive, did it match the right project, did
Git diff inspection work, why was the update skipped/patched/regenerated/manual,
did RAG sync complete, and was `last_ingested_commit_sha` advanced?

The implementation should repair the current trust gaps without changing the
core documentation quality goals. Small bounded changes should patch existing
docs. Regeneration should be a last-case fallback because it can cost roughly
2M-8M tokens. Existing projects also need a repair/rebaseline path because live
inspection found authentication failures, tokenized remotes, missing baseline
SHAs, and missing `_rag_support`/`analysis_cache` artifacts.

## Problem

The current Git reingestion mechanism is not trusted in normal use. Webhooks are
hard to set up, may not fire consistently, and no successful production-like
patch update has been observed. Unit tests for the isolated services pass, but
they do not prove real GitHub delivery, real private-repo auth behavior, or an
end-to-end patch from GitHub push through RAG sync.

Observed issues from the 2026-06-18 analysis:

- Some live Git projects have `auto_reingest_error=authentication_failed`.
- Some live Git projects have no `last_ingested_commit_sha`, so incremental
  patching cannot safely run on the next push.
- Several existing Git remotes are persisted with embedded credentials, which is
  both a reliability defect and a security defect.
- Sampled generated-doc directories lack `_rag_support/phase2_analysis` and
  `analysis_cache`, so targeted regeneration falls back to full generation.
- The patch versus regenerate policy exists in code, but the operational
  contract is not explicit enough to make cost and correctness predictable.
- A partial success, especially docs updated but RAG stale, is hard to diagnose
  from the current project state alone.

## Goals

- Make webhook setup and delivery easy to verify from project state, logs, and a
  repeatable smoke test.
- Persist one clear reingestion outcome per push in `git_update_summary`, with
  the decision, reason, source SHA, target SHA, touched files, and RAG result.
- Make patch the default for small, bounded, doc-relevant changes that can be
  safely mapped to existing documentation.
- Make skip/manual/targeted-regeneration/full-regeneration decisions explicit
  and testable.
- Prevent personal access tokens or other credentials from being persisted in
  `.git/config`, project summaries, logs, or API responses.
- Provide a repair/rebaseline path for existing Git projects with tokenized
  remotes, auth failures, missing baseline SHAs, or missing support cache.
- Preserve the current server-side RAG push contract: MCP pushes generated or
  patched markdown through `RagBridge` and the RAG backend ingest APIs.
- Keep old docs intact when generation fails, using the existing staging swap
  behavior.

## Non-Goals

- Migrating from webhook secrets/PATs to a GitHub App.
- Redesigning the dashboard beyond necessary webhook/reingestion visibility and
  operator actions.
- Changing generated documentation quality requirements.
- Changing the RAG retrieval algorithm.
- Changing auth/RBAC behavior except where needed to keep webhook and project
  access checks intact.
- Changing the core documentation generation prompt strategy except where needed
  to reuse existing outputs and support incremental generation correctly.

## User Stories

- As an operator, I can register or verify a GitHub webhook and see whether a
  `ping` event reached the MCP server and matched the expected project.
- As an operator, after pushing a small code or config change, I can see the
  update classified as a bounded patch, with patched files and RAG sync status.
- As an operator, after pushing a broad or unsafe change, I can see exactly why
  the system selected targeted regeneration, full regeneration, or manual review.
- As an operator, I can repair an existing project so its Git remote is clean,
  Git auth works, baseline SHA is known, and support cache state is visible.
- As a maintainer, I can run a local/smoke test that simulates or exercises a
  GitHub push and proves the workflow from webhook receipt through SHA advance.
- As a maintainer, I can audit logs and summaries without seeing raw credentials.

## Functional Requirements

### Webhook Receipt and Matching

- The GitHub webhook endpoint must verify HMAC signatures unless the explicit
  development bypass is enabled.
- `ping` events must return a clear matched/unmatched result and persist project
  evidence such as `last_webhook_ping_at`, matched repository URL, and any
  mismatch reason.
- `push` events must persist durable run evidence before and after processing,
  even when processing fails early.
- Repository matching must handle canonical URL variants, including `.git`
  suffix differences and sanitized URLs.
- Unregistered repositories must be rejected or recorded as unmatched without
  triggering scan, patch, generation, or RAG sync.

### Git Credential and Remote Handling

- Clone, fetch, diff, poller, patcher, and reingestion paths must use credentials
  only for the active Git operation and must restore the clean remote URL in a
  `finally` path.
- Newly registered Git projects must not persist tokenized remotes after clone.
- Existing projects must have a repair routine that detects and cleans tokenized
  remotes, then verifies fetch access using the stored credential.
- Logs, errors, project summaries, and API responses must redact credentials.
- Tests must cover clone/setup, fetch failure, fetch success, diff inspection,
  patcher, poller, and reingestion remote restoration.

### Reingestion State Machine

- Every push must resolve to exactly one top-level outcome:
  `skip`, `bounded_patch`, `targeted_regen`, `full_regen`, `manual_review`, or
  `failed`.
- Each outcome must include a machine-readable reason code and a human-readable
  explanation in `git_update_summary`.
- The state machine must record at least these stages:
  `webhook_received`, `project_matched`, `git_fetch_started`,
  `git_fetch_completed`, `diff_inspected`, `decision_made`,
  `execution_started`, `rag_sync_started`, `rag_sync_completed`,
  `sha_advanced`, and `failed`.
- `last_ingested_commit_sha` must advance only after the selected update path
  and required RAG sync finish successfully.
- If RAG sync fails after docs are updated, the project must record a distinct
  RAG error state and must not pretend the push is fully ingested.

### Patch-First Decision Policy

- `skip` is valid only for ignored or clearly non-doc-impacting changes.
- `bounded_patch` is valid only when all of these are true:
  - The project has a known baseline SHA and existing docs.
  - The diff is small enough for the configured patch thresholds.
  - Changed files are doc-relevant and map to existing generated docs/sections.
  - The patcher can produce bounded markdown changes without broad rewrites.
  - Patch validation passes before RAG sync.
- `manual_review` is required when there is no safe automated action, including
  first push without baseline, missing docs, unregistered repo, auth failure,
  unknown project match, no mapped docs, unsafe diff, or patch validation failure.
- `targeted_regen` is valid when patching is unsafe but changed groups can be
  resolved and required generation cache/support artifacts are usable.
- `full_regen` is valid only when a baseline must be created, targeted cache is
  missing or invalid, changed scope is too broad for targeted regeneration, or a
  successful repair/rebaseline explicitly requires it.
- Regeneration decisions must record an estimated cost or at least a clear
  "expensive fallback" flag before running.

### Generation, Cache, and RAG Sync

- Bounded patch must push only patched top-level markdown files to RAG.
- Targeted/full regeneration must preserve the existing staging swap contract:
  write to `docs_staging/`, swap to `docs/` only on success, and delete failed
  staging output without losing old docs.
- Regeneration must push canonical top-level markdown docs to RAG and sync any
  supported internal layers through the existing server-side bridge.
- Successful generation must rebuild or restore `analysis_cache` from
  `_rag_support/phase2_analysis` when the support package exists.
- If `_rag_support` or `analysis_cache` is missing, the system must record the
  fallback reason and avoid presenting targeted regeneration as available.
- Removed docs must be deleted from RAG when regeneration removes them.

### Existing Project Repair and Rebaseline

- Provide an operator-safe repair flow for each Git project:
  clean remote URL, verify stored credential fetch, inspect baseline SHA, inspect
  docs/support-cache state, and report the next safe action.
- The repair flow must not run expensive generation without an explicit operator
  action or an approved plan step.
- Projects with no `last_ingested_commit_sha` must be marked as needing baseline
  rather than trying to patch an unknown diff.
- Projects with missing `_rag_support`/`analysis_cache` must be marked as needing
  full rebaseline before targeted regeneration can be trusted.

### Verification and Observability

- Add or update automated tests for webhook ping/push, repo matching,
  token-safe remotes, patch decisioning, manual-review fallbacks, targeted/full
  regen fallbacks, RAG sync success/failure, and SHA advancement.
- Add a smoke script or documented runbook for a real or local GitHub-style push:
  commit/push to test repo, webhook received, decision recorded, patch or regen
  executed, RAG sync observed, and SHA advanced.
- Minimal dashboard/API visibility must show webhook status, last ping, last push
  outcome, last error, last ingested SHA, remote hygiene status, and cache
  readiness.

## Non-Functional Requirements

- Security: no raw PATs or secrets in Git remotes, logs, summaries, test output,
  or API responses.
- Cost control: full regeneration must be explicitly justified and visible before
  running whenever an operator path is involved.
- Reliability: processing must be idempotent for duplicate GitHub deliveries and
  must not run two conflicting reingestions for the same project/SHA.
- Observability: every failure path must leave a specific reason code and enough
  context to diagnose the next action without reading container internals first.
- Performance: webhook handlers should acknowledge promptly and do long-running
  scan/generation/RAG work in background tasks.
- Compatibility: public and private GitHub repositories must remain supported.
- Maintainability: duplicated Git token/remote logic should be centralized in a
  small service or helper used by all Git paths.
- Code quality: Python changes must keep type hints, async I/O boundaries, and
  specific exception handling.

## Architecture Impact

- `knowledge_base/mcp-server/src/mcp_server/api/routes/webhook.py` will need
  stronger durable event evidence, canonical project matching, and clearer early
  failure summaries.
- `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` may need
  updates for Git project registration, webhook status, repair/rebaseline
  actions, and any background generation/RAG state reporting.
- `knowledge_base/mcp-server/src/mcp_server/services/auto_doc_update.py` should
  become the explicit decision/state-machine owner for skip, patch, regeneration,
  manual review, and failure reason codes.
- `knowledge_base/mcp-server/src/mcp_server/services/git_diff_patcher.py`,
  `git_poller.py`, and `git_reingestion.py` should share centralized
  credential-safe Git remote handling instead of each managing remotes directly.
- `knowledge_base/mcp-server/src/mcp_server/services/rag_bridge.py` and the RAG
  ingest endpoints may need tighter success/failure reporting, but RAG retrieval
  behavior is not in scope.
- `knowledge_base/mcp-server/src/mcp_server/services/rag_support_package.py` and
  generation background logic must keep the `_rag_support` to `analysis_cache`
  contract visible to reingestion decisions.
- Dashboard changes, if any, should be limited to operational status and repair
  controls needed to validate webhook/reingestion health.
- Deployment docs such as `deploy/WEBHOOK-SETUP.md` should be updated to match
  the verified setup and smoke-test flow.

## Implementation Phases

1. Git credential safety and repair foundation:
   centralize clean/injected remote handling, stop future token persistence, add
   detection/cleanup for existing tokenized remotes, and test all Git paths.
2. Webhook observability and setup verification:
   persist ping/push receipt evidence, normalize repository matching, expose
   last delivery status, and document/setup smoke checks.
3. Patch-first decision state machine:
   make the outcome policy explicit, add reason codes, ensure manual review is
   chosen for unsafe cases, and gate SHA advancement on successful execution and
   RAG sync.
4. Rebaseline and cache readiness:
   inspect baseline SHA, docs, `_rag_support`, and `analysis_cache`; provide a
   safe repair report/action path; make targeted/full regeneration fallback
   reasons visible.
5. End-to-end validation:
   run focused MCP/RAG tests plus a real or locally simulated GitHub push smoke
   test that proves webhook to decision to patch/regen to RAG to SHA advancement.

## Risks and Open Questions

- Real GitHub delivery can fail outside code because of public URL, tunnel,
  reverse proxy, secret mismatch, webhook event configuration, or GitHub repo
  permissions.
- Private repo auth may fail after cleaning remotes if token injection/restoration
  is incomplete in any Git path.
- Existing projects may require one expensive rebaseline before patch-first or
  targeted regeneration can be trusted.
- A patch policy that is too aggressive can corrupt docs; a policy that is too
  conservative can trigger expensive regeneration too often.
- RAG sync may need better partial-failure semantics if docs swap succeeds but
  indexing fails.
- Open question: should durable webhook/reingestion history stay in existing
  JSON fields, or should a small run/event table be added for auditability?
- Open question: what is the exact production webhook URL and is it stable
  enough for GitHub delivery without a tunnel?
- Open question: which existing projects should be rebaselined first after the
  repair flow is available?

## Acceptance Criteria

- Focused MCP tests pass for webhook route, Git poller, auto-doc update, Git
  reingestion, RAG support package, RAG bridge, and Git diff patcher.
- Focused RAG ingest tests pass for markdown ingest/delete/internal-layer
  behavior touched by this work.
- No newly cloned or fetched Git project persists credentials in `.git/config`.
- Existing sampled Git projects can be reported as clean or repaired without
  exposing credentials.
- A webhook `ping` records matched project evidence and is visible through the
  project status path.
- A simulated or real `push` for a small bounded change records
  `bounded_patch`, patches only expected docs, pushes those docs to RAG, and
  advances `last_ingested_commit_sha` only after RAG success.
- A push with no baseline or missing docs records `manual_review` or
  `full_regen_required` without silently running an expensive regeneration.
- A push that cannot safely patch records a targeted/full regeneration reason,
  cache readiness, and expected cost/fallback status before regeneration.
- RAG sync failure is recorded distinctly and does not masquerade as a fully
  ingested commit.
- `deploy/WEBHOOK-SETUP.md` or an equivalent runbook contains the verified
  webhook setup and end-to-end smoke-test procedure.
- Because MCP API routes and harness-adjacent generation behavior are in scope,
  `/inspect` is run before commit.
