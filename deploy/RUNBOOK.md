# IntoData KB Deployment Foundation Runbook

This runbook is the canonical EP-001 deployment path for a clean Ubuntu VPS or staging host. It uses the current deployment architecture:

- Docker Compose for the seven application services in the health boundary: `postgres`, `qdrant`, `hindsight`, `backend`, `frontend`, `mcp-server`, `dashboard`
- Plus `mission-control`, an eighth service that `docker compose up -d` also starts. It is the operator-side heartbeat aggregator, binds `127.0.0.1` only, and is deliberately outside the health boundary — it is not a per-client application service.
- Host-level Ollama exposed to containers through `host.docker.internal:11434`
- nginx + certbot for HTTPS
- UFW and app-level allowlists for approved CIDRs

nginx/certbot is the only supported HTTPS path.

Optional subsystems are configured in the later sections and are all off by
default: live runtime sources (13), GitHub App repo connection (14), external
doc-generation providers (15), and the agent runtime v2 rollout flags (16).

The subscription licence (17) is NOT optional: a production instance without a
valid licence is view-only.

Registering a client in the central Mission Control and giving it its own
heartbeat key (18) happens on the operator host, plus one redeploy of the
client with three env lines.

## 1. Set Deployment Variables

Replace these values for the environment being deployed.

```bash
export CLIENT_SLUG=staging
export BASE_DOMAIN=nexus.intodata.nl
export FQDN="${CLIENT_SLUG}.${BASE_DOMAIN}"
export ADMIN_EMAIL=ops@intodata.nl
export REPO_URL=https://github.com/intodata/intodata-kb-v2.git
export REPO_ROOT="/opt/intodata-kb/${CLIENT_SLUG}"
export ENV_FILE="/etc/intodata-kb/envs/${CLIENT_SLUG}.env"
export APPROVED_CIDRS="<your-current-ssh-ip>/32,<office-vpn-cidr>"
```

`APPROVED_CIDRS` is intentionally a placeholder. Do not run the UFW commands
until it includes the public IP or CIDR you are currently SSHing from. The
recommended repo path is `/opt/intodata-kb/<client>`; other paths are supported
only when the `intodata` service user can traverse every parent directory.

## 2. Prepare Ubuntu

```bash
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl git gnupg lsb-release openssl ufw fail2ban nginx certbot python3-certbot-nginx gettext-base python3 python3-venv python3-pip
```

Install Docker and Compose:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker version
sudo docker compose version
```

Create the service user and install root:

```bash
sudo useradd --system --create-home --shell /bin/bash intodata || true
sudo usermod -aG docker intodata
sudo mkdir -p "$REPO_ROOT"
sudo chown "$USER:$USER" "$REPO_ROOT"
```

## 3. Clone The Company Repo

```bash
sudo rm -rf "$REPO_ROOT"
sudo mkdir -p "$(dirname "$REPO_ROOT")"
sudo chown "$USER:$USER" "$(dirname "$REPO_ROOT")"
git clone "$REPO_URL" "$REPO_ROOT"
cd "$REPO_ROOT"
git remote -v
```

The remote must point at the company GitHub organisation. If you use an SSH
remote such as `git@github.com:intodata/intodata-kb-v2.git`, clone as the user
that has the GitHub SSH key configured. Avoid `sudo git clone` unless root has
its own GitHub credentials.

The MCP API and `intodoc` scanner are installed into the `mcp-server` image
during the Docker build. No host Python virtualenv is required for the deployed
stack.

Ensure the MCP output directory is owned by the `intodata` service user. If the repo
was cloned as a different user, the MCP service will throw a PermissionError when
writing generated docs:

```bash
sudo mkdir -p "$REPO_ROOT/knowledge_base/output"
sudo chown intodata:intodata "$REPO_ROOT/knowledge_base/output"
```

## 4. Install Ollama On The Host

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama
ollama pull nomic-embed-text
ollama pull gemma3:4b
```

The `OLLAMA_HOST` override is required because containers reach the host through
Docker's bridge gateway, not through the host's `127.0.0.1`.

Confirm both models are present before continuing — the stack will fail silently if either is missing:

```bash
ollama list | grep -E "nomic-embed-text|gemma3"
```

Expected output shows at least `nomic-embed-text:latest` and `gemma3:4b` (or the variant configured in the env file). If a model is absent, re-run the `ollama pull` command and wait for the download to complete before proceeding.

Verify container access to Ollama:

```bash
sudo docker run --rm --add-host=host.docker.internal:host-gateway curlimages/curl:8.8.0 -fsS http://host.docker.internal:11434/api/tags
curl -fsS http://127.0.0.1:11434/api/tags
```

## 5. Configure DNS

Create a DNS `A` record:

```text
Name: <client>.<base-domain>
Value: <server-public-ip>
TTL: 300
```

Verify:

```bash
dig +short "$FQDN"
curl -4 ifconfig.me
```

The DNS result must match the VPS public IP before running certbot.

If DNS has not propagated yet, continue the deployment without certbot and run
the certbot command later, after `dig +short "$FQDN"` returns the VPS IP. The
deploy script currently prints the certbot command for you; `--no-certbot`
suppresses that printed instruction when DNS is not ready.

## 6. Deploy The Docker Stack

```bash
cd "$REPO_ROOT"
sudo deploy/deploy-client.sh \
  --client-name "$CLIENT_SLUG" \
  --port-offset 0 \
  --base-domain "$BASE_DOMAIN" \
  --admin-email "$ADMIN_EMAIL" \
  --repo-root "$REPO_ROOT"
```

The script generates `/etc/intodata-kb/envs/<client>.env`, renders nginx config from `deploy/nginx/nexus-client.conf.template`, and starts the Docker Compose stack.

Secrets generated on first run (never commit or reuse these across clients):
`JWT_SECRET_KEY` / `MCP_JWT_SECRET_KEY` (must match), `POSTGRES_PASSWORD`,
`ADMIN_DEFAULT_PASSWORD`, `MCP_INGEST_API_KEY`, `MCP_TOKEN_ENCRYPTION_KEY`,
`SOURCE_CREDENTIALS_ENCRYPTION_KEY`, `MC_API_KEY`.

`SOURCE_CREDENTIALS_ENCRYPTION_KEY` is mandatory. Compose declares it fail-fast,
so `up -d` aborts with `Set SOURCE_CREDENTIALS_ENCRYPTION_KEY (Fernet key) in
.env` if it is absent. It is required even when no runtime source is configured,
because runtime source credentials are stored encrypted at rest. Re-running
`deploy-client.sh` against an env file written by an older revision backfills
this key (and `MC_PORT` / `MC_API_KEY`) without touching existing secrets.

The script also writes (or backfills) the subscription licence settings
`LICENCE_ENFORCEMENT=enforce`, `LICENCE_CLIENT_SLUG=<client>`, `LICENCE_DIR`
and `LICENCE_FILE`, and creates `/etc/intodata-kb/licences/<client>/`
(`root:1001`, `0750`). Until a licence is installed there it prints
`NOTICE: no licence installed ...` and the instance is view-only. Install one
with section 17 before handing the instance over.

If DNS is not ready, add `--no-certbot` to suppress the certbot instruction and
return to Step 9 after DNS resolves.

Set app-level allowlists:

```bash
sudo sed -i "s|^ALLOWED_IPS=.*|ALLOWED_IPS=${APPROVED_CIDRS}|" "$ENV_FILE"
sudo sed -i "s|^MCP_ALLOWED_IPS=.*|MCP_ALLOWED_IPS=${APPROVED_CIDRS}|" "$ENV_FILE"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d
```

If the env file existed before this runbook was updated, confirm these values exist and add them if missing:

```bash
sudo grep -E '^(REPO_ROOT|PUBLIC_APP_URL|MCP_PUBLIC_URL|MCP_DATABASE_URL|MCP_RAG_BASE_URL|MCP_PROJECT_ROOT|MCP_INTODOC_COMMAND|MCP_PORT|SOURCE_CREDENTIALS_ENCRYPTION_KEY|MC_PORT|MC_API_KEY|TRUSTED_PROXIES|SMTP_HOST|SMTP_PORT|LICENCE_ENFORCEMENT|LICENCE_CLIENT_SLUG|LICENCE_DIR|LICENCE_FILE)=' "$ENV_FILE"
```

`TRUSTED_PROXIES` must include the Compose bridge range
(`127.0.0.1,172.16.0.0/12`) and must appear exactly once — nginx and the
frontend container both sit in front of FastAPI, and a later duplicate line wins
in an env file, which silently collapses every per-IP rate limit into a single
bucket keyed on the frontend container:

```bash
sudo grep -c '^TRUSTED_PROXIES=' "$ENV_FILE"   # must print 1
```

After the stack is up, verify the `wizard_events` table has the `updated_at` column.
A missing column causes 500 errors in the onboarding wizard. Alembic should cover this,
but run as a safety check:

```bash
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" \
  exec postgres psql -U admin "intodata_kb_${CLIENT_SLUG}" \
  -c "ALTER TABLE wizard_events ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW();"
```

## 7. Configure Email (SMTP)

The platform sends invite emails and login OTP codes via SMTP. Set these variables in the env file before starting services if email is required at launch:

```bash
sudo sed -i "s|^#\?SMTP_HOST=.*|SMTP_HOST=smtp.yourprovider.com|" "$ENV_FILE"
sudo sed -i "s|^#\?SMTP_PORT=.*|SMTP_PORT=587|" "$ENV_FILE"
sudo sed -i "s|^#\?SMTP_USERNAME=.*|SMTP_USERNAME=your@sender.com|" "$ENV_FILE"
sudo sed -i "s|^#\?SMTP_PASSWORD=.*|SMTP_PASSWORD=yourpassword|" "$ENV_FILE"
sudo sed -i "s|^#\?SMTP_FROM_ADDRESS=.*|SMTP_FROM_ADDRESS=noreply@yourdomain.com|" "$ENV_FILE"
sudo sed -i "s|^#\?SMTP_FROM_NAME=.*|SMTP_FROM_NAME=IntoData Nexus|" "$ENV_FILE"
```

Any SMTP provider (Resend, SendGrid, Mailgun, AWS SES, Office365 relay) works. Use port 587 with STARTTLS. The sender domain must have SPF and DKIM records published before emails will pass spam filters reliably.

Send a test email after services are up:

```bash
curl -fsS -X POST "https://${FQDN}/api/v1/admin/email/test" \
  -H "Content-Type: application/json" \
  -d '{"to":"ops@intodata.nl"}' \
  --cookie-jar /tmp/cookies.txt --cookie /tmp/cookies.txt
```

If SMTP is not configured in development or staging, invite URLs and OTP codes are logged to the backend journal instead of sent.

## 8. Verify MCP API Container

```bash
cd "$REPO_ROOT"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" ps mcp-server
MCP_PORT="$(grep '^MCP_PORT=' "$ENV_FILE" | cut -d= -f2)"
curl -fsS "http://127.0.0.1:${MCP_PORT:-8002}/health"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" logs --tail=100 mcp-server
```

If using an external LLM provider for doc generation (see section 15), the default
token limits are too low for large projects. Set these in the env file after MCP is running:

```bash
grep -q '^MCP_PHASE2_MAX_TOKENS' "$ENV_FILE" || echo "MCP_PHASE2_MAX_TOKENS=32000" | sudo tee -a "$ENV_FILE"
grep -q '^MCP_PHASE3_MAX_TOKENS' "$ENV_FILE" || echo "MCP_PHASE3_MAX_TOKENS=64000" | sudo tee -a "$ENV_FILE"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d mcp-server
```

MCP runs as a Compose service, so env changes need `up -d` (which recreates the
container with the new environment), not `restart` and not `systemctl`. The
old host-virtualenv systemd units have been removed; `deploy-client.sh` never
installed them, and step 3 of this runbook states no host virtualenv is required.

Doc-generation packs are read from the host directory mounted at
`/app/workspace/packs`, i.e. `$REPO_ROOT/knowledge_base/packs`. The script
creates it. `plugin.yaml` is the only creatable pack format; the older governed
pack stack was retired (migration `090_retire_governed_pack_stack`).

Optional: enable central IntoData Ops telemetry after the central Ops API has a
client record and API key. This reports metadata only and does not send uploaded
documents, source code, generated document bodies, chat history, raw logs, or
secrets.

```bash
sudo sed -i "s|^MCP_OPS_CLIENT_SLUG=.*|MCP_OPS_CLIENT_SLUG=${CLIENT_SLUG}|" "$ENV_FILE"
sudo sed -i "s|^MCP_OPS_ENVIRONMENT=.*|MCP_OPS_ENVIRONMENT=production|" "$ENV_FILE"
sudo sed -i "s|^MCP_OPS_RELEASE_VERSION=.*|MCP_OPS_RELEASE_VERSION=$(git rev-parse --short HEAD)|" "$ENV_FILE"
sudo sed -i "s|^MCP_OPS_TELEMETRY_URL=.*|MCP_OPS_TELEMETRY_URL=https://ops.nexus.intodata.nl|" "$ENV_FILE"
sudo sed -i "s|^MCP_OPS_TELEMETRY_API_KEY=.*|MCP_OPS_TELEMETRY_API_KEY=<client-telemetry-key>|" "$ENV_FILE"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d mcp-server
```

## 9. Enable HTTPS With nginx/certbot

Run this step only after DNS resolves to the VPS. If Step 6 was run before DNS
was ready, this is where you run certbot. If certbot has already succeeded for
`$FQDN`, skip the certbot command and only run the verification commands.

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d "$FQDN" --email "$ADMIN_EMAIL" --agree-tos -n
sudo systemctl status certbot.timer --no-pager
```

Verify HTTPS, redirect, and security headers:

```bash
curl -I "https://${FQDN}/"
curl -I "http://${FQDN}/"
curl -I "https://${FQDN}/dashboard/"
curl -I "https://${FQDN}/mcp/health"
curl -I "https://${FQDN}/logs/stream"
```

Expected public responses:

- `/` and `/api/v1/*` are reachable through the customer domain.
- `/dashboard/`, `/mcp/*`, and `/logs/*` return 404 on the customer domain. This is intentional public-domain lockdown, not a deployment failure.
- `/webhook/*` is public and proxies to the MCP service for HMAC-verified GitHub webhooks.
- The Next.js frontend proxies required onboarding source actions through `/api/source/*`.

If private operator access to MCP is needed for debugging, use SSH port
forwarding to the VPS-local MCP port instead of exposing `/mcp/*` publicly:

```bash
ssh -L 8002:127.0.0.1:8002 "intodata@${FQDN}"
curl -fsS http://127.0.0.1:8002/health
```

## 10. Restrict Inbound Access

Do not close the current SSH session until a second approved connection is verified.
Before enabling UFW, confirm `APPROVED_CIDRS` is not a placeholder and includes
your current SSH source IP or VPN CIDR.

```bash
echo "APPROVED_CIDRS=${APPROVED_CIDRS}"
echo "Current SSH session:"
who am i
ss -tnp | grep ':22' || true
```

If the current SSH source IP is not covered by `APPROVED_CIDRS`, stop here and
fix Step 1 first.

```bash
sudo ufw --force reset
sudo ufw default deny incoming
sudo ufw default allow outgoing

IFS=',' read -ra CIDRS <<< "$APPROVED_CIDRS"
for cidr in "${CIDRS[@]}"; do
  sudo ufw allow from "$cidr" to any port 22 proto tcp
  sudo ufw allow from "$cidr" to any port 80 proto tcp
  sudo ufw allow from "$cidr" to any port 443 proto tcp
done

sudo ufw --force enable
sudo ufw status verbose
```

## 11. Verify Health

If this is a new shell, re-export the Step 1 variables first, then load the env
file for generated values like `MCP_PORT`:

```bash
export CLIENT_SLUG=staging
export BASE_DOMAIN=nexus.intodata.nl
export FQDN="${CLIENT_SLUG}.${BASE_DOMAIN}"
export REPO_ROOT="/opt/intodata-kb/${CLIENT_SLUG}"
export ENV_FILE="/etc/intodata-kb/envs/${CLIENT_SLUG}.env"
set -a
. "$ENV_FILE"
set +a
cd "$REPO_ROOT"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" ps
curl -fsS "https://${FQDN}/health"
curl -fsS "https://${FQDN}/api/v1/auth/bootstrap-status"
MCP_PORT="$(grep '^MCP_PORT=' "$ENV_FILE" | cut -d= -f2)"
curl -fsS "http://127.0.0.1:${MCP_PORT:-8002}/health"
curl -fsS http://127.0.0.1:11434/api/tags
MC_PORT="$(grep '^MC_PORT=' "$ENV_FILE" | cut -d= -f2)"
curl -fsS "http://127.0.0.1:${MC_PORT:-9100}/health"
```

Expected:

- `docker compose ps` lists **eight** containers: the seven health-boundary
  services plus `mission-control`. All seven boundary services must be healthy.
- `mission-control` healthy is expected but not an acceptance gate — it is the
  operator-side aggregator, reachable on `127.0.0.1:${MC_PORT}` only, and never
  proxied by nginx. If it is not used for this client it may be left stopped
  (`docker compose stop mission-control`); that does not affect the boundary.
- MCP API returns `{"status":"ok","service":"intodoc-mcp-server"}` on the VPS-local port.
- Backend health is `healthy`, or `degraded` only if a non-core dependency is intentionally unavailable.
- Ollama returns model tags.

## 12. Run Smoke Test

Open:

```text
https://<client>.<base-domain>
```

Create the first admin user if the bootstrap screen appears, then run [SMOKE-TEST.md](./SMOKE-TEST.md).

## 13. Enable Live Runtime (Optional, EP-004/EP-005)

Skip this section unless the client requires live execution data (currently
Talend TMC) in chat. The default stack runs with runtime disabled.

Runtime has two independent halves. Configure both, in this order.

### 13a. Register the runtime source (self-service, per project)

This is the current path. Credentials are entered by an admin in the app, stored
encrypted at rest, and never placed in `.env` or the Compose environment.

- UI: **Settings → Sources** to register a connector and bind it to projects;
  **Admin → Runtime** for sync state and captured data.
- API: `POST /api/v1/runtime/sources` (create), `POST .../discover` (probe the
  credentials and list visible TMC environments before saving),
  `POST .../{id}/scopes`, `POST .../{id}/sync` (sync now),
  `GET .../{id}/sync` (status), `POST .../{id}/credentials/rotate`.
- For `talend_tmc` the required fields are `api_token` (secret) and `base_url`;
  optional task/artifact filters, run-history days (1–90), and an environment
  scope selection.

Constraints and behaviour worth knowing before onboarding a client:

- Encryption uses `SOURCE_CREDENTIALS_ENCRYPTION_KEY` (section 6). Without it the
  backend cannot start, so this is already in place.
- **One TMC source per project** (migration `091_one_tmc_source_per_project`).
  To let several projects read one runtime mirror, use read grants
  (`095_runtime_source_read_grants`) rather than registering the source again.
- The scan project a source binds to must be the project whose runtime data it
  is. Binding to the wrong `project_id` produces a silently wrong join between
  runtime and scan data — verify the binding after onboarding.
- Automatic polling is on by default (`RUNTIME_SYNC_SCHEDULER_ENABLED=true`,
  `RUNTIME_SYNC_INTERVAL_SECONDS=60`). The scheduler is source-driven: it polls
  every *enabled* registered source on that source's own interval with that
  source's stored credentials, so the automatic path matches the manual
  "Sync now" action and honours the per-source toggle. With no sources
  registered it does nothing.

Per-sync capture ceilings (defaults are deliberate — each is a bounded number of
TMC round trips inside the backend's 1 GiB container). Raise only with a reason,
and expect longer syncs:

| Variable | Default | Bounds |
|----------|---------|--------|
| `TALEND_TMC_FAILED_LOG_RUN_LIMIT` | 10 | failed runs whose logs are downloaded per sync |
| `TALEND_TMC_RUNS_PER_EXECUTABLE_LIMIT` | 250 | history runs ingested per executable per sync; decides how far back a task's history reaches |
| `TALEND_TMC_COMPONENT_METRIC_RUN_LIMIT` | 25 | runs with component metrics downloaded per sync |

Uncaptured failures are reported as capture-limited in the dashboard rather than
presented as diagnosed.

Optional: `RUNTIME_SEMANTIC_ENABLED=true` adds semantic recall over captured
runtime logs (indexes into Qdrant, off by default).

TMC is a **runtime source**, not an agent connector. Its scheduled ingestion
fills the same stored runtime repository used by **Admin → Runtime** and Labs'
stored-runtime evidence tools. Keep it here so a dashboard incident and a chat
answer refer to the same captured runs, logs, freshness and project grants.
Do not register a second direct-to-TMC search connector for Labs.

### 13b. Expose runtime to chat

Registering a source fills the mirror; it does not expose runtime tools to the
LLM. That is gated separately by `RUNTIME_PROVIDER_KEY` — empty means the tool
loop returns `Live runtime disabled`.

| `RUNTIME_PROVIDER_KEY` | Reads from | Use for |
|------------------------|-----------|---------|
| *(empty)* | — | runtime disabled (default) |
| `stored` | the DB mirror filled by 13a | **production with registered sources** |
| `mock` | fixtures | local validation |
| `talend_tmc` | live TMC on every tool call, using the env vars below | direct/legacy adapter path |

For the registered-source path:

```bash
sudo sed -i "s|^RUNTIME_PROVIDER_KEY=.*|RUNTIME_PROVIDER_KEY=stored|" "$ENV_FILE"
sudo sed -i "s|^RUNTIME_PROVIDER_LABEL=.*|RUNTIME_PROVIDER_LABEL=Talend TMC|" "$ENV_FILE"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d backend
```

Reads are project-scoped from the authenticated request's RBAC allow-list, never
from LLM-supplied tool arguments.

### Agent evidence connectors (separate from runtime ingestion)

**Admin → Connectors** registers query-time, read-only evidence sources. The
installed types are fixture data, Elasticsearch-compatible HTTPS search, an
approved PostgreSQL view and one reviewed OpenAPI 3 GET operation. Owners bind
each capability to explicit projects, run a health check, and can preview the
bounded evidence returned to Labs. An empty project binding grants nothing.
These connectors do not ingest into or replace the runtime dashboard.

For authenticated agent connectors, provision a backend-only file at
`deploy/connectors.env` (git-ignored), or set `NEXUS_CONNECTOR_ENV_FILE` to an
absolute path. Compose loads the file only into the backend container. Each
variable must start with `NEXUS_CONNECTOR_` and contain one JSON object with a
host-bound secret, for example:

```text
NEXUS_CONNECTOR_SEARCH_READER={"host":"search.example.com","secret":"<provisioned-key>"}
NEXUS_CONNECTOR_REPORTING_READER={"host":"db.example.com","port":5432,"secret":"<provisioned-password>"}
```

Select `env:NEXUS_CONNECTOR_SEARCH_READER` (or the appropriate variable) in the
connector UI; never paste the secret itself there. Provision a dedicated,
least-privilege credential, and for PostgreSQL grant SELECT on only the approved
view. The view must expose `project_id`, `searchable_text`, `title`, `excerpt`
and `source_ref`. Elasticsearch needs a project keyword field (default
`project_id.keyword`); the reviewed API must require project and limit query
parameters and return each item's project ID. Registration without a real
credential and reachable endpoint is not a successful live integration.

**`RUNTIME_PROVIDER_KEY` does not gate the v2 harness.** It gates
`execute_runtime_tool` (`src/runtime/client.py`), used by the legacy v1 agent
loop, and it is what `GET /api/v1/runtime/status` and the UI status badge report.
The v2 harness reads runtime evidence by instantiating `StoredRuntimeProvider`
directly (`src/harness/evidence.py`) and never consults the key. So on a stack
running v2 chat traffic (section 16), runtime answers can work correctly while
the badge reads "Live runtime disabled". Set the key anyway for a client using
runtime — it keeps status reporting honest — but do not diagnose runtime
availability from that badge.

Tool-loop bounds apply to every provider and rarely need changing:
`RUNTIME_TOOL_LOOP_MAX_ITERATIONS` (4 LLM↔tool round trips per turn),
`RUNTIME_TOOL_MAX_PARALLEL_CALLS` (5 calls per assistant turn; calls beyond the
cap get a synthetic budget-exceeded result rather than being dropped),
`RUNTIME_TOOL_TIMEOUT` (10s), `RUNTIME_TOOL_RESULT_MAX_CHARS` (12000).

`RUNTIME_LLM_FALLBACK_ENABLED=true` (default false) lets an incident whose
deterministic confidence is *low* escalate to a single bounded, redacted LLM
call. High/medium incidents never escalate. Leave it off unless the client has
accepted that runtime incident text is sent to the configured LLM.

### 13c. Legacy direct adapter (`talend_tmc`) — env + mounted token

Only for the direct-call path. The Talend API token must never be placed in
`.env`, the Compose `environment` block, logs, or the repo; it is read from a
mounted file.

1. Create the token file on the host with restrictive permissions:

   ```bash
   sudo install -d -m 700 /opt/intodata/secrets
   sudo install -m 600 /dev/null /opt/intodata/secrets/talend_tmc_api_token
   sudo tee /opt/intodata/secrets/talend_tmc_api_token >/dev/null   # paste token, then Ctrl-D
   ```

2. Bind-mount the secret into the backend container. Add to the `backend`
   service in `docker-compose.yml` (or a `docker-compose.override.yml`):

   ```yaml
   backend:
     volumes:
       - /opt/intodata/secrets/talend_tmc_api_token:/run/secrets/talend_tmc_api_token:ro
   ```

3. Set the runtime variables in the env file:

   ```bash
   RUNTIME_PROVIDER_KEY=talend_tmc
   RUNTIME_PROVIDER_LABEL=Talend TMC
   RUNTIME_PROVIDER_HEALTH_URL=https://<tmc-region-host>/health
   TALEND_TMC_BASE_URL=https://<tmc-region-host>
   TALEND_TMC_API_TOKEN_FILE=/run/secrets/talend_tmc_api_token
   ```

   `RUNTIME_PROVIDER_HEALTH_URL` is optional; without it status reports
   `unavailable` even though tools work. If TMC is reached through an internal
   service on the Compose network, use that service name and do not publish it
   on a public host port.

4. Recreate the backend (env change needs `up -d`, not `restart`):

   ```bash
   sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d backend
   ```

5. Confirm status and that the token never leaks (see SMOKE-TEST section 8):

   ```bash
   curl -fsS http://127.0.0.1:${BACKEND_PORT:-8000}/api/v1/runtime/status   # needs auth cookie
   docker compose logs backend | grep -i "$(sudo cat /opt/intodata/secrets/talend_tmc_api_token)" && echo "LEAK" || echo "no token in logs"
   ```

To disable runtime again, set `RUNTIME_PROVIDER_KEY=` (empty) and recreate the
backend. Registered sources keep syncing; only the chat surface goes away. To
stop syncing too, disable the sources in the UI or set
`RUNTIME_SYNC_SCHEDULER_ENABLED=false`.

### 13d. Rotating the credential encryption key

Rotation is lazy: both keys are accepted during the window, and rows re-encrypt
as each source is saved.

```bash
# 1. old primary becomes the previous key, new primary is generated
OLD="$(grep '^SOURCE_CREDENTIALS_ENCRYPTION_KEY=' "$ENV_FILE" | cut -d= -f2-)"
NEW="$(openssl rand -base64 32 | tr '+/' '-_')"
sudo sed -i "s|^SOURCE_CREDENTIALS_ENCRYPTION_KEY=.*|SOURCE_CREDENTIALS_ENCRYPTION_KEY=${NEW}|" "$ENV_FILE"
grep -q '^SOURCE_CREDENTIALS_ENCRYPTION_KEY_PREVIOUS=' "$ENV_FILE" \
  && sudo sed -i "s|^SOURCE_CREDENTIALS_ENCRYPTION_KEY_PREVIOUS=.*|SOURCE_CREDENTIALS_ENCRYPTION_KEY_PREVIOUS=${OLD}|" "$ENV_FILE" \
  || echo "SOURCE_CREDENTIALS_ENCRYPTION_KEY_PREVIOUS=${OLD}" | sudo tee -a "$ENV_FILE"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d backend

# 2. re-encrypt: POST /api/v1/runtime/sources/{id}/credentials/rotate per source,
#    or re-save each source from Settings -> Sources
# 3. once every source is re-encrypted, clear the previous key and recreate again
```

`SOURCE_CREDENTIALS_ENCRYPTION_KEY_ID` / `_PREVIOUS_ID` (defaults `primary` /
`previous`) only label which key encrypted a row; leave them alone unless you
are tracking key generations externally.

## 14. Enable GitHub App Repo Connection (Optional)

Skip unless the client connects repositories via the GitHub App instead of
per-project Personal Access Tokens. When enabled, a repo connects by installing
the App on the owner's org; the backend stores only a non-secret installation id
and mints short-lived (~1h) installation tokens on demand. PAT connection remains
a fallback and is unaffected.

Requires a public HTTPS domain (section 9). The browser connect flow needs a
reachable callback URL — it does NOT work against `localhost`/`127.0.0.1`
(GitHub rejects them). The domain from section 1/5 is what makes it work.

1. Register the GitHub App at `https://github.com/settings/apps` → **New GitHub App**:
   - **Callback URL**: `https://${CLIENT}.${NEXUS_BASE}/api/source/github/callback`
     (the frontend route — nginx returns 404 for `/mcp/` on customer domains, so the
     callback must go through the Next.js app, not the MCP path).
   - Check **"Request user authorization (OAuth) during installation"** (required —
     it makes GitHub return the `code` the ownership check uses).
   - Leave **Setup URL** blank.
   - **Webhook** → Active; URL `https://${CLIENT}.${NEXUS_BASE}/webhook/github/webhook`
     (nginx proxies `/webhook/` → MCP); Secret = the `MCP_GITHUB_WEBHOOK_SECRET` value.
   - **Repository permissions**: Contents = Read-only, Metadata = Read-only.
   - **Subscribe to events**: Push, Pull request (these appear only after Contents is set).
   - **Where can this GitHub App be installed?**: Any account (multi-tenant — customers
     install on their own orgs).
   - After creating: note **App ID**, **Client ID**; generate a **Client secret** and a
     **private key** (`.pem`).

2. Store the private key as a mounted file (never in `.env`, the Compose block, or the repo):

   ```bash
   sudo install -d -m 700 /opt/intodata/secrets
   sudo install -m 600 /dev/null /opt/intodata/secrets/github-app-private-key.pem
   sudo tee /opt/intodata/secrets/github-app-private-key.pem >/dev/null   # paste .pem contents, then Ctrl-D
   ```

   Bind-mount it into the `mcp-server` service in `docker-compose.yml` (or an override):

   ```yaml
   mcp-server:
     volumes:
       - /opt/intodata/secrets/github-app-private-key.pem:/run/secrets/github-app-private-key.pem:ro
   ```

3. Set the App variables in `.env` (the private key is referenced by path, not inline):

   ```bash
   MCP_GITHUB_APP_ID=<App ID>
   MCP_GITHUB_APP_SLUG=<app URL slug>
   MCP_GITHUB_APP_CLIENT_ID=<Client ID>
   MCP_GITHUB_APP_CLIENT_SECRET=<Client secret>
   MCP_GITHUB_APP_PRIVATE_KEY_FILE=/run/secrets/github-app-private-key.pem
   # leave MCP_GITHUB_APP_POST_INSTALL_REDIRECT empty — the frontend owns the redirect
   # MCP_PUBLIC_URL and MCP_GITHUB_WEBHOOK_SECRET are already set (sections 1/6)
   ```

   `MCP_GITHUB_APP_CLIENT_ID`/`_SECRET` are mandatory for multi-tenant deployments:
   the callback verifies the connecting user actually controls the installation via
   their own `GET /user/installations` before binding it. Without them the callback
   binds without verification (single-tenant only) and logs a warning.

4. Apply migrations and recreate the affected services (env change needs `up -d`, not
   `restart`; code is already in the images built in section 6):

   ```bash
   docker compose exec -w /app mcp-server alembic upgrade head   # adds 019 + 020
   docker compose up -d mcp-server frontend
   ```

5. Verify the App auth works (should print `configured: True | oauth_configured: True`
   and a JWT length):

   ```bash
   docker compose exec mcp-server python -c "from mcp_server.utils.github_app import build_app_jwt, github_app_configured, github_app_oauth_configured; print('configured:', github_app_configured(), '| oauth_configured:', github_app_oauth_configured()); print('jwt len:', len(build_app_jwt()))"
   ```

6. End-to-end check: log into the app, Settings → Sources → **Connect with GitHub App**
   → install on an org → GitHub redirects back to the callback → pick a repo →
   **Import repository**. The project appears under "Connected sources" with
   `connection_kind=app` and no stored token, reading "Not scanned yet".

   Importing deliberately stops there. Expand the project and use **Scan now**,
   then **Generate documentation** (which asks which pack to use). Nothing runs
   unless asked, so this check costs a clone rather than a full documentation run.

   If the `MCP_GITHUB_APP_*` variables are left unset, the GitHub card says so and
   offers **Import with an access token** instead. That fallback is a supported
   path, not a degraded one.

### GitHub App troubleshooting

Treat GitHub App connection issues as two separate gates:

1. **Install gate**: GitHub must let the external user install the App.
2. **Clone gate**: after install, the MCP server must mint an installation token and
   use it to clone the selected repo.

If an external account sees a GitHub page saying the App is private, the install
gate is failing before Nexus receives a callback. Check the GitHub App settings:

- The operator must open the install URL generated by Nexus:
  `https://github.com/apps/<MCP_GITHUB_APP_SLUG>/installations/new?...`.
  If `<MCP_GITHUB_APP_SLUG>` points at a different or old App, GitHub may show the
  private-App page even though the intended App is configured correctly.
- In the GitHub App's settings, **Where can this GitHub App be installed?** must be
  **Any account**. **Only this account** limits installation to the App owner's
  account and produces the private-App behavior for other users.
- The App cannot stay in a draft/private-only sharing state for a customer flow.
  Make the App installable by external accounts before testing with a customer org.
- The external installer must be an owner/admin for the target GitHub org, or must
  be allowed by that org to request/install GitHub Apps.
- If the App settings were changed after a previous failed attempt, start again from
  Nexus **Settings → Sources → Connect with GitHub App**. Reconfiguring the App from
  GitHub's own settings page does not reliably redirect back to Nexus.

If the App installs and Nexus shows the repo picker, but **Import repository** fails
with `Git clone failed — check the URL and credentials`, the clone gate is failing.
Check these in order:

- The target repo must be selected in the installation. For GitHub installations
  scoped to selected repositories, adding the App to the org is not enough; the
  specific repo must be granted to that installation.
- The App must have **Contents: Read-only** and **Metadata: Read-only** permissions.
  After changing permissions, the GitHub org may need to approve/reinstall the App
  before new installation tokens receive those permissions.
- The callback must include a GitHub `code`. If MCP logs mention
  `Authorization code missing`, enable **Request user authorization (OAuth) during
  installation** and set `MCP_GITHUB_APP_CLIENT_ID` plus
  `MCP_GITHUB_APP_CLIENT_SECRET`.
- The repo picker is the first server-side proof that token minting works:
  `GET /mcp/github/app/installations/{installation_id}/repos` calls GitHub using
  the installation token. If the target repo is absent there, the installation
  does not have repo access.
- On the server, inspect MCP logs for the exact failing stage:

  ```bash
  docker compose logs --tail=200 mcp-server | grep -E "Installation token mint failed|Git clone failed|ownership verification|installation repos"
  ```

- A failed clone can leave a partial directory under the configured repos
  directory, usually `knowledge_base/repos/<project_id>` in local/dev setups or the
  mounted `MCP_REPOS_DIR` in deployment. Confirm the directory is incomplete, then
  move it aside before retrying the same project id; otherwise the add-repo path
  may see the directory and skip the clone attempt.

Notes:
- **First-time install** redirects cleanly. Re-configuring an already-installed App from
  GitHub's own settings page does not redirect back — connect from within Nexus instead.
- If the alembic upgrade reports `github_app_installations already exists` (a prior
  `create_all` made it), run `alembic upgrade 019_add_github_app_installation` then
  `alembic stamp 020_add_github_app_installations`.
- To disable, unset the `MCP_GITHUB_APP_*` variables and `docker compose up -d mcp-server`;
  PAT-connected repos keep working.

## 15. Configure The Doc-Generation Provider

Doc generation runs in the `mcp-server` container. `MCP_DOC_GENERATION_PROVIDER`
defaults to `custom` (the in-repo Python harness). Supported values:

- `custom` — the harness, using `MCP_DOC_GENERATION_MODEL`
- `openai/<model>`, `anthropic/<model>`, `google/<model>`, `deepseek/<model>`,
  `openrouter/<model>`, `ollama/<model>` — the harness with
  that adapter
- `claude-cli` — the Claude Code CLI, pinned in the image
  (`MCP_CLAUDE_CODE_VERSION`). This is the canonical four-phase generation
  method: `providers/claude_cli.py` owns the prompt and `opencode_cli.py`
  reuses it verbatim. Requires the one-off login in section 15a.
- `opencode-cli` — an OpenCode transport for that same prompt, pinned in the
  image (`MCP_OPENCODE_*` vars) and selectable per generation run

An unrecognised value raises at generation time, not at startup.

### 15a. Authenticate The Claude Code CLI

`claude-cli` authenticates by OAuth subscription, not by API key, so it needs one
interactive login per deployment. The token lives in the `claude_home` named
volume mounted at `/app/.home/.claude`, which is what makes it survive an image
rebuild — nothing is written into the repo.

```bash
docker compose exec -it -u appuser mcp-server claude auth login --claudeai
```

Two details that are easy to get wrong and both fail quietly:

- **The subcommand is `claude auth login`, not `claude login`.** A bare
  `claude login` is parsed as a *prompt*: the CLI replies "It looks like you
  typed 'login' as a regular message", exits 0, and authenticates nothing.
- **`-u appuser` is not optional.** `docker compose exec` runs as root by
  default, and a root login writes credentials the service user cannot read.

Verify — `claude auth status` prints JSON naming the method actually in use:

```bash
docker compose exec -u appuser mcp-server claude auth status
```

Expect `"authMethod": "claudeai"` (or similar subscription value). If it reports
`"authMethod": "api_key"` with `"apiKeySource": "ANTHROPIC_API_KEY"`, the login
has not happened yet — see the note below.

**`ANTHROPIC_API_KEY` outranks an OAuth login inside the CLI.** That variable is
set on `mcp-server` for the Python harness's `anthropic/<model>` adapter, which
is a different lane with different billing. Left in place, a deployment put on a
subscription on purpose would silently bill the API account instead, with
nothing in the run output naming which one paid. The provider therefore removes
it from the CLI subprocess whenever OAuth credentials exist
(`services/token_refresh.py::apply_auth_env`) — the parent process keeps it, so
the harness adapter is unaffected. To bill the API account deliberately, simply
do not log in.

For a fully non-interactive host, `claude setup-token` issues a long-lived
subscription token instead. The service does not attempt to renew a credential
format it does not recognise; it defers to the CLI and logs a warning.

If nothing is configured at all, a `claude-cli` run fails immediately at
preflight and names the login command in its error, rather than dying later
inside the subprocess.

The model defaults are **hosted** models, and no key ships with the stack:

| Variable | Default |
|----------|---------|
| `MCP_DOC_GENERATION_MODEL` / `_WRITE_MODEL` | `anthropic/claude-sonnet-5` |
| `MCP_DOC_GENERATION_INVESTIGATION_MODEL` | `google/gemini-3.5-flash` |
| `MCP_DOC_GENERATION_FALLBACK_MODEL` | `openai/gpt-6-luna` |
| `MCP_DOC_GENERATION_API_KEY` | empty (falls back to `LLM_API_KEY`) |

So a stack deployed with defaults and no key will start and pass health checks,
then fail at the first doc-generation run. Decide per client:

- **Hosted models**: set `MCP_DOC_GENERATION_API_KEY` (plus `..._BASE_URL` for a
  gateway), or a provider-specific pair — `MCP_DOC_GENERATION_OPENROUTER_API_KEY`,
  `..._DEEPSEEK_API_KEY`, each with a matching
  `..._BASE_URL`. Confirm the client accepts source code leaving the VPS before
  enabling any hosted provider.
- **Local only**: use `ollama/<model>` routes against `MCP_OLLAMA_BASE_URL`
  (`http://host.docker.internal:11434`) and pull those models on the host as in
  section 4. No key needed; expect materially lower doc quality.

`MCP_MODEL_PRICING_JSON` supplies per-model pricing for cost reporting; leave
empty to report tokens without cost. Pack authoring uses
`MCP_PACK_AUTHOR_MODEL` (default `kimi/kimi-k2.6`, needs `KIMI_API_KEY`).

Apply with `up -d` (env change, not `restart`):

```bash
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d mcp-server
```

Related MCP rollout flags, safe at their defaults:
`MCP_LIVING_DOC_PATCHING_ENABLED=true` with
`MCP_LIVING_DOC_ROLLOUT_STAGE=approval_only` (documents are amended in place,
but only after approval) and `MCP_IMPACT_ROUTING_ENABLED=false`.

## 16. Agent Runtime v2 Rollout Flags (Leave Off For A New Client)

The chat agent runtime has a staged rollout surface (`AGENT_RUNTIME_V2_*`, ~25
variables). Every one is disabled or zero by default, and a fresh client
deployment should keep them that way — the defaults in `docker-compose.yml` are
the supported production posture.

Do not hand-edit these. `AGENT_RUNTIME_V2_CANARY_STAGE` (`off` by default) is
validated against the rest at startup by `CanaryStagePolicy`, so a stage that
disagrees with the percentage/allowlist settings fails the backend fast rather
than silently widening exposure. The pattern is the same for each capability:
`_ENABLED` (bool), `_USERS` (explicit user-id allowlist, JSON array), `_PERCENT`
(stable per-user bucket, 0-100), covering read-only, planning, effects, and live
streaming.

The remaining variables are bounds rather than gates and only need changing with
a measured reason: `_MODEL_TIMEOUT_SECONDS` (60),
`_READ_TOOL_TIMEOUT_SECONDS` (90), `_WRITE_TOOL_TIMEOUT_SECONDS` (30),
`_WORKFLOW_HANDOFF_TIMEOUT_SECONDS` (15), `_SOFT_DEADLINE_SECONDS` (180),
`_HARD_DEADLINE_SECONDS` (300), `_MAX_MODEL_CALLS` (8),
`_SYNTHESIS_RESERVE_CALLS` (2), `_PROVIDER_MAX_RETRIES` (1),
`_DRAFT_ANSWER_STREAMING_ENABLED` (true).

On any disagreement between `.env.production.template` and
`docker-compose.yml`, the Compose file is authoritative — it is what a
`deploy-client.sh` stack actually runs with. (`deploy-client.sh` writes its own
env file and never reads the template.)

To enable a canary later, change the stage and its matching rollout settings
together, then recreate the backend:

```bash
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d backend
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" logs --tail=50 backend
```

A validation failure appears in the backend log at startup, before traffic is
served.

## 17. Issue or renew a subscription licence

Every client instance runs on a time-boxed, Ed25519-signed licence (see
`knowledge_base/.agent/decisions/037-subscription-licence-is-a-signed-file.md`).
While it is valid, users can chat and run generations up to the plan's limit
(plan `basic`: 3 generations per period). Without a valid licence the instance
is **view-only**: chat, LLM routes, scans and generations return HTTP 402,
while login, user management and reading existing documents keep working.

Where things live:

| Item | Location |
|------|----------|
| Signing key (private) | Operator machine ONLY, file named by `MC_LICENCE_SIGNING_KEY_FILE` |
| Current key id | `MC_LICENCE_SIGNING_KID` (currently `nexus-2026-09`) |
| Plan catalogue | `mission_control/src/mission_control/licensing/plans.json` |
| Pinned public keys | `RAG_pipeline/backend/src/licensing/keys.py` and `knowledge_base/mcp-server/src/mcp_server/licensing/keys.py` (byte-identical mirrors) |
| Licence on the VPS | `/etc/intodata-kb/licences/<client>/licence.jwt` (host), mounted read-only at `/etc/intodata-kb/licence/licence.jwt` in `backend` and `mcp-server` |
| Env vars (client env file) | `LICENCE_ENFORCEMENT`, `LICENCE_CLIENT_SLUG`, `LICENCE_DIR`, `LICENCE_FILE` |

The signing key and `MC_LICENCE_SIGNING_*` must never be put on a client VPS.
`deploy-client.sh` aborts (exit 3) if it finds any `*SIGNING_KEY*` variable or a
PEM private key in the client env file, the connectors env file, or the licence
directory.

### 17-UI. Issue and push from the Fleet screen (preferred)

See ADR-038. One-time setup on the operator machine, which is the only place the key lives:

1. Register each client in the Fleet screen (section 18) with its `mcp_url`
   (`https://<client>.<base-domain>`) and control key (its `MCP_OPS_CONTROL_KEY`,
   from `/etc/intodata-kb/envs/<client>.env` on the VPS). `mcp_url` is REQUIRED
   and must be https: Mission Control never addresses a push by the heartbeat
   URL. Clients already listed in a hand-written `deploy/operator/clients.json`
   (legacy, read-only, gitignored) keep working.
2b. Set a strong `MC_API_KEY` (not the repo default, which disables issuing).
2. Set `MC_LICENCE_SIGNING_KEY_HOST_PATH` and `MC_LICENCE_SIGNING_KID=nexus-2026-09`
   in your shell or root `.env`. This is never a client env file.
3. `docker compose -f docker-compose.yml -f deploy/operator/docker-compose.issuer.yml up -d --build mission-control dashboard`.
4. Open `http://localhost:8001` as a superadmin and go to Fleet, then the client, then the Licence tab.
   "Issuing is not configured" means the key did not mount. Check step 2.

To issue:
- Pick a plan and a duration.
- Start "Now" for a new client, or "When the current licence ends" for a gapless renewal.
  The renewal starts when the last licence in the ledger ends. The instance keeps it as a
  queued next licence (`licence.next.jwt`), and it takes over at its start date with no
  lockout. The result shows "Queued as next licence".
- Keep "Push to instance" on, then confirm.
- The result shows the instance's own read-back (`state: valid`), and the history table records it.

If a push fails, the licence is still issued and recorded, and the reason is shown:
- `503`: the VPS has no `MCP_OPS_CONTROL_KEY`. Re-run `deploy-client.sh` there.
- `401`: the control key in the Fleet registry (or `clients.json`) is wrong.
- `422` from the instance: wrong client slug, or an expired licence.
- `422` from Mission Control, before anything is issued: `start_in_past` (more than
  24h back), `period_too_long` (more than 37 months), `invalid_period`, or
  `instance_not_addressable` (`mcp_url` not pinned, not https, or invalid).
- Unreachable: check `mcp_url` and `OPS_CONTROL_ALLOWED_IPS`.

To fall back to a manual install, untick push, tick "Show licence for manual install", and continue with 17b.

The Fleet screen is loopback-only by design: the client edge does not route `/mc/`.
Reach a remote operator host through an SSH tunnel (`ssh -L 8001:127.0.0.1:8001 ...`).
Back up the `mission_control_licences` volume, which holds the ledger `issued.jsonl`,
together with the signing key.

### 17a. Issue a licence by CLI (operator machine, fallback)

```bash
cd /path/to/intodata-kb-v2/mission_control
export MC_LICENCE_SIGNING_KEY_FILE=~/.intodata/licence-signing/nexus-2026-09.pem
export MC_LICENCE_SIGNING_KID=nexus-2026-09
# Needs Python with PyJWT[crypto] + cryptography, e.g. `pip install -e .`
PYTHONPATH=src python -m mission_control.licensing issue \
  --client "$CLIENT_SLUG" --plan basic --months 1 --out "./${CLIENT_SLUG}-licence.jwt"
PYTHONPATH=src python -m mission_control.licensing inspect "./${CLIENT_SLUG}-licence.jwt"
```

- `--client` must equal the instance's `LICENCE_CLIENT_SLUG` (the `--client-name`
  given to `deploy-client.sh`). A mismatch is `licence_invalid`.
- The period starts now by default. Use `--start` / `--end` (ISO dates, UTC) for
  a fixed period, e.g. a renewal that starts exactly when the current one ends.
- `inspect` shows the claims WITHOUT verifying; it is for eyeballing only.
- A new period resets the generation count (quota is counted within `[nbf, exp)`).

### 17b. Install or renew on the VPS

```bash
scp "./${CLIENT_SLUG}-licence.jwt" "ops@${FQDN}:/tmp/licence.jwt"
ssh "ops@${FQDN}" "sudo install -o root -g 1001 -m 0640 /tmp/licence.jwt \
  /etc/intodata-kb/licences/${CLIENT_SLUG}/licence.jwt && rm /tmp/licence.jwt"
```

No restart is needed: both services re-read the file when its mtime changes,
so the next request sees the new licence. The directory (not the file) is
bind-mounted, which is why replacing the file is picked up.

Verify:

```bash
# Inside the containers, no login needed:
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" exec backend \
  python -c "from src.licensing.runtime import get_policy; print(get_policy().status().to_public_dict())"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" exec mcp-server \
  python -c "from mcp_server.licensing.runtime import get_policy; print(get_policy().status().to_public_dict())"

# Through the app, as any logged-in user (cookie jar from SMOKE-TEST section 3):
curl -fsS -b cookies.txt "https://${FQDN}/api/v1/licence/status"

# The startup line each service logs once (reflects the state at start-up):
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" logs backend mcp-server \
  | grep 'licence: enforcement='
```

Expected: `state` is `valid`, `plan` is `basic`, `period_end` is the new expiry,
and the log line reads `licence: enforcement=enforce state=valid ...`.

### 17c. Rollout order for EXISTING clients

The first release with licence enforcement refuses to start in production unless
`LICENCE_ENFORCEMENT=enforce`, so enforcement switches on with the upgrade
itself. Install the licence FIRST, or the client drops to view-only on upgrade:

1. Issue a licence for the client (17a).
2. Create the directory and install the file before upgrading:
   `sudo install -d -o root -g 1001 -m 0750 /etc/intodata-kb/licences/$CLIENT_SLUG`,
   then 17b.
3. Pull the release and re-run `deploy/deploy-client.sh` (section 6). It
   backfills `LICENCE_ENFORCEMENT=enforce`, `LICENCE_CLIENT_SLUG`,
   `LICENCE_DIR` and `LICENCE_FILE` without touching existing secrets, and no
   longer prints the "no licence installed" notice.
4. Verify as in 17b. Then run SMOKE-TEST section 10.

Guard rails: `deploy-client.sh` aborts (exit 4) when it re-deploys an EXISTING
client that has no licence file, so step 3 cannot lock a customer out by
accident; pass `--allow-view-only` only when a view-only instance is intended.
Restarting the stack on the new release WITHOUT re-running the script is
still unsafe: compose defaults enforcement to `enforce` (fail closed, no
crash on the `off` check), but an env file without `LICENCE_CLIENT_SLUG` and
`MCP_OPS_CLIENT_SLUG` fails the production empty-slug check and both services
refuse to start. Always upgrade through the script.

### 17d. Signing key rotation

1. On the operator machine: `PYTHONPATH=src python -m mission_control.licensing keygen --out ~/.intodata/licence-signing/<new-kid>.pem`.
   It prints the public key.
2. Add `"<new-kid>": "<public PEM>"` to BOTH `keys.py` mirrors, keeping the old
   kid. The mirror parity test fails if the two copies differ.
3. Release and deploy to every client.
4. Only then set `MC_LICENCE_SIGNING_KID=<new-kid>` and
   `MC_LICENCE_SIGNING_KEY_FILE` to the new key, and issue with it.
5. Remove the old kid from both mirrors only after every licence signed with it
   has expired.

If the private key is suspected compromised: do steps 1-2 but REMOVE the old kid
in the same change, issue new licences for every client with the new kid,
install them (17b) and deploy the release in the same maintenance window.
Licences signed with the old kid become `licence_invalid` as soon as the release
is live.

### 17e. A client is locked out by mistake

Symptoms: users see the licence banner and chat or generation returns 402 with
`detail.code` one of `licence_missing`, `licence_invalid`, `licence_expired`,
`licence_not_yet_valid`. Nobody is logged out (402 is never treated as 401).

1. Run the in-container check from 17b; `reason` names the failure.
2. Common causes and fixes:
   - `missing`: file not at `/etc/intodata-kb/licences/<client>/licence.jwt`,
     wrong name, or not readable by gid 1001 (`root:1001`, `0640`; dir `0750`).
   - `invalid` + subject mismatch: re-issue with `--client` equal to
     `LICENCE_CLIENT_SLUG` in the env file.
   - `invalid` + unknown kid / bad signature: the release on the VPS does not
     pin that kid; deploy the release that does, or re-issue with a pinned kid.
   - `not_yet_valid`: the VPS clock is behind or `--start` is in the future.
     Check `timedatectl`; re-issue with an earlier `--start`. Leeway is 60s.
   - `expired`: issue and install a renewal.
3. The remedy is always a correct licence. There is no production break-glass
   that turns enforcement off: `LICENCE_ENFORCEMENT=off` is refused at startup
   when `APP_ENV=production`, and changing `APP_ENV` also disables the other
   production secret checks, so do not do that.

## 18. Fleet: add a client

See ADR-039. Clients are registered in the central Mission Control (the one
that issues licences, section 17-UI), not by editing `clients.json`. Each
registered client gets its own telemetry key for its heartbeat. That key only
authorises its own slug, so a leaked client key cannot overwrite another
client's Fleet data.

### 18a. One-time setup on the operator host

1. Run Mission Control with a persistent registry. Both operator compose files
   (`deploy/operator/docker-compose.issuer.yml`,
   `deploy/operator/docker-compose.operator.yml`) set
   `MC_FLEET_REGISTRY=/app/fleet/fleet_clients.json` on a named volume
   (`mission_control_fleet` / `mission_control_operator_fleet`). Back that
   volume up together with the licence ledger: it holds every client's control
   key (file mode 0600).
2. Set `MC_PUBLIC_URL` to the public HTTPS base URL that client servers
   heartbeat to, e.g. `https://nexus.intodata.nl`, in the root `.env` or
   `deploy/operator/operator.env`. Then recreate the container:
   `docker compose ... up -d mission-control`. The Fleet screen puts this value
   into the env lines it shows. If it is unset, `MCP_OPS_TELEMETRY_URL` is shown
   empty and you have to fill it in by hand.
3. Publish the heartbeat route on that host. The route is exactly
   `POST /ops/clients/<slug>/telemetry` and nothing else. No other `/ops` or
   `/mc` path is exposed. Choose one of:
   - **Host deployed with `deploy-client.sh`:** re-run it with
     `--fleet-host --fleet-mc-port <port>`, where `<port>` is the port the
     central Mission Control publishes on 127.0.0.1 (its `MC_PORT`, 9100 for
     the headless operator compose). `--fleet-mc-port` is required: the
     script exits 2 without it rather than guess from this stack's offset.
   - **Any other host** (for example `nexus.intodata.nl`, which was NOT
     deployed with the script):
     ```bash
     sudo install -m 0644 deploy/operator/nginx-fleet-telemetry.conf \
       /etc/nginx/snippets/nexus-fleet-telemetry.conf
     # edit 9100 in the snippet if Mission Control listens on another loopback port
     # then, inside the `listen 443 ssl` server block for that host, add:
     #     include /etc/nginx/snippets/nexus-fleet-telemetry.conf;
     sudo nginx -t && sudo systemctl reload nginx
     ```
     The snippet's location is a regex location, so it wins over prefix
     locations such as `location /`. It does NOT win over a `location ^~ /ops/`
     or an earlier regex that also matches, so check the existing server block
     for either.
4. Verify from outside. You should get 401 for a wrong key, 403 for GET, and
   400 for a query string:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Authorization: Bearer wrong' \
     -H 'Content-Type: application/json' -d '{}' "$MC_PUBLIC_URL/ops/clients/probe/telemetry"   # 401
   curl -s -o /dev/null -w '%{http_code}\n' "$MC_PUBLIC_URL/ops/clients/probe/telemetry"       # 403
   curl -s -o /dev/null -w '%{http_code}\n' "$MC_PUBLIC_URL/ops/fleet/clients"                 # not 200 from Mission Control
   ```

### 18b. Add a client

1. In the Fleet screen (loopback `http://localhost:8001`, or an SSH tunnel to
   it), choose **Add client** and enter:
   - the slug, which is the `--client-name` of its `deploy-client.sh`;
   - `mcp_url`, which is `https://<client>.<base-domain>` (https is required
     except on localhost);
   - optionally its control key, which is `MCP_OPS_CONTROL_KEY` from
     `/etc/intodata-kb/envs/<client>.env` on the client VPS. You can add a
     client before its server exists and set the control key later with
     **Edit**. Until it is set, licence pushes and triggers answer 422
     `no_control_key`.
2. The result shows the telemetry key **once**, as three env lines:
   ```
   MCP_OPS_TELEMETRY_URL=<MC_PUBLIC_URL>
   MCP_OPS_TELEMETRY_API_KEY=<the new key>
   MCP_OPS_CLIENT_SLUG=<slug>
   ```
   Only a hash is stored, so the key cannot be shown again. If you lose it,
   use **Rotate key**.
3. On the client VPS, apply them in either of these ways:
   - Put the key in a root-only file and re-run the script with it, so the
     key never appears in argv, `ps`, shell history or sudo's auth log:
     ```bash
     sudo install -m 0600 /dev/null /root/<slug>.telemetry-key
     sudo nano /root/<slug>.telemetry-key          # paste the key as the first line
     sudo deploy/deploy-client.sh --client-name <slug> ... \
       --ops-telemetry-url <MC_PUBLIC_URL> \
       --ops-telemetry-key-file /root/<slug>.telemetry-key
     sudo shred -u /root/<slug>.telemetry-key      # the env file now holds it
     ```
     Alternatively export `OPS_TELEMETRY_KEY` and run
     `sudo --preserve-env=OPS_TELEMETRY_KEY deploy/deploy-client.sh ...` with
     neither key flag. `--ops-telemetry-key <key>` still works but prints a
     warning, because the key is then visible on the command line. The script
     validates the key, replaces any old values, sets
     `MCP_OPS_CLIENT_SLUG=<slug>`, never prints the key, keeps the env file
     0600, and redeploys. The licence guard (exit 4) still applies to an
     existing client without a licence.
   - Or paste the three lines into `/etc/intodata-kb/envs/<slug>.env`,
     replacing the existing empty `MCP_OPS_TELEMETRY_*` lines (a duplicate
     later in the file wins), then redeploy the same way.
4. Within about 5 minutes (`MCP_OPS_TELEMETRY_INTERVAL_SECONDS=300`), the Fleet
   list shows `last_heartbeat_at` for the client and `heartbeat_auth: client_key`.
   The stored snapshot records `_mc_auth: client_key`.

### 18c. Rotate, edit, remove

- **Rotate key.** The old key stops working immediately. Apply the new one
  as in 18b step 3. Until you do, the client's heartbeats get 401 and its
  Fleet data goes stale.
- **Edit.** Changes `mcp_url` and/or the control key. Neither is ever shown
  back; the list only shows `has_control_key`.
- **Remove.** Deletes the registry entry and its keys. The last snapshot and
  the licence history stay.
- Clients still in the legacy `clients.json` are listed with
  `source: file`. They are read-only through the API (409 `managed_by_file`).
  To move one to the registry, delete it from `clients.json`, then add it in
  the Fleet screen.

### 18d. Moving existing instances off the shared key

Heartbeats signed with the operator's global `MC_API_KEY` (the "shared key")
are legacy only, kept for instances deployed before per-client keys. Mission
Control accepts one only when ALL of these hold; otherwise it answers the
same 401 as a wrong key:

- `MC_ALLOW_SHARED_HEARTBEAT` is true (the default);
- `MC_API_KEY` is not the public repo default `mc-dev-local-secret`
  (Mission Control also logs a startup WARNING whenever it runs on that key);
- the fleet registry is readable (a corrupt registry fails closed, since
  Mission Control cannot tell which slugs have their own key; the Fleet list
  then reports `registry_unavailable: true`);
- the slug has NO registered telemetry key. As soon as a client is in the
  registry, only its own key works for it.

Accepted shared-key heartbeats are logged as
`shared-key heartbeat client=<slug>` and refused ones as
`shared-key heartbeat refused client=<slug>: <reason>`, each once per slug per
process. The Fleet list shows unkeyed clients as
`heartbeat_auth: shared_key_only`.

For each such instance:

1. Add it in the Fleet screen (18b). Its shared-key heartbeats stop being
   accepted at that moment, so do step 2 straight away; until then its Fleet
   data goes stale (the last snapshot is kept).
2. Apply the three env lines on its VPS (18b step 3) and redeploy.
3. Confirm the snapshot shows `_mc_auth: client_key`.

Once no instance uses the shared key, turn it off and rotate the old value:

```bash
# deploy/operator/operator.env (or the root .env) on the operator host;
# the compose files pass MC_ALLOW_SHARED_HEARTBEAT through to Mission Control
MC_ALLOW_SHARED_HEARTBEAT=false
MC_API_KEY=<new random value, e.g. openssl rand -hex 32>
docker compose ... up -d mission-control dashboard
```

Startup then logs `Heartbeats: per-client telemetry keys only`. Rotating
`MC_API_KEY` matters because the old value may still sit in client env files.
The dashboard's `/mc/` proxy injects the same `MC_API_KEY`, which is why it is
recreated too.

## Gap Capture

If a deployment step fails:

1. Record the failed command, error, and fix.
2. Update this runbook before marking the story done.
3. Re-run the affected section.
4. Add missing verification to [SMOKE-TEST.md](./SMOKE-TEST.md) if it protects a critical path.

## Rollback Procedure

Use this when a deployment leaves the stack broken and a clean restart is needed. It does not destroy data volumes.

### Stop the stack without losing data

```bash
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" down
```

### Revert to a known-good commit

```bash
cd "$REPO_ROOT"
git log --oneline -10          # find the last known-good SHA
git checkout <sha>             # check out that commit
```

### Rebuild and restart

```bash
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d --build
```

### If the database migration caused the failure

Identify the migration that broke the schema:

```bash
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" \
  exec backend alembic history --verbose | head -30
```

Downgrade one revision:

```bash
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" \
  exec backend alembic downgrade -1
```

Then fix the migration file, rebuild, and run `alembic upgrade head` again.

### Nightly backups

`deploy/backup-postgres.sh <client>` dumps the client database to
`/var/backups/intodata-kb/` (14-day retention). Schedule it once per client:

```bash
echo "0 2 * * * root $REPO_ROOT/deploy/backup-postgres.sh $CLIENT_SLUG >> /var/log/intodata-backup.log 2>&1" | \
  sudo tee /etc/cron.d/intodata-backup-$CLIENT_SLUG
```

### If Postgres data is corrupted

Restore from the most recent backup (see `backup-postgres.sh`):

```bash
# List available backups
ls -lht /var/backups/intodata-kb/

# Stop everything, then start only postgres: the restore needs it running
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" stop
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" start postgres

# Restore (the dump drops and recreates each object)
gunzip -c /var/backups/intodata-kb/<backup-file>.sql.gz | \
  sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" \
  exec -T postgres psql -U admin "intodata_kb_${CLIENT_SLUG}"

# Restart
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d
```

### Verify recovery

Run sections 1–2 of [SMOKE-TEST.md](./SMOKE-TEST.md) to confirm the stack is healthy again before restoring client access.
