#!/usr/bin/env bash
# Helper dùng chung cho mọi script infra. KHÔNG chạy trực tiếp — được `source` bởi script khác.
# Nạp config.sh, validate biến bắt buộc, và cung cấp hàm log + idempotency.

# --- màu + log ---
_c_green="\033[32m"; _c_yellow="\033[33m"; _c_red="\033[31m"; _c_reset="\033[0m"
log()  { printf "${_c_green}[+]${_c_reset} %s\n" "$*"; }
warn() { printf "${_c_yellow}[!]${_c_reset} %s\n" "$*"; }
err()  { printf "${_c_red}[x]${_c_reset} %s\n" "$*" >&2; }
skip() { printf "    ${_c_yellow}đã có, bỏ qua:${_c_reset} %s\n" "$*"; }

die() { err "$*"; exit 1; }

# --- nạp config ---
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "${_here}/config.sh" ]]; then
  die "Thiếu ${_here}/config.sh — chạy: cp config.example.sh config.sh rồi điền PROJECT_ID."
fi
# shellcheck disable=SC1091
source "${_here}/config.sh"

# --- validate biến bắt buộc ---
[[ -n "${PROJECT_ID:-}" && "${PROJECT_ID}" != "dien-project-id-cua-ban" ]] \
  || die "PROJECT_ID chưa điền trong config.sh."
[[ -n "${REGION:-}" ]] || die "REGION trống trong config.sh."
[[ -n "${BQ_LOCATION:-}" ]] || die "BQ_LOCATION trống trong config.sh."

# --- kiểm công cụ ---
command -v gcloud >/dev/null 2>&1 || die "Không thấy 'gcloud'. Dùng Google Cloud Shell hoặc cài Cloud SDK."

# email SA đầy đủ từ tên ngắn
sa_email() { echo "$1@${PROJECT_ID}.iam.gserviceaccount.com"; }

# banner xác nhận mục tiêu trước khi tác động (chống chạy nhầm project)
show_target() {
  echo "----------------------------------------------------------------"
  echo "  PROJECT : ${PROJECT_ID}"
  echo "  REGION  : ${REGION}  (BQ location: ${BQ_LOCATION})"
  echo "  SCRIPT  : $(basename "${0}")"
  echo "----------------------------------------------------------------"
}

# đảm bảo gcloud đang trỏ đúng project (idempotent)
ensure_project() {
  local cur; cur="$(gcloud config get-value project 2>/dev/null || true)"
  if [[ "${cur}" != "${PROJECT_ID}" ]]; then
    log "Đặt project mặc định: ${PROJECT_ID}"
    gcloud config set project "${PROJECT_ID}" >/dev/null
  fi
}
