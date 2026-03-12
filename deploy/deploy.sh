#!/usr/bin/env bash
set -euo pipefail

# Deploy intodata-kb to Ubuntu VPS
# Usage: sudo bash deploy/deploy.sh

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="/opt/intodata-kb"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_USER="deploy"

echo "=== intodata-kb deployment ==="
echo "Repo:    $REPO_DIR"
echo "Install: $INSTALL_DIR"
echo ""

# ── 1. Check prerequisites ──────────────────────────────────────────────────

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        echo "ERROR: $1 is not installed. Please install it first."
        exit 1
    fi
}

check_cmd docker
check_cmd python3
check_cmd node
check_cmd npm

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]; }; then
    echo "ERROR: Python 3.11+ required, found $PYTHON_VERSION"
    exit 1
fi

echo "[OK] Prerequisites: docker, python$PYTHON_VERSION, node $(node -v), npm $(npm -v)"

# ── 2. Create service user if needed ─────────────────────────────────────────

if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
    usermod -aG docker "$SERVICE_USER"
    echo "[OK] Created user: $SERVICE_USER"
else
    echo "[OK] User $SERVICE_USER already exists"
fi

# ── 3. Copy repo to install dir ─────────────────────────────────────────────

if [ "$REPO_DIR" != "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR"
    rsync -a --delete \
        --exclude='.git' \
        --exclude='node_modules' \
        --exclude='venv' \
        --exclude='__pycache__' \
        --exclude='.mypy_cache' \
        "$REPO_DIR/" "$INSTALL_DIR/"
    echo "[OK] Synced repo to $INSTALL_DIR"
else
    echo "[OK] Already in $INSTALL_DIR"
fi

# ── 4. Create env file from template if missing ─────────────────────────────

if [ ! -f "$INSTALL_DIR/.env" ]; then
    if [ -f "$INSTALL_DIR/.env.production.template" ]; then
        cp "$INSTALL_DIR/.env.production.template" "$INSTALL_DIR/.env"
        echo "[WARN] Created .env from template -- review and update values!"
    else
        echo "[WARN] No .env found. Create one before starting the service."
    fi
fi

# ── 5. Python virtualenv + install packages ──────────────────────────────────

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip

# Install intodoc CLI
"$VENV_DIR/bin/pip" install -e "$INSTALL_DIR/knowledge_base/intodoc"

# Install MCP server
"$VENV_DIR/bin/pip" install -e "$INSTALL_DIR/knowledge_base/mcp-server"

echo "[OK] Python packages installed in $VENV_DIR"

# ── 6. Build dashboard ──────────────────────────────────────────────────────

cd "$INSTALL_DIR/knowledge_base/dashboard"
npm ci
npm run build
echo "[OK] Dashboard built at $INSTALL_DIR/knowledge_base/dashboard/dist"

# ── 7. Set ownership ─────────────────────────────────────────────────────────

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
echo "[OK] Ownership set to $SERVICE_USER"

# ── 8. Firewall (UFW) ──────────────────────────────────────────────────────

if command -v ufw &>/dev/null; then
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp   # SSH
    ufw allow 80/tcp   # HTTP (redirect to HTTPS)
    ufw allow 443/tcp  # HTTPS
    ufw --force enable
    echo "[OK] UFW firewall configured (22, 80, 443 only)"
else
    echo "[WARN] UFW not found, skipping firewall setup"
fi

# ── 9. fail2ban ────────────────────────────────────────────────────────────

if ! command -v fail2ban-client &>/dev/null; then
    apt-get install -y fail2ban
fi
cp "$INSTALL_DIR/deploy/jail.local" /etc/fail2ban/jail.local
systemctl enable fail2ban
systemctl restart fail2ban
echo "[OK] fail2ban configured"

# ── 10. PostgreSQL backup cron ─────────────────────────────────────────────

cp "$INSTALL_DIR/deploy/backup-postgres.sh" /opt/intodata-kb/backup-postgres.sh
chmod +x /opt/intodata-kb/backup-postgres.sh
# Add daily cron job if not already present
CRON_CMD="0 2 * * * /opt/intodata-kb/backup-postgres.sh >> /var/log/intodata-backup.log 2>&1"
(crontab -l 2>/dev/null | grep -qF "backup-postgres.sh") || (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
echo "[OK] Daily PostgreSQL backup cron installed (02:00)"

# ── 11. Validate secrets before starting ────────────────────────────────────

if [ -f "$INSTALL_DIR/.env" ]; then
    # Source the .env file to check values
    _JWT=$(grep -E '^JWT_SECRET_KEY=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
    _PG=$(grep -E '^POSTGRES_PASSWORD=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)

    INSECURE_DEFAULTS="change-me-in-production-use-a-strong-random-key changeme admin password secret"
    _fail=0
    for _default in $INSECURE_DEFAULTS; do
        if [ "$_JWT" = "$_default" ]; then
            echo "ERROR: JWT_SECRET_KEY is set to an insecure default ('$_default')."
            echo "       Generate a strong key: openssl rand -hex 32"
            _fail=1
        fi
        if [ "$_PG" = "$_default" ]; then
            echo "ERROR: POSTGRES_PASSWORD is set to an insecure default ('$_default')."
            echo "       Generate a strong password: openssl rand -hex 16"
            _fail=1
        fi
    done

    if [ -z "$_JWT" ]; then
        echo "ERROR: JWT_SECRET_KEY is not set in .env"
        _fail=1
    fi
    if [ -z "$_PG" ]; then
        echo "ERROR: POSTGRES_PASSWORD is not set in .env"
        _fail=1
    fi
    if [ ${#_JWT} -lt 32 ] && [ -n "$_JWT" ]; then
        echo "WARNING: JWT_SECRET_KEY is shorter than 32 characters. Consider using: openssl rand -hex 32"
    fi

    if [ "$_fail" -eq 1 ]; then
        echo ""
        echo "Aborting deployment. Fix the .env file and re-run."
        exit 1
    fi
    echo "[OK] Secrets validated"
fi

# ── 12. Docker Compose ──────────────────────────────────────────────────────

cd "$INSTALL_DIR"
docker compose up -d --build
echo "[OK] Docker services started"

# ── 13. Install systemd service ─────────────────────────────────────────────

cp "$INSTALL_DIR/deploy/mcp-server.service" /etc/systemd/system/mcp-server.service
systemctl daemon-reload
systemctl enable mcp-server
systemctl restart mcp-server
echo "[OK] mcp-server.service installed and started"

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "=== Deployment complete ==="
echo ""
echo "Services:"
echo "  Dashboard + MCP API:  http://$(hostname -I | awk '{print $1}'):8002"
echo "  RAG Chat UI:          http://$(hostname -I | awk '{print $1}'):3000"
echo ""
echo "Status commands:"
echo "  docker compose ps                    # Docker services"
echo "  systemctl status mcp-server          # MCP server"
echo "  journalctl -u mcp-server -f          # MCP server logs"
echo ""
echo "Post-deploy manual steps:"
echo "  1. Install Ollama:  curl -fsSL https://ollama.com/install.sh | sh"
echo "  2. Pull models:     ollama pull nomic-embed-text && ollama pull gemma3:12b"
echo "  3. Install Claude:  npm install -g @anthropic-ai/claude-code"
echo "  4. Authenticate:    claude login"
echo "  5. Configure Caddy: sudo bash deploy/caddy-install.sh <your-domain>"
echo ""
echo "Claude Code MCP config (~/.claude.json or project .mcp.json):"
echo '  {'
echo '    "mcpServers": {'
echo '      "intodoc": {'
echo '        "command": "'$VENV_DIR'/bin/python",'
echo '        "args": ["-m", "mcp_server", "--mcp"],'
echo '        "cwd": "'$INSTALL_DIR'/knowledge_base"'
echo '      }'
echo '    }'
echo '  }'
