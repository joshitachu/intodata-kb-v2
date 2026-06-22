# GitHub Webhook Setup

Use this guide after the project has already been registered and initially ingested in the platform. Do not create the GitHub webhook first; the platform needs a registered project so the initial ping and later push events can be matched to a project.

## Endpoint

- Webhook URL: `https://<client>.nexus.intodata.nl/webhook/github/webhook`
- Content type: `application/json`
- Events: select push and pull request events. GitHub also sends an automatic `ping` event when the webhook is created or updated; the platform accepts that ping for validation.
- Secret environment variable: `MCP_GITHUB_WEBHOOK_SECRET`
- Signature header: GitHub sends `X-Hub-Signature-256`; the server verifies this HMAC-SHA256 value against `MCP_GITHUB_WEBHOOK_SECRET`.

Generate a strong secret:

```bash
openssl rand -hex 32
```

Set the generated value on the MCP server host as `MCP_GITHUB_WEBHOOK_SECRET`, then restart the service so the API process reads it.

## Private Repository Access

For private repositories, configure host-level Git access before registering the repo. Use one of:

- A read-only deploy key installed on the host.
- A GitHub PAT with the minimum repository read permissions needed by `git clone` and `git pull`.
- SSH credentials loaded for the service user that runs the MCP server.

Verify the service user can clone and pull the repository before adding it in the platform.

## Setup Order

1. Configure `MCP_GITHUB_WEBHOOK_SECRET` on the MCP server host.
2. Configure Git credentials on the host if the repository is private.
3. Register the repository in the platform.
4. Run the initial scan, document generation, and RAG push.
5. In GitHub, open the repository settings and add a webhook.
6. Enter `https://<client>.nexus.intodata.nl/webhook/github/webhook` as the Payload URL.
7. Select `application/json` as the content type.
8. Paste the same secret value into the webhook Secret field.
9. Select push and pull request events.
10. Save the webhook and confirm GitHub's automatic ping delivery succeeds.
11. Push a small change and confirm the platform performs partial re-ingestion.

## Validation

A successful GitHub ping returns `ping_received`. If the repository is already registered, the response includes `registered: true` and `project_id`, the project log records the ping, and the project metadata records `git_update_summary.webhook` with `webhook_received` and `project_matched` observations.

A push or pull request event for a registered repository returns `webhook_triggered`. Before any background work starts, the project metadata records `git_update_summary.webhook` with the delivery ID, sanitized repository URL, event type, before/after SHAs for push events, and `webhook_received` / `project_matched` observations. Push events run the diff-only documentation update; pull request events run the existing scan behavior.

Use the compact operator status endpoint for all webhook/reingestion checks:

```bash
PROJECT_ID=<project-id>
curl -sS "https://<client>.nexus.intodata.nl/mcp/projects/${PROJECT_ID}/reingestion-status" \
  | jq '{
      project_id,
      project_status,
      webhook,
      last_update,
      last_ingested_commit_sha,
      remote_hygiene,
      cache
    }'
```

If API auth is enabled, include the same `access_token` cookie used for other MCP
project API calls. This endpoint is read-only: it inspects local project state
and does not start scan, generation, patching, RAG sync, fetch, or pull work.

Reason-code responses that do not start scan, generation, patching, reingestion, or RAG sync:

- `missing_clone_url` -- GitHub payload did not include `repository.clone_url`.
- `unsupported_event` -- the delivery was not `ping`, `push`, or `pull_request`.
- `unregistered_repo` -- no project or plugin pack matched the sanitized canonical repository URL.
- `invalid_sha_payload` -- a push event did not include valid 40-character before/after SHAs.

## Real GitHub Smoke Test

1. Confirm the project has completed initial scan, generation, and RAG push.
2. In GitHub, open the webhook delivery list and redeliver the latest `ping`, or
   update/save the webhook to create a fresh ping.
3. Read the status endpoint. Expected ping observations:
   - `webhook.last_observation.event` is `ping`.
   - `webhook.last_observation.status` is `matched`.
   - `webhook.last_observation.observations` includes `webhook_received` and
     `project_matched`.
   - `webhook.status` is `active` when the platform auto-registered the webhook,
     or `observed` when a manually created webhook has delivered and matched.
4. Push a small doc-relevant change to the registered repository.
5. Read the status endpoint immediately. Expected push receipt observations:
   - `webhook.last_observation.event` is `push`.
   - `webhook.last_observation.status` is `queued`.
   - `webhook.last_observation.before_sha` and `after_sha` match the GitHub
     delivery payload.
6. Poll the status endpoint until `last_update.outcome` changes from the prior
   state. Expected decision recording:
   - `last_update.action`, `outcome`, `reason_code`, and `reason` are populated.
   - `last_update.decision_strategy` shows the selected path when available.
   - `last_update.rag_result` or `last_update.regen_result` is populated when
     the selected path reaches RAG sync or generation.
7. Confirm SHA advancement only on success:
   - For successful `bounded_patch` or `targeted_regen`, `last_ingested_commit_sha`
     advances to the push `after_sha` after the required RAG sync succeeds.
   - For manual review, full rebaseline required, or RAG sync failure,
     `last_ingested_commit_sha` stays at the previous baseline.

## Local Simulated Smoke Test

The local smoke helper can post GitHub-style `ping` and `push` JSON payloads to
any MCP base URL. It signs requests when `--secret` is supplied and never prints
the secret.

Validate the CLI without a live server:

```bash
cd knowledge_base/mcp-server
python3 scripts/smoke_reingestion_webhook.py --help
```

Preview payloads without sending network requests:

```bash
python3 scripts/smoke_reingestion_webhook.py \
  --dry-run \
  --base-url http://127.0.0.1:8002 \
  --project-id "$PROJECT_ID" \
  --repo-url https://github.com/<org>/<repo>.git \
  --secret "$MCP_GITHUB_WEBHOOK_SECRET"
```

Run against a live MCP server:

```bash
python3 scripts/smoke_reingestion_webhook.py \
  --base-url https://<client>.nexus.intodata.nl \
  --project-id "$PROJECT_ID" \
  --repo-url https://github.com/<org>/<repo>.git \
  --secret "$MCP_GITHUB_WEBHOOK_SECRET" \
  --event both
```

If the project status endpoint requires API auth, add a JWT access token. The
script sends it as the MCP API's `access_token` cookie:

```bash
  --auth-token "$MCP_ACCESS_TOKEN"
```

The script prints the raw webhook responses and a final status summary with:
webhook event/delivery, last update outcome/action/reason, last ingested SHA,
remote hygiene state, and cache readiness.

## Expected Outcome States

- Ping matched: `webhook.last_observation.event=ping`,
  `webhook.last_observation.status=matched`, observations include
  `webhook_received` and `project_matched`.
- Push queued: `webhook.last_observation.event=push`,
  `webhook.last_observation.status=queued`, before/after SHAs are present.
- Bounded patch success: `last_update.outcome=bounded_patch`,
  `last_update.action=patched`, `last_update.rag_result.pushed` is greater than
  zero, `last_ingested_commit_sha` equals the push `after_sha`.
- Targeted regeneration success: `last_update.outcome=targeted_regen`,
  `last_update.regen_result.success=true`, `last_update.fallback_used=true`,
  `last_ingested_commit_sha` advances only after generation and RAG sync finish.
- Manual review: `last_update.outcome=manual_review`,
  `last_update.manual_review_reason` explains the blocker, and SHA does not
  advance.
- Full rebaseline required: `last_update.reason_code=full_rebaseline_required`,
  `last_update.requires_full_rebaseline=true`,
  `cache.requires_full_rebaseline=true`, and `cache.reason_codes` explains the
  missing baseline/docs/support/cache condition.
- RAG sync failure: `last_update.outcome=failed`,
  `last_update.reason_code=rag_sync_failed`, `last_update.rag_result.errors`
  contains the backend error, `last_rag_push_at` is not updated, and SHA does not
  advance.
- Cache ready for targeted regeneration:
  `cache.targeted_regen_ready=true`, `cache.has_rag_support=true`,
  `cache.has_analysis_cache=true`, and `cache.cache_support_match=true`.
- Remote hygiene ready: `remote_hygiene.status=ready`,
  `remote_hygiene.origin_had_credentials=false`,
  `remote_hygiene.repo_url_had_credentials=false`, and
  `remote_hygiene.origin_matches_repo_url=true`.

As part of release validation, ask three repository-specific questions in the chat UI and confirm the answers cite or reflect the newly generated project documentation. Testing by someone other than the implementation author remains a manual sign-off item.

## Troubleshooting

### `401 Invalid signature`

The webhook Secret in GitHub does not match `MCP_GITHUB_WEBHOOK_SECRET`, the server was not restarted after changing the environment variable, or the payload was modified by an intermediary. Regenerate the secret if needed, update both sides, restart the service, and redeliver the GitHub event.

### `503 Webhook secret not configured`

`MCP_GITHUB_WEBHOOK_SECRET` is missing from the MCP server process environment. Add it to the deployment environment and restart the service.

### Ping Succeeds But Shows `registered: false`

The repository URL in the platform does not match GitHub's `clone_url`, or the repository has not been registered yet. Register the repo first, then redeliver the ping. Matching is canonicalized for credential-stripped HTTPS URLs and a trailing `.git` suffix, so `https://github.com/org/repo` and `https://github.com/org/repo.git` are treated as the same repository.

### Push Event Is Ignored As Unregistered

Confirm the project exists in the platform and its `repo_url` matches the GitHub repository. URLs with and without a trailing `.git` are treated as equivalent, and token-injected HTTPS URLs are sanitized before comparison. SSH and HTTPS forms are different values.

### Push Event Is Ignored As Invalid SHA Payload

Check the GitHub delivery payload. Push handling requires valid 40-character `before` and `after` SHAs before it can queue diff analysis. The project metadata records the rejection under `git_update_summary.webhook` and sets `auto_reingest_error` to `invalid_sha_payload`.

### Clone Or Pull Failures

Check that the service user can run `git clone` and `git pull` for the repo from the host. For private repos, verify deploy key, PAT, or SSH agent setup. Also check outbound network access from the host to GitHub.

### No RAG Update After Push

Confirm the initial scan, generation, and RAG push completed before the webhook was created. Check project logs for scan or generation failures. For first pushes or unmapped file changes, the platform falls back to broader regeneration; for normal pushes, it stages unchanged analyses and performs partial RAG sync.
