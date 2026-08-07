#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_DIR}"

if [[ -f "${PROJECT_DIR}/.env.ext" ]]; then
  # shellcheck disable=SC1091
  set -a
  source "${PROJECT_DIR}/.env.ext"
  set +a
fi

PYTHON_BIN="python3"
if [[ -n "${PERSONAL_NEWS_VENV:-}" && -x "${PERSONAL_NEWS_VENV}/bin/python" ]]; then
  PYTHON_BIN="${PERSONAL_NEWS_VENV}/bin/python"
fi

args=(
  --workers "${PNA_CRAWL_WORKERS:-2}"
  --due-limit "${PNA_CRAWL_DUE_LIMIT:-20}"
  --per-section-limit "${PNA_CRAWL_PER_SECTION_LIMIT:-20}"
  --fetch-articles "${PNA_CRAWL_FETCH_ARTICLES:-5}"
  --idle-seconds "${PNA_CRAWL_IDLE_SECONDS:-30}"
  --extraction-limit "${PNA_TOPIC_EXTRACTION_LIMIT:-20}"
)

if [[ -n "${PNA_CRAWL_CATEGORY:-}" ]]; then
  args+=(--category "${PNA_CRAWL_CATEGORY}")
fi

exec "${PYTHON_BIN}" scripts/run_crawl_loop.py "${args[@]}"
