#!/usr/bin/env bash
# macOS/Linux equivalent of scripts/stop.ps1
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$PROJECT_ROOT/.run"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

stop_tracked_process() {
  local name="$1" pid_file="$RUN_DIR/$1.pid"
  if [ ! -f "$pid_file" ]; then
    echo "$name: no PID file; skipping."
    return
  fi

  local saved_pid
  saved_pid="$(tr -d '[:space:]' < "$pid_file")"
  if [ -n "$saved_pid" ] && kill -0 "$saved_pid" 2>/dev/null; then
    echo "Stopping $name (PID $saved_pid)..."
    # Kill children first (e.g. uvicorn --reload worker), then the parent.
    pkill -TERM -P "$saved_pid" 2>/dev/null || true
    kill -TERM "$saved_pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      kill -0 "$saved_pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$saved_pid" 2>/dev/null; then
      pkill -KILL -P "$saved_pid" 2>/dev/null || true
      kill -KILL "$saved_pid" 2>/dev/null || true
    fi
  else
    echo "$name: process has already exited."
  fi
  rm -f "$pid_file"
}

cd "$PROJECT_ROOT"
stop_tracked_process web
stop_tracked_process pipeline-worker
stop_tracked_process collection-scheduler
stop_tracked_process api

echo "Stopping PostgreSQL and pgAdmin containers..."
docker compose stop

printf '\nLoL Daily Intel has stopped.\n'
echo "Database data remains in the Docker volumes."
