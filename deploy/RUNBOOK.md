# IntoData KB Deployment Foundation Runbook

This runbook is the canonical EP-001 deployment path for a clean Ubuntu VPS or staging host. It uses the current deployment architecture:

- Docker Compose for the seven application services: `postgres`, `qdrant`, `hindsight`, `backend`, `frontend`, `mcp-server`, `dashboard`
- Host-level Ollama exposed to containers through `host.docker.internal:11434`
- nginx + certbot for HTTPS
- UFW and app-level allowlists for approved CIDRs

Older Caddy assets remain in the repo for legacy/reference use, but nginx/certbot is the canonical EP-001 path.

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

If DNS is not ready, add `--no-certbot` to suppress the certbot instruction and
return to Step 9 after DNS resolves.

Set app-level allowlists:

```bash
sudo sed -i "s|^ALLOWED_IPS=.*|ALLOWED_IPS=${APPROVED_CIDRS}|" "$ENV_FILE"
sudo sed -i "s|^MCP_ALLOWED_IPS=.*|MCP_ALLOWED_IPS=${APPROVED_CIDRS}|" "$ENV_FILE"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d
```

If the env file existed before this runbook was updated, confirm these MCP values exist and add them if missing:

```bash
sudo grep -E '^(REPO_ROOT|PUBLIC_APP_URL|MCP_PUBLIC_URL|MCP_DATABASE_URL|MCP_RAG_BASE_URL|MCP_PROJECT_ROOT|MCP_INTODOC_COMMAND|MCP_PORT|SMTP_HOST|SMTP_PORT)=' "$ENV_FILE"
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

If using an external LLM provider (Deepseek, OpenAI, etc.) for doc generation, the default
token limits are too low for large projects. Set these in the env file after MCP is running:

```bash
grep -q '^MCP_PHASE2_MAX_TOKENS' "$ENV_FILE" || echo "MCP_PHASE2_MAX_TOKENS=32000" | sudo tee -a "$ENV_FILE"
grep -q '^MCP_PHASE3_MAX_TOKENS' "$ENV_FILE" || echo "MCP_PHASE3_MAX_TOKENS=64000" | sudo tee -a "$ENV_FILE"
sudo systemctl restart "mcp-server@${CLIENT_SLUG}.service"
```

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
```

Expected:

- Seven Docker services are running and healthy.
- MCP API returns `{"status":"ok","service":"intodoc-mcp-server"}` on the VPS-local port.
- Backend health is `healthy`, or `degraded` only if a non-core dependency is intentionally unavailable.
- Ollama returns model tags.

## 12. Run Smoke Test

Open:

```text
https://<client>.<base-domain>
```

Create the first admin user if the bootstrap screen appears, then run [SMOKE-TEST.md](./SMOKE-TEST.md).

## 13. Enable Talend TMC Runtime (Optional, EP-004)

Skip this section unless the client requires live Talend TMC execution data in
chat. The default stack runs with runtime disabled.

The Talend API token must never be placed in `.env`, the Compose `environment`
block, logs, or the repo. It is read from a mounted file.

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

3. Set the runtime variables in `.env`:

   ```bash
   RUNTIME_PROVIDER_KEY=talend_tmc
   RUNTIME_PROVIDER_LABEL=Talend TMC
   RUNTIME_PROVIDER_HEALTH_URL=http://talend-tmc-runtime:9000/health
   TALEND_TMC_BASE_URL=http://talend-tmc-runtime:9000
   TALEND_TMC_API_TOKEN_FILE=/run/secrets/talend_tmc_api_token
   ```

   `TALEND_TMC_BASE_URL` must point at the internal TMC runtime service on the
   Compose network. Do not publish that service on a public host port.

4. Restart the backend:

   ```bash
   docker compose up -d backend
   ```

5. Confirm status and that the token never leaks (see SMOKE-TEST section 8):

   ```bash
   curl -fsS http://127.0.0.1:${BACKEND_PORT:-8000}/api/v1/runtime/status   # needs auth cookie
   docker compose logs backend | grep -i "$(sudo cat /opt/intodata/secrets/talend_tmc_api_token)" && echo "LEAK" || echo "no token in logs"
   ```

To disable again, set `RUNTIME_PROVIDER_KEY=` (empty) and restart the backend.

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

### If Postgres data is corrupted

Restore from the most recent backup (see `backup-postgres.sh`):

```bash
# List available backups
ls -lht /var/backups/intodata-kb/

# Stop the stack
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" down

# Restore
gunzip -c /var/backups/intodata-kb/<backup-file>.sql.gz | \
  sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" \
  exec -T postgres psql -U admin "intodata_kb_${CLIENT_SLUG}"

# Restart
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d
```

### Verify recovery

Run sections 1–2 of [SMOKE-TEST.md](./SMOKE-TEST.md) to confirm the stack is healthy again before restoring client access.
