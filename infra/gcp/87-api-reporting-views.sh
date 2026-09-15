#!/usr/bin/env bash
# Tạo các view rpt_api_* (API metrics) trong jobs_reporting từ reporting/views_api.sql.
# Chạy SAU 86-logging-sink.sh và SAU khi đã sinh traffic (bảng log mới xuất hiện).
# Thứ tự: (a) xác nhận bảng log tồn tại → (b) authorize jobs_reporting đọc jobs_prod_logs
#         → (c) tạo/replace view → (d) smoke query.
# Idempotent. Chạy: bash 87-api-reporting-views.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project
require_cmd bq python3

# --- validate ---
[[ -n "${DATASET_LOGS:-}" ]]      || die "DATASET_LOGS trống trong config.sh."
[[ -n "${DATASET_REPORTING:-}" ]] || die "DATASET_REPORTING trống trong config.sh."
[[ "${API_REPORT_LOOKBACK_DAYS:-}" =~ ^[1-9][0-9]*$ ]] \
  || die "API_REPORT_LOOKBACK_DAYS phải là số nguyên dương (thấy: '${API_REPORT_LOOKBACK_DAYS:-}')."
[[ "${LOG_PARTITION_EXPIRATION_DAYS:-}" =~ ^[1-9][0-9]*$ ]] \
  || die "LOG_PARTITION_EXPIRATION_DAYS phải là số nguyên dương (thấy: '${LOG_PARTITION_EXPIRATION_DAYS:-}')."
# lookback KHÔNG được vượt retention (nếu không, dashboard hiển thị tối đa = retention, gây hiểu nhầm).
(( API_REPORT_LOOKBACK_DAYS <= LOG_PARTITION_EXPIRATION_DAYS )) \
  || die "API_REPORT_LOOKBACK_DAYS (${API_REPORT_LOOKBACK_DAYS}) > LOG_PARTITION_EXPIRATION_DAYS (${LOG_PARTITION_EXPIRATION_DAYS}) — lookback vượt retention của log."

SQL_SRC="./reporting/views_api.sql"
[[ -f "${SQL_SRC}" ]] || die "Không thấy ${SQL_SRC}."

LOG_TABLE="run_googleapis_com_stdout"

# --- (a) xác nhận bảng log tồn tại VÀ thật sự partitioned (để filter theo ngày prune được) ---
RENDERED=""; TBL_JSON=""; trap 'rm -f "${RENDERED}" "${TBL_JSON}"' EXIT   # dọn chung cả 2 temp
TBL_JSON="$(mktemp)"
if ! bq --project_id="${PROJECT_ID}" show --format=prettyjson \
      "${PROJECT_ID}:${DATASET_LOGS}.${LOG_TABLE}" > "${TBL_JSON}" 2>/dev/null; then
  die "Không đọc được bảng ${DATASET_LOGS}.${LOG_TABLE} — chưa tồn tại, hoặc thiếu quyền/credential. Bật sink (86) + sinh traffic rồi chạy lại."
fi
# Parse metadata → "PARTITIONED <expirationMs>" | "NOTPART". Python lỗi (JSON hỏng/exception) →
# exit≠0 → die 'lỗi tool', TÁCH khỏi trường hợp đọc+parse OK nhưng bảng không partitioned.
part_out="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1],encoding="utf-8"))
tp=d.get("timePartitioning")
print("NOTPART" if not tp else "PARTITIONED "+str(tp.get("expirationMs") or ""))' "${TBL_JSON}")" \
  || die "Không parse được metadata bảng ${DATASET_LOGS}.${LOG_TABLE} (lỗi python3/định dạng JSON) — KHÔNG kết luận được partitioning."
read -r part exp_ms <<<"${part_out}"
[[ "${part}" == "PARTITIONED" ]] \
  || die "Bảng ${DATASET_LOGS}.${LOG_TABLE} KHÔNG partitioned (thiếu timePartitioning) — filter theo ngày sẽ không prune. Kiểm tra sink có --use-partitioned-tables và xử lý bảng cũ."
# Kiểm expiration KHỚP config (bắt trường hợp 86 bỏ qua đồng bộ do lỗi tạm/thiếu quyền → retention sai).
expected_ms=$(( LOG_PARTITION_EXPIRATION_DAYS * 86400 * 1000 ))
[[ "${exp_ms}" == "${expected_ms}" ]] \
  || die "Partition expiration của ${LOG_TABLE} = '${exp_ms:-<không đặt>}'ms ≠ mong đợi ${expected_ms}ms (${LOG_PARTITION_EXPIRATION_DAYS}d). Chạy lại 86-logging-sink.sh để đồng bộ retention."
log "Bảng log ${DATASET_LOGS}.${LOG_TABLE} partitioned, expiration ${LOG_PARTITION_EXPIRATION_DAYS}d ✓."

# cảnh báo bảng date-sharded cũ (view API KHÔNG đọc chúng)
LEGACY="$(bq --project_id="${PROJECT_ID}" ls --format=prettyjson "${DATASET_LOGS}" 2>/dev/null \
  | python3 -c 'import json,sys
for table in json.load(sys.stdin):
    print(table["tableReference"]["tableId"])' \
  | grep -E '^run_googleapis_com_stdout_[0-9]{8}$' || true)"
if [[ -n "${LEGACY}" ]]; then
  warn "Có bảng date-sharded cũ (view API bỏ qua): $(echo "${LEGACY}" | tr '\n' ' ')"
fi

# --- (b) authorize jobs_reporting đọc jobs_prod_logs (helper ở lib.sh) ---
authorize_dataset_reader "${DATASET_LOGS}" "${DATASET_REPORTING}"

# --- (c) render + tạo view ---
render_api_sql() {
  local src="$1" out="$2"
  sed -e "s|__PROJECT__|${PROJECT_ID}|g" \
      -e "s|__DS_LOGS__|${DATASET_LOGS}|g" \
      -e "s|__DS_RPT__|${DATASET_REPORTING}|g" \
      -e "s|__LOG_LOOKBACK_DAYS__|${API_REPORT_LOOKBACK_DAYS}|g" \
      "${src}" > "${out}"
  if grep -qE '__[A-Z_]+__' "${out}"; then
    err "Còn placeholder chưa thay:"; grep -oE '__[A-Z_]+__' "${out}" | sort -u | sed 's/^/    /' >&2
    die "render lỗi."
  fi
}

RENDERED="$(mktemp)"   # trap dọn đã đặt ở bước (a)
render_api_sql "${SQL_SRC}" "${RENDERED}"

log "Tạo/replace các view rpt_api_*..."
bq --project_id="${PROJECT_ID}" --location="${BQ_LOCATION}" query \
  --use_legacy_sql=false --quiet < "${RENDERED}"

# --- (d) smoke ---
log "Smoke: đếm dòng mỗi view API..."
for v in rpt_api_requests rpt_api_latency_hourly rpt_api_cache_events rpt_api_bq_queries_daily rpt_api_429_by_client; do
  n="$(bq --project_id="${PROJECT_ID}" --location="${BQ_LOCATION}" query --use_legacy_sql=false \
        --format=csv --quiet "SELECT COUNT(*) FROM \`${PROJECT_ID}.${DATASET_REPORTING}.${v}\`" 2>/dev/null \
        | tail -n +2 | head -n1)"
  printf "    %-28s %s dòng\n" "${v}" "${n:-?}"
done

log "Xong API reporting views. Nếu view trống → cần sinh thêm traffic; log chỉ có TỪ lúc bật sink."
