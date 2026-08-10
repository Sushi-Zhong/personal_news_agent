#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_DIR}"

# shellcheck disable=SC1091
source "${PROJECT_DIR}/scripts/service_control_lib.sh"
pna_load_runtime_env

PYTHON_BIN="$(pna_python_bin)"

exec "${PYTHON_BIN}" -m uvicorn personal_news_agent.app:app \
  --host "${PNA_WEB_HOST:-127.0.0.1}" \
  --port "${PNA_WEB_PORT:-8000}" \
  --workers "${PNA_WEB_WORKERS:-1}"
