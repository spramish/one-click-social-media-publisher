#!/usr/bin/env sh
# ===========================================================================
# Database backup — One-Click Social Media Video Publisher
# Author: Pramish Sharma
#
#   ./deploy/backup.sh [output-dir]
#
# Dumps the production database through the compose stack into a timestamped,
# gzip-compressed file and prunes anything older than BACKUP_RETENTION_DAYS.
# Run it from cron (see docs/DEPLOYMENT.md) and copy the result off the host —
# a backup that only exists on the same disk is not a backup.
# ===========================================================================
set -eu

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
OUTPUT_DIR="${1:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

# Read the same credentials the stack uses.
if [ -f .env ]; then
    # shellcheck disable=SC1091
    . ./.env
fi
: "${POSTGRES_USER:=publisher}"
: "${POSTGRES_DB:=publisher_db}"

mkdir -p "$OUTPUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$OUTPUT_DIR/${POSTGRES_DB}_${STAMP}.sql.gz"

echo "==> dumping ${POSTGRES_DB} to ${TARGET}"
docker compose -f "$COMPOSE_FILE" exec -T postgres \
    pg_dump --clean --if-exists --no-owner --no-privileges \
    -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip -9 > "$TARGET"

echo "==> pruning backups older than ${RETENTION_DAYS} days"
find "$OUTPUT_DIR" -name "${POSTGRES_DB}_*.sql.gz" -type f \
    -mtime "+${RETENTION_DAYS}" -delete

echo "==> done: $(du -h "$TARGET" | cut -f1)"
