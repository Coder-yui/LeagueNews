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

DEPLOY_BUILD_LOCAL_VALUE=$(
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --environment |
    awk -F= '
      $1 == "DEPLOY_BUILD_LOCAL" {
        print substr($0, length($1) + 2)
        found = 1
        exit
      }
      END {
        if (!found) print "false"
      }
    '
)

if [ "$DEPLOY_BUILD_LOCAL_VALUE" = "true" ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build api web
elif [ "$DEPLOY_BUILD_LOCAL_VALUE" = "false" ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull api web
else
  echo "DEPLOY_BUILD_LOCAL must be either true or false (got: $DEPLOY_BUILD_LOCAL_VALUE)" >&2
  exit 2
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --no-build --remove-orphans
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
