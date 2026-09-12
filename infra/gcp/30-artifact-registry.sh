#!/usr/bin/env bash
# Tạo Artifact Registry (Docker repo) - kho lưu image trên GCP + cleanup policy + quyền writer cho CI deployer.
# File này chỉ tạo kho rỗng
# Idempotent. Chạy: bash 30-artifact-registry.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project

# --- 1) tạo repo Docker (region-scoped) ---
if gcloud artifacts repositories describe "${AR_REPO}" \
     --location="${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  skip "Artifact Registry repo ${AR_REPO}"
else
  log "Tạo Artifact Registry repo ${AR_REPO} (docker, ${REGION})"
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Jobs Serving API container images" \
    --labels=app=jobs-serving \
    --project "${PROJECT_ID}"
fi

# --- 2) cleanup policy: xoá untagged >7 ngày, luôn giữ 10 version tagged gần nhất ---
# Chặn phình dung lượng (mỗi commit SHA đẩy 1 image). Declarative → set lại = idempotent.
policy="$(mktemp)"
cat > "${policy}" <<'JSON'
[
  {
    "name": "delete-untagged",
    "action": {"type": "Delete"},
    "condition": {"tagState": "UNTAGGED", "olderThan": "604800s"}
  },
  {
    "name": "keep-recent-tagged",
    "action": {"type": "Keep"},
    "mostRecentVersions": {"keepCount": 10}
  }
]
JSON
log "Áp cleanup policy (untagged>7d → xoá; giữ 10 tagged gần nhất)"
gcloud artifacts repositories set-cleanup-policies "${AR_REPO}" \
  --location="${REGION}" --project "${PROJECT_ID}" \
  --policy="${policy}" --no-dry-run
rm -f "${policy}"

# --- 3) quyền writer cho 2 CI deployer (repo-scoped, tight hơn project-level) ---
grant_ar_writer() {
  local sa="$1"
  log "artifactregistry.writer @${AR_REPO} → ${sa}"
  gcloud artifacts repositories add-iam-policy-binding "${AR_REPO}" \
    --location="${REGION}" --project "${PROJECT_ID}" \
    --member="serviceAccount:$(sa_email "${sa}")" \
    --role="roles/artifactregistry.writer" >/dev/null
}
grant_ar_writer "${SA_CI_DEPLOYER_STAGING}"
grant_ar_writer "${SA_CI_DEPLOYER_PROD}"

# LƯU Ý: Cloud Run PULL image bằng service agent riêng của Cloud Run (tự có quyền đọc AR
# cùng project) — KHÔNG cần cấp reader cho runtime SA ở đây.

log "Repo image path: ${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}"
log "Bước tiếp: bash 40-secrets.sh"
