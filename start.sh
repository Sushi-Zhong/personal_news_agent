#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${PROJECT_DIR}/scripts/service_control_lib.sh"

pna_manage_scope all "start.sh" "${1:-start}"
