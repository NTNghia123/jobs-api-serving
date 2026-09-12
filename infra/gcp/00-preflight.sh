#!/usr/bin/env bash
# Kiểm tra sẵn sàng TRƯỚC khi tạo tài nguyên: đăng nhập, project, billing.
# Chỉ ĐỌC trạng thái — không tạo/sửa gì. Chạy: bash 00-preflight.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source ./lib.sh

show_target

# 1) đã đăng nhập?
active="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
if [[ -z "${active}" ]]; then
  die "Chưa đăng nhập. Chạy: gcloud auth login  (Cloud Shell thì đã tự đăng nhập)."
fi
log "Tài khoản đang đăng nhập: ${active}"

# 2) project tồn tại + có quyền?
if ! gcloud projects describe "${PROJECT_ID}" >/dev/null 2>&1; then
  die "Không truy cập được project '${PROJECT_ID}' (sai ID hoặc thiếu quyền)."
fi
log "Project OK: ${PROJECT_ID}"
ensure_project

# 3) billing đã bật? (cần cho hầu hết API)
billing="$(gcloud billing projects describe "${PROJECT_ID}" \
  --format='value(billingEnabled)' 2>/dev/null || echo "unknown")"
case "${billing}" in
  True|true) log "Billing: đã bật." ;;
  unknown)   warn "Không đọc được trạng thái billing (thiếu quyền billing?). Kiểm tra thủ công ở Console → Billing." ;;
  *)         die "Billing CHƯA bật cho ${PROJECT_ID}. Vào Console → Billing → liên kết billing account (kích hoạt \$300 free trial)." ;;
esac

log "Preflight OK. Bước tiếp: bash 01-enable-apis.sh"
