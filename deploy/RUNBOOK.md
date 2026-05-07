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
export REPO_URL=git@github.com:intodata/intodata-kb-v2.git
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
curl -fsS http://127.0.0.1:11434/api/tags
```

Verify container access to Ollama:

```bash
sudo docker run --rm --add-host=host.docker.internal:host-gateway curlimages/curl:8.8.0 -fsS http://host.docker.internal:11434/api/tags
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

## 7. Enable MCP API Systemd Service

```bash
cd "$REPO_ROOT"
sudo cp deploy/mcp-server@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now "mcp-server@${CLIENT_SLUG}.service"
sudo systemctl status "mcp-server@${CLIENT_SLUG}.service" --no-pager
```

## 8. Enable HTTPS With nginx/certbot

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
```

`/logs/*` must keep SSE buffering disabled through nginx:

```bash
sudo nginx -T | grep -A8 'location /logs/'
```

Expected nginx settings include `proxy_buffering off`, `proxy_cache off`, and a long `proxy_read_timeout`.

## 9. Restrict Inbound Access

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

## 10. Verify Health

```bash
cd "$REPO_ROOT"
sudo docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" ps
curl -fsS "https://${FQDN}/health"
curl -fsS "https://${FQDN}/mcp/health"
curl -fsS "https://${FQDN}/api/v1/auth/bootstrap-status"
curl -fsS http://127.0.0.1:11434/api/tags
```

Expected:

- Six Docker services are running and healthy.
- MCP API returns `{"status":"ok","service":"intodoc-mcp-server"}`.
- Backend health is `healthy`, or `degraded` only if a non-core dependency is intentionally unavailable.
- Ollama returns model tags.

## 11. Run Smoke Test

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
