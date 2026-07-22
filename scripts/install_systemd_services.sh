#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_DIR="${PROJECT_DIR}/deploy/systemd"
SYSTEMD_DIR="/etc/systemd/system"
RUN_USER="${PNA_RUN_USER:-${SUDO_USER:-$(id -un)}}"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl is required; run this installer on the Linux service host." >&2
  exit 1
fi
if ! id "${RUN_USER}" >/dev/null 2>&1; then
  echo "Service user does not exist: ${RUN_USER}" >&2
  exit 1
fi
RUN_GROUP="${PNA_RUN_GROUP:-$(id -gn "${RUN_USER}")}"
if [[ "${EUID}" -eq 0 ]]; then
  sudo_cmd=()
elif command -v sudo >/dev/null 2>&1; then
  sudo_cmd=(sudo)
else
  echo "Run as root or install sudo before installing system services." >&2
  exit 1
fi

escape_sed() {
  printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}

project_replacement="$(escape_sed "${PROJECT_DIR}")"
user_replacement="$(escape_sed "${RUN_USER}")"
group_replacement="$(escape_sed "${RUN_GROUP}")"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

for unit in personal-news-web.service personal-news-crawler.service personal-news.target; do
  sed \
    -e "s|__PROJECT_DIR__|${project_replacement}|g" \
    -e "s|__RUN_USER__|${user_replacement}|g" \
    -e "s|__RUN_GROUP__|${group_replacement}|g" \
    "${TEMPLATE_DIR}/${unit}.in" > "${tmp_dir}/${unit}"
  "${sudo_cmd[@]}" install -m 0644 "${tmp_dir}/${unit}" "${SYSTEMD_DIR}/${unit}"
done

"${sudo_cmd[@]}" systemctl daemon-reload
"${sudo_cmd[@]}" systemctl enable --now personal-news.target
"${sudo_cmd[@]}" systemctl --no-pager --full status personal-news-web.service personal-news-crawler.service
