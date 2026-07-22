#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_DIR}"

if [[ -f "${PROJECT_DIR}/.env.ext" ]]; then
  # shellcheck disable=SC1091
  source "${PROJECT_DIR}/.env.ext"
fi

PYTHON_BIN="python3"
if [[ -n "${PERSONAL_NEWS_VENV:-}" && -x "${PERSONAL_NEWS_VENV}/bin/python" ]]; then
  PYTHON_BIN="${PERSONAL_NEWS_VENV}/bin/python"
fi

args=(
  --limit "${PNA_TASK_DUE_LIMIT:-10}"
  --idle-seconds "${PNA_TASK_IDLE_SECONDS:-30}"
)

if [[ -n "${PNA_TASK_USER_ID:-}" ]]; then
  args+=(--user-id "${PNA_TASK_USER_ID}")
fi

exec "${PYTHON_BIN}" scripts/run_task_loop.py "${args[@]}"
