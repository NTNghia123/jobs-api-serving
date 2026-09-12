#!/usr/bin/env bash
# Tạo 5 service account tách môi trường. Idempotent: có rồi → bỏ qua. KHÔNG tạo JSON key.
# Phân quyền IAM ở 21-iam-bindings.sh (sau khi SA tồn tại).
# Chạy: bash 20-service-accounts.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project

create_sa() {
  local name="$1" display="$2"
  if gcloud iam service-accounts describe "$(sa_email "$name")" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    skip "SA ${name}"
  else
    log "Tạo SA ${name}"
    gcloud iam service-accounts create "${name}" \
      --display-name="${display}" --project "${PROJECT_ID}"
  fi
}

create_sa "${SA_API_READER_STAGING}"   "Jobs API reader (staging) — Cloud Run runtime"
create_sa "${SA_API_READER_PROD}"      "Jobs API reader (prod) — Cloud Run runtime"
create_sa "${SA_DAGSTER_ELT}"          "Dagster ELT writer (Mongo→BigQuery)"
create_sa "${SA_CI_DEPLOYER_STAGING}"  "CI deployer (staging) — GitHub Actions via WIF"
create_sa "${SA_CI_DEPLOYER_PROD}"     "CI deployer (prod) — manual"

log "SA hiện có (dự án này):"
gcloud iam service-accounts list --project "${PROJECT_ID}" \
  --format="value(email)" | grep -E "sa-(api-reader|dagster|ci-deployer)" | sed 's/^/    /' || true
log "Bước tiếp: bash 21-iam-bindings.sh"
