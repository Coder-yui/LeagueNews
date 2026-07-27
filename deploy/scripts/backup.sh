#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
ENV_FILE=${ENV_FILE:-"$PROJECT_ROOT/.env.production"}
COMPOSE_FILE="$PROJECT_ROOT/deploy/docker-compose.prod.yml"
BACKUP_DIR=${BACKUP_DIR:-"$PROJECT_ROOT/backups"}
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUTPUT="$BACKUP_DIR/league-news-$TIMESTAMP.dump"

mkdir -p "$BACKUP_DIR"

docker compose \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  exec -T postgres \
  sh -c 'pg_dump --format=custom --no-owner --no-privileges -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "$OUTPUT"

echo "Database backup written to $OUTPUT"
