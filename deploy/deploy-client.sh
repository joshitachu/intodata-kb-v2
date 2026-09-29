#!/usr/bin/env bash
#
# deploy-client.sh — stand up a fully-isolated IntoData KB stack for a client.
#
# Per the saved tenancy decision this targets one VPS per client in production
# (so a port offset of 0 is fine), but the script also supports running multiple
# stacks on a single host via a non-zero --port-offset, which is useful for
# dev/staging boxes.
#
# Usage:
#   deploy/deploy-client.sh \
#       --client-name acme \
#       --port-offset 0 \
#       --base-domain nexus.intodata.nl \
#       --admin-email ops@intodata.nl \
#       --initial-admin-email owner@acme.example
#
# The script is idempotent: re-running with the same --client-name updates the
# rendered nginx block and env file, then re-applies the compose stack.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: deploy-client.sh --client-name <slug> [options]

Required:
  --client-name <slug>       Short identifier, used as compose project name,
                             env filename, and subdomain prefix.

Optional:
  --port-offset <int>        Added to every default port. Use 0 on a dedicated
                             VPS. Default: 0.
  --base-domain <host>       Base domain for the client subdomain.
                             Default: nexus.intodata.nl
  --admin-email <email>      Email used by certbot for ACME registration.
                             Default: support@intodata.nl
  --initial-admin-email <email>
                             Email seeded for the first Nexus admin account.
                             Default: admin@<client>.<base-domain>
  --no-certbot               Do not print the certbot command. Use when DNS is
                             not ready yet or on CI.
  --env-dir <path>           Directory for rendered env files.
                             Default: /etc/intodata-kb/envs
  --allow-view-only          Re-deploy an EXISTING client even though no licence
                             is installed (the instance comes up view-only).
                             Without it the script aborts, so a live customer
                             is never locked out by an upgrade.
  --licence-root <path>      Parent of the per-client licence directories.
                             The licence goes in <path>/<slug>/licence.jwt.
                             Default: /etc/intodata-kb/licences
  --nginx-sites-available    Path to nginx sites-available.
                             Default: /etc/nginx/sites-available
  --nginx-sites-enabled      Path to nginx sites-enabled.
                             Default: /etc/nginx/sites-enabled
  --repo-root <path>         Repository root containing docker-compose.yml.
                             Default: script's ../.
  --ops-telemetry-url <url>  Central Mission Control base URL this instance
                             heartbeats to (its MC_PUBLIC_URL). Written to
                             MCP_OPS_TELEMETRY_URL, replacing any old value.
                             https:// required (http only for localhost).
  --ops-telemetry-key-file <path>
                             File whose first line is this client's telemetry
                             key from the Fleet screen ("Add client" / "Rotate
                             key"). Written to MCP_OPS_TELEMETRY_API_KEY,
                             replacing any old value; never printed. Preferred:
                             the key stays out of argv, `ps` and shell history.
                             Without either key flag, a non-empty
                             OPS_TELEMETRY_KEY environment variable is used
                             instead (under sudo: sudo
                             --preserve-env=OPS_TELEMETRY_KEY ...). Any key
                             source, or --ops-telemetry-url, also sets
                             MCP_OPS_CLIENT_SLUG to --client-name.
  --ops-telemetry-key <key>  Same, with the key on the command line. Still
                             accepted, but it is visible in `ps`, shell
                             history and sudo's auth log; prefer the file or
                             the environment variable.
  --fleet-host               THIS host runs the central Mission Control: render
                             the public heartbeat route (POST
                             /ops/clients/<slug>/telemetry only) into this
                             client's nginx block. Never on a client VPS.
                             Requires --fleet-mc-port.
  --fleet-mc-port <port>     The port the central Mission Control publishes on
                             127.0.0.1 (its MC_PORT, e.g. 9100). Required with
                             --fleet-host: it is NOT this client stack's own
                             MC_PORT, so there is no default.
  --help                     Show this help and exit.
EOF
}

# Captured before the option variables below are reset: the key may come from
# the environment instead of argv (see --ops-telemetry-key-file).
OPS_TELEMETRY_KEY_ENV="${OPS_TELEMETRY_KEY-}"
unset OPS_TELEMETRY_KEY

CLIENT=""
PORT_OFFSET=0
BASE_DOMAIN="nexus.intodata.nl"
ADMIN_EMAIL="support@intodata.nl"
INITIAL_ADMIN_EMAIL=""
RUN_CERTBOT=1
ENV_DIR="/etc/intodata-kb/envs"
LICENCE_ROOT="/etc/intodata-kb/licences"
ALLOW_VIEW_ONLY=0
NGINX_AVAILABLE="/etc/nginx/sites-available"
NGINX_ENABLED="/etc/nginx/sites-enabled"
REPO_ROOT=""
OPS_TELEMETRY_URL=""
OPS_TELEMETRY_KEY=""
OPS_TELEMETRY_URL_SET=0
OPS_TELEMETRY_KEY_SET=0
OPS_TELEMETRY_KEY_ARG_SET=0
OPS_TELEMETRY_KEY_FILE=""
OPS_TELEMETRY_KEY_FILE_SET=0
FLEET_HOST=0
FLEET_MC_PORT=""

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT_DEFAULT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --client-name) CLIENT="$2"; shift 2 ;;
        --port-offset) PORT_OFFSET="$2"; shift 2 ;;
        --base-domain) BASE_DOMAIN="$2"; shift 2 ;;
        --admin-email) ADMIN_EMAIL="$2"; shift 2 ;;
        --initial-admin-email) INITIAL_ADMIN_EMAIL="$2"; shift 2 ;;
        --no-certbot) RUN_CERTBOT=0; shift ;;
        --env-dir) ENV_DIR="$2"; shift 2 ;;
        --licence-root) LICENCE_ROOT="$2"; shift 2 ;;
        --allow-view-only) ALLOW_VIEW_ONLY=1; shift ;;
        --nginx-sites-available) NGINX_AVAILABLE="$2"; shift 2 ;;
        --nginx-sites-enabled) NGINX_ENABLED="$2"; shift 2 ;;
        --repo-root) REPO_ROOT="$2"; shift 2 ;;
        --ops-telemetry-url) OPS_TELEMETRY_URL="${2-}"; OPS_TELEMETRY_URL_SET=1; shift 2 ;;
        --ops-telemetry-key) OPS_TELEMETRY_KEY="${2-}"; OPS_TELEMETRY_KEY_ARG_SET=1; shift 2 ;;
        --ops-telemetry-key-file) OPS_TELEMETRY_KEY_FILE="${2-}"; OPS_TELEMETRY_KEY_FILE_SET=1; shift 2 ;;
        --fleet-host) FLEET_HOST=1; shift ;;
        --fleet-mc-port) FLEET_MC_PORT="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
    esac
done

[[ -n "$CLIENT" ]] || { echo "--client-name is required" >&2; exit 2; }
[[ "$CLIENT" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || {
    echo "Invalid --client-name: must match ^[a-z0-9][a-z0-9-]{0,62}$" >&2
    exit 2
}
[[ "$PORT_OFFSET" =~ ^[0-9]+$ ]] || {
    echo "--port-offset must be a non-negative integer" >&2
    exit 2
}
INITIAL_ADMIN_EMAIL="${INITIAL_ADMIN_EMAIL:-admin@$CLIENT.$BASE_DOMAIN}"

# Heartbeat settings are pasted into the env file verbatim, so refuse anything
# that is not plainly a URL / token before a single file is written.
TELEMETRY_URL_HTTPS='^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$'
TELEMETRY_URL_LOCAL='^http://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$'
if (( OPS_TELEMETRY_URL_SET )); then
    OPS_TELEMETRY_URL="${OPS_TELEMETRY_URL%/}"
    [[ "$OPS_TELEMETRY_URL" =~ $TELEMETRY_URL_HTTPS || "$OPS_TELEMETRY_URL" =~ $TELEMETRY_URL_LOCAL ]] || {
        echo "Invalid --ops-telemetry-url: must be https://<host>[:port][/path] (http only for localhost), without credentials, query or fragment" >&2
        exit 2
    }
fi
# The telemetry key: from a file (preferred), the environment, or argv (legacy).
# The value is never echoed, whatever the source.
if (( OPS_TELEMETRY_KEY_ARG_SET && OPS_TELEMETRY_KEY_FILE_SET )); then
    echo "Give either --ops-telemetry-key-file or --ops-telemetry-key, not both" >&2
    exit 2
fi
OPS_TELEMETRY_KEY_SOURCE=""
if (( OPS_TELEMETRY_KEY_FILE_SET )); then
    OPS_TELEMETRY_KEY_SOURCE="--ops-telemetry-key-file"
    [[ -n "$OPS_TELEMETRY_KEY_FILE" && -f "$OPS_TELEMETRY_KEY_FILE" && -r "$OPS_TELEMETRY_KEY_FILE" ]] || {
        echo "--ops-telemetry-key-file: '$OPS_TELEMETRY_KEY_FILE' is not a readable file" >&2
        exit 2
    }
    OPS_TELEMETRY_KEY=""
    IFS= read -r OPS_TELEMETRY_KEY < "$OPS_TELEMETRY_KEY_FILE" || true  # a last line without \n still fills it
    OPS_TELEMETRY_KEY="${OPS_TELEMETRY_KEY%$'\r'}"
    OPS_TELEMETRY_KEY_SET=1
elif (( OPS_TELEMETRY_KEY_ARG_SET )); then
    OPS_TELEMETRY_KEY_SOURCE="--ops-telemetry-key"
    OPS_TELEMETRY_KEY_SET=1
    echo "WARNING: --ops-telemetry-key puts the key in the process list, shell history and sudo's log." >&2
    echo "         Prefer --ops-telemetry-key-file <path> or the OPS_TELEMETRY_KEY environment variable." >&2
elif [[ -n "$OPS_TELEMETRY_KEY_ENV" ]]; then
    OPS_TELEMETRY_KEY_SOURCE="OPS_TELEMETRY_KEY"
    OPS_TELEMETRY_KEY="$OPS_TELEMETRY_KEY_ENV"
    OPS_TELEMETRY_KEY_SET=1
fi
unset OPS_TELEMETRY_KEY_ENV
if (( OPS_TELEMETRY_KEY_SET )); then
    # (Length checked separately: some regex engines cap {m,n} at 255.)
    [[ "$OPS_TELEMETRY_KEY" =~ ^[A-Za-z0-9._~+/=-]{16,}$ && ${#OPS_TELEMETRY_KEY} -le 512 ]] || {
        echo "Invalid telemetry key (from $OPS_TELEMETRY_KEY_SOURCE): expected the 16-512 character key shown in the Fleet screen" >&2
        exit 2
    }
fi

# --fleet-host proxies the public heartbeat route to the CENTRAL Mission
# Control, whose port is unrelated to this stack's offset; guessing it would
# silently send every client's heartbeat to the wrong (or no) service.
if (( FLEET_HOST )) && [[ -z "$FLEET_MC_PORT" ]]; then
    echo "--fleet-host requires --fleet-mc-port <port>: the port the central Mission Control publishes on 127.0.0.1 (its MC_PORT, e.g. 9100)" >&2
    exit 2
fi
if [[ -n "$FLEET_MC_PORT" ]]; then
    if ! [[ "$FLEET_MC_PORT" =~ ^[0-9]{1,5}$ ]] || (( 10#$FLEET_MC_PORT < 1 || 10#$FLEET_MC_PORT > 65535 )); then
        echo "--fleet-mc-port must be a TCP port number" >&2
        exit 2
    fi
    FLEET_MC_PORT=$(( 10#$FLEET_MC_PORT ))
fi

REPO_ROOT="${REPO_ROOT:-$REPO_ROOT_DEFAULT}"
[[ -f "$REPO_ROOT/docker-compose.yml" ]] || {
    echo "docker-compose.yml not found under $REPO_ROOT" >&2
    exit 2
}

rand_hex() {
    openssl rand -hex "${1:-32}"
}

# A Fernet key is urlsafe-base64 of 32 random bytes. Generating it with openssl
# keeps this script free of a host Python virtualenv, which RUNBOOK.md step 3
# explicitly says is not required for a deployed stack.
fernet_key() {
    openssl rand -base64 32 | tr '+/' '-_'
}

# --- Compute per-client port assignments ---
export CLIENT
export NEXUS_BASE="$BASE_DOMAIN"
export POSTGRES_PORT=$(( 5432 + PORT_OFFSET ))
export QDRANT_HTTP_PORT=$(( 6333 + PORT_OFFSET ))
export QDRANT_GRPC_PORT=$(( 6334 + PORT_OFFSET ))
export HINDSIGHT_API_PORT=$(( 8888 + PORT_OFFSET ))
export HINDSIGHT_INTERNAL_PORT=$(( 9999 + PORT_OFFSET ))
export BACKEND_PORT=$(( 8000 + PORT_OFFSET ))
export FRONTEND_PORT=$(( 3000 + PORT_OFFSET ))
export DASHBOARD_PORT=$(( 8001 + PORT_OFFSET ))
export MCP_PORT=$(( 8002 + PORT_OFFSET ))
# mission-control binds 127.0.0.1 only, but it still needs the offset so two
# stacks on one host don't collide on 9100.
export MC_PORT=$(( 9100 + PORT_OFFSET ))

# Subscription licence. The directory (not the file) is bind-mounted read-only
# into backend and mcp-server at /etc/intodata-kb/licence, so dropping in a
# renewed licence.jwt is picked up on the next request without a restart.
LICENCE_DIR="$LICENCE_ROOT/$CLIENT"
LICENCE_FILE_HOST="$LICENCE_DIR/licence.jwt"

# --- Render (or merge) the env file ---
mkdir -p "$ENV_DIR"
ENV_FILE="$ENV_DIR/$CLIENT.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Generating fresh env file at $ENV_FILE"
    JWT_SECRET="$(rand_hex 32)"
    POSTGRES_PASSWORD="$(rand_hex 24)"
    MCP_INGEST_API_KEY="$(rand_hex 32)"
    ADMIN_DEFAULT_PASSWORD="$(rand_hex 16)"
    MCP_TOKEN_ENCRYPTION_KEY="$(fernet_key)"
    SOURCE_CREDENTIALS_ENCRYPTION_KEY="$(fernet_key)"
    MC_API_KEY="$(rand_hex 32)"
    MCP_OPS_CONTROL_KEY="$(rand_hex 32)"

    cat > "$ENV_FILE" <<EOF
# Generated by deploy-client.sh for $CLIENT on $(date -Iseconds)
TENANT_NAME=$CLIENT
TENANT_SLUG=$CLIENT
TENANT_ENVIRONMENT=production
CLIENT_SLUG=$CLIENT

# Auth (shared by backend + MCP — the KEYS must match)
JWT_SECRET_KEY=$JWT_SECRET
MCP_JWT_SECRET_KEY=$JWT_SECRET
ADMIN_DEFAULT_PASSWORD=$ADMIN_DEFAULT_PASSWORD
ADMIN_INITIAL_EMAIL=$INITIAL_ADMIN_EMAIL
COOKIE_SECURE=true
COOKIE_DOMAIN=$CLIENT.$BASE_DOMAIN
ALLOWED_ORIGINS=https://$CLIENT.$BASE_DOMAIN
PUBLIC_APP_URL=https://$CLIENT.$BASE_DOMAIN
# nginx on the host plus the frontend container both sit in front of FastAPI;
# the Compose bridge range must be trusted or every rate limit collapses into a
# single bucket keyed on the frontend container. See .env.production.template.
TRUSTED_PROXIES=127.0.0.1,172.16.0.0/12

# Runtime source credentials (Fernet). Compose fails fast without this key
# because runtime source credentials are stored encrypted at rest, so it is
# required even when no runtime source is configured yet. Rotation: move the
# current value to SOURCE_CREDENTIALS_ENCRYPTION_KEY_PREVIOUS, set a new primary,
# restart the backend, then re-save each source to re-encrypt.
SOURCE_CREDENTIALS_ENCRYPTION_KEY=$SOURCE_CREDENTIALS_ENCRYPTION_KEY

# Repository root used by host-level services
REPO_ROOT=$REPO_ROOT

# MCP ingest auth (server-to-server)
MCP_INGEST_API_KEY=$MCP_INGEST_API_KEY
MCP_TOKEN_ENCRYPTION_KEY=$MCP_TOKEN_ENCRYPTION_KEY
MCP_PORT=$MCP_PORT
MCP_DATABASE_URL=postgresql+asyncpg://admin:$POSTGRES_PASSWORD@postgres:5432/intodata_kb_$CLIENT
MCP_RAG_BASE_URL=http://backend:8000
MCP_PUBLIC_URL=https://$CLIENT.$BASE_DOMAIN
MCP_GITHUB_WEBHOOK_SECRET=
MCP_DATA_DIR=/app/data
MCP_INTODOC_OUTPUT_DIR=/app/intodoc-output
MCP_PROJECT_ROOT=/app/workspace
MCP_INTODOC_COMMAND=intodoc
MCP_HINDSIGHT_ENABLED=true
MCP_HINDSIGHT_URL=http://hindsight:8888
MCP_LOG_LEVEL=INFO
MCP_OPS_CLIENT_SLUG=$CLIENT
MCP_OPS_ENVIRONMENT=production
MCP_OPS_RELEASE_VERSION=
MCP_OPS_TELEMETRY_URL=
MCP_OPS_TELEMETRY_API_KEY=
MCP_OPS_TELEMETRY_INTERVAL_SECONDS=300
# Bearer key Mission Control uses to reach /ops/control/* on this instance
# (scan, generate, licence push). Copy it into the central Mission Control's
# clients.json entry for this client. Rotate by replacing it here and there.
MCP_OPS_CONTROL_KEY=$MCP_OPS_CONTROL_KEY
# Optional edge allowlist for /ops/control/: comma-separated IPs or CIDRs of
# the central Mission Control host. Empty = no IP restriction (the bearer key
# still applies). Rendered into the nginx block by this script.
OPS_CONTROL_ALLOWED_IPS=

# Subscription licence. ONE set of vars; docker-compose.yml maps it onto both
# the backend (LICENCE_*) and mcp-server (MCP_LICENCE_*). Production refuses to
# start unless enforcement is 'enforce'. LICENCE_FILE is the path INSIDE the
# containers; LICENCE_DIR is the host directory mounted there read-only.
# The operator's signing key must NEVER be added here (deploy aborts if it is).
LICENCE_ENFORCEMENT=enforce
LICENCE_CLIENT_SLUG=$CLIENT
LICENCE_DIR=$LICENCE_DIR
LICENCE_FILE=/etc/intodata-kb/licence/licence.jwt

# Ports
POSTGRES_PORT=$POSTGRES_PORT
QDRANT_HTTP_PORT=$QDRANT_HTTP_PORT
QDRANT_GRPC_PORT=$QDRANT_GRPC_PORT
HINDSIGHT_API_PORT=$HINDSIGHT_API_PORT
HINDSIGHT_INTERNAL_PORT=$HINDSIGHT_INTERNAL_PORT
BACKEND_PORT=$BACKEND_PORT
FRONTEND_PORT=$FRONTEND_PORT
DASHBOARD_PORT=$DASHBOARD_PORT

# mission-control (operator-side heartbeat aggregator, bound to 127.0.0.1 only;
# not part of the seven-service health boundary)
MC_PORT=$MC_PORT
MC_API_KEY=$MC_API_KEY

# Databases
POSTGRES_DB=intodata_kb_$CLIENT
POSTGRES_USER=admin
POSTGRES_PASSWORD=$POSTGRES_PASSWORD

# Qdrant collection (one-VPS-per-client still benefits from a unique name in
# case of future cross-stack tooling)
QDRANT_COLLECTION=${CLIENT}_documents

# Network hardening. TRUSTED_PROXIES is set once, above, with the Compose bridge
# range included — do not repeat it here: a later duplicate wins in an env file
# and silently collapses every per-IP rate limit into one bucket.
ALLOWED_IPS=
MCP_ALLOWED_IPS=
MCP_TRUSTED_PROXIES=127.0.0.1
MCP_APP_ENV=production

# App
APP_ENV=production
LOG_LEVEL=INFO

# SMTP
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_ADDRESS=
SMTP_FROM_NAME=IntoData Nexus
SMTP_USE_TLS=true
EOF
    chmod 600 "$ENV_FILE"
    echo "Wrote $ENV_FILE (chmod 600)"
else
    echo "Env file already exists at $ENV_FILE — leaving secrets in place"
    EXISTING_CLIENT=1
fi

# --- Backfill keys added after the env file was first generated ---
# Compose fails fast on a missing SOURCE_CREDENTIALS_ENCRYPTION_KEY, so an env
# file written by an older revision of this script would break `up -d` on
# upgrade. Backfill any missing key rather than expecting a hand edit. Existing
# values are never touched, so this stays safe to re-run.
ensure_env_var() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" "$ENV_FILE"; then
        return 0
    fi
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    echo "Backfilled $key in $ENV_FILE"
}

ensure_env_var SOURCE_CREDENTIALS_ENCRYPTION_KEY "$(fernet_key)"
ensure_env_var MC_PORT "$MC_PORT"
ensure_env_var MC_API_KEY "$(rand_hex 32)"
ensure_env_var LICENCE_ENFORCEMENT enforce
ensure_env_var LICENCE_CLIENT_SLUG "$CLIENT"
ensure_env_var LICENCE_DIR "$LICENCE_DIR"
ensure_env_var LICENCE_FILE /etc/intodata-kb/licence/licence.jwt
ensure_env_var MCP_OPS_CONTROL_KEY "$(rand_hex 32)"
ensure_env_var OPS_CONTROL_ALLOWED_IPS ""
ensure_env_var MCP_OPS_CLIENT_SLUG "$CLIENT"
ensure_env_var MCP_OPS_TELEMETRY_URL ""
ensure_env_var MCP_OPS_TELEMETRY_API_KEY ""

# --- Explicitly passed values replace what is there ---
# ensure_env_var only backfills a MISSING key; a flag the operator passes must
# win over an existing (possibly empty) value. Rewrites the file through a temp
# file in the same directory (mode 600, then mv), keeps the first occurrence's
# position, drops duplicates (a later duplicate would win in an env file), and
# never prints the value. awk reads the value from ENVIRON, not -v, so
# backslashes in it are not interpreted.
set_env_var() {
    local key="$1"
    local value="$2"
    local tmp
    tmp="$(mktemp "$ENV_FILE.XXXXXX")"
    chmod 600 "$tmp"
    if ! SET_KEY="$key" SET_VALUE="$value" awk '
        BEGIN { k = ENVIRON["SET_KEY"]; v = ENVIRON["SET_VALUE"]; done = 0 }
        index($0, k "=") == 1 { if (!done) { print k "=" v; done = 1 }; next }
        { print }
        END { if (!done) print k "=" v }
    ' "$ENV_FILE" > "$tmp"; then
        rm -f "$tmp"
        echo "Failed to update $key in $ENV_FILE" >&2
        exit 2
    fi
    mv "$tmp" "$ENV_FILE"
    echo "Set $key in $ENV_FILE"
}

if (( OPS_TELEMETRY_URL_SET )); then
    set_env_var MCP_OPS_TELEMETRY_URL "$OPS_TELEMETRY_URL"
fi
if (( OPS_TELEMETRY_KEY_SET )); then
    set_env_var MCP_OPS_TELEMETRY_API_KEY "$OPS_TELEMETRY_KEY"
fi
if (( OPS_TELEMETRY_URL_SET || OPS_TELEMETRY_KEY_SET )); then
    # A telemetry key authorises exactly one slug; the sender must use it.
    set_env_var MCP_OPS_CLIENT_SLUG "$CLIENT"
fi

# --- The licence signing key must never reach a client stack ---
# Licences are signed on the operator machine only. If the private key (or the
# variable pointing at it) is in any file this stack reads, anyone with access
# to this VPS could mint licences for every client. Abort, loudly.
signing_key_leaks() {
    local file
    for file in "$@"; do
        [[ -f "$file" ]] || continue
        if grep -Eiq '^[[:space:]]*(export[[:space:]]+)?[A-Z0-9_]*SIGNING_KEY[A-Z0-9_]*[[:space:]]*=' "$file"; then
            echo "$file: contains a *SIGNING_KEY* variable" >&2
            return 0
        fi
        if grep -q -- '-----BEGIN [A-Z ]*PRIVATE KEY-----' "$file"; then
            echo "$file: contains a PEM private key" >&2
            return 0
        fi
    done
    return 1
}
CONNECTOR_ENV_FILE="$(grep -E '^NEXUS_CONNECTOR_ENV_FILE=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
CONNECTOR_ENV_FILE="${CONNECTOR_ENV_FILE:-$REPO_ROOT/deploy/connectors.env}"
if signing_key_leaks "$ENV_FILE" "$CONNECTOR_ENV_FILE" "$LICENCE_DIR"/*; then
    cat >&2 <<LEAK
ABORTING: licence signing material found in a file this client stack reads.
The licence signing key (any *SIGNING_KEY* variable, e.g.
MC_LICENCE_SIGNING_KEY_FILE, and the PEM itself) belongs on the operator
machine only. Remove it from the
file named above, rotate the key if it was ever deployed (deploy/RUNBOOK.md,
"Issue or renew a subscription licence"), then re-run this script.
LEAK
    exit 3
fi

# --- Render nginx server block ---
NGINX_TEMPLATE="$SCRIPT_DIR/nginx/nexus-client.conf.template"
[[ -f "$NGINX_TEMPLATE" ]] || {
    echo "Missing template: $NGINX_TEMPLATE" >&2
    exit 2
}

mkdir -p "$NGINX_AVAILABLE" "$NGINX_ENABLED"
NGINX_DEST="$NGINX_AVAILABLE/nexus-$CLIENT.conf"

# /ops/control/ allowlist, read from the env file (the one place it is set).
# Each entry must look like an IP or CIDR: the value is pasted into nginx
# config, so anything else is refused rather than rendered.
env_file_value() {
    local line
    line="$(grep -E "^${1}=" "$ENV_FILE" | tail -n 1 || true)"
    line="${line#*=}"
    # Tolerate KEY="value" / KEY='value' as well as the unquoted form written here.
    line="${line//\"/}"
    line="${line//\'/}"
    printf '%s' "$line"
}
OPS_CONTROL_ALLOWED_IPS="$(env_file_value OPS_CONTROL_ALLOWED_IPS)"
OPS_CONTROL_ALLOW=""
# Whole-value check first, so the unquoted split below can never glob.
[[ "$OPS_CONTROL_ALLOWED_IPS" =~ ^[0-9A-Fa-f.:/,[:space:]]*$ ]] || {
    echo "Invalid OPS_CONTROL_ALLOWED_IPS in $ENV_FILE: '$OPS_CONTROL_ALLOWED_IPS'" >&2
    exit 2
}
for entry in $(printf '%s' "$OPS_CONTROL_ALLOWED_IPS" | tr ',' ' '); do
    [[ "$entry" =~ ^[0-9A-Fa-f.:]+(/[0-9]{1,3})?$ ]] || {
        echo "Invalid OPS_CONTROL_ALLOWED_IPS entry in $ENV_FILE: '$entry'" >&2
        exit 2
    }
    OPS_CONTROL_ALLOW+="allow $entry;"$'\n        '
done
# "deny all" only after at least one allow; an empty list means no restriction.
[[ -z "$OPS_CONTROL_ALLOW" ]] || OPS_CONTROL_ALLOW+="deny all;"
export OPS_CONTROL_ALLOW

# Public heartbeat route to the central Mission Control, only on the host that
# runs it (--fleet-host). Rendered from the same snippet a hand-configured host
# includes, with the loopback port rewritten; empty everywhere else.
FLEET_TELEMETRY_INGRESS=""
if (( FLEET_HOST )); then
    FLEET_SNIPPET="$SCRIPT_DIR/operator/nginx-fleet-telemetry.conf"
    [[ -f "$FLEET_SNIPPET" ]] || { echo "Missing snippet: $FLEET_SNIPPET" >&2; exit 2; }
    FLEET_TELEMETRY_INGRESS="$(cat "$FLEET_SNIPPET")"
    [[ "$FLEET_TELEMETRY_INGRESS" == *"127.0.0.1:9100"* ]] || {
        echo "$FLEET_SNIPPET: expected proxy_pass to 127.0.0.1:9100" >&2
        exit 2
    }
    FLEET_TELEMETRY_INGRESS="${FLEET_TELEMETRY_INGRESS//127.0.0.1:9100/127.0.0.1:$FLEET_MC_PORT}"
fi
export FLEET_TELEMETRY_INGRESS

# envsubst only replaces the named variables so stray $ in the template don't
# get clobbered. Listing each variable explicitly keeps this predictable.
envsubst '${CLIENT} ${NEXUS_BASE} ${BACKEND_PORT} ${FRONTEND_PORT} ${DASHBOARD_PORT} ${MCP_PORT} ${OPS_CONTROL_ALLOW} ${FLEET_TELEMETRY_INGRESS}' \
    < "$NGINX_TEMPLATE" > "$NGINX_DEST"
ln -sf "$NGINX_DEST" "$NGINX_ENABLED/nexus-$CLIENT.conf"
echo "Rendered nginx config at $NGINX_DEST"

# nginx -t can't run without root in most environments; let the operator
# decide whether to validate + reload. We print the commands.
echo ""
echo "Next steps (run as root):"
echo "  sudo nginx -t && sudo systemctl reload nginx"

if (( RUN_CERTBOT )); then
    echo "  sudo certbot --nginx -d $CLIENT.$BASE_DOMAIN --email $ADMIN_EMAIL --agree-tos -n"
fi

# --- Prepare host directories mounted into the MCP container ---
mkdir -p \
    "$REPO_ROOT/knowledge_base/mcp-server/data" \
    "$REPO_ROOT/knowledge_base/intodoc-output" \
    "$REPO_ROOT/knowledge_base/output" \
    "$REPO_ROOT/knowledge_base/packs"

# The MCP image runs as uid/gid 1001. Keep generated state writable when this
# script is run as root on a VPS.
if [[ "$(id -u)" -eq 0 ]]; then
    chown -R 1001:1001 \
        "$REPO_ROOT/knowledge_base/mcp-server/data" \
        "$REPO_ROOT/knowledge_base/intodoc-output" \
        "$REPO_ROOT/knowledge_base/output"
fi

# --- Prepare the licence directory (shared by two containers) ---
# root-owned, group 1001 (the appgroup gid of both the backend and mcp-server
# images), 0770: mcp-server (mounted read-write) must create its temp file and
# rename it over licence.jwt when Mission Control pushes a licence; the backend
# mounts it read-only; other host users get nothing. The file itself stays
# 0640, which is what install.py writes too.
mkdir -p "$LICENCE_DIR"
if [[ "$(id -u)" -eq 0 ]]; then
    chown root:1001 "$LICENCE_DIR"
    chmod 0770 "$LICENCE_DIR"
    if [[ -f "$LICENCE_FILE_HOST" ]]; then
        chown root:1001 "$LICENCE_FILE_HOST"
        chmod 0640 "$LICENCE_FILE_HOST"
    fi
else
    echo "WARNING: not running as root; set $LICENCE_DIR to root:1001 0770 by hand." >&2
fi
if [[ ! -s "$LICENCE_FILE_HOST" && "${EXISTING_CLIENT:-0}" == 1 && "$ALLOW_VIEW_ONLY" != 1 ]]; then
    cat >&2 <<ABORT

ABORTING: $CLIENT is an existing client and no licence is installed at
$LICENCE_FILE_HOST. This release enforces the subscription licence, so
bringing the stack up now would make a live customer view-only.
Install the licence first (deploy/RUNBOOK.md section 17c), or pass
--allow-view-only if a view-only instance is intended.
ABORT
    exit 4
fi
if [[ ! -s "$LICENCE_FILE_HOST" ]]; then
    cat <<NOTICE

NOTICE: no licence installed at $LICENCE_FILE_HOST.
The instance will be view-only until a licence is installed: chat and
generations return HTTP 402, existing documents stay readable. Issue one on
the operator machine and copy it here (deploy/RUNBOOK.md, "Issue or renew a
subscription licence"); no restart is needed.
NOTICE
fi

# --- Bring up the Docker stack ---
echo ""
echo "Bringing up docker-compose stack (project=$CLIENT) ..."
(
    cd "$REPO_ROOT"
    # shellcheck disable=SC2046
    docker compose --env-file "$ENV_FILE" -p "$CLIENT" up -d
)

cat <<EOF

Docker stack for '$CLIENT' is up. Verify:
     curl -fsS https://$CLIENT.$BASE_DOMAIN/health
     curl -fsS https://$CLIENT.$BASE_DOMAIN/api/v1/auth/bootstrap-status
     curl -fsS http://127.0.0.1:$MCP_PORT/health

The stack's ports (on localhost):
  postgres=$POSTGRES_PORT, qdrant=$QDRANT_HTTP_PORT, backend=$BACKEND_PORT,
  frontend=$FRONTEND_PORT, dashboard=$DASHBOARD_PORT, mcp=$MCP_PORT,
  mission-control=$MC_PORT

Env file:   $ENV_FILE
Nginx conf: $NGINX_DEST
Licence:    $LICENCE_FILE_HOST

Mission Control: add (or edit) this client in the Fleet screen with
mcp_url https://$CLIENT.$BASE_DOMAIN and the value of MCP_OPS_CONTROL_KEY
from $ENV_FILE as its control key (the key is not printed here).
Edge allowlist for /ops/control/: ${OPS_CONTROL_ALLOWED_IPS:-none (bearer key only)}
EOF

HEARTBEAT_URL="$(env_file_value MCP_OPS_TELEMETRY_URL)"
if [[ -n "$HEARTBEAT_URL" && -n "$(env_file_value MCP_OPS_TELEMETRY_API_KEY)" ]]; then
    echo "Heartbeat: to $HEARTBEAT_URL as '$(env_file_value MCP_OPS_CLIENT_SLUG)' (key set, not shown)"
else
    echo "Heartbeat: off (pass --ops-telemetry-url and --ops-telemetry-key-file; deploy/RUNBOOK.md section 18)"
fi
if (( FLEET_HOST )); then
    cat <<EOF
Fleet host: POST /ops/clients/<slug>/telemetry on $CLIENT.$BASE_DOMAIN now
proxies to Mission Control on 127.0.0.1:$FLEET_MC_PORT (after nginx reload).
Set MC_PUBLIC_URL=https://$CLIENT.$BASE_DOMAIN for that Mission Control.
EOF
fi
