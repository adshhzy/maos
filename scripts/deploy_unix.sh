#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-python3}"
HOST="${HOST:-127.0.0.1}"
SANDBOX_PORT="${SANDBOX_PORT:-8765}"
SIMULATOR_PORT="${SIMULATOR_PORT:-8767}"
AGENT_SERVICE_PORT="${AGENT_SERVICE_PORT:-8091}"
TEMPORAL_PORT="${TEMPORAL_PORT:-7233}"
TEMPORAL_UI_PORT="${TEMPORAL_UI_PORT:-8233}"
DATA_DIR="${DATA_DIR:-$HOME/maos-temporal-data}"
SKIP_AGENT_SERVICE="${SKIP_AGENT_SERVICE:-0}"
USE_EXTERNAL_TEMPORAL="${USE_EXTERNAL_TEMPORAL:-0}"
TEMPORAL_ADDRESS="${TEMPORAL_ADDRESS:-127.0.0.1:7233}"
TEMPORAL_NAMESPACE="${TEMPORAL_NAMESPACE:-default}"
SPLIT_WORKER="${SPLIT_WORKER:-0}"
PRODUCTION_MODE="${PRODUCTION_MODE:-0}"
NO_TEMPORAL_UI="${NO_TEMPORAL_UI:-0}"
MULTICA_BIN="${MULTICA_BIN:-}"
MULTICA_PROFILE="${MULTICA_PROFILE:-desktop-api.multica.ai}"
MULTICA_WORKSPACE_ID="${MULTICA_WORKSPACE_ID:-}"
HERMES_BIN="${HERMES_BIN:-}"
HERMES_WORKDIR="${HERMES_WORKDIR:-$PROJECT_ROOT}"
HERMES_GIT_BASH_PATH="${HERMES_GIT_BASH_PATH:-}"

VENV_PY="$PROJECT_ROOT/.venv/bin/python"
LOG_DIR="$PROJECT_ROOT/runtime_logs"
TEMPORAL_DB_FILE="$DATA_DIR/temporal.db"
A2A_REGISTRY_FILE="$DATA_DIR/a2a-invocations.json"

step() {
  printf '\n==> %s\n' "$1"
}

port_free() {
  "$PYTHON" - "$HOST" "$1" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket()
try:
    sock.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

wait_http() {
  local url="$1"
  local name="$2"
  for _ in $(seq 1 30); do
    if "$PYTHON" - "$url" <<'PY'
import sys
from urllib.request import urlopen

with urlopen(sys.argv[1], timeout=3) as response:
    if response.status >= 400:
        raise SystemExit(1)
PY
    then
      printf '%s is healthy: %s\n' "$name" "$url"
      return 0
    fi
    sleep 2
  done
  printf '%s did not become healthy: %s\n' "$name" "$url" >&2
  return 1
}

cd "$PROJECT_ROOT"

step "Preparing directories"
mkdir -p "$DATA_DIR" "$LOG_DIR"

if [[ "$PRODUCTION_MODE" == "1" && "$USE_EXTERNAL_TEMPORAL" != "1" ]]; then
  echo "PRODUCTION_MODE=1 requires USE_EXTERNAL_TEMPORAL=1 and a reachable TEMPORAL_ADDRESS." >&2
  exit 1
fi

step "Checking ports"
port_free "$SANDBOX_PORT" || { echo "Port $SANDBOX_PORT is in use" >&2; exit 1; }
port_free "$SIMULATOR_PORT" || { echo "Port $SIMULATOR_PORT is in use" >&2; exit 1; }
if [[ "$SKIP_AGENT_SERVICE" != "1" ]]; then
  port_free "$AGENT_SERVICE_PORT" || { echo "Port $AGENT_SERVICE_PORT is in use" >&2; exit 1; }
fi
if [[ "$USE_EXTERNAL_TEMPORAL" != "1" ]]; then
  port_free "$TEMPORAL_PORT" || { echo "Port $TEMPORAL_PORT is in use" >&2; exit 1; }
  if [[ "$NO_TEMPORAL_UI" != "1" ]]; then
    port_free "$TEMPORAL_UI_PORT" || { echo "Port $TEMPORAL_UI_PORT is in use" >&2; exit 1; }
  fi
fi

step "Creating virtual environment"
if [[ ! -x "$VENV_PY" ]]; then
  "$PYTHON" -m venv "$PROJECT_ROOT/.venv"
fi

step "Installing Python dependencies"
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r "$PROJECT_ROOT/requirements.txt"

step "Writing .env if missing"
if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  {
    [[ -n "$MULTICA_BIN" ]] && echo "MULTICA_BIN=$MULTICA_BIN"
    echo "MULTICA_PROFILE=$MULTICA_PROFILE"
    [[ -n "$MULTICA_WORKSPACE_ID" ]] && echo "MULTICA_WORKSPACE_ID=$MULTICA_WORKSPACE_ID"
    echo "AGENT_SERVICE_HOST=$HOST"
    echo "AGENT_SERVICE_PORT=$AGENT_SERVICE_PORT"
    echo "AGENT_SERVICE_POLL_SECONDS=2"
    echo "AGENT_SERVICE_COMMAND_TIMEOUT_SECONDS=60"
    echo "AGENT_SERVICE_HERMES_TIMEOUT_SECONDS=90"
    [[ -n "$HERMES_BIN" ]] && echo "HERMES_BIN=$HERMES_BIN"
    echo "HERMES_WORKDIR=$HERMES_WORKDIR"
    [[ -n "$HERMES_GIT_BASH_PATH" ]] && echo "HERMES_GIT_BASH_PATH=$HERMES_GIT_BASH_PATH"
  } > "$PROJECT_ROOT/.env"
  echo "Created $PROJECT_ROOT/.env"
else
  echo "Using existing $PROJECT_ROOT/.env"
fi

export AGENT_SERVICE_API_BASE="http://$HOST:$AGENT_SERVICE_PORT"
export SIMULATOR_API_BASE="http://$HOST:$SIMULATOR_PORT"
export SANDBOX_API_BASE="http://$HOST:$SANDBOX_PORT"
export A2A_INVOCATION_REGISTRY_FILE="$A2A_REGISTRY_FILE"
export PRODUCTION_MODE

if [[ "$SKIP_AGENT_SERVICE" != "1" ]]; then
  step "Starting agent-service"
  nohup "$VENV_PY" -m uvicorn services.agent_service.main:app \
    --host "$HOST" \
    --port "$AGENT_SERVICE_PORT" \
    > "$LOG_DIR/agent_service.out.log" \
    2> "$LOG_DIR/agent_service.err.log" &
  echo $! > "$LOG_DIR/agent_service.pid"
fi

if [[ "$SPLIT_WORKER" == "1" ]]; then
  step "Starting execution-api, agent-simulator, and temporal-server"
else
  step "Starting execution-api, agent-simulator, execution-worker, and temporal-server"
fi
sandbox_args=(
  sandbox_service.py
  --host "$HOST"
  --port "$SANDBOX_PORT"
  --simulator-host "$HOST"
  --simulator-port "$SIMULATOR_PORT"
  --temporal-namespace "$TEMPORAL_NAMESPACE"
)

if [[ "$USE_EXTERNAL_TEMPORAL" == "1" ]]; then
  sandbox_args+=(--temporal-address "$TEMPORAL_ADDRESS")
else
  sandbox_args+=(
    --temporal-host "$HOST"
    --temporal-port "$TEMPORAL_PORT"
    --temporal-ui-port "$TEMPORAL_UI_PORT"
    --temporal-db-file "$TEMPORAL_DB_FILE"
  )
  if [[ "$NO_TEMPORAL_UI" == "1" ]]; then
    sandbox_args+=(--no-temporal-ui)
  fi
fi
if [[ "$SPLIT_WORKER" == "1" ]]; then
  sandbox_args+=(--no-worker)
fi
if [[ "$PRODUCTION_MODE" == "1" ]]; then
  sandbox_args+=(--production)
fi

nohup "$VENV_PY" "${sandbox_args[@]}" \
  > "$LOG_DIR/sandbox_service.out.log" \
  2> "$LOG_DIR/sandbox_service.err.log" &
echo $! > "$LOG_DIR/sandbox_service.pid"

step "Health checks"
if [[ "$SKIP_AGENT_SERVICE" != "1" ]]; then
  wait_http "http://$HOST:$AGENT_SERVICE_PORT/readyz" "agent-service"
fi
wait_http "http://$HOST:$SANDBOX_PORT/api/readyz" "execution-api"

if [[ "$SPLIT_WORKER" == "1" ]]; then
  step "Starting execution-worker"
  worker_temporal_address="$TEMPORAL_ADDRESS"
  if [[ "$USE_EXTERNAL_TEMPORAL" != "1" ]]; then
    worker_temporal_address="$HOST:$TEMPORAL_PORT"
  fi
  nohup "$VENV_PY" execution_worker.py \
    --temporal-address "$worker_temporal_address" \
    --temporal-namespace "$TEMPORAL_NAMESPACE" \
    > "$LOG_DIR/execution_worker.out.log" \
    2> "$LOG_DIR/execution_worker.err.log" &
  echo $! > "$LOG_DIR/execution_worker.pid"
fi

step "Deployment complete"
echo "Web UI:          http://$HOST:$SANDBOX_PORT/"
echo "execution-api:   http://$HOST:$SANDBOX_PORT/api/health"
echo "agent-simulator: http://$HOST:$SIMULATOR_PORT"
if [[ "$SKIP_AGENT_SERVICE" != "1" ]]; then
  echo "agent-service:   http://$HOST:$AGENT_SERVICE_PORT/health"
fi
if [[ "$SPLIT_WORKER" == "1" ]]; then
  echo "execution-worker: standalone process"
else
  echo "execution-worker: embedded in execution-api"
fi
if [[ "$PRODUCTION_MODE" == "1" ]]; then
  echo "production mode: enabled"
fi
if [[ "$USE_EXTERNAL_TEMPORAL" != "1" && "$NO_TEMPORAL_UI" != "1" ]]; then
  echo "Temporal UI:   http://$HOST:$TEMPORAL_UI_PORT"
fi
echo "Temporal DB:   $TEMPORAL_DB_FILE"
echo "A2A registry:  $A2A_REGISTRY_FILE"
echo "Logs:          $LOG_DIR"
