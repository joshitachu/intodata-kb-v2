# IntoData KB Deployment Smoke Test

Run this after completing [RUNBOOK.md](./RUNBOOK.md). Record the target domain, commit SHA, tester, and pass/fail result.

## Variables

```bash
export CLIENT_SLUG=staging
export BASE_DOMAIN=nexus.intodata.nl
export FQDN="${CLIENT_SLUG}.${BASE_DOMAIN}"
export ENV_FILE="/etc/intodata-kb/envs/${CLIENT_SLUG}.env"
export REPO_ROOT="/opt/intodata-kb/${CLIENT_SLUG}"
cd "$REPO_ROOT"
```

## 1. Service Health

```bash
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" ps
curl -fsS "https://${FQDN}/health"
curl -fsS "https://${FQDN}/mcp/health"
curl -fsS "https://${FQDN}/api/v1/auth/bootstrap-status"
curl -fsS http://127.0.0.1:11434/api/tags
sudo systemctl is-active "mcp-server@${CLIENT_SLUG}.service"
```

Pass criteria:

- `postgres`, `qdrant`, `hindsight`, `backend`, `frontend`, and `dashboard` are running.
- Docker healthchecks are healthy within two minutes of startup.
- MCP API service is active and returns `status: ok`.
- Ollama returns model tags.

## 2. HTTPS, Redirect, And Headers

```bash
curl -I "https://${FQDN}/"
curl -I "http://${FQDN}/"
curl -I "https://${FQDN}/dashboard/"
```

Pass criteria:

- Browser certificate is valid.
- HTTP redirects to HTTPS.
- Responses include `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`, and `Referrer-Policy`.
- Browser console has no mixed-content errors.

## 3. Login And Logout

1. Open `https://<client>.<base-domain>`.
2. If bootstrap is shown, create the first admin user.
3. Log in as admin.
4. Refresh the page and confirm the session remains valid.
5. Log out.
6. Log in again.

Pass criteria:

- Login succeeds.
- Logout clears the session.
- Refresh after login keeps the user authenticated.

## 4. Document Ingest

1. Log in as admin.
2. Use the upload or onboarding source step to upload a small known-good PDF, Markdown file, or ZIP fixture.
3. Wait for ingestion to complete.
4. Inspect logs:

```bash
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" logs --tail=200 backend
sudo journalctl -u "mcp-server@${CLIENT_SLUG}.service" --no-pager -n 200
```

Pass criteria:

- Upload completes without browser error.
- No new backend or MCP 500 tracebacks appear.
- Uploaded content is visible to the platform.

## 5. Query And Streaming Answer

1. Ask a question that should match the uploaded document.
2. Confirm the answer streams incrementally.
3. Confirm relevant sources or references appear when available.

Pass criteria:

- Stream starts within 30 seconds.
- Stream completes without reconnect loops.
- No new 500s appear for query, conversation stream, `/mcp/*`, or `/logs/*`.

## 6. Dashboard

1. Open `https://<client>.<base-domain>/dashboard/`.
2. Confirm projects load.
3. Open a project.
4. Confirm project status, scan history, run history, docs, comparison, and settings views load.

Pass criteria:

- Dashboard is not blank.
- Project scan status is visible.
- `/mcp/projects`, `/mcp/status/*`, and generation-run calls do not return 500.

## 7. IP Allowlist

From an approved IP:

```bash
curl -I "https://${FQDN}/"
ssh "intodata@${FQDN}" true
```

From an unapproved IP:

```bash
curl --connect-timeout 10 -I "https://${FQDN}/"
```

Pass criteria:

- Approved IPs can reach SSH and HTTPS.
- Unapproved IPs cannot establish a connection.
- `sudo ufw status verbose` shows only approved CIDRs for ports `22`, `80`, and `443`.

## Sign-Off

```text
Date:
Tester:
Commit SHA:
Domain:
Docker services healthy: PASS/FAIL
MCP API healthy: PASS/FAIL
HTTPS/security headers: PASS/FAIL
Login/logout: PASS/FAIL
Document ingest: PASS/FAIL
Streaming answer: PASS/FAIL
Dashboard: PASS/FAIL
IP allowlist: PASS/FAIL
Notes:
```
