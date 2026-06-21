#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-python3}"
HOST="${AGENT_SERVICE_HOST:-127.0.0.1}"
PORT="${AGENT_SERVICE_PORT:-8091}"
RELOAD="${RELOAD:-0}"
INSTALL_DEPENDENCIES="${INSTALL_DEPENDENCIES:-0}"

VENV_PY="$PROJECT_ROOT/.venv/bin/python"

cd "$PROJECT_ROOT"

if [[ ! -x "$VENV_PY" ]]; then
  "$PYTHON" -m venv "$PROJECT_ROOT/.venv"
fi

if [[ "$INSTALL_DEPENDENCIES" == "1" ]]; then
  "$VENV_PY" -m pip install --upgrade pip
  "$VENV_PY" -m pip install -r "$PROJECT_ROOT/requirements.txt"
fi

args=(-m services.agent_service --host "$HOST" --port "$PORT")
if [[ "$RELOAD" == "1" ]]; then
  args+=(--reload)
fi

printf 'Starting Multica Agent Service at http://%s:%s\n' "$HOST" "$PORT"
exec "$VENV_PY" "${args[@]}"
