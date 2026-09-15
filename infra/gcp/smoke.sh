#!/usr/bin/env bash
# Smoke test sau deploy — gọi các endpoint hợp lệ, assert HTTP code (Phase 6, cụm 6.3).
# Standalone: KHÔNG cần config.sh. Chạy được từ CI, Cloud Shell hay máy local.
#
#   BASE_URL=https://<service>-xxxx.run.app API_KEY=<key-thô> bash smoke.sh
#
# ENV:
#   BASE_URL     (bắt buộc) URL gốc của service Cloud Run.
#   API_KEY      (nên có)   key THÔ khớp hash trong secret api-keys. Trống → bỏ qua các /v1 cần key.
#   AUTH_BEARER  (tuỳ)      identity token; gắn 'Authorization: Bearer' cho MỌI request để gọi
#                           service còn PRIVATE (chưa allow-public). /v1 vẫn cần X-API-Key riêng.
#   TIMEOUT      (tuỳ)      giây/mỗi request (mặc định 30 — giữ client 30 > Cloud Run 25).
set -uo pipefail   # KHÔNG -e: muốn chạy hết mọi check rồi mới tổng kết

BASE_URL="${BASE_URL:?Đặt BASE_URL=https://... (URL Cloud Run)}"
BASE_URL="${BASE_URL%/}"                  # bỏ dấu '/' cuối nếu có
API_KEY="${API_KEY:-}"

# ★ TUẦN 8 — nghiệm thu W8 CẦN key: REQUIRE_API_KEY=1 mà thiếu key → trượt ngay (không skip /v1).
# Mặc định off để giữ đường CI gọi service private (chỉ /health + kiểm 401) như trước.
if [[ "${REQUIRE_API_KEY:-}" == "1" && -z "${API_KEY}" ]]; then
  echo "REQUIRE_API_KEY=1 nhưng API_KEY rỗng — nghiệm thu W8 bắt buộc có key." >&2
  exit 1
fi

resp="$(mktemp)"; trap 'rm -f "$resp"' EXIT
pass=0; fail=0

_c_g="\033[32m"; _c_r="\033[31m"; _c_y="\033[33m"; _c_0="\033[0m"

run() {  # label  expect_code  method  url  [curl-args...]
  local label="$1" expect="$2" method="$3" url="$4"; shift 4
  local code
  code="$(curl -sS -m "${TIMEOUT:-30}" -o "$resp" -w '%{http_code}' -X "$method" "$url" "$@" 2>>"$resp")" || code="000"
  if [[ "$code" == "$expect" ]]; then
    printf "${_c_g}  [PASS]${_c_0} %s → %s\n" "$label" "$code"; pass=$((pass+1)); return 0
  fi
  printf "${_c_r}  [FAIL]${_c_0} %s → nhận %s, mong %s\n" "$label" "$code" "$expect"; fail=$((fail+1))
  head -c 300 "$resp" | sed 's/^/        /'; echo; return 1
}

contains() {  # substring — kiểm body của lần run gần nhất
  if grep -q "$1" "$resp"; then
    printf "         ✓ body chứa '%s'\n" "$1"
  else
    printf "${_c_r}         ✗ body THIẾU '%s'${_c_0}\n" "$1"; fail=$((fail+1))
  fi
}

BEAR=(); [[ -n "${AUTH_BEARER:-}" ]] && BEAR=(-H "Authorization: Bearer ${AUTH_BEARER}")
KEY=(-H "X-API-Key: ${API_KEY}")

# ★ TUẦN 8 — TRACE_DEMO=1: sinh traceparent W3C có cờ sampled (-01) để CHỦ ĐỘNG tạo trace, gắn
# RIÊNG vào request search; in trace_id để tra cứu trên Cloud Trace (không phụ thuộc sample ngẫu nhiên).
TRACE=()
if [[ "${TRACE_DEMO:-}" == "1" ]]; then
  tid="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  sid="$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  TRACE=(-H "traceparent: 00-${tid}-${sid}-01")
  printf "  TRACE_DEMO: traceparent trace_id=%s (tra trên Cloud Trace)\n" "$tid"
fi

echo "== Smoke: ${BASE_URL} =="

# 1) liveness — không cần X-API-Key
run "/health" 200 GET "${BASE_URL}/health" "${BEAR[@]}" && contains '"status"'

# 2) auth THỰC SỰ bật: thiếu key → 401 (không phải 200 hớ hênh)
run "/v1/metadata (thiếu key → 401)" 401 GET "${BASE_URL}/v1/metadata" "${BEAR[@]}"

if [[ -n "${API_KEY}" ]]; then
  # 3) metadata với key hợp lệ
  run "/v1/metadata" 200 GET "${BASE_URL}/v1/metadata" "${BEAR[@]}" "${KEY[@]}"

  # 4) jobs/search là POST (ADR-002); posted_after BẮT BUỘC nằm trong filters.
  #    Gắn traceparent (nếu TRACE_DEMO=1) RIÊNG vào request này để nghiệm thu trace có chủ đích.
  run "/v1/jobs/search" 200 POST "${BASE_URL}/v1/jobs/search" \
    "${BEAR[@]}" "${KEY[@]}" "${TRACE[@]}" -H "Content-Type: application/json" \
    -d '{"filters":{"posted_after":"2026-01-01"},"limit":5}' && contains '"items"'

  # 5) market metrics — 2 tổ hợp dimension/window theo plan
  run "/v1/market/metrics?dimension=source&window=90d" 200 GET \
    "${BASE_URL}/v1/market/metrics?dimension=source&window=90d" "${BEAR[@]}" "${KEY[@]}"
  run "/v1/market/metrics?dimension=category&window=all_time" 200 GET \
    "${BASE_URL}/v1/market/metrics?dimension=category&window=all_time" "${BEAR[@]}" "${KEY[@]}"
else
  printf "${_c_y}  [!] API_KEY trống → BỎ QUA smoke /v1 cần key (chỉ /health + kiểm 401).${_c_0}\n"
fi

echo "== Kết quả: ${pass} pass, ${fail} fail =="
[[ "$fail" -eq 0 ]] || exit 1
