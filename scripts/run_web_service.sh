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

exec "${PYTHON_BIN}" -m uvicorn personal_news_agent.app:app \
  --host "${PNA_WEB_HOST:-127.0.0.1}" \
  --port "${PNA_WEB_PORT:-8000}" \
  --workers "${PNA_WEB_WORKERS:-1}"
