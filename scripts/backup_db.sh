#!/usr/bin/env bash
# Snapshot the database and uploaded media into backups/.
#
# Usage: make backup
# Schedule (documented, not installed): 30 23 * * * cd <repo> && make backup >> backups/backup.log 2>&1
set -euo pipefail

RETENTION_DAYS=14
POSTGRES_DB="${POSTGRES_DB:-spicy}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"

cd "$(dirname "$0")/.."
mkdir -p backups

TS=$(date +%Y-%m-%d-%H%M%S)
DB_DUMP="backups/spicy-${TS}.sql.gz"
MEDIA_TAR="backups/media-${TS}.tar.gz"

docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$DB_DUMP"
tar -czf "$MEDIA_TAR" media/

# Keep 14 daily snapshots; snapshot files only, never the log or drill records.
find backups/ -maxdepth 1 -name 'spicy-*.sql.gz' -mtime +"$RETENTION_DAYS" -delete
find backups/ -maxdepth 1 -name 'media-*.tar.gz' -mtime +"$RETENTION_DAYS" -delete

echo "Database snapshot: $(readlink -f "$DB_DUMP")"
echo "Media snapshot: $(readlink -f "$MEDIA_TAR")"
