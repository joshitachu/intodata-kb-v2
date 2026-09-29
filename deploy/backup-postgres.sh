#!/usr/bin/env bash
set -euo pipefail

# Automated PostgreSQL backup for one deployed intodata-kb client stack.
#
# Targets the root docker-compose.yml exactly as deploy/deploy-client.sh brings
# it up (`docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG"` from the repo
# root), and writes to the directory deploy/RUNBOOK.md restores from.
#
# Usage: sudo ./backup-postgres.sh <client-slug>
#        (or set CLIENT_SLUG; ENV_FILE, REPO_ROOT, BACKUP_DIR, RETENTION_DAYS
#        may be overridden from the environment)
# Cron:  0 2 * * * /opt/intodata-kb/<client>/deploy/backup-postgres.sh <client> >> /var/log/intodata-backup.log 2>&1

CLIENT_SLUG="${1:-${CLIENT_SLUG:-}}"
if [[ -z "$CLIENT_SLUG" ]]; then
    echo "Usage: $0 <client-slug>   (or set CLIENT_SLUG)" >&2
    exit 2
fi

ENV_FILE="${ENV_FILE:-/etc/intodata-kb/envs/${CLIENT_SLUG}.env}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/intodata-kb}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

[[ -f "$ENV_FILE" ]] || {
    echo "[$(date)] ERROR: env file not found: $ENV_FILE" >&2
    exit 2
}

# Read one KEY=value from the client env file (last occurrence wins, as in
# docker compose). The env file is not sourced: it is data, not a script.
env_value() {
    grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true
}

# deploy-client.sh records REPO_ROOT in the env file; fall back to the checkout
# this script lives in.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(env_value REPO_ROOT)}"
REPO_ROOT="${REPO_ROOT:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
[[ -f "$REPO_ROOT/docker-compose.yml" ]] || {
    echo "[$(date)] ERROR: docker-compose.yml not found under $REPO_ROOT" >&2
    exit 2
}

# Same defaults as the postgres service in docker-compose.yml.
DB_NAME="$(env_value POSTGRES_DB)"
DB_NAME="${DB_NAME:-intodata_kb}"
DB_USER="$(env_value POSTGRES_USER)"
DB_USER="${DB_USER:-admin}"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql.gz"
TMP_FILE="$BACKUP_FILE.partial"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
umask 077
trap 'rm -f "$TMP_FILE"' EXIT

echo "[$(date)] Starting backup of $DB_NAME (project=$CLIENT_SLUG)..."

# pg_dump inside the compose-managed postgres service. -T: no TTY under cron.
# --clean --if-exists: the dump drops each object before recreating it, so a
# restore into the damaged database replaces it instead of clashing.
# pipefail makes a pg_dump failure fail the pipeline, not just gzip's status.
if ! (cd "$REPO_ROOT" && docker compose --env-file "$ENV_FILE" -p "$CLIENT_SLUG" \
        exec -T postgres pg_dump --clean --if-exists -U "$DB_USER" "$DB_NAME") | gzip > "$TMP_FILE"; then
    echo "[$(date)] ERROR: pg_dump failed; no backup written" >&2
    exit 1
fi

if [[ -s "$TMP_FILE" ]]; then
    mv "$TMP_FILE" "$BACKUP_FILE"
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "[$(date)] Backup complete: $BACKUP_FILE ($SIZE)"
else
    echo "[$(date)] ERROR: Backup file is empty, removing" >&2
    exit 1
fi

# Remove this database's backups older than the retention period.
DELETED=$(find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime +"${RETENTION_DAYS}" -print -delete | wc -l)
if [[ "$DELETED" -gt 0 ]]; then
    echo "[$(date)] Removed $DELETED backup(s) older than $RETENTION_DAYS days"
fi
