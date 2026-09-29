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
- Responses include `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, and `Content-Security-Policy`.
- With the CSP on, open the chat, a topology view and `/dashboard/`: the browser console shows no `Content-Security-Policy` violations and the dashboard fonts load.
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

## 7. Nexus Capability-Aware Chat Planner Smoke Test

Run this locally after the RAG backend, frontend, database, and MCP server are
up. This procedure verifies the capability-aware chat path without depending on
stale RAG documentation.

Local variables:

```bash
export FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:3000}"
export BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8000/api/v1}"
export SMOKE_USER="${SMOKE_USER:-admin}"
export SMOKE_PASSWORD="${SMOKE_PASSWORD:-admin}"
export COOKIE_JAR="/tmp/nexus_chat_planner_smoke.cookies"
```

Log in:

```bash
curl -fsS -c "$COOKIE_JAR" \
  -H "Content-Type: application/json" \
  -d "{\"identifier\":\"${SMOKE_USER}\",\"password\":\"${SMOKE_PASSWORD}\"}" \
  "${FRONTEND_URL}/api/auth/login" | jq .
```

If the response contains `mfa_required: true`, complete MFA through the browser
or post the returned `mfa_token` and current MFA code to
`${FRONTEND_URL}/api/auth/verify-mfa`, reusing the same cookie jar.

List accessible projects and choose one:

```bash
curl -fsS -b "$COOKIE_JAR" "${FRONTEND_URL}/api/source/projects" | jq .
export PROJECT_ID="<project id from the response>"
```

Create a conversation:

```bash
export CONVERSATION_ID="$(
  curl -fsS -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
    -H "Content-Type: application/json" \
    -d '{"title":"Capability planner smoke"}' \
    "${FRONTEND_URL}/api/conversations" | jq -r '.id'
)"
```

Ask a capability question:

```bash
curl -fsS -N -b "$COOKIE_JAR" \
  -H "Content-Type: application/json" \
  -d '{"query":"Can you generate documentation?"}' \
  "${FRONTEND_URL}/api/conversations/${CONVERSATION_ID}/messages/stream"
```

Pass criteria:

- The assistant says documentation generation is available when the capability
  is enabled.
- The answer mentions that a project is required.
- The answer does not say Nexus is read-only or cannot generate documentation.
- No workflow run is created for this capability question.

Ask an unscoped generation request:

```bash
curl -fsS -N -b "$COOKIE_JAR" \
  -H "Content-Type: application/json" \
  -d '{"query":"Generate documentation for me."}' \
  "${FRONTEND_URL}/api/conversations/${CONVERSATION_ID}/messages/stream"
```

Pass criteria:

- The assistant asks which project to use and lists accessible projects.
- The stream may show planning/scope activity, but no provider trigger occurs.
- No workflow run is created for the unscoped request.

Ask a scoped generation request:

```bash
curl -fsS -N -b "$COOKIE_JAR" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"Generate documentation for ${PROJECT_ID}.\",\"project_id\":\"${PROJECT_ID}\"}" \
  "${FRONTEND_URL}/api/conversations/${CONVERSATION_ID}/messages/stream"
```

Pass criteria:

- The stream includes generic activity for planning, scope resolution,
  capability selection, provider trigger, and provider result when those events
  are emitted.
- Activity labels are provider-agnostic and expanded details show only safe
  values such as project id, provider id, status, and workflow id.
- Existing runtime tool progress still renders for runtime questions.
- The final answer includes a workflow run id and relevant generation/docs
  links or a sanitized provider failure.

Inspect the workflow receipt:

```bash
curl -fsS -b "$COOKIE_JAR" \
  "${BACKEND_URL}/harness/workflows?conversation_id=${CONVERSATION_ID}&project_id=${PROJECT_ID}&limit=5" | jq .

export WORKFLOW_RUN_ID="<workflow run id from the response>"

curl -fsS -b "$COOKIE_JAR" \
  "${BACKEND_URL}/harness/workflows/${WORKFLOW_RUN_ID}" | jq .
```

Pass criteria:

- The scoped request creates exactly one relevant workflow run.
- The run has the documentation-generation capability id.
- Activities include scope/provider trigger/provider result, and any
  planning/capability-selected activities emitted by the backend.
- Activity `safe_details` do not contain prompts, raw provider payloads,
  credentials, tokens, or logs.

Browser check:

1. Repeat the same three prompts in the chat UI.
2. Expand the activity row during the scoped request.
3. Confirm details are readable and safe, then ask a runtime question and
   confirm runtime tool events still render separately.

## 8. IP Allowlist

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

## 9. Talend TMC Runtime (Optional, EP-004)

Run only when `RUNTIME_PROVIDER_KEY=talend_tmc` (see RUNBOOK section 13). Skip
otherwise.

After logging in (reuse the cookie jar from section 3):

```bash
# Runtime status should report connected when the health URL is reachable
curl -fsS -b cookies.txt "https://${FQDN}/api/v1/runtime/status"
```

Then, in the chat UI, ask a runtime question (e.g. "did the latest nightly_sync
run fail?") and watch for the runtime tool progress row in the streaming answer.

```bash
# Token must NEVER appear in backend logs or response payloads
docker compose logs backend | grep -i "$(sudo cat /opt/intodata/secrets/talend_tmc_api_token)" && echo "LEAK" || echo "OK: no token in logs"

# The Talend runtime service must not be publicly exposed
curl --connect-timeout 5 -I "http://${FQDN}:9000/" && echo "EXPOSED" || echo "OK: not public"
```

Pass criteria:

- `runtime/status` returns `"status":"connected"` (or `"unavailable"` if the
  health URL is intentionally not wired), never a 500.
- A runtime chat prompt shows tool progress and a final answer; failures show a
  "Runtime check unavailable" hint rather than crashing the stream.
- No token value appears in `docker compose logs backend`.
- The internal Talend runtime service is not reachable on a public host port.

## 10. Subscription Licence

Run with `LICENCE_ENFORCEMENT=enforce` (the production default written by
`deploy-client.sh`). Licences are issued on the operator machine (RUNBOOK 17a)
and installed as in RUNBOOK 17b; no restart is needed between steps. Reuse the
variables and cookie jar from section 7 (`FRONTEND_URL`, `BACKEND_URL`,
`COOKIE_JAR`, `CONVERSATION_ID`). Keep the currently installed licence aside
and put it back at the end.

```bash
export LICENCE_HOST_FILE="/etc/intodata-kb/licences/${CLIENT_SLUG}/licence.jwt"
sudo cp -p "$LICENCE_HOST_FILE" /root/licence.jwt.smoke-backup
licence_status() { curl -fsS -b "$COOKIE_JAR" "${BACKEND_URL}/licence/status" | jq .; }
```

### 10a. Valid licence: chat works

On the operator machine, issue a 1-day licence
(`issue --client "$CLIENT_SLUG" --plan basic --start <now> --end <now + 1 day>`),
install it, then:

```bash
licence_status
curl -fsS -N -b "$COOKIE_JAR" -H "Content-Type: application/json" \
  -d '{"query":"What does this project do?"}' \
  "${FRONTEND_URL}/api/conversations/${CONVERSATION_ID}/messages/stream" | head -c 400
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" logs backend mcp-server \
  | grep 'licence: enforcement=' | tail -n 2
```

Pass criteria:

- `licence/status` returns `state: "valid"`, `plan: "basic"`,
  `near_expiry: true`, `days_remaining` 0 or 1.
- The chat stream starts and completes.
- The UI shows the near-expiry banner.
- Each service logged `licence: enforcement=enforce state=...` once at start-up.

### 10b. Expired licence: view-only, still logged in

Issue an already-expired licence (`--start` and `--end` both in the past),
install it, and within one request:

```bash
licence_status
curl -sS -o /tmp/licence_402.json -w '%{http_code}\n' -b "$COOKIE_JAR" \
  -H "Content-Type: application/json" -d '{"query":"hello"}' \
  "${FRONTEND_URL}/api/conversations/${CONVERSATION_ID}/messages/stream"
jq . /tmp/licence_402.json
curl -fsS -o /dev/null -w '%{http_code}\n' -b "$COOKIE_JAR" "${FRONTEND_URL}/api/conversations"
```

Pass criteria:

- `licence/status` returns `state: "expired"`.
- The chat POST returns HTTP `402` with `detail.code == "licence_expired"`
  (no SSE stream opens, no half-broken turn).
- Listing conversations still returns `200`.
- In the browser: the user stays logged in (no redirect to `/login`), the
  banner shows the subscription as expired, the composer is disabled with a
  reason, and existing conversations and generated documents still open.
- Generate and scan buttons in the dashboard are disabled or refused with 402.

### 10c. Quota: the 4th generation is refused

Install a fresh valid `basic` licence (new period, so the count starts at 0).
Start three generations on a small test project from the dashboard or via
`POST /mcp/generate`, then a fourth. Read the used/allowed count from the
dashboard's generate action, or from `GET /mcp/licence/status` authenticated
the same way as any other `/mcp` route.

Pass criteria:

- Generations 1-3 start; the used/allowed count reads 1/3, 2/3, 3/3.
- The 4th returns HTTP `402` with `detail.code == "quota_exhausted"`.
- A generation that FAILED does not consume a slot (optional: cancel or break
  one run and confirm the count does not move).
- Chat keeps working while the generation quota is exhausted.

Restore the original licence afterwards:

```bash
sudo install -o root -g 1001 -m 0640 /root/licence.jwt.smoke-backup "$LICENCE_HOST_FILE"
licence_status   # back to the client's real period
```

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
Capability-aware chat planner: PASS/FAIL
IP allowlist: PASS/FAIL
Talend TMC runtime (if enabled): PASS/FAIL/N-A
Licence valid / expired / quota: PASS/FAIL
Notes:
```
