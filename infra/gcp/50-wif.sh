#!/usr/bin/env bash
# Workload Identity Federation cho GitHub Actions — deploy KHÔNG cần JSON key.
# GitHub Actions xuất OIDC token → GCP tin token đó (giới hạn ĐÚNG repo) → impersonate
# sa-ci-deployer-staging. Idempotent. Chạy: bash 50-wif.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project

WIF_POOL="${WIF_POOL:-github-pool}"
WIF_PROVIDER="${WIF_PROVIDER:-github-provider}"
[[ -n "${GITHUB_REPO:-}" && "${GITHUB_REPO}" == */* ]] \
  || die "GITHUB_REPO chưa đặt đúng dạng 'owner/repo' trong config.sh."

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
log "Project number: ${PROJECT_NUMBER} | GitHub repo: ${GITHUB_REPO}"

# --- 1) Workload Identity Pool ---
if gcloud iam workload-identity-pools describe "${WIF_POOL}" \
     --location="global" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  skip "pool ${WIF_POOL}"
else
  log "Tạo pool ${WIF_POOL}"
  gcloud iam workload-identity-pools create "${WIF_POOL}" \
    --location="global" --display-name="GitHub Actions" --project "${PROJECT_ID}"
fi

# --- 2) OIDC Provider (giới hạn ĐÚNG repo qua attribute-condition) ---
# attribute-condition chặn mọi repo khác mạo danh: chỉ token của GITHUB_REPO mới hợp lệ.
if gcloud iam workload-identity-pools providers describe "${WIF_PROVIDER}" \
     --location="global" --workload-identity-pool="${WIF_POOL}" \
     --project "${PROJECT_ID}" >/dev/null 2>&1; then
  skip "provider ${WIF_PROVIDER}"
else
  log "Tạo OIDC provider ${WIF_PROVIDER} (chỉ repo ${GITHUB_REPO})"
  gcloud iam workload-identity-pools providers create-oidc "${WIF_PROVIDER}" \
    --location="global" --workload-identity-pool="${WIF_POOL}" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository == '${GITHUB_REPO}'" \
    --project "${PROJECT_ID}"
fi

# --- 3) Cho phép danh tính từ repo impersonate sa-ci-deployer-staging (chỉ staging = WIF tự động) ---
PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository/${GITHUB_REPO}"
log "workloadIdentityUser: repo ${GITHUB_REPO} → ${SA_CI_DEPLOYER_STAGING}"
gcloud iam service-accounts add-iam-policy-binding "$(sa_email "${SA_CI_DEPLOYER_STAGING}")" \
  --role="roles/iam.workloadIdentityUser" \
  --member="${PRINCIPAL}" \
  --project "${PROJECT_ID}" >/dev/null

echo ""
echo "================= DÁN VÀO GitHub Actions (Phase 6) ================="
echo "  workload_identity_provider: projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}"
echo "  service_account:            $(sa_email "${SA_CI_DEPLOYER_STAGING}")"
echo "==================================================================="
warn "prod deploy KHÔNG dùng WIF (thủ công theo plan) — chỉ staging tự động."
log "Bước tiếp: bash 60-networking-redis.sh"
