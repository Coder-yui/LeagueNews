#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
ENV_FILE=${ENV_FILE:-"$PROJECT_ROOT/.env.production"}
COMPOSE_FILE="$PROJECT_ROOT/deploy/docker-compose.prod.yml"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing production environment file: $ENV_FILE" >&2
  exit 2
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --quiet

if [ "${DEPLOY_BUILD_LOCAL:-false}" = "true" ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build api web
else
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull api web
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --no-build --remove-orphans
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
