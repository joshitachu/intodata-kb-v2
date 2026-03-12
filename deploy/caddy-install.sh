#!/usr/bin/env bash
set -euo pipefail

# Install Caddy reverse proxy with auto-TLS
# Usage: sudo bash deploy/caddy-install.sh <domain>
# Example: sudo bash deploy/caddy-install.sh myapp.duckdns.org

DOMAIN="${1:-}"
INSTALL_DIR="/opt/intodata-kb"

if [ -z "$DOMAIN" ]; then
    echo "Usage: $0 <domain>"
    echo "Example: $0 myapp.duckdns.org"
    exit 1
fi

echo "=== Caddy Installation ==="
echo "Domain: $DOMAIN"
echo ""

# ── 1. Install Caddy ────────────────────────────────────────────────────────

if ! command -v caddy &>/dev/null; then
    apt-get update
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
    apt-get update
    apt-get install -y caddy
    echo "[OK] Caddy installed"
else
    echo "[OK] Caddy already installed"
fi

# ── 2. Copy Caddyfile ───────────────────────────────────────────────────────

cp "$INSTALL_DIR/Caddyfile" /etc/caddy/Caddyfile

# Set the domain as environment variable for Caddy
mkdir -p /etc/caddy
cat > /etc/caddy/caddy.env <<EOF
CADDY_DOMAIN=$DOMAIN
EOF

# ── 3. Configure systemd override for env file ─────────────────────────────

mkdir -p /etc/systemd/system/caddy.service.d
cat > /etc/systemd/system/caddy.service.d/override.conf <<EOF
[Service]
EnvironmentFile=/etc/caddy/caddy.env
EOF

# ── 4. Enable and start ─────────────────────────────────────────────────────

systemctl daemon-reload
systemctl enable caddy
systemctl restart caddy

echo ""
echo "[OK] Caddy configured for $DOMAIN"
echo ""
echo "Verify with: curl -I https://$DOMAIN"
echo "Logs: journalctl -u caddy -f"
