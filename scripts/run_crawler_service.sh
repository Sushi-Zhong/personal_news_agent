#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_DIR}"

# shellcheck disable=SC1091
source "${PROJECT_DIR}/scripts/service_control_lib.sh"
pna_load_runtime_env

PYTHON_BIN="$(pna_python_bin)"

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
