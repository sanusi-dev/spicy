#!/usr/bin/env bash
# Restore a database snapshot produced by scripts/backup_db.sh.
#
# Usage: make restore ARGS='backups/spicy-<ts>.sql.gz [--yes] [--with-media backups/media-<ts>.tar.gz]'
set -euo pipefail

POSTGRES_DB="${POSTGRES_DB:-spicy}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"

cd "$(dirname "$0")/.."

DUMP=""
ASSUME_YES=0
MEDIA_TAR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --yes)
            ASSUME_YES=1
            shift
            ;;
        --with-media)
            MEDIA_TAR="${2:-}"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            if [ -z "$DUMP" ]; then
                DUMP="$1"
            else
                echo "Unexpected argument: $1" >&2
                exit 1
            fi
            shift
            ;;
    esac
done

if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
    echo "Usage: $0 <dump.sql.gz> [--yes] [--with-media <media.tar.gz>]" >&2
    exit 1
fi
if [ -n "$MEDIA_TAR" ] && [ ! -f "$MEDIA_TAR" ]; then
    echo "Media archive not found: $MEDIA_TAR" >&2
    exit 1
fi

if ! docker compose exec -T db pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; then
    echo "The db container is not running. Start it first (make start-bg), then retry." >&2
    exit 1
fi

echo "WARNING: stop the dev server before restoring (make stop), then re-run this script."
echo "This will DROP and recreate the '$POSTGRES_DB' database from: $DUMP"
if [ "$ASSUME_YES" -ne 1 ]; then
    printf "Type 'yes' to continue: "
    read -r answer
    if [ "$answer" != "yes" ]; then
        echo "Aborted."
        exit 1
    fi
fi

docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$POSTGRES_DB' AND pid <> pg_backend_pid();"
docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE \"$POSTGRES_DB\""
docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE \"$POSTGRES_DB\""
gunzip -c "$DUMP" | docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"
uv run manage.py migrate

if [ -n "$MEDIA_TAR" ]; then
    mkdir -p media
    tar -xzf "$MEDIA_TAR" -C .
    echo "Media restored from: $(readlink -f "$MEDIA_TAR")"
fi

echo "Restore complete: $(readlink -f "$DUMP") -> database '$POSTGRES_DB'"
