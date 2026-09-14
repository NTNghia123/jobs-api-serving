#!/usr/bin/env bash
# Tạo dataset log (jobs_prod_logs) + Cloud Logging → BigQuery sink cho API metrics của Cloud Run.
# - Dataset: đặt DEFAULT PARTITION EXPIRATION (không đặt table expiration → bảng luôn còn, chỉ partition cũ bị xoá).
# - Sink: --use-partitioned-tables (→ bảng cố định run_googleapis_com_stdout, không date-sharded);
#         lọc HẸP theo logName=.../stdout + allowlist jsonPayload.message (giảm noise/cost).
# - Cấp writer identity của sink quyền dataEditor CẤP DATASET (không phải project).
# Idempotent: dataset/sink có rồi → update để đồng bộ; grant có rồi → no-op.
#
# LƯU Ý: log chỉ tích luỹ TỪ LÚC bật sink (không hồi tố). Views API (87) tạo SAU khi bảng log xuất hiện.
# Chạy: bash 86-logging-sink.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project
require_cmd bq python3

# --- validate ---
[[ -n "${DATASET_LOGS:-}" ]]     || die "DATASET_LOGS trống trong config.sh."
[[ -n "${LOG_SINK_NAME:-}" ]]    || die "LOG_SINK_NAME trống trong config.sh."
[[ -n "${RUN_SERVICE_PROD:-}" ]] || die "RUN_SERVICE_PROD trống trong config.sh."
[[ "${LOG_PARTITION_EXPIRATION_DAYS:-}" =~ ^[1-9][0-9]*$ ]] \
  || die "LOG_PARTITION_EXPIRATION_DAYS phải là số nguyên dương."

PART_EXP_SEC=$(( LOG_PARTITION_EXPIRATION_DAYS * 86400 ))
SINK_DEST="bigquery.googleapis.com/projects/${PROJECT_ID}/datasets/${DATASET_LOGS}"

# lọc: chỉ stdout JSON của app (bỏ system/stderr) + đúng service prod + allowlist message
LOG_FILTER="resource.type=\"cloud_run_revision\" \
AND resource.labels.service_name=\"${RUN_SERVICE_PROD}\" \
AND logName=\"projects/${PROJECT_ID}/logs/run.googleapis.com%2Fstdout\" \
AND (jsonPayload.message=\"http_request\" \
OR jsonPayload.message=\"market_metrics\" \
OR jsonPayload.message=\"bq_query\" \
OR jsonPayload.message=\"bq_query_timeout\")"

# --- 1) dataset log: partition expiration, KHÔNG table expiration ---
if bq --project_id="${PROJECT_ID}" show --dataset "${PROJECT_ID}:${DATASET_LOGS}" >/dev/null 2>&1; then
  ensure_dataset_location "${DATASET_LOGS}"
  skip "dataset ${DATASET_LOGS} (cập nhật partition expiration=${LOG_PARTITION_EXPIRATION_DAYS}d)"
  bq --project_id="${PROJECT_ID}" update \
    --default_partition_expiration "${PART_EXP_SEC}" \
    "${PROJECT_ID}:${DATASET_LOGS}" >/dev/null
else
  log "Tạo dataset ${DATASET_LOGS} (partition expiration=${LOG_PARTITION_EXPIRATION_DAYS}d, no table expiration)..."
  bq --project_id="${PROJECT_ID}" mk --dataset \
    --location="${BQ_LOCATION}" \
    --default_partition_expiration "${PART_EXP_SEC}" \
    --description="Cloud Logging sink đích — API metrics (Cloud Run stdout JSON)" \
    --label=app:jobs-serving --label=role:logs \
    "${PROJECT_ID}:${DATASET_LOGS}"
fi

# --- 1b) đồng bộ partition expiration cho BẢNG log nếu đã tồn tại ---
# Dataset default chỉ áp cho bảng partitioned TẠO MỚI; bảng đã có giữ cấu hình cũ → phải update riêng.
LOG_TABLE_FQ="${PROJECT_ID}:${DATASET_LOGS}.run_googleapis_com_stdout"
if bq --project_id="${PROJECT_ID}" show "${LOG_TABLE_FQ}" >/dev/null 2>&1; then
  log "Đồng bộ partition expiration của bảng log = ${LOG_PARTITION_EXPIRATION_DAYS}d..."
  bq --project_id="${PROJECT_ID}" update \
    --time_partitioning_expiration "${PART_EXP_SEC}" \
    "${LOG_TABLE_FQ}" >/dev/null
else
  # bq show fail = bảng CHƯA tồn tại (thường gặp: log đầu chưa tới) HOẶC thiếu quyền/credential —
  # không phân biệt được ở đây; nếu là lỗi quyền thì bước tạo sink/grant bên dưới sẽ báo lỗi rõ.
  log "Chưa đọc được bảng log (có thể chưa tồn tại — sẽ tạo khi có log đầu, kế thừa partition expiration của dataset; hoặc thiếu quyền)."
fi

# --- 2) sink: create nếu chưa có, update nếu đã có (đồng bộ dest/filter/partitioned) ---
if gcloud logging sinks describe "${LOG_SINK_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  log "Sink ${LOG_SINK_NAME} đã có → update (dest/filter/partitioned)."
  gcloud logging sinks update "${LOG_SINK_NAME}" "${SINK_DEST}" \
    --log-filter="${LOG_FILTER}" \
    --use-partitioned-tables \
    --project="${PROJECT_ID}" >/dev/null
else
  log "Tạo sink ${LOG_SINK_NAME} → ${DATASET_LOGS} (partitioned)..."
  gcloud logging sinks create "${LOG_SINK_NAME}" "${SINK_DEST}" \
    --log-filter="${LOG_FILTER}" \
    --use-partitioned-tables \
    --project="${PROJECT_ID}" >/dev/null
fi

# --- 3) cấp writer identity của sink quyền dataEditor CẤP DATASET ---
WRITER="$(gcloud logging sinks describe "${LOG_SINK_NAME}" --project="${PROJECT_ID}" \
  --format='value(writerIdentity)')"   # dạng: serviceAccount:...
[[ -n "${WRITER}" ]] || die "Không lấy được writerIdentity của sink."
WRITER_EMAIL="${WRITER#serviceAccount:}"
log "Writer identity: ${WRITER_EMAIL}"

# Exit code Python: 0 = đã sửa file (cần update) · 10 = đã có (no-op) · khác = LỖI → die.
grant_writer_on_logs() {
  local email="$1" dataset="$2"
  local tmp; tmp="$(mktemp)"
  bq show --format=prettyjson "${PROJECT_ID}:${dataset}" > "${tmp}"
  local rc=0
  python3 - "${tmp}" "${email}" <<'PY' || rc=$?
import json, sys
path, email = sys.argv[1], sys.argv[2]
d = json.load(open(path, encoding="utf-8"))
acc = d.setdefault("access", [])
if any(e.get("role") == "WRITER" and e.get("userByEmail") == email for e in acc):
    sys.exit(10)  # đã có → no-op
acc.append({"role": "WRITER", "userByEmail": email})
json.dump(d, open(path, "w", encoding="utf-8"))
PY
  case "${rc}" in
    0)  log "dataset ${dataset}: WRITER (dataEditor) → ${email}"
        bq update --source "${tmp}" "${PROJECT_ID}:${dataset}" >/dev/null ;;
    10) skip "dataset ${dataset}: WRITER → ${email}" ;;
    *)  rm -f "${tmp}"; die "grant_writer_on_logs: python lỗi (rc=${rc}) trên dataset ${dataset}." ;;
  esac
  rm -f "${tmp}"
}
grant_writer_on_logs "${WRITER_EMAIL}" "${DATASET_LOGS}"

# --- 4) cảnh báo bảng date-sharded cũ (nếu dataset từng nhận log không-partitioned) — KHÔNG tự xoá ---
LEGACY="$(bq --project_id="${PROJECT_ID}" ls --format="value(tableId)" "${DATASET_LOGS}" 2>/dev/null \
  | grep -E '^run_googleapis_com_stdout_[0-9]{8}$' || true)"
if [[ -n "${LEGACY}" ]]; then
  warn "Phát hiện bảng date-sharded cũ trong ${DATASET_LOGS} (không do sink partitioned tạo):"
  echo "${LEGACY}" | sed 's/^/    /'
  warn "Views API (87) CHỈ đọc bảng partitioned 'run_googleapis_com_stdout'. Xử lý các bảng cũ THỦ CÔNG nếu cần."
fi

log "Xong log sink. Log mới sẽ vào ${DATASET_LOGS}.run_googleapis_com_stdout (partitioned) sau vài phút."
log "Sinh traffic tới API prod (search/market/429) rồi chạy: bash 87-api-reporting-views.sh"
