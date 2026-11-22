#!/usr/bin/env bash
set -euo pipefail

export REPO_ROOT="${REPO_ROOT:-/app}"
export DATA_DIR="${DATA_DIR:-/data}"
export PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHONPATH="${PYTHONPATH:-/app/src}"
export MODEL_CACHE_DIR="${MODEL_CACHE_DIR:-/app/models}"
export HOSTNAME="${HOSTNAME:-0.0.0.0}"
export PORT="${PORT:-3000}"

mkdir -p "${DATA_DIR}/jobs"

SERVER_JS="/app/web-standalone/server.js"
if [[ ! -f "${SERVER_JS}" ]]; then
  SERVER_JS="$(find /app/web-standalone -maxdepth 3 -name server.js ! -path '*/node_modules/*' | head -n 1 || true)"
fi
if [[ -z "${SERVER_JS}" || ! -f "${SERVER_JS}" ]]; then
  echo "Next standalone server.js not found under /app/web-standalone" >&2
  ls -laR /app/web-standalone | head -80 >&2 || true
  exit 1
fi

cd "$(dirname "${SERVER_JS}")"
exec node "$(basename "${SERVER_JS}")"
