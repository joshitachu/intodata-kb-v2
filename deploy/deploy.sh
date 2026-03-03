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

if [ ! -f "$INSTALL_DIR/.env.production" ]; then
    if [ -f "$INSTALL_DIR/.env.production.template" ]; then
        cp "$INSTALL_DIR/.env.production.template" "$INSTALL_DIR/.env.production"
        echo "[WARN] Created .env.production from template -- review and update values!"
    else
        echo "[WARN] No .env.production found. Create one before starting the service."
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

# ── 7. Docker Compose ───────────────────────────────────────────────────────

cd "$INSTALL_DIR"
docker compose up -d --build
echo "[OK] Docker services started"

# ── 8. Install systemd service ──────────────────────────────────────────────

cp "$INSTALL_DIR/deploy/mcp-server.service" /etc/systemd/system/mcp-server.service
systemctl daemon-reload
systemctl enable mcp-server
systemctl restart mcp-server
echo "[OK] mcp-server.service installed and started"

# ── 9. Set ownership ────────────────────────────────────────────────────────

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "=== Deployment complete ==="
echo ""
echo "Services:"
echo "  Dashboard + MCP API:  http://$(hostname -I | awk '{print $1}'):8001"
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
echo "  5. Open firewall:   ufw allow 3000/tcp && ufw allow 8001/tcp"
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
