#!/usr/bin/env bash
# Bật các API cần cho toàn dự án. `gcloud services enable` idempotent (bật rồi → no-op).
# Chạy: bash 01-enable-apis.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project

APIS=(
  bigquery.googleapis.com          # kho dữ liệu
  run.googleapis.com               # Cloud Run (serving API)
  artifactregistry.googleapis.com  # kho Docker image
  secretmanager.googleapis.com     # secret (API keys, page-token)
  iam.googleapis.com               # service accounts
  iamcredentials.googleapis.com    # WIF / impersonation
  sts.googleapis.com               # Security Token Service (WIF)
  redis.googleapis.com             # Memorystore (cụm 1.6)
  vpcaccess.googleapis.com         # VPC connector (fallback egress)
  compute.googleapis.com           # VPC/network cho Direct VPC egress + Redis
  cloudbilling.googleapis.com      # đọc billing / budget
  cloudresourcemanager.googleapis.com
  logging.googleapis.com
  monitoring.googleapis.com
  telemetry.googleapis.com         # ★ TUẦN 8 — nhận OTLP trace (endpoint telemetry.googleapis.com)
  cloudtrace.googleapis.com        # ★ TUẦN 8 — lưu/xem trace trên Cloud Trace
)

log "Bật ${#APIS[@]} API (có thể mất 1–2 phút)..."
gcloud services enable "${APIS[@]}" --project "${PROJECT_ID}"
log "Xong. API đã bật:"
gcloud services list --enabled --project "${PROJECT_ID}" \
  --filter="config.name:(bigquery OR run OR artifactregistry OR secretmanager OR redis OR telemetry OR cloudtrace)" \
  --format="value(config.name)" | sed 's/^/    /'

log "Bước tiếp: bash 10-bigquery-datasets.sh"
