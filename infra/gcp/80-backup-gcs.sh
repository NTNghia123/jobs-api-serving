#!/usr/bin/env bash
# Hạ tầng backup Mongo → GCS (Phase 7). Idempotent. Chạy: bash 80-backup-gcs.sh
#
# Fork đã chốt:
#   - (3A) chỉ LIFECYCLE (xoá > N ngày), KHÔNG retention-lock (tránh kẹt giai đoạn thử nghiệm).
#   - (B) writer = sa-dagster-elt (gắn sẵn trên VM) với CHỈ objectCreator → tạo-chỉ, KHÔNG xoá/ghi
#         đè ⇒ backup bất biến. Restore dùng SA RIÊNG (objectViewer). Không impersonation, không key.
#   - Bucket cùng project serving-api. Mã hoá at-rest mặc định (Google-managed); CMEK để [SAU].
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target
ensure_project

BUCKET="${BACKUP_BUCKET:-${PROJECT_ID}-mongo-backup}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
GS="gs://${BUCKET}"
log "Backup bucket: ${GS} | retention: ${RETENTION_DAYS} ngày | writer: ${SA_DAGSTER_ELT} (objectCreator)"

# --- 1) tạo bucket (uniform IAM + chặn public) ---
if gcloud storage buckets describe "${GS}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  skip "bucket ${GS}"
else
  log "Tạo bucket ${GS} (${REGION}, uniform access, chặn public)"
  gcloud storage buckets create "${GS}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention \
    --project "${PROJECT_ID}"
fi

# --- 2) lifecycle: xoá object cũ hơn N ngày (cost guard, dọn backup cũ) ---
lc="$(mktemp)"
cat > "${lc}" <<JSON
{"rule": [{"action": {"type": "Delete"}, "condition": {"age": ${RETENTION_DAYS}}}]}
JSON
log "Áp lifecycle: xoá object > ${RETENTION_DAYS} ngày"
gcloud storage buckets update "${GS}" --lifecycle-file="${lc}" --project "${PROJECT_ID}"
rm -f "${lc}"

# --- 3) writer = sa-dagster-elt, CHỈ objectCreator (tạo-chỉ → bất biến) ---
log "objectCreator @${BUCKET} → ${SA_DAGSTER_ELT} (writer: tạo-chỉ, KHÔNG xoá/ghi đè)"
gcloud storage buckets add-iam-policy-binding "${GS}" \
  --member="serviceAccount:$(sa_email "${SA_DAGSTER_ELT}")" \
  --role="roles/storage.objectCreator" \
  --project "${PROJECT_ID}" >/dev/null

# --- 4) restore identity RIÊNG (objectViewer) ---
if gcloud iam service-accounts describe "$(sa_email "${SA_BACKUP_RESTORE}")" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  skip "SA ${SA_BACKUP_RESTORE}"
else
  log "Tạo SA restore ${SA_BACKUP_RESTORE}"
  gcloud iam service-accounts create "${SA_BACKUP_RESTORE}" \
    --display-name="Mongo backup restore (read-only)" --project "${PROJECT_ID}"
fi
log "objectViewer @${BUCKET} → ${SA_BACKUP_RESTORE} (restore: chỉ đọc)"
gcloud storage buckets add-iam-policy-binding "${GS}" \
  --member="serviceAccount:$(sa_email "${SA_BACKUP_RESTORE}")" \
  --role="roles/storage.objectViewer" \
  --project "${PROJECT_ID}" >/dev/null

echo ""
echo "================= Backup GCS sẵn sàng ================="
echo "  Bucket    : ${GS}  (lifecycle xoá > ${RETENTION_DAYS} ngày)"
echo "  Writer    : $(sa_email "${SA_DAGSTER_ELT}")  (objectCreator — tạo-chỉ)"
echo "  Restore   : $(sa_email "${SA_BACKUP_RESTORE}")  (objectViewer)"
echo "======================================================="
warn "Backup KHÔNG bị teardown tự động (dữ liệu quý). Xoá thủ công nếu thật sự cần."
log "Bước tiếp (cụm 7.4): backup.sh (mongodump→GCS) + systemd timer + restore.sh trên VM."
