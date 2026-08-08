#!/usr/bin/env bash
# ClassHub OSS — single-command deploy
# Usage: ./deploy-classhub.sh [restart|build|start|stop|status]
#   start   — start API + frontend (default)
#   stop    — stop both
#   restart — stop then start
#   build   — rebuild frontend (vite build) into web/dist
#   status  — show running state
#
# Config (override via env or edit here):
PORT_API=${PORT_API:-8100}
PORT_WEB=${PORT_WEB:-5174}
DB_PATH=${DB_PATH:-/tmp/classhub-oss.db}
REPO_DIR=${REPO_DIR:-$(cd "$(dirname "$0")" && pwd)}
PID_DIR=${PID_DIR:-/tmp/classhub-pids}

set -euo pipefail

API_DIR="$REPO_DIR/api"
WEB_DIR="$REPO_DIR/web"
mkdir -p "$PID_DIR"

api_pid()  { cat "$PID_DIR/api.pid"  2>/dev/null || echo ""; }
web_pid()  { cat "$PID_DIR/web.pid"  2>/dev/null || echo ""; }

is_running() { local pid="$1"; [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; }

stop_one() {
  local name="$1" pidfile="$PID_DIR/$2"
  local pid; pid=$(cat "$pidfile" 2>/dev/null || echo "")
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
    echo "  $name stopped (pid $pid)"
  else
    echo "  $name not running"
  fi
  rm -f "$pidfile"
}

do_stop() {
  echo "Stopping ClassHub..."
  stop_one "API"      "api.pid"
  stop_one "Frontend" "web.pid"
}

do_start_api() {
  cd "$API_DIR"
  if [[ ! -d .venv ]]; then
    echo "  Creating Python venv..."
    python3 -m venv .venv
    .venv/bin/pip install -q -e '.[dev]'
  fi
  source .venv/bin/activate
  echo "  Starting API on :$PORT_API (DB: $DB_PATH)"
  CLASSHUB_DB="$DB_PATH" nohup uvicorn app.main:app \
    --host 0.0.0.0 --port "$PORT_API" \
    > "$PID_DIR/api.log" 2>&1 &
  echo $! > "$PID_DIR/api.pid"
  deactivate
}

do_start_web() {
  cd "$WEB_DIR"
  if [[ ! -d node_modules ]]; then
    echo "  Installing frontend dependencies..."
    npm install
  fi
  echo "  Starting Frontend on :$PORT_WEB (proxy → :$PORT_API)"
  VITE_API_PROXY_TARGET="http://localhost:$PORT_API" \
    nohup npm run dev -- --port "$PORT_WEB" --host 0.0.0.0 \
    > "$PID_DIR/web.log" 2>&1 &
  echo $! > "$PID_DIR/web.pid"
}

do_start() {
  echo "Starting ClassHub..."
  do_start_api
  do_start_web
  sleep 2
  do_status
}

do_restart() {
  do_stop
  sleep 1
  do_start
}

do_build() {
  echo "Building frontend..."
  cd "$WEB_DIR"
  VITE_API_PROXY_TARGET="http://localhost:$PORT_API" npm run build
  echo "  Built → $WEB_DIR/dist"
}

do_status() {
  local apid; apid=$(api_pid)
  local wpid; wpid=$(web_pid)
  echo "ClassHub OSS status:"
  if is_running "$apid"; then
    echo "  API       : running (pid $apid) → http://localhost:$PORT_API"
    local probe; probe=$(curl -sf "http://localhost:$PORT_API/api/app-settings" 2>/dev/null || echo "")
    [[ -n "$probe" ]] && echo "    probe: $probe"
  else
    echo "  API       : stopped"
  fi
  if is_running "$wpid"; then
    echo "  Frontend  : running (pid $wpid) → http://localhost:$PORT_WEB"
  else
    echo "  Frontend  : stopped"
  fi
  echo "  DB        : $DB_PATH"
  echo "  Logs      : $PID_DIR/api.log  $PID_DIR/web.log"
}

do_logs() {
  local name="${1:-api}"
  local f="$PID_DIR/$name.log"
  if [[ -f "$f" ]]; then tail -f "$f"; else echo "No log at $f"; fi
}

case "${1:-start}" in
  start)   do_start   ;;
  stop)    do_stop    ;;
  restart) do_restart ;;
  build)   do_build   ;;
  status)  do_status  ;;
  logs)    do_logs "${2:-api}" ;;
  *) echo "Usage: $0 {start|stop|restart|build|status|logs [api|web]}"
     exit 1 ;;
esac
