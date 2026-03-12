#!/usr/bin/env bash
set -euo pipefail

# Automated PostgreSQL backup for intodata-kb
# Usage: ./backup-postgres.sh
# Cron:  0 2 * * * /opt/intodata-kb/backup-postgres.sh >> /var/log/intodata-backup.log 2>&1

BACKUP_DIR="/opt/intodata-kb/backups"
RETENTION_DAYS=14
DB_NAME="${POSTGRES_DB:-intodata_kb}"
DB_USER="${POSTGRES_USER:-admin}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[$(date)] Starting backup of $DB_NAME..."

# pg_dump via Docker container to reach the postgres service
docker exec postgres pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"

if [ -s "$BACKUP_FILE" ]; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "[$(date)] Backup complete: $BACKUP_FILE ($SIZE)"
else
    echo "[$(date)] ERROR: Backup file is empty, removing"
    rm -f "$BACKUP_FILE"
    exit 1
fi

# Remove backups older than retention period
DELETED=$(find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime +${RETENTION_DAYS} -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
    echo "[$(date)] Removed $DELETED backup(s) older than $RETENTION_DAYS days"
fi
