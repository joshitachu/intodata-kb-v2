# IntoData KB Deployment Smoke Test

Run this after completing [RUNBOOK.md](./RUNBOOK.md). Record the target domain, commit SHA, tester, and pass/fail result.

## Variables

```bash
export CLIENT_SLUG=staging
export BASE_DOMAIN=nexus.intodata.nl
export FQDN="${CLIENT_SLUG}.${BASE_DOMAIN}"
export ENV_FILE="/etc/intodata-kb/envs/${CLIENT_SLUG}.env"
export REPO_ROOT="/opt/intodata-kb/${CLIENT_SLUG}"
set -a
. "$ENV_FILE"
set +a
cd "$REPO_ROOT"
```

## 1. Service Health

```bash
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" ps
curl -fsS "https://${FQDN}/health"
curl -fsS "https://${FQDN}/api/v1/auth/bootstrap-status"
curl -fsS "http://127.0.0.1:${MCP_PORT:-8002}/health"
curl -fsS http://127.0.0.1:11434/api/tags
sudo docker run --rm --add-host=host.docker.internal:host-gateway curlimages/curl:8.8.0 -fsS http://host.docker.internal:11434/api/tags
```

Pass criteria:

- `postgres`, `qdrant`, `hindsight`, `backend`, `frontend`, `mcp-server`, and `dashboard` are running.
- Docker healthchecks are healthy within two minutes of startup.
- MCP API container is healthy locally and returns `status: ok`.
- Ollama returns model tags from both the host and a temporary container.

## 2. HTTPS, Redirect, And Headers

```bash
curl -I "https://${FQDN}/"
curl -I "http://${FQDN}/"
curl -I "https://${FQDN}/dashboard/"
curl -I "https://${FQDN}/mcp/health"
curl -I "https://${FQDN}/logs/stream"
```

Pass criteria:

- Browser certificate is valid.
- HTTP redirects to HTTPS.
- Responses include `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`, and `Referrer-Policy`.
- `/dashboard/`, `/mcp/*`, and `/logs/*` return 404 on the public customer domain.
- Browser console has no mixed-content errors.

## 3. Bootstrap Admin And Login

First, confirm bootstrap is available and create the superadmin:

```bash
# Should return {"bootstrapped": false} on a fresh deploy
curl -fsS "https://${FQDN}/api/v1/auth/bootstrap-status"

# Create the first superadmin (only works once; subsequent calls return 400)
curl -fsS -X POST "https://${FQDN}/api/v1/auth/bootstrap-admin" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<your-admin-password>","email":"ops@intodata.nl"}' | jq .

# Confirm bootstrapped now
curl -fsS "https://${FQDN}/api/v1/auth/bootstrap-status"
```

Pass criteria for bootstrap:

- First `/bootstrap-status` returns `{"bootstrapped": false}`.
- `POST /bootstrap-admin` returns HTTP 201 with `{"id": ..., "username": "admin"}`.
- Second `/bootstrap-status` returns `{"bootstrapped": true}`.
- A second `POST /bootstrap-admin` returns HTTP 400.

Then verify login flow:

1. Open `https://<client>.<base-domain>`.
2. Log in as the admin user created above.
3. Refresh the page and confirm the session remains valid.
4. Log out.
5. Log in again.

Pass criteria for login:

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
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" logs --tail=200 mcp-server
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
- No new 500s appear for query, conversation stream, `/api/source/*`, or MCP logs on the host.

## 6. Developer Surface Lockdown

From a normal customer network, run:

```bash
curl -o /dev/null -sS -w "%{http_code}\n" "https://${FQDN}/dashboard/"
curl -o /dev/null -sS -w "%{http_code}\n" "https://${FQDN}/mcp/projects"
curl -o /dev/null -sS -w "%{http_code}\n" "https://${FQDN}/logs/stream"
```

Pass criteria:

- All three public requests return `404`.
- The customer-facing app still completes onboarding source upload through `/api/source/*`.

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
Bootstrap admin: PASS/FAIL
Login/logout: PASS/FAIL
Document ingest: PASS/FAIL
Streaming answer: PASS/FAIL
Developer surface locked down: PASS/FAIL
IP allowlist: PASS/FAIL
Notes:
```
