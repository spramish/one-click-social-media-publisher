#!/usr/bin/env sh
# ===========================================================================
# Database restore — One-Click Social Media Video Publisher
# Author: Pramish Sharma
#
#   ./deploy/restore.sh backups/publisher_db_20261122T101500Z.sql.gz
#
# DESTRUCTIVE: the dump is applied with --clean --if-exists, so objects it
# contains are dropped and recreated. Stop the backend, worker and beat first
# so nothing writes while the schema is being replaced. See docs/DEPLOYMENT.md.
# ===========================================================================
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <path-to-backup.sql.gz>" >&2
    exit 2
fi

DUMP="$1"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

if [ ! -f "$DUMP" ]; then
    echo "no such backup: $DUMP" >&2
    exit 2
fi

if [ -f .env ]; then
    # shellcheck disable=SC1091
    . ./.env
fi
: "${POSTGRES_USER:=publisher}"
: "${POSTGRES_DB:=publisher_db}"

echo "!! This replaces the contents of ${POSTGRES_DB} on the running stack."
printf "   Type the database name to confirm: "
read -r CONFIRM
if [ "$CONFIRM" != "$POSTGRES_DB" ]; then
    echo "aborted." >&2
    exit 1
fi

echo "==> stopping writers"
docker compose -f "$COMPOSE_FILE" stop backend worker beat

echo "==> restoring ${DUMP}"
gunzip -c "$DUMP" | docker compose -f "$COMPOSE_FILE" exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"

echo "==> bringing writers back up"
docker compose -f "$COMPOSE_FILE" start backend worker beat

echo "==> done. Verify with: docker compose -f $COMPOSE_FILE exec backend alembic current"
