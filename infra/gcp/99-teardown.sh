#!/usr/bin/env bash
# Dọn tài nguyên tốn phí. CÓ GUARD — không xoá nhầm. KHÔNG BAO GIỜ đụng prod.
#
#   bash 99-teardown.sh                 # chỉ IN kế hoạch, xoá gì cả (mặc định an toàn)
#   bash 99-teardown.sh redis           # xoá Memorystore Redis (nguồn tốn phí idle chính)
#   bash 99-teardown.sh staging-data    # xoá DATASET staging + toàn bộ bảng (hỏi gõ tên xác nhận)
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project
scope="${1:-plan}"   # require_cmd bq chỉ cần cho nhánh staging-data (redis/plan chỉ dùng gcloud)

teardown_redis() {
  if gcloud redis instances describe "${REDIS_INSTANCE}" \
       --region="${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    log "Xoá Redis ${REDIS_INSTANCE}..."
    gcloud redis instances delete "${REDIS_INSTANCE}" \
      --region="${REGION}" --project "${PROJECT_ID}" --quiet
    log "Đã xoá Redis."
  else
    skip "Redis ${REDIS_INSTANCE} (không tồn tại)"
  fi
}

teardown_staging_data() {
  require_cmd bq python3   # nhánh này cần bq (rm) + python3 (đọc label)

  # GUARD 1: staging PHẢI khác prod — chống cấu hình nhầm trỏ cả hai vào cùng 1 dataset.
  [[ "${DATASET_STAGING}" != "${DATASET_PROD}" ]] \
    || die "DATASET_STAGING == DATASET_PROD == '${DATASET_STAGING}' — TỪ CHỐI xoá (chống xoá nhầm prod). Sửa config.sh."

  # GUARD 2: dataset PHẢI có label env=staging (đọc metadata; tách lỗi tool/quyền khỏi trạng thái).
  local json env_label
  json="$(bq --project_id="${PROJECT_ID}" --format=json show --dataset "${PROJECT_ID}:${DATASET_STAGING}")" \
    || die "Không đọc được metadata dataset ${DATASET_STAGING} (credential/network/quyền?) — TỪ CHỐI xoá."
  env_label="$(printf '%s' "${json}" | python3 -c 'import sys,json;print((json.load(sys.stdin).get("labels") or {}).get("env",""))')" \
    || die "Không parse được metadata dataset ${DATASET_STAGING} — TỪ CHỐI xoá."
  [[ "${env_label}" == "staging" ]] \
    || die "Dataset ${DATASET_STAGING} có label env='${env_label:-<không có>}' ≠ 'staging' — TỪ CHỐI xoá (chỉ xoá dataset staging thật; prod/không nhãn được bảo vệ)."

  # GUARD 3: xác nhận tên (chỉ tới đây sau khi qua 2 guard trên).
  warn "SẮP XOÁ dataset '${DATASET_STAGING}' + TOÀN BỘ bảng trong đó (KHÔNG khôi phục được)."
  printf "Gõ đúng tên dataset để xác nhận [%s]: " "${DATASET_STAGING}"
  read -r ans
  [[ "${ans}" == "${DATASET_STAGING}" ]] || die "Không khớp — huỷ, không xoá gì."
  log "Xoá dataset ${DATASET_STAGING}..."
  bq rm -r -f --dataset "${PROJECT_ID}:${DATASET_STAGING}"
  log "Đã xoá dataset staging."
}

case "${scope}" in
  plan)
    echo "Các lựa chọn teardown (chưa xoá gì):"
    echo "  redis         → xoá Memorystore Redis (${REDIS_INSTANCE}) — nên xoá khi hết demo"
    echo "  staging-data  → xoá dataset ${DATASET_STAGING} + bảng (hỏi xác nhận)"
    echo "prod KHÔNG có trong teardown — bảo vệ dữ liệu."
    ;;
  redis)         teardown_redis ;;
  staging-data)  teardown_staging_data ;;
  *)             die "scope không hợp lệ: '${scope}'. Dùng: plan | redis | staging-data" ;;
esac
