#!/usr/bin/env bash
# Deploy Serving API lên Cloud Run — tham số hoá theo môi trường (staging|prod).
# Fork Phase 6 đã chốt (ADR-022): (1A) gcloud flags; (2A) staging tự động qua CI/WIF, prod THỦ CÔNG;
# (3A) memory-first, toggle WITH_REDIS mới bật Redis + Direct VPC egress.
#
# CÁCH DÙNG:
#   IMAGE_TAG=<git-sha> bash deploy-cloud-run.sh staging     # CI (cụm 6.2) thường gọi cách này
#   IMAGE_TAG=<git-sha> bash deploy-cloud-run.sh prod        # THỦ CÔNG (không qua WIF)
#   WITH_REDIS=1 REDIS_HOST=10.x.x.x IMAGE_TAG=<sha> bash deploy-cloud-run.sh staging
#
# Không set IMAGE_TAG → dùng commit HEAD hiện tại (image tag đó PHẢI đã build & push lên AR).
# KHÔNG dùng GOOGLE_APPLICATION_CREDENTIALS — Cloud Run chạy dưới SA reader, ADC tự lấy token.
# Idempotent: chạy lại = tạo revision mới của cùng service.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

# --- 1) tham số môi trường ---
ENV="${1:-}"
case "${ENV}" in
  staging|prod) ;;
  *) die "Cách dùng: bash $(basename "$0") <staging|prod>  (thiếu hoặc sai môi trường)";;
esac

# --- 2) cấu hình runtime (default an toàn; override được trong config.sh) ---
AR_IMAGE="${AR_IMAGE:-api}"
RUN_CPU="${RUN_CPU:-1}"
RUN_MEMORY="${RUN_MEMORY:-512Mi}"
RUN_CONCURRENCY="${RUN_CONCURRENCY:-40}"
RUN_TIMEOUT="${RUN_TIMEOUT:-25}"                 # Cloud Run > request(20) > query(10) — xem ADR-016
RUN_MAX_INSTANCES="${RUN_MAX_INSTANCES:-3}"

# --- 3) phân giải theo môi trường (dataset + SA reader + secret + tên service) ---
if [[ "${ENV}" == "staging" ]]; then
  service="${RUN_SERVICE_STAGING:-jobs-serving-api-staging}"
  dataset="${DATASET_STAGING}"
  sa="$(sa_email "${SA_API_READER_STAGING}")"
  secret_api_keys="${SECRET_API_KEYS_STAGING}"
  secret_page_token="${SECRET_PAGE_TOKEN_STAGING}"
else
  service="${RUN_SERVICE_PROD:-jobs-serving-api-prod}"
  dataset="${DATASET_PROD}"
  sa="$(sa_email "${SA_API_READER_PROD}")"
  secret_api_keys="${SECRET_API_KEYS_PROD}"
  secret_page_token="${SECRET_PAGE_TOKEN_PROD}"
fi

# --- 4) image tag = commit SHA (bất biến, truy vết được) ---
IMAGE_TAG="${IMAGE_TAG:-}"
if [[ -z "${IMAGE_TAG}" ]]; then
  IMAGE_TAG="$(git rev-parse --short=12 HEAD 2>/dev/null || true)"
  [[ -n "${IMAGE_TAG}" ]] || die "Thiếu IMAGE_TAG và không đọc được git HEAD. Đặt IMAGE_TAG=<sha>."
  warn "IMAGE_TAG không đặt → dùng HEAD ${IMAGE_TAG} (chắc chắn tag này ĐÃ push lên AR)."
fi
image="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${AR_IMAGE}:${IMAGE_TAG}"

show_target
echo "  ENV     : ${ENV}"
echo "  SERVICE : ${service}"
echo "  IMAGE   : ${image}"
echo "  SA      : ${sa}"
echo "  DATASET : ${dataset}"
echo "----------------------------------------------------------------"
ensure_project

# --- 5) verify image tồn tại trong AR (chặn deploy nhầm tag gõ sai) ---
if gcloud artifacts docker images describe "${image}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  log "Image tồn tại trong Artifact Registry."
else
  die "Không thấy image ${image} trong AR. Build & push tag ${IMAGE_TAG} trước (CI cụm 6.2, hoặc thủ công)."
fi

# --- 6) pin secret version (bất biến/tái lập; override bằng *_SECRET_VERSION) ---
latest_enabled() {  # in số version ENABLED mới nhất của 1 secret
  gcloud secrets versions list "$1" --project "${PROJECT_ID}" \
    --filter="state=ENABLED" --sort-by="~createTime" --limit=1 --format="value(name)" 2>/dev/null
}
ver_api_keys="${API_KEYS_SECRET_VERSION:-$(latest_enabled "${secret_api_keys}")}"
ver_page_token="${PAGE_TOKEN_SECRET_VERSION:-$(latest_enabled "${secret_page_token}")}"
[[ -n "${ver_api_keys}" ]]   || die "Secret ${secret_api_keys} chưa có version ENABLED. Nạp JSON key (xem 40-secrets.sh) trước khi deploy ${ENV}."
[[ -n "${ver_page_token}" ]] || die "Secret ${secret_page_token} chưa có version ENABLED (chạy 40-secrets.sh)."
log "Pin secret: ${secret_api_keys}:${ver_api_keys} · ${secret_page_token}:${ver_page_token}"

# --- 7) env vars runtime (backend=bigquery + cost guard + k-anon dùng default settings) ---
env_vars="JOBS_API_ENV=${ENV}"
env_vars+=",JOBS_API_WAREHOUSE_BACKEND=bigquery"
env_vars+=",JOBS_API_BQ_PROJECT=${PROJECT_ID}"
env_vars+=",JOBS_API_BQ_DATASET=${dataset}"
env_vars+=",JOBS_API_BQ_LOCATION=${BQ_LOCATION}"
env_vars+=",JOBS_API_BQ_MAXIMUM_BYTES_BILLED=${BQ_MAX_BYTES_BILLED}"
# ★ TUẦN 8 — tracing OTLP → Telemetry API → Cloud Trace. Override được qua config.sh.
# Sampling mặc định THẤP (0.1) — prod không trace 100% (tốn chi phí/volume). Nghiệm thu trace
# KHÔNG cần nâng ratio: smoke TRACE_DEMO gửi traceparent '-01' → ParentBased ép sample bất kể ratio.
# Chỉ đặt OTEL_SAMPLING_RATIO=1.0 khi thực sự muốn trace toàn bộ (vd staging điều tra).
env_vars+=",JOBS_API_OTEL_TRACES_EXPORTER=${OTEL_TRACES_EXPORTER:-otlp}"
env_vars+=",JOBS_API_OTEL_SAMPLING_RATIO=${OTEL_SAMPLING_RATIO:-0.1}"

# --- 8) fork 3A: memory-first; WITH_REDIS=1 mới bật Redis + Direct VPC egress ---
vpc_flags=()
if [[ "${WITH_REDIS:-}" == "1" ]]; then
  [[ -n "${REDIS_HOST:-}" ]] || die "WITH_REDIS=1 cần REDIS_HOST=<ip private Memorystore> (chạy 60-networking-redis.sh để lấy)."
  redis_port="${REDIS_PORT:-6379}"
  log "Bật Redis (${REDIS_HOST}:${redis_port}) + Direct VPC egress qua network ${VPC_NETWORK}."
  env_vars+=",JOBS_API_CACHE_BACKEND=redis"
  env_vars+=",JOBS_API_RATE_LIMITER_BACKEND=redis"
  env_vars+=",JOBS_API_REDIS_URL=redis://${REDIS_HOST}:${redis_port}/0"
  vpc_flags=(--network="${VPC_NETWORK}" --subnet="${VPC_NETWORK}" --vpc-egress=private-ranges-only)
else
  log "Chế độ memory (chưa Redis) — cache & rate-limit in-process, không VPC egress (khỏi tốn phí Memorystore)."
  env_vars+=",JOBS_API_CACHE_BACKEND=memory"
  env_vars+=",JOBS_API_RATE_LIMITER_BACKEND=memory"
fi

# --- 9) public access (fork B: least-privilege) ---
# Public = gán allUsers→run.invoker, CẦN quyền setIamPolicy (chỉ run.admin có). CI deployer chỉ
# run.developer → PUBLIC_ACCESS=0: deploy giữ private, KHÔNG chạm IAM; bind public 1 lần riêng
# bằng creds owner (allow-public.sh). Prod chạy tay bằng owner → mặc định PUBLIC_ACCESS=1 OK.
# Dù public ở tầng hạ tầng, /v1/* vẫn bắt buộc X-API-Key ở tầng app (ADR-012).
auth_flags=()
if [[ "${PUBLIC_ACCESS:-1}" == "1" ]]; then
  auth_flags+=(--allow-unauthenticated)
  log "PUBLIC_ACCESS=1 → set public (allUsers→run.invoker) ngay khi deploy."
else
  warn "PUBLIC_ACCESS=0 → deploy giữ private (không chạm IAM). Bind public 1 lần: bash allow-public.sh ${ENV}"
fi

# --- 10) deploy (tạo hoặc cập nhật service = revision mới) ---
log "Deploy Cloud Run ${service} ..."
gcloud run deploy "${service}" \
  --image="${image}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --service-account="${sa}" \
  --quiet \
  --port=8080 \
  --cpu="${RUN_CPU}" \
  --memory="${RUN_MEMORY}" \
  --concurrency="${RUN_CONCURRENCY}" \
  --timeout="${RUN_TIMEOUT}" \
  --max-instances="${RUN_MAX_INSTANCES}" \
  --set-env-vars="${env_vars}" \
  --set-secrets="JOBS_API_PAGE_TOKEN_SECRET=${secret_page_token}:${ver_page_token},JOBS_API_API_KEYS=${secret_api_keys}:${ver_api_keys}" \
  "${auth_flags[@]}" \
  "${vpc_flags[@]}" \
  --labels=app=jobs-serving,env="${ENV}"

url="$(gcloud run services describe "${service}" --region="${REGION}" \
  --project="${PROJECT_ID}" --format='value(status.url)')"
log "Deploy xong. URL: ${url}"
echo "    → Smoke test (cụm 6.3): BASE_URL=${url} API_KEY=<key-thô> bash smoke.sh"
