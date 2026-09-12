#!/usr/bin/env bash
# Budget alert: cảnh báo khi chi phí project vượt ngưỡng (50/80/100% của BUDGET_AMOUNT_USD).
# Bảo vệ trial credit khỏi cháy ngoài ý muốn. Idempotent (theo display-name). Chỉ CẢNH BÁO,
# KHÔNG tự chặn chi tiêu. Chạy: bash 70-budget.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project

# API cho budget
gcloud services enable billingbudgets.googleapis.com --project "${PROJECT_ID}" >/dev/null

# billing account gắn với project
BA="$(gcloud billing projects describe "${PROJECT_ID}" \
  --format='value(billingAccountName)' 2>/dev/null || true)"   # dạng billingAccounts/XXXXXX
[[ -n "${BA}" ]] || die "Không đọc được billing account của ${PROJECT_ID} (thiếu quyền billing?)."
log "Billing account: ${BA}"

DISPLAY="jobs-serving budget"
existing="$(gcloud billing budgets list --billing-account="${BA}" \
  --filter="displayName='${DISPLAY}'" --format="value(name)" 2>/dev/null | head -1 || true)"

if [[ -n "${existing}" ]]; then
  skip "budget '${DISPLAY}' (đã có)"
else
  log "Tạo budget '${DISPLAY}' = ${BUDGET_AMOUNT_USD} USD/tháng, cảnh báo 50/80/100%"
  gcloud billing budgets create \
    --billing-account="${BA}" \
    --display-name="${DISPLAY}" \
    --budget-amount="${BUDGET_AMOUNT_USD}USD" \
    --filter-projects="projects/${PROJECT_ID}" \
    --threshold-rule=percent=0.5 \
    --threshold-rule=percent=0.8 \
    --threshold-rule=percent=1.0
fi

log "Budget cảnh báo gửi email tới admin của billing account (${BA})."
log "Bước tiếp: bash 99-teardown.sh (chỉ khi cần dọn)."
