#!/usr/bin/env bash
# Cấu hình hạ tầng GCP — COPY file này thành `config.sh` rồi điền giá trị của BẠN.
#   cp config.example.sh config.sh    # rồi sửa config.sh
# `config.sh` KHÔNG commit (đã ignore) — mỗi người/môi trường một bản.
#
# CÁCH TÌM PROJECT_ID: mở https://console.cloud.google.com → góc trên chọn project →
# hộp thoại hiện "ID" (khác với "Name"). Hoặc chạy trong Cloud Shell: gcloud config get-value project

# --- BẮT BUỘC điền ---
export PROJECT_ID="dien-project-id-cua-ban"     # vd: jobs-serving-471203

# --- Region (đã khoá asia-southeast1 = Singapore, gần VN nhất). Đổi TRƯỚC khi chạy nếu cần. ---
export REGION="asia-southeast1"
export BQ_LOCATION="asia-southeast1"             # PHẢI trùng REGION; KHÔNG đổi sau khi tạo dataset

# --- BigQuery datasets (1 project, 2 môi trường) ---
export DATASET_STAGING="jobs_staging"
export DATASET_PROD="jobs_prod"

# --- Looker Studio reporting (Phase Looker — 85/86/87) ---
export DATASET_REPORTING="jobs_reporting"         # dataset chứa view rpt_* cho Looker (authorized reader của jobs_prod + jobs_prod_logs)
export DATASET_LOGS="jobs_prod_logs"              # đích của Cloud Logging sink (API metrics); default PARTITION expiration
export METRICS_MIN_SAMPLE_SIZE="5"               # ngưỡng k-anon median lương — PHẢI khớp app settings metrics_min_sample_size
export LOG_SINK_NAME="jobs-api-logs-to-bq"       # tên Cloud Logging sink → BigQuery
export LOG_PARTITION_EXPIRATION_DAYS="90"        # tuổi tối đa 1 partition log (retention; không đặt table expiration)
export API_REPORT_LOOKBACK_DAYS="90"             # cửa sổ hiển thị của view rpt_api_* (≤ retention để có đủ dữ liệu)

# --- Service accounts (tên ngắn, không kèm @...; script tự ghép email) ---
export SA_API_READER_STAGING="sa-api-reader-staging"
export SA_API_READER_PROD="sa-api-reader-prod"
export SA_DAGSTER_ELT="sa-dagster-elt"
export SA_CI_DEPLOYER_STAGING="sa-ci-deployer-staging"
export SA_CI_DEPLOYER_PROD="sa-ci-deployer-prod"

# --- Artifact Registry (Docker) ---
export AR_REPO="jobs-serving"

# --- Secret Manager (tách môi trường — KHÔNG dùng chung) ---
export SECRET_API_KEYS_STAGING="jobs-api-keys-staging"
export SECRET_API_KEYS_PROD="jobs-api-keys-prod"
export SECRET_PAGE_TOKEN_STAGING="jobs-api-page-token-secret-staging"
export SECRET_PAGE_TOKEN_PROD="jobs-api-page-token-secret-prod"

# --- Cost guard ---
export BUDGET_AMOUNT_USD="50"                    # ngưỡng cảnh báo chi phí/tháng (USD)
export BQ_MAX_BYTES_BILLED="2000000000"          # 2 GB trần mỗi query (an toàn cho corpus ~15k job)
export RUN_MAX_INSTANCES="3"                     # trần số instance Cloud Run

# --- Cloud Run runtime (Phase 6 — deploy-cloud-run.sh) ---
export RUN_SERVICE_STAGING="jobs-serving-api-staging"
export RUN_SERVICE_PROD="jobs-serving-api-prod"
export AR_IMAGE="api"                            # tên image trong repo AR → path .../${AR_REPO}/${AR_IMAGE}
export RUN_CPU="1"
export RUN_MEMORY="512Mi"
export RUN_CONCURRENCY="40"                      # số request đồng thời mỗi instance
export RUN_TIMEOUT="25"                          # giây; PHẢI > request_timeout_s(20) > query_timeout_s(10)

# --- WIF cho GitHub Actions (điền khi tới cụm 1.5; dạng "owner/repo") ---
export GITHUB_REPO="NTNghia123/jobs-api-serving"

# --- Memorystore Redis (cụm 1.6 — script sẵn, chạy sau khi cần demo) ---
export REDIS_INSTANCE="jobs-cache"
export REDIS_TIER="basic"                        # basic = rẻ nhất (1 node, không HA)
export REDIS_SIZE_GB="1"
export VPC_NETWORK="default"

# --- Backup Mongo → GCS (Phase 7 — 80-backup-gcs.sh; fork B: writer = sa-dagster-elt objectCreator) ---
export BACKUP_BUCKET=""                           # trống → script tự đặt "${PROJECT_ID}-mongo-backup" (tên bucket TOÀN CẦU duy nhất)
export BACKUP_RETENTION_DAYS="30"                 # lifecycle: xoá object cũ hơn N ngày
export SA_BACKUP_RESTORE="sa-backup-restore"      # identity RIÊNG để restore (objectViewer), tách khỏi writer
