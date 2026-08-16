#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
COMPOSE_FILE="$PROJECT_ROOT/deploy/docker-compose.prod.yml"
ENV_EXAMPLE="$PROJECT_ROOT/.env.production.example"
DEPLOY_SCRIPT="$PROJECT_ROOT/deploy/scripts/deploy.sh"
REAL_DOCKER=$(command -v docker)
PYTHON_BIN=${PYTHON_BIN:-"$PROJECT_ROOT/services/api/.venv/bin/python"}

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN=$(command -v python3)
fi

TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT HUP INT TERM

docker compose --env-file "$ENV_EXAMPLE" -f "$COMPOSE_FILE" \
  config --format json > "$TEST_DIR/compose.json"

"$PYTHON_BIN" - "$TEST_DIR/compose.json" "$PROJECT_ROOT/services/api/Dockerfile" <<'PY'
import json
import sys
from pathlib import Path

compose = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
services = compose["services"]


def mount(service: str, target: str) -> dict:
    matches = [
        item for item in services[service].get("volumes", []) if item["target"] == target
    ]
    assert len(matches) == 1, (service, target, matches)
    return matches[0]


api_profile = mount("api", "/data/weibo-profile")
scheduler_profile = mount("collection-scheduler", "/data/weibo-profile")
assert api_profile["type"] == "volume"
assert scheduler_profile["type"] == "volume"
assert api_profile["source"] == "weibo_api_profile"
assert scheduler_profile["source"] == "weibo_scheduler_profile"
assert api_profile["source"] != scheduler_profile["source"]

for service in ("api", "collection-scheduler"):
    environment = services[service]["environment"]
    assert environment["WEIBO_BROWSER_PROFILE"] == "/data/weibo-profile"
    assert environment["WEIBO_COOKIE_FILE"] == "/run/secrets/weibo-cookies.json"
    for target in (
        "/run/secrets/x-cookies.json",
        "/run/secrets/weibo-cookies.json",
    ):
        cookie_mount = mount(service, target)
        assert cookie_mount["type"] == "bind"
        assert cookie_mount["read_only"] is True

worker = services["pipeline-worker"]
worker_targets = {item["target"] for item in worker.get("volumes", [])}
assert "/data/weibo-profile" not in worker_targets
assert "/run/secrets/x-cookies.json" not in worker_targets
assert "/run/secrets/weibo-cookies.json" not in worker_targets
worker_environment = worker.get("environment", {})
assert "X_COOKIE_FILE" not in worker_environment
assert "WEIBO_BROWSER_PROFILE" not in worker_environment
assert "WEIBO_COOKIE_FILE" not in worker_environment

dockerfile = Path(sys.argv[2]).read_text(encoding="utf-8")
profile_created = dockerfile.index("mkdir -p /data/media /data/weibo-profile")
data_chowned = dockerfile.index("chown -R app:app /app /data /ms-playwright")
non_root_user = dockerfile.index("USER app")
assert profile_created < data_chowned < non_root_user
PY

mkdir -p "$TEST_DIR/bin"
cat > "$TEST_DIR/bin/docker" <<'SH'
#!/bin/sh
set -eu

for argument in "$@"; do
  if [ "$argument" = "config" ]; then
    exec "$REAL_DOCKER" "$@"
  fi
done

printf '%s\n' "$*" >> "$DEPLOY_TEST_LOG"
SH
chmod +x "$TEST_DIR/bin/docker"

run_deploy_case() {
  case_name=$1
  setting=$2
  expected_action=$3
  rejected_action=$4
  env_file="$TEST_DIR/$case_name.env"
  log_file="$TEST_DIR/$case_name.log"

  if [ "$setting" = "unset" ]; then
    sed '/^DEPLOY_BUILD_LOCAL=/d' "$ENV_EXAMPLE" > "$env_file"
  else
    sed "s/^DEPLOY_BUILD_LOCAL=.*/DEPLOY_BUILD_LOCAL=$setting/" \
      "$ENV_EXAMPLE" > "$env_file"
  fi

  : > "$log_file"
  (
    unset DEPLOY_BUILD_LOCAL
    PATH="$TEST_DIR/bin:$PATH" \
      REAL_DOCKER="$REAL_DOCKER" \
      DEPLOY_TEST_LOG="$log_file" \
      ENV_FILE="$env_file" \
      "$DEPLOY_SCRIPT"
  )

  grep -q " $expected_action api web$" "$log_file"
  if grep -q " $rejected_action api web$" "$log_file"; then
    echo "Unexpected $rejected_action action for DEPLOY_BUILD_LOCAL=$setting" >&2
    exit 1
  fi
}

run_deploy_case build true build pull
run_deploy_case pull false pull build
run_deploy_case default unset pull build

echo "Production deployment regression checks passed."
