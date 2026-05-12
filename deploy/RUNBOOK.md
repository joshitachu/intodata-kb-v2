# IntoData KB Deployment Foundation Runbook

This runbook is the canonical EP-001 deployment path for a clean Ubuntu VPS or staging host. It uses the current deployment architecture:

- Docker Compose for the six application services: `postgres`, `qdrant`, `hindsight`, `backend`, `frontend`, `dashboard`
- Host-level Ollama exposed to containers through `host.docker.internal:11434`
- Host-level MCP API via `mcp-server@<client>.service`
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
export REPO_URL=git@github.com:intodata/intodata-kb-v2.git  # migrated 2026-05-11
export REPO_ROOT="/opt/intodata-kb/${CLIENT_SLUG}"
export ENV_FILE="/etc/intodata-kb/envs/${CLIENT_SLUG}.env"
export APPROVED_CIDRS="203.0.113.10/32,198.51.100.0/24"
```

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
sudo git clone "$REPO_URL" "$REPO_ROOT"
sudo chown -R "$USER:$USER" "$REPO_ROOT"
cd "$REPO_ROOT"
git remote -v
```

The remote must point at the company GitHub organisation.

Install the MCP/intodoc Python packages for the host-level MCP systemd service:

```bash
python3 -m venv "$REPO_ROOT/venv"
"$REPO_ROOT/venv/bin/pip" install --upgrade pip
"$REPO_ROOT/venv/bin/pip" install -e "$REPO_ROOT/knowledge_base/intodoc"
"$REPO_ROOT/venv/bin/pip" install -e "$REPO_ROOT/knowledge_base/mcp-server"
```

## 4. Install Ollama On The Host

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama pull nomic-embed-text
ollama pull gemma3:4b
```

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

## 6. Deploy The Docker Stack

```bash
cd "$REPO_ROOT"
sudo deploy/deploy-client.sh \
  --client-name "$CLIENT_SLUG" \
  --port-offset 0 \
  --base-domain "$BASE_DOMAIN" \
  --admin-email "$ADMIN_EMAIL"
```

The script generates `/etc/intodata-kb/envs/<client>.env`, renders nginx config from `deploy/nginx/nexus-client.conf.template`, and starts the Docker Compose stack.

Set app-level allowlists:

```bash
sudo sed -i "s|^ALLOWED_IPS=.*|ALLOWED_IPS=${APPROVED_CIDRS}|" "$ENV_FILE"
sudo sed -i "s|^MCP_ALLOWED_IPS=.*|MCP_ALLOWED_IPS=${APPROVED_CIDRS}|" "$ENV_FILE"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d
```

If the env file existed before this runbook was updated, confirm these MCP values exist and add them if missing:

```bash
sudo grep -E '^(MCP_DATABASE_URL|MCP_RAG_BASE_URL|MCP_PROJECT_ROOT|MCP_INTODOC_COMMAND|MCP_PORT)=' "$ENV_FILE"
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

## 8. Enable MCP API Systemd Service

```bash
cd "$REPO_ROOT"
sudo cp deploy/mcp-server@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now "mcp-server@${CLIENT_SLUG}.service"
sudo systemctl status "mcp-server@${CLIENT_SLUG}.service" --no-pager
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
sudo systemctl restart "mcp-server@${CLIENT_SLUG}.service"
```

## 9. Enable HTTPS With nginx/certbot

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
- `/dashboard/`, `/mcp/*`, and `/logs/*` return 404 on the customer domain.
- The Next.js frontend proxies required onboarding source actions through `/api/source/*`.

If private operator access to MCP is needed for debugging, use SSH port
forwarding to the VPS-local MCP port instead of exposing `/mcp/*` publicly:

```bash
ssh -L 8002:127.0.0.1:8002 "intodata@${FQDN}"
curl -fsS http://127.0.0.1:8002/health
```

## 10. Restrict Inbound Access

Do not close the current SSH session until a second approved connection is verified.

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

```bash
cd "$REPO_ROOT"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" ps
curl -fsS "https://${FQDN}/health"
curl -fsS "https://${FQDN}/api/v1/auth/bootstrap-status"
MCP_PORT="$(grep '^MCP_PORT=' "$ENV_FILE" | cut -d= -f2)"
curl -fsS "http://127.0.0.1:${MCP_PORT:-8002}/health"
curl -fsS http://127.0.0.1:11434/api/tags
```

Expected:

- Six Docker services are running and healthy.
- MCP API returns `{"status":"ok","service":"intodoc-mcp-server"}` on the VPS-local port.
- Backend health is `healthy`, or `degraded` only if a non-core dependency is intentionally unavailable.
- Ollama returns model tags.

## 12. Run Smoke Test

Open:

```text
https://<client>.<base-domain>
```

Create the first admin user if the bootstrap screen appears, then run [SMOKE-TEST.md](./SMOKE-TEST.md).

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
  exec -T postgres psql -U admin intodata_kb

# Restart
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" up -d
```

### Verify recovery

Run sections 1–2 of [SMOKE-TEST.md](./SMOKE-TEST.md) to confirm the stack is healthy again before restoring client access.
