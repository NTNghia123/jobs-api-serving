#!/usr/bin/env bash
# Gán quyền IAM cho các SA — least-privilege, TÁCH MÔI TRƯỜNG. Idempotent (add binding có sẵn = no-op).
#
# Ma trận quyền:
#   sa-api-reader-staging : dataViewer@jobs_staging  + jobUser@project
#   sa-api-reader-prod    : dataViewer@jobs_prod     + jobUser@project
#   sa-dagster-elt        : dataEditor@{staging,prod} + jobUser@project
#   sa-ci-deployer-staging: run.developer@project + serviceAccountUser trên sa-api-reader-staging
#   sa-ci-deployer-prod   : run.developer@project + serviceAccountUser trên sa-api-reader-prod
# (secretAccessor → 40-secrets.sh; artifactregistry.writer → 30-artifact-registry.sh)
#
# LƯU Ý: dataViewer/dataEditor gán ở CẤP DATASET (không phải project) để staging KHÔNG đọc/ghi prod.
# Chạy: bash 21-iam-bindings.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project

# --- helper: quyền cấp DATASET (BigQuery) ---
# Dùng access-entry của dataset (đọc → thêm nếu chưa có → cập nhật) — cách chuẩn, mọi bản bq.
# bqrole: READER = roles/bigquery.dataViewer · WRITER = roles/bigquery.dataEditor.
grant_dataset() {
  local sa="$1" bqrole="$2" dataset="$3"
  local email; email="$(sa_email "${sa}")"
  local tmp; tmp="$(mktemp)"
  bq show --format=prettyjson "${PROJECT_ID}:${dataset}" > "${tmp}"
  if python3 - "${tmp}" "${bqrole}" "${email}" <<'PY'
import json, sys
path, role, email = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(path, encoding="utf-8"))
acc = d.setdefault("access", [])
if any(e.get("role") == role and e.get("userByEmail") == email for e in acc):
    sys.exit(1)  # đã có → không cần update
acc.append({"role": role, "userByEmail": email})
json.dump(d, open(path, "w", encoding="utf-8"))
PY
  then
    log "dataset ${dataset}: ${bqrole} → ${sa}"
    bq update --source "${tmp}" "${PROJECT_ID}:${dataset}" >/dev/null
  else
    skip "dataset ${dataset}: ${bqrole} → ${sa}"
  fi
  rm -f "${tmp}"
}

# --- helper: quyền cấp PROJECT ---
grant_project() {
  local sa="$1" role="$2"
  log "project: ${role} → ${sa}"
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:$(sa_email "${sa}")" \
    --role="${role}" --condition=None >/dev/null
}

# --- helper: cho CI SA quyền 'actAs' runtime SA (deploy Cloud Run dưới danh tính đó) ---
grant_act_as() {
  local ci_sa="$1" runtime_sa="$2"
  log "serviceAccountUser: ${ci_sa} actAs ${runtime_sa}"
  gcloud iam service-accounts add-iam-policy-binding "$(sa_email "${runtime_sa}")" \
    --member="serviceAccount:$(sa_email "${ci_sa}")" \
    --role="roles/iam.serviceAccountUser" \
    --project "${PROJECT_ID}" >/dev/null
}

echo "== BigQuery: reader chỉ đọc dataset môi trường của mình (READER = dataViewer) =="
grant_dataset "${SA_API_READER_STAGING}" "READER" "${DATASET_STAGING}"
grant_dataset "${SA_API_READER_PROD}"    "READER" "${DATASET_PROD}"

echo "== BigQuery: ELT ghi cả hai dataset (WRITER = dataEditor; writer guard tầng app chặn ghi nhầm env) =="
grant_dataset "${SA_DAGSTER_ELT}" "WRITER" "${DATASET_STAGING}"
grant_dataset "${SA_DAGSTER_ELT}" "WRITER" "${DATASET_PROD}"

echo "== BigQuery jobUser (chạy query) — cấp project =="
grant_project "${SA_API_READER_STAGING}" "roles/bigquery.jobUser"
grant_project "${SA_API_READER_PROD}"    "roles/bigquery.jobUser"
grant_project "${SA_DAGSTER_ELT}"        "roles/bigquery.jobUser"

echo "== Cloud Run deploy (CI) =="
grant_project "${SA_CI_DEPLOYER_STAGING}" "roles/run.developer"
grant_project "${SA_CI_DEPLOYER_PROD}"    "roles/run.developer"

echo "== CI actAs runtime SA tương ứng =="
grant_act_as "${SA_CI_DEPLOYER_STAGING}" "${SA_API_READER_STAGING}"
grant_act_as "${SA_CI_DEPLOYER_PROD}"    "${SA_API_READER_PROD}"

log "Xong IAM. Kiểm nhanh policy cấp project:"
gcloud projects get-iam-policy "${PROJECT_ID}" \
  --flatten="bindings[].members" \
  --filter="bindings.members:sa-*" \
  --format="table(bindings.role, bindings.members)" 2>/dev/null | grep -E "sa-(api-reader|dagster|ci-deployer)" | sed 's/^/    /' || true
log "Bước tiếp: bash 30-artifact-registry.sh"
