---
feature: Git webhook reingestion reliability and patch-first update flow
pipeline_step: 1_grill
date: 2026-06-18
---

## Feature

Make GitHub push-triggered reingestion reliable, observable, secure, and
patch-first. A push to a registered GitHub project should be seen by the MCP
webhook, classified by impact, and then either skipped, patched, or regenerated
only when patching is not safe.

## Problem Statement

The current Git reingestion mechanism is not trusted. Webhooks are hard to set up
and do not appear to fire consistently. No successful patch update has been
observed in normal use. The full fallback behavior is also unproven, and the
decision logic for patch versus regenerate is unclear.

Initial evidence from the 2026-06-18 analysis:

- Focused unit tests pass for webhook dispatch, poller detection, patch/regen
  decisioning, incremental cache staging, `_rag_support`, RAG bridge sync, and RAG
  internal ingest endpoints.
- The local stack is healthy and a local webhook `ping` reaches the MCP endpoint.
- Several live Git projects have `auto_reingest_error=authentication_failed`.
- Several live Git projects have no `last_ingested_commit_sha`, so first-push
  logic cannot perform incremental patching.
- Existing Git remotes are tokenized for most Git projects, which is a security
  and reliability defect.
- Sampled Git projects lack `_rag_support/phase2_analysis` and `analysis_cache`,
  so targeted regeneration currently falls back to full generation instead of
  reusing cached Phase 2 group analyses.

## Definition of Done

- GitHub webhook setup is deterministic and easy to verify from the dashboard and
  logs.
- A GitHub `ping` event records clear project-level evidence that the webhook is
  reachable and matched to a registered project.
- A GitHub `push` event runs the full workflow:
  webhook received -> project matched -> Git diff inspected -> intent/impact
  decision made -> skip, bounded patch, targeted regen, or manual review
  selected -> result persisted in `git_update_summary`.
- Patch is the default for small, bounded, doc-relevant changes that can be safely
  mapped to existing docs.
- Regeneration is a last-case fallback because it can cost roughly 2M-8M tokens.
- Regeneration happens only when the diff is structurally unsafe to patch, the
  changed code cannot be mapped to existing docs, a patch fails validation in a
  way that requires regenerated evidence, or there is no usable baseline/cache.
- Successful patch updates push only patched markdown files to RAG and advance
  `last_ingested_commit_sha`.
- Successful full/targeted regeneration scans, generates, swaps docs, pushes
  canonical docs and internal support layers to RAG, rebuilds `analysis_cache`,
  and advances `last_ingested_commit_sha`.
- Tokenized Git remotes are cleaned and future clone/fetch/update paths never
  persist PATs in `.git/config`.
- Existing projects can be repaired or re-baselined so the next push has a clean
  `last_ingested_commit_sha`, clean remote, and support cache where available.
- Verification includes an end-to-end test or smoke script: commit/push to a test
  GitHub repo -> webhook fires -> decision is recorded -> patch or regen runs ->
  RAG sync runs -> commit SHA advances.

## Out of Scope

- GitHub App migration.
- Dashboard redesign beyond necessary visibility/status controls.
- Changing generated documentation quality requirements.
- Changing the RAG retrieval algorithm.
- Changing auth/RBAC behavior except where needed to keep webhook and project
  access checks intact.
- Changing the core documentation generation prompt strategy except where needed
  to make reingestion reuse existing outputs correctly.

## Solution Rationale

The right direction is to treat reingestion as an explicit state machine with a
small number of auditable outcomes. Webhook receipt, Git access, diff impact,
patch applicability, RAG sync, and commit advancement should each leave durable
evidence. This avoids guessing whether "the webhook worked" and makes the patch
versus regeneration decision explainable.

Patch-first is required because full regeneration is expensive. However, patching
must be bounded and validated; a bad patch silently corrupting docs/RAG is worse
than a manual review. Regeneration remains available but should be triggered by
specific evidence, not vague low confidence.

Tokenized remotes must be cleaned as part of the work because they can break Git
auth behavior, leak PATs through future errors, and undermine trust in the entire
mechanism.

## Assumptions and Risk

- Assumption: GitHub webhook delivery may be inconsistent because of setup,
  public URL, webhook registration, signature config, token permissions, or
  project matching, not only code.
- Assumption: The local focused tests prove unit wiring but not real GitHub
  delivery or real end-to-end patch behavior.
- Assumption: Existing projects may need a one-time rebaseline after fixes so
  `last_ingested_commit_sha`, clean remotes, `_rag_support`, and `analysis_cache`
  are correct.
- Assumption: Some old generated docs were created before `_rag_support` existed,
  so incremental regen cannot reuse Phase 2 analysis until a fresh successful
  generation runs.
- Risk: The patch decision could be too aggressive and update the wrong doc
  section.
- Risk: The patch decision could be too conservative and trigger expensive full
  regeneration too often.
- Risk: Fixing tokenized remotes incorrectly could break private repo access.
- Risk: A partial RAG sync failure could leave docs updated but chatbot answers
  stale.

## Critical Areas Touched

- MCP API routes: webhook route, GitHub webhook management endpoints, project
  status/summary fields.
- MCP services: Git poller, auto-doc update service, diff patcher, Git
  reingestion, Git token/remote handling, RAG bridge handoff.
- Harness/pipeline adjacent: targeted regeneration relies on staged analysis
  cache and `_rag_support`; changes may touch generation resume/cache behavior.
- RAG ingest routes may be touched if RAG sync observability or deletion/update
  semantics need tightening.

Because MCP API routes and harness-adjacent generation behavior are in scope,
`/inspect` is required before commit.

## Risk if Wrong

The entire reingestion mechanism remains unreliable. Worst cases:

- GitHub pushes are missed and docs/RAG silently go stale.
- PATs remain persisted in Git remotes or leak through logs/errors.
- Patch mode corrupts generated docs or indexes bad content into RAG.
- Regeneration runs too often and burns 2M-8M tokens per update.
- RAG sync does not reflect the docs on disk, so chatbot answers become stale or
  inconsistent.
