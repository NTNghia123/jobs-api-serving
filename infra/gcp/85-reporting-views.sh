#!/usr/bin/env bash
# Tạo dataset jobs_reporting + các view rpt_* CORE (cho Looker Studio) từ reporting/views_core.sql.
# Authorize jobs_reporting làm authorized-dataset reader của jobs_prod (viewer Looker không cần quyền
# trực tiếp trên jobs_prod — dùng Owner's Credentials ở data source).
# Idempotent: dataset có rồi → bỏ qua; view dùng CREATE OR REPLACE; authorization có rồi → bỏ qua.
#
# YÊU CẦU: jobs_prod đã có bảng silver_jobs/gold_market_metrics/warehouse_* (ELT đã publish ≥1 batch
#          thì verify mới có số; view vẫn tạo được khi chưa có batch).
# Chạy: bash 85-reporting-views.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project
require_cmd bq python3

# --- validate biến riêng của cụm này ---
[[ -n "${DATASET_REPORTING:-}" ]] || die "DATASET_REPORTING trống trong config.sh."
[[ -n "${DATASET_PROD:-}" ]]      || die "DATASET_PROD trống trong config.sh."
[[ "${METRICS_MIN_SAMPLE_SIZE:-}" =~ ^[1-9][0-9]*$ ]] \
  || die "METRICS_MIN_SAMPLE_SIZE phải là số nguyên dương (thấy: '${METRICS_MIN_SAMPLE_SIZE:-}')."

SQL_SRC="./reporting/views_core.sql"
[[ -f "${SQL_SRC}" ]] || die "Không thấy ${SQL_SRC}."

# --- 1) dataset jobs_reporting (idempotent, cùng location) ---
create_reporting_dataset() {
  local fq="${PROJECT_ID}:${DATASET_REPORTING}"
  if bq --project_id="${PROJECT_ID}" show --dataset "${fq}" >/dev/null 2>&1; then
    ensure_dataset_location "${DATASET_REPORTING}"
    skip "dataset ${DATASET_REPORTING} (location ${BQ_LOCATION})"
    return
  fi
  log "Tạo dataset ${DATASET_REPORTING} tại ${BQ_LOCATION}..."
  bq --project_id="${PROJECT_ID}" mk --dataset \
    --location="${BQ_LOCATION}" \
    --description="Looker Studio reporting views (rpt_*) — authorized reader của ${DATASET_PROD}" \
    --label=app:jobs-serving --label=role:reporting \
    "${fq}"
}

# --- 2) render SQL: thay placeholder; __K__ PHẢI đúng 1 lần; không còn placeholder sau render ---
render_sql() {
  local src="$1" out="$2"
  local k_count; k_count="$( { grep -o '__K__' "${src}" || true; } | wc -l | tr -d '[:space:]')"
  [[ "${k_count}" == "1" ]] \
    || die "views_core.sql phải chứa __K__ đúng 1 lần (thấy ${k_count}) — k phải tập trung ở rpt_reporting_config."
  sed -e "s|__PROJECT__|${PROJECT_ID}|g" \
      -e "s|__DS_PROD__|${DATASET_PROD}|g" \
      -e "s|__DS_RPT__|${DATASET_REPORTING}|g" \
      -e "s|__K__|${METRICS_MIN_SAMPLE_SIZE}|g" \
      "${src}" > "${out}"
  if grep -qE '__[A-Z_]+__' "${out}"; then
    err "Còn placeholder chưa thay trong SQL đã render:"
    grep -oE '__[A-Z_]+__' "${out}" | sort -u | sed 's/^/    /' >&2
    die "render lỗi."
  fi
  log "Render SQL OK (k=${METRICS_MIN_SAMPLE_SIZE})."
}

# --- 3) authorized dataset: helper authorize_dataset_reader nằm ở lib.sh ---

# --- 4) verify: SKIP nếu chưa publish batch; nhưng khi đã có batch, mismatch = LỖI (die) ---
# bq_scalar để set -e bắt được lỗi query (không nuốt bằng 2>/dev/null): query hỏng → script dừng.
bq_scalar() {  # chạy 1 query trả 1 giá trị (cột đầu, dòng đầu)
  bq --project_id="${PROJECT_ID}" --location="${BQ_LOCATION}" query \
    --use_legacy_sql=false --format=csv --quiet "$1" | tail -n +2 | head -n1
}

verify_views() {
  log "Verify..."
  local silver_rows job_rows k_rows dict_dup null_sen

  # Các invariant KHÔNG phụ thuộc có batch hay chưa → luôn strict.
  k_rows="$(bq_scalar "SELECT COUNT(*) FROM \`${PROJECT_ID}.${DATASET_REPORTING}.rpt_reporting_config\`")"
  [[ "${k_rows}" == "1" ]] || die "rpt_reporting_config = ${k_rows} dòng (mong đợi 1)."
  log "  rpt_reporting_config = 1 dòng ✓"

  null_sen="$(bq_scalar "SELECT COUNTIF(seniority IS NULL) FROM \`${PROJECT_ID}.${DATASET_REPORTING}.rpt_jobs\`")"
  [[ "${null_sen:-0}" == "0" ]] || die "seniority IS NULL = ${null_sen} (mong đợi 0)."
  log "  seniority IS NULL = 0 ✓"

  dict_dup="$(bq_scalar "SELECT COUNT(*) FROM (SELECT category_key FROM \`${PROJECT_ID}.${DATASET_REPORTING}.rpt_jobs_by_category\` GROUP BY category_key HAVING COUNT(DISTINCT source)>1 OR COUNT(DISTINCT category_name)>1)")"
  [[ "${dict_dup:-0}" == "0" ]] || die "category_key đa tên/source = ${dict_dup} (mong đợi 0)."
  log "  category_key không đa tên/source = 0 ✓"

  # Đối chiếu số dòng: chỉ SKIP khi chưa publish batch; đã có batch mà lệch = lỗi triển khai → die.
  silver_rows="$(bq_scalar "SELECT COALESCE(CAST(silver_rows AS STRING),'') FROM \`${PROJECT_ID}.${DATASET_REPORTING}.rpt_current_batch\`")"
  if [[ -z "${silver_rows}" ]]; then
    warn "  rpt_current_batch trống — chưa publish batch nào. Bỏ qua đối chiếu silver_rows."
    return 0
  fi
  job_rows="$(bq_scalar "SELECT COUNT(*) FROM \`${PROJECT_ID}.${DATASET_REPORTING}.rpt_jobs\`")"
  [[ "${silver_rows}" == "${job_rows}" ]] \
    || die "rpt_jobs COUNT(*)=${job_rows} ≠ current_batch.silver_rows=${silver_rows}."
  log "  rpt_jobs COUNT(*)=${job_rows} == current_batch.silver_rows ✓"
}

# ===== chạy =====
create_reporting_dataset

RENDERED="$(mktemp)"; trap 'rm -f "${RENDERED}"' EXIT
render_sql "${SQL_SRC}" "${RENDERED}"

log "Tạo/replace các view rpt_* CORE..."
bq --project_id="${PROJECT_ID}" --location="${BQ_LOCATION}" query \
  --use_legacy_sql=false --quiet < "${RENDERED}"
log "Đã tạo view. Danh sách:"
bq --project_id="${PROJECT_ID}" ls --format="value(tableId)" "${DATASET_REPORTING}" | sed 's/^/    /'

authorize_dataset_reader "${DATASET_PROD}" "${DATASET_REPORTING}"

verify_views

log "Xong CORE reporting views."
log "Bước tiếp: cấp cho data-source owner (Looker) roles/bigquery.dataViewer trên ${DATASET_REPORTING} + roles/bigquery.jobUser trên project; rồi bật log sink: bash 86-logging-sink.sh"
