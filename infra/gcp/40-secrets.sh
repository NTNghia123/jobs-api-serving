#!/usr/bin/env bash
# Project cần 2 loại secret: api-keys (JSON) + page-token (ngẫu nhiên).
# Tạo secret + tách môi trường + version khởi tạo + quyền secretAccessor cho reader SA.
# Mỗi key là 1 file JSON bất biến, nếu sửa key thì sẽ tạo version mới.
# Nếu SA có quyền đọc secret, Cloud Run sẽ nạp secret vào env var.
# Idempotent: KHÔNG xoay page-token khi re-run (chỉ sinh nếu chưa có version nào).
# Chạy: bash 40-secrets.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project

# --- tạo secret container (replication automatic) ---
create_secret() {
  local name="$1" env="$2"
  if gcloud secrets describe "${name}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    skip "secret ${name}"
  else
    log "Tạo secret ${name}"
    gcloud secrets create "${name}" \
      --replication-policy="automatic" \
      --labels="app=jobs-serving,env=${env}" \
      --project "${PROJECT_ID}"
  fi
}

# --- sinh page-token secret NGẪU NHIÊN, chỉ khi chưa có version (không xoay khoá) ---
seed_page_token() {
  local name="$1"
  local n; n="$(gcloud secrets versions list "${name}" --project "${PROJECT_ID}" \
    --filter="state=ENABLED" --format="value(name)" 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "${n}" != "0" ]]; then
    skip "page-token version cho ${name} (đã có ${n} — KHÔNG xoay)"
    return
  fi
  local val
  if command -v openssl >/dev/null 2>&1; then
    val="$(openssl rand -base64 48)"
  else
    val="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
  fi
  log "Sinh page-token ngẫu nhiên cho ${name}"
  printf '%s' "${val}" | gcloud secrets versions add "${name}" \
    --data-file=- --project "${PROJECT_ID}" >/dev/null
}

# --- quyền: reader SA đọc CHỈ secret môi trường của mình ---
grant_secret_accessor() {
  local sa="$1" secret="$2"
  log "secretAccessor @${secret} → ${sa}"
  gcloud secrets add-iam-policy-binding "${secret}" \
    --member="serviceAccount:$(sa_email "${sa}")" \
    --role="roles/secretmanager.secretAccessor" \
    --project "${PROJECT_ID}" >/dev/null
}

echo "== tạo 4 secret =="
create_secret "${SECRET_API_KEYS_STAGING}"    "staging"
create_secret "${SECRET_API_KEYS_PROD}"        "prod"
create_secret "${SECRET_PAGE_TOKEN_STAGING}"   "staging"
create_secret "${SECRET_PAGE_TOKEN_PROD}"       "prod"

echo "== sinh page-token (nếu chưa có) =="
seed_page_token "${SECRET_PAGE_TOKEN_STAGING}"
seed_page_token "${SECRET_PAGE_TOKEN_PROD}"

echo "== gán secretAccessor tách môi trường =="
grant_secret_accessor "${SA_API_READER_STAGING}" "${SECRET_API_KEYS_STAGING}"
grant_secret_accessor "${SA_API_READER_STAGING}" "${SECRET_PAGE_TOKEN_STAGING}"
grant_secret_accessor "${SA_API_READER_PROD}"    "${SECRET_API_KEYS_PROD}"
grant_secret_accessor "${SA_API_READER_PROD}"    "${SECRET_PAGE_TOKEN_PROD}"

warn "api-keys secret đang RỖNG (chưa có version). Trước khi deploy prod, nạp JSON key đã hash:"
echo "    # sinh key + hash bằng scripts/issue_key.py rồi:"
echo "    printf '%s' '{\"team-ai\":{\"key_sha256\":\"<hash>\",\"expires_at\":null}}' \\"
echo "      | gcloud secrets versions add ${SECRET_API_KEYS_PROD} --data-file=- --project ${PROJECT_ID}"
log "Bước tiếp: bash 50-wif.sh"
