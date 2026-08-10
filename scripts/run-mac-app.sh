#!/bin/zsh

set -euo pipefail

ROOT="${0:A:h:h}"
WEB_RESOURCES="$ROOT/apps/mac-app/Sources/ChronosMacApp/Resources/Web"
SERVER_LOG="${TMPDIR:-/tmp}/chronos-schedule-app.log"
MONITOR_LOG="${TMPDIR:-/tmp}/chronos-monitor-app.log"
STARTED_SERVER_PID=""
STARTED_MONITOR_PID=""
SWIFT=(swift)

if [[ -d /Applications/Xcode.app/Contents/Developer ]]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
  SWIFT=(xcrun swift)
fi

export CLANG_MODULE_CACHE_PATH="${TMPDIR:-/tmp}/chronos-clang-cache"
export SWIFTPM_MODULECACHE_OVERRIDE="${TMPDIR:-/tmp}/chronos-swiftpm-cache"

cd "$ROOT"

npm --prefix web run build
mkdir -p "$WEB_RESOURCES"
rsync -a --delete --exclude='.gitkeep' web/dist/ "$WEB_RESOURCES/"

cleanup() {
  if [[ -n "$STARTED_MONITOR_PID" ]]; then
    kill "$STARTED_MONITOR_PID" 2>/dev/null || true
    wait "$STARTED_MONITOR_PID" 2>/dev/null || true
  fi
  if [[ -n "$STARTED_SERVER_PID" ]]; then
    kill "$STARTED_SERVER_PID" 2>/dev/null || true
    wait "$STARTED_SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

PORT=8765
if curl --noproxy '*' --silent --fail --max-time 1 \
  "http://127.0.0.1:$PORT/api/schedule" >/dev/null \
  && ! curl --noproxy '*' --silent --fail --max-time 1 \
    "http://127.0.0.1:$PORT/api/health" \
    | grep -q 'timeline-task-storage'; then
  PORT=8766
  echo "An older Chronos service is using port 8765; starting the current version on 8766."
fi

HEALTH_URL="http://127.0.0.1:$PORT/api/health"
MONITOR_URL="http://127.0.0.1:$PORT/api/current-state"
INGEST_URL="http://127.0.0.1:$PORT/api/monitor/observations"

if ! curl --noproxy '*' --silent --fail --max-time 1 "$HEALTH_URL" \
  | grep -q 'timeline-task-storage'; then
  ./scripts/run-schedule.sh --port "$PORT" >"$SERVER_LOG" 2>&1 &
  STARTED_SERVER_PID=$!

  for _ in {1..50}; do
    if curl --noproxy '*' --silent --fail --max-time 1 "$HEALTH_URL" \
      | grep -q 'timeline-task-storage'; then
      break
    fi
    if ! kill -0 "$STARTED_SERVER_PID" 2>/dev/null; then
      echo "Chronos local service failed to start:"
      tail -20 "$SERVER_LOG"
      exit 1
    fi
    sleep 0.1
  done

  if ! curl --noproxy '*' --silent --fail --max-time 1 "$HEALTH_URL" \
    | grep -q 'timeline-task-storage'; then
    echo "Chronos local service did not become ready. See $SERVER_LOG"
    exit 1
  fi
fi

if ! curl --noproxy '*' --silent --fail --max-time 1 "$MONITOR_URL" \
  | grep -q '"status": "live"'; then
  PYTHONPATH=src python3 -m chronos.api.monitor_runner \
    --endpoint "$INGEST_URL" >"$MONITOR_LOG" 2>&1 &
  STARTED_MONITOR_PID=$!
fi

export CHRONOS_WEB_URL="${CHRONOS_WEB_URL:-http://127.0.0.1:$PORT}"
"${SWIFT[@]}" run --package-path apps/mac-app chronos-mac-app
