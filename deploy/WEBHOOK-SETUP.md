# GitHub Webhook Setup

Use this guide after the project has already been registered and initially ingested in the platform. Do not create the GitHub webhook first; the platform needs a registered project so the initial ping and later push events can be matched to a project.

## Endpoint

- Webhook URL: `https://<client>.nexus.intodata.nl/webhook/github/webhook`
- Content type: `application/json`
- Events: select push events. GitHub also sends an automatic `ping` event when the webhook is created or updated; the platform accepts that ping for validation.
- Secret environment variable: `MCP_GITHUB_WEBHOOK_SECRET`

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
9. Select push events.
10. Save the webhook and confirm GitHub's automatic ping delivery succeeds.
11. Push a small change and confirm the platform performs partial re-ingestion.

## Validation

A successful GitHub ping returns `ping_received`. If the repository is already registered, the response includes `registered: true` and the project log records the ping.

A push event for a registered repository returns `scan_triggered`. The background worker then pulls the latest code, scans the changed project, regenerates affected documentation, and pushes updates to RAG.

As part of release validation, ask three repository-specific questions in the chat UI and confirm the answers cite or reflect the newly generated project documentation. Testing by someone other than the implementation author remains a manual sign-off item.

## Troubleshooting

### `401 Invalid signature`

The webhook Secret in GitHub does not match `MCP_GITHUB_WEBHOOK_SECRET`, the server was not restarted after changing the environment variable, or the payload was modified by an intermediary. Regenerate the secret if needed, update both sides, restart the service, and redeliver the GitHub event.

### `503 Webhook secret not configured`

`MCP_GITHUB_WEBHOOK_SECRET` is missing from the MCP server process environment. Add it to the deployment environment and restart the service.

### Ping Succeeds But Shows `registered: false`

The repository URL in the platform does not match GitHub's `clone_url`, or the repository has not been registered yet. Register the repo first, using the same HTTPS GitHub repository URL, then redeliver the ping.

### Push Event Is Ignored As Unregistered

Confirm the project exists in the platform and its `repo_url` matches the GitHub repository. URLs with and without a trailing `.git` are treated as equivalent, but SSH and HTTPS forms are different values.

### Clone Or Pull Failures

Check that the service user can run `git clone` and `git pull` for the repo from the host. For private repos, verify deploy key, PAT, or SSH agent setup. Also check outbound network access from the host to GitHub.

### No RAG Update After Push

Confirm the initial scan, generation, and RAG push completed before the webhook was created. Check project logs for scan or generation failures. For first pushes or unmapped file changes, the platform falls back to broader regeneration; for normal pushes, it stages unchanged analyses and performs partial RAG sync.
