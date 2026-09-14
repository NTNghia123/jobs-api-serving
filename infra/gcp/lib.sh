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
# gcloud là yêu cầu TOÀN CỤC (mọi script infra đều cần). Các công cụ khác (bq, python3) chỉ vài
# script dùng → KHÔNG ép ở tầng source (tránh làm hỏng allow-public/networking/budget...); script
# nào cần thì tự gọi require_cmd ở đầu.
command -v gcloud >/dev/null 2>&1 || die "Không thấy 'gcloud'. Dùng Google Cloud Shell hoặc cài Cloud SDK."

# require_cmd bq python3 ... — die nếu thiếu bất kỳ công cụ nào liệt kê. Gọi ở script THỰC SỰ cần.
require_cmd() {
  local c
  for c in "$@"; do
    command -v "${c}" >/dev/null 2>&1 \
      || die "Không thấy '${c}' — cần cho $(basename "${0}"). Dùng Cloud Shell / cài Cloud SDK / Python 3."
  done
}

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

# kiểm location của dataset đã tồn tại phải == BQ_LOCATION (BigQuery cần cùng location trong 1 query).
# TÁCH 3 loại lỗi (không gộp lỗi công cụ vào lỗi trạng thái tài nguyên):
#   (1) không đọc được metadata → lỗi credential/network/quyền
#   (2) không parse được metadata → lỗi tool/định dạng
#   (3) đọc+parse OK nhưng location lệch → lỗi tài nguyên (mới khuyên xoá & tạo lại)
ensure_dataset_location() {
  local dataset="$1" json loc
  json="$(bq --project_id="${PROJECT_ID}" --format=json show --dataset "${PROJECT_ID}:${dataset}")" \
    || die "Không đọc được metadata dataset ${dataset} (credential/network/quyền?) — KHÔNG kết luận được location."
  loc="$(printf '%s' "${json}" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("location",""))')" \
    || die "Không parse được metadata dataset ${dataset} (lỗi python3/định dạng JSON) — KHÔNG kết luận được location."
  [[ "${loc}" == "${BQ_LOCATION}" ]] \
    || die "Dataset ${dataset} có location='${loc}' ≠ BQ_LOCATION='${BQ_LOCATION}'. BigQuery cần cùng location; xoá & tạo lại nếu cần."
}

# authorized dataset (BigQuery): reader_ds được đọc source_ds qua VIEWS mà không cần
# cấp quyền trực tiếp trên source_ds. Idempotent. Dùng cho Looker (Owner's Credentials).
# Exit code Python: 0 = đã sửa file (cần update) · 10 = entry đã đúng (no-op) · khác = LỖI → die.
authorize_dataset_reader() {
  local source_ds="$1" reader_ds="$2"
  local tmp; tmp="$(mktemp)"
  bq show --format=prettyjson "${PROJECT_ID}:${source_ds}" > "${tmp}"
  local rc=0
  python3 - "${tmp}" "${PROJECT_ID}" "${reader_ds}" <<'PY' || rc=$?
import json, sys
path, proj, ds = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(path, encoding="utf-8"))
acc = d.setdefault("access", [])
for e in acc:
    de = e.get("dataset")
    if not de:
        continue
    ref = de.get("dataset", {})
    # chỉ coi là "đã cấu hình" khi khớp ĐỦ project + dataset + có VIEWS trong targetTypes
    if (ref.get("projectId") == proj and ref.get("datasetId") == ds
            and "VIEWS" in (de.get("targetTypes") or [])):
        sys.exit(10)  # no-op
acc.append({"dataset": {"dataset": {"projectId": proj, "datasetId": ds}, "targetTypes": ["VIEWS"]}})
json.dump(d, open(path, "w", encoding="utf-8"))
PY
  case "${rc}" in
    0)  log "authorized dataset: ${reader_ds} → đọc ${source_ds}"
        bq update --source "${tmp}" "${PROJECT_ID}:${source_ds}" >/dev/null ;;
    10) skip "authorized dataset: ${reader_ds} → ${source_ds}" ;;
    *)  rm -f "${tmp}"; die "authorize_dataset_reader: python lỗi (rc=${rc}) trên dataset ${source_ds}." ;;
  esac
  rm -f "${tmp}"
}
