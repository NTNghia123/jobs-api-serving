#!/usr/bin/env bash
# Memorystore Redis (cache + rate-limit chia sẻ đa instance) — cùng region, private IP.
#
# ⚠️  CHI PHÍ: Memorystore KHÔNG thuộc Always Free — tính tiền kể cả khi idle.
#     Script này CHỈ chạy khi bạn chủ động đặt CONFIRM_REDIS=1:
#         CONFIRM_REDIS=1 bash 60-networking-redis.sh
#     Không có biến đó → chỉ IN kế hoạch rồi thoát (không tạo gì). Xoá sau khi demo: bash 99-teardown.sh
#
# Cloud Run → Redis dùng Direct VPC egress (đặt lúc deploy Cloud Run ở Phase 6, KHÔNG ở đây).
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project

REDIS_VERSION="${REDIS_VERSION:-redis_7_0}"
net="projects/${PROJECT_ID}/global/networks/${VPC_NETWORK}"

plan() {
  echo "  Redis instance : ${REDIS_INSTANCE}  (tier=${REDIS_TIER}, ${REDIS_SIZE_GB}GB, ${REDIS_VERSION})"
  echo "  Region/network : ${REGION} / ${VPC_NETWORK}  (connect-mode DIRECT_PEERING, private IP)"
  echo "  Cloud Run egress (Phase 6): --network=${VPC_NETWORK} --subnet=${VPC_NETWORK} --vpc-egress=private-ranges-only"
}

if [[ "${CONFIRM_REDIS:-}" != "1" ]]; then
  warn "CHƯA tạo Redis (an toàn chi phí). Sẽ tạo nếu chạy: CONFIRM_REDIS=1 bash $(basename "$0")"
  echo "Kế hoạch nếu chạy:"
  plan
  exit 0
fi

if gcloud redis instances describe "${REDIS_INSTANCE}" \
     --region="${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  skip "Redis ${REDIS_INSTANCE}"
else
  log "Tạo Memorystore Redis (tốn phí từ giờ)..."
  plan
  gcloud redis instances create "${REDIS_INSTANCE}" \
    --size="${REDIS_SIZE_GB}" \
    --region="${REGION}" \
    --tier="${REDIS_TIER}" \
    --redis-version="${REDIS_VERSION}" \
    --network="${net}" \
    --connect-mode=DIRECT_PEERING \
    --labels=app=jobs-serving \
    --project "${PROJECT_ID}"
fi

host="$(gcloud redis instances describe "${REDIS_INSTANCE}" --region="${REGION}" \
  --project "${PROJECT_ID}" --format='value(host)' 2>/dev/null || echo '?')"
port="$(gcloud redis instances describe "${REDIS_INSTANCE}" --region="${REGION}" \
  --project "${PROJECT_ID}" --format='value(port)' 2>/dev/null || echo '?')"
log "Redis private endpoint: ${host}:${port}"
echo "    → Cloud Run env (Phase 6): JOBS_API_REDIS_URL=redis://${host}:${port}/0"
warn "Nhớ teardown khi hết demo để khỏi tốn credit: bash 99-teardown.sh"
