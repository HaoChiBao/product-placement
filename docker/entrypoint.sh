#!/usr/bin/env bash
set -euo pipefail

export REPO_ROOT="${REPO_ROOT:-/app}"
export DATA_DIR="${DATA_DIR:-/data}"
export PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHONPATH="${PYTHONPATH:-/app/src}"
export MODEL_CACHE_DIR="${MODEL_CACHE_DIR:-/app/models}"
export HOSTNAME="${HOSTNAME:-0.0.0.0}"
export PORT="${PORT:-3000}"
export SEED_CATALOG_ON_BOOT="${SEED_CATALOG_ON_BOOT:-1}"

mkdir -p "${DATA_DIR}/jobs" "${DATA_DIR}/objects"

# Schema + optional catalog seed in the background so Railway healthchecks
# are not blocked by Blender downloads. seed-blender is idempotent.
(
  set +e
  echo "[catalog-boot] init-db $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "${PYTHON_BIN}" -m product_placement.catalog init-db
  if [[ "${SEED_CATALOG_ON_BOOT}" == "1" || "${SEED_CATALOG_ON_BOOT}" == "true" ]]; then
    echo "[catalog-boot] seed-blender (idempotent)"
    "${PYTHON_BIN}" -m product_placement.catalog seed-blender
  fi
  echo "[catalog-boot] done $(date -u +%Y-%m-%dT%H:%M:%SZ)"
) >"${DATA_DIR}/catalog-boot.log" 2>&1 &

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
