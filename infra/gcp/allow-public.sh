#!/usr/bin/env bash
# Cho phép truy cập PUBLIC (unauthenticated ở tầng hạ tầng) cho service Cloud Run — chạy 1 LẦN
# bằng creds owner (fork B: CI deployer chỉ run.developer, KHÔNG tự set được IAM này).
#   /v1/* vẫn bắt buộc X-API-Key ở tầng app (ADR-012) — "public" ở đây chỉ là allUsers→run.invoker.
#
# Điều kiện: service PHẢI tồn tại trước (deploy lần đầu qua CI/thủ công). Bind kế thừa cho mọi
# revision sau → chỉ cần chạy 1 lần cho mỗi service (staging 1 lần, prod 1 lần).
# Idempotent: add-iam-policy-binding có sẵn = no-op.
#
# Chạy: bash allow-public.sh <staging|prod>
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

ENV="${1:-}"
case "${ENV}" in
  staging) service="${RUN_SERVICE_STAGING:-jobs-serving-api-staging}";;
  prod)    service="${RUN_SERVICE_PROD:-jobs-serving-api-prod}";;
  *) die "Cách dùng: bash $(basename "$0") <staging|prod>";;
esac

show_target
echo "  ENV     : ${ENV}"
echo "  SERVICE : ${service}"
echo "----------------------------------------------------------------"
ensure_project

gcloud run services describe "${service}" --region="${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1 \
  || die "Service ${service} chưa tồn tại. Deploy lần đầu trước (CI hoặc: bash deploy-cloud-run.sh ${ENV})."

log "Gán allUsers → roles/run.invoker cho ${service} (public ở tầng hạ tầng)"
gcloud run services add-iam-policy-binding "${service}" \
  --region="${REGION}" --project "${PROJECT_ID}" \
  --member="allUsers" --role="roles/run.invoker" >/dev/null

log "Xong. Service ${service} nhận request không xác thực ở hạ tầng; /v1/* vẫn cần X-API-Key."
