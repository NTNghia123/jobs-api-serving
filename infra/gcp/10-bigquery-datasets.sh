#!/usr/bin/env bash
# Tạo 2 dataset BigQuery (staging + prod) cùng location. Idempotent: có rồi → bỏ qua.
# LƯU Ý: location của dataset KHÔNG đổi được sau khi tạo — chắc chắn BQ_LOCATION đúng.
# Bảng (silver/gold/warehouse_*) do ELT tạo ở Phase 2/3, KHÔNG tạo ở đây.
# Chạy: bash 10-bigquery-datasets.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project

create_dataset() {
  local ds="$1" env="$2"
  local fq="${PROJECT_ID}:${ds}"
  if bq --project_id="${PROJECT_ID}" show --dataset "${fq}" >/dev/null 2>&1; then
    # kiểm location khớp (cảnh báo nếu lệch — không tự sửa được)
    local loc; loc="$(bq --project_id="${PROJECT_ID}" --format=json show --dataset "${fq}" \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["location"])' 2>/dev/null || echo "?")"
    if [[ "${loc}" != "${BQ_LOCATION}" ]]; then
      warn "Dataset ${ds} đã tồn tại nhưng location=${loc} ≠ ${BQ_LOCATION} (không đổi được — phải xoá & tạo lại nếu muốn khác)."
    else
      skip "dataset ${ds} (location ${loc})"
    fi
    return
  fi
  log "Tạo dataset ${ds} tại ${BQ_LOCATION}..."
  bq --project_id="${PROJECT_ID}" mk \
    --dataset \
    --location="${BQ_LOCATION}" \
    --description="Jobs serving warehouse (${env}) — silver/gold/warehouse_state/batches" \
    --label=app:jobs-serving --label=env:"${env}" \
    "${fq}"
}

create_dataset "${DATASET_STAGING}" "staging"
create_dataset "${DATASET_PROD}" "prod"

log "Dataset hiện có:"
bq --project_id="${PROJECT_ID}" ls --datasets --format="value(datasetId)" | sed 's/^/    /'
log "Bước tiếp: bash 20-service-accounts.sh"
