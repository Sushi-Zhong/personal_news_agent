#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_DIR}"

# shellcheck disable=SC1091
source "${PROJECT_DIR}/scripts/service_control_lib.sh"
pna_load_runtime_env

PYTHON_BIN="$(pna_python_bin)"

args=(
  --limit "${PNA_TASK_DUE_LIMIT:-10}"
  --idle-seconds "${PNA_TASK_IDLE_SECONDS:-30}"
)

if [[ -n "${PNA_TASK_USER_ID:-}" ]]; then
  args+=(--user-id "${PNA_TASK_USER_ID}")
fi

exec "${PYTHON_BIN}" scripts/run_task_loop.py "${args[@]}"
