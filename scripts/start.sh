#!/usr/bin/env bash
# macOS/Linux equivalent of scripts/start.ps1
# Starts: PostgreSQL + pgAdmin (Docker), migrations, FastAPI, Next.js,
# pipeline worker and collection scheduler.
set -euo pipefail

SKIP_BROWSER=false
for arg in "$@"; do
  case "$arg" in
    --skip-browser|-SkipBrowser) SKIP_BROWSER=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$PROJECT_ROOT/.run"
LOG_DIR="$RUN_DIR/logs"
API_DIR="$PROJECT_ROOT/services/api"
ENV_FILE="$PROJECT_ROOT/.env"

# Some shells (e.g. IDE-integrated terminals) export PYTHONHOME/PYTHONPATH for a
# bundled Python, which breaks the project interpreter. Clear them for children.
unset PYTHONHOME PYTHONPATH || true
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

step() { printf '\n==> %s\n' "$1"; }

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command '$1' was not found. $2" >&2
    exit 1
  fi
}

docker_daemon_ok() { docker info >/dev/null 2>&1; }

wait_http() {
  local url="$1" timeout="$2"
  local deadline=$((SECONDS + timeout))
  while [ $SECONDS -lt $deadline ]; do
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$url" 2>/dev/null || true)"
    if [ -n "$code" ] && [ "$code" -ge 200 ] && [ "$code" -lt 500 ]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

pid_alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }

recorded_process_running() {
  local name="$1" pid_file="$RUN_DIR/$1.pid"
  [ -f "$pid_file" ] || return 1
  local saved_pid
  saved_pid="$(tr -d '[:space:]' < "$pid_file")"
  if pid_alive "$saved_pid"; then
    return 0
  fi
  rm -f "$pid_file"
  return 1
}

assert_port_available() {
  local port="$1" service="$2"
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "$service cannot start because port $port is in use. Run scripts/stop.sh or close the process using that port." >&2
    exit 1
  fi
}

start_tracked_process() {
  local name="$1" workdir="$2" command="$3"
  local stdout="$LOG_DIR/$name.out.log" stderr="$LOG_DIR/$name.error.log"
  (
    cd "$workdir"
    nohup bash -c "$command" >>"$stdout" 2>>"$stderr" &
    echo $! > "$RUN_DIR/$name.pid"
  )
}

cd "$PROJECT_ROOT"
mkdir -p "$RUN_DIR" "$LOG_DIR"

require_command docker "Install OrbStack or Docker Desktop first."
require_command uv "Install uv first (brew install uv)."
require_command pnpm "Install pnpm first (brew install pnpm)."

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Run: cp .env.example .env, then configure OPENAI_API_KEY." >&2
  exit 1
fi
if [ ! -d "$API_DIR/.venv" ]; then
  echo "Backend dependencies are missing. Run 'uv sync --dev' in services/api." >&2
  exit 1
fi
if [ ! -d "$PROJECT_ROOT/node_modules" ]; then
  echo "Frontend dependencies are missing. Run 'pnpm install' in the project root." >&2
  exit 1
fi

step "Checking Docker daemon"
if ! docker_daemon_ok; then
  if [ -d "/Applications/OrbStack.app" ]; then
    echo "Docker daemon is not running. Starting OrbStack and waiting..."
    open -a OrbStack
  elif [ -d "/Applications/Docker.app" ]; then
    echo "Docker daemon is not running. Starting Docker Desktop and waiting..."
    open -a Docker
  else
    echo "Docker daemon is unavailable and no Docker runtime was found. Start it manually and retry." >&2
    exit 1
  fi
  deadline=$((SECONDS + 120))
  while [ $SECONDS -lt $deadline ] && ! docker_daemon_ok; do
    sleep 2
  done
  if ! docker_daemon_ok; then
    echo "Timed out waiting for the Docker daemon. Wait until it is running, then retry." >&2
    exit 1
  fi
fi

step "Starting PostgreSQL and pgAdmin"
docker compose up -d

step "Applying database migrations"
(
  cd "$API_DIR"
  MIGRATIONS_DIR="$PROJECT_ROOT/infra/postgres/migrations" \
    .venv/bin/python -m scripts.migrate_database
)

if recorded_process_running api; then
  echo "FastAPI is already tracked as running; skipping."
else
  assert_port_available 8000 "FastAPI"
  step "Starting FastAPI"
  start_tracked_process api "$API_DIR" \
    "uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
fi

if recorded_process_running web; then
  echo "Next.js is already tracked as running; skipping."
else
  assert_port_available 3000 "Next.js"
  step "Starting Next.js"
  start_tracked_process web "$PROJECT_ROOT" "pnpm dev:web"
fi

step "Waiting for health checks"
api_ready=false; web_ready=false
wait_http "http://localhost:8000/api/v1/health" 45 && api_ready=true
wait_http "http://localhost:3000" 60 && web_ready=true

if ! $api_ready || ! $web_ready; then
  echo "Not all services became ready before the timeout." >&2
  echo "API log: $LOG_DIR/api.error.log" >&2
  echo "Web log: $LOG_DIR/web.error.log" >&2
  exit 1
fi

if recorded_process_running pipeline-worker; then
  echo "Automatic pipeline worker is already tracked as running; skipping."
else
  step "Starting automatic pipeline worker"
  start_tracked_process pipeline-worker "$API_DIR" \
    "uv run python -m scripts.run_pipeline_worker"
fi

if recorded_process_running collection-scheduler; then
  echo "Source collection scheduler is already tracked as running; skipping."
else
  step "Starting source collection scheduler"
  start_tracked_process collection-scheduler "$API_DIR" \
    "uv run python -m scripts.run_collection_scheduler"
fi

printf '\nLoL Daily Intel is running:\n'
echo "  Website    http://localhost:3000"
echo "  API docs   http://localhost:8000/docs"
echo "  pgAdmin    http://localhost:5050"
echo "  Logs       $LOG_DIR"
printf '\nStop with: ./scripts/stop.sh\n'

if ! $SKIP_BROWSER; then
  open "http://localhost:3000"
  open "http://localhost:8000/docs"
  open "http://localhost:5050"
fi
