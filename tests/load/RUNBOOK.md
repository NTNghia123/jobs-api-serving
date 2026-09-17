# RUNBOOK — Load & Latency Test (từng bước)

Hướng dẫn thao tác chi tiết. Tổng quan thành phần + SLA: [`README.md`](README.md); kế hoạch:
[`docs/load-test-plan.md`](../../docs/load-test-plan.md).

**Quy ước quan trọng**
- Lệnh viết cho **Git Bash**. PowerShell đặt env kiểu `$env:X="..."` (xem cuối).
- **Windows: luôn `PYTHONUTF8=1`** khi chạy locust (nó đọc `pyproject.toml` tiếng Việt bằng charmap → lỗi).
- **Mọi lệnh `gcloud` đều kèm `--project "$PERF_PROJECT" --region "$PERF_REGION"`** để không phụ thuộc
  project đang active trên máy (tránh chạm nhầm production).
- **Locust exit 0 chỉ là SLA CLIENT-SIDE SƠ BỘ.** Suite chỉ PASS khi bước **finalize** (B10) cũng PASS.
- Exit code: `0 PASS · 2 SLA/BUDGET · 3 INVALID/INSUFFICIENT · 4 SAFETY_ABORT · 5 không đọc được`.

---

## Track A — Chạy thử trên máy (miễn phí)
Mục đích: kiểm bộ đo (histogram/report/exit code), KHÔNG phải nghiệm thu SLA (dữ liệu thật chưa có →
dùng backend `fake`).

**A1.** Cài deps (một lần):
```bash
cd "E:/FIS Intern/DE/W2/jobs-serving-api-week2/jobs-serving-api" && .venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```
**A2.** Terminal 1 — API (fake, rate-limit nới):
```bash
cd "E:/FIS Intern/DE/W2/jobs-serving-api-week2/jobs-serving-api" && JOBS_API_ENV=local JOBS_API_WAREHOUSE_BACKEND=fake JOBS_API_CACHE_BACKEND=memory JOBS_API_RATE_LIMITER_BACKEND=memory JOBS_API_RATE_LIMIT_PER_MINUTE=100000 .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```
**A3.** Terminal 2 — scenario `smoke` (có thể dừng ngay ở pagination pre-flight):
```bash
cd "E:/FIS Intern/DE/W2/jobs-serving-api-week2/jobs-serving-api" && PYTHONUTF8=1 BASE_URL=http://127.0.0.1:8010 API_KEY=dev-local-key-team-ai RUN_ID=local1 LOAD_SCENARIO=smoke REPORT_PREFIX=reports/local1-smoke/run .venv/Scripts/python.exe -m locust -f tests/load/locustfile.py --headless --html reports/local1-smoke/run.html --csv reports/local1-smoke/run --csv-full-history
```
> Trên `fake` kỳ vọng **exit 3** (không có `next_page_token` → pagination 0% → WORKLOAD_INVALID + thiếu
> mẫu). Đây là bộ đo bắt lỗi đúng, không phải hệ thống hỏng.

**A4.** Kiểm logic đo:
```bash
cd "E:/FIS Intern/DE/W2/jobs-serving-api-week2/jobs-serving-api" && .venv/Scripts/python.exe -m pytest tests/test_load_metrics.py tests/test_load_shape.py tests/test_load_coldstart.py tests/test_load_finalize.py tests/test_load_aggregate.py tests/test_load_preflight.py tests/test_load_response_validation.py tests/test_load_test_config.py -q
```

---

## Track B — Nghiệm thu thật (Cloud Run + BigQuery)
> ⚠️ Tốn tiền BigQuery. Dùng **service perf riêng** + **project billing riêng**, KHÔNG chạm production.

### B0. Biến chung
> **Cloud Run region ≠ BigQuery location.** Nếu dataset snapshot ở `US`/`EU`… phải khai riêng, nếu không
> truy vấn `INFORMATION_SCHEMA` sẽ fail.
```bash
export PERF_PROJECT=<perf-project> PERF_REGION=asia-southeast1 PERF_BQ_LOCATION=asia-southeast1 PERF_SVC=<perf-svc> PERF_KEY=<perf-api-key> PERF_DATASET=<snapshot-dataset> PERF_BATCH_ID=<published-batch-id-của-snapshot> PERF_BUDGET_GIB=<ngân-sách-mỗi-scenario> PERF_SUITE_BUDGET_GIB=<ngân-sách-cả-suite>
```
URL Cloud Run **không suy ra được từ tên service**. B3 sẽ lấy `status.url` sau khi service đã deploy;
không tự ghép URL từ tên service.

### B1. Dựng service perf (một lần)
- Deploy Cloud Run service **riêng**, giống prod: `--cpu=1 --memory=512Mi --concurrency=40 --max-instances=3`,
  và `--min-instances=1` **cho các pha warm** (B5–B8). *(Cold-start ở B9 sẽ tạm đưa min về 0.)*
- Không dựa vào default ẩn: revision warm ban đầu phải khai tường minh
  `JOBS_API_ENV=staging`, `JOBS_API_CACHE_BACKEND=memory`,
  `JOBS_API_BQ_USE_QUERY_CACHE=true` và toàn bộ `JOBS_API_BQ_PROJECT/DATASET/LOCATION`.
- Trỏ dataset snapshot (giữ partitioning/clustering/`require_partition_filter`).
- API key riêng; nới `JOBS_API_RATE_LIMIT_PER_MINUTE` cao (để 429 không cản tải).
- Khai tường minh trên mọi revision perf: `JOBS_API_QUERY_TIMEOUT_S=10`,
  `JOBS_API_CACHE_TTL_SECONDS=300`, `JOBS_API_RATE_LIMIT_PER_MINUTE=100000`,
  `JOBS_API_RATE_LIMIT_BURST=100000` (hoặc giá trị đã duyệt khác, nhưng phải giữ nhất quán cả suite).
- Deploy đúng phiên bản app có gắn BigQuery job label `load_test_run`; finalizer lọc theo label này để
  không cộng nhầm query khác trong cùng project. Thiếu label sẽ làm scenario INVALID thay vì PASS giả.
- Tài khoản chạy finalizer cần quyền đọc **Cloud Monitoring time-series** và **Cloud Logging** ngoài quyền
  đọc `INFORMATION_SCHEMA`; thiếu quyền/không có điểm metric sẽ exit 5, không PASS ngầm.

### B2. Ngân sách & guard chi phí (làm TRƯỚC khi bắn)
1. **Dry-run định cỡ guard mỗi query** (không tốn tiền):
   ```bash
   cd "E:/FIS Intern/DE/W2/jobs-serving-api-week2/jobs-serving-api" && JOBS_API_BQ_PROJECT="$PERF_PROJECT" JOBS_API_BQ_DATASET="$PERF_DATASET" JOBS_API_BQ_LOCATION="$PERF_BQ_LOCATION" .venv/Scripts/python.exe -m tests.load.dryrun --margin 0.3
   ```
   → deploy lại service perf với `JOBS_API_BQ_MAXIMUM_BYTES_BILLED=<giá-trị-đề-xuất>`, đồng thời export
   **đúng cùng giá trị** trên máy chạy tải:
   ```bash
   export PERF_BQ_MAXIMUM_BYTES_BILLED=<giá-trị-đề-xuất-đã-deploy>
   ```
   Finalizer đối chiếu giá trị khai với revision thật. **Đây là guard CHÍNH** (hard cap mỗi query).
2. **Ngân sách CẢ SUITE** (không chỉ 1 query). Trần request mỗi scenario (safety cap ×1.1) — nhân với
   ~1.5–2 BQ jobs/request để ước jobs, rồi × bytes/job:

   | scenario | max HTTP req | ghi chú |
   |---|---:|---|
   | realistic | 24.226 | search first=2 jobs, next=1; market=1–2 |
   | bq_cold_search | 10.674 | BQ cache off → tốn nhất/query |
   | spike_recovery | 11.982 | |
   | market_coldmiss | 1.261 | tải nhỏ |
   | ops_baseline | 1.261 | /health,/metadata (không BQ nặng) |

3. **Ngân sách phân bổ**: đặt `PERF_BUDGET_GIB` cho **từng scenario** sao cho **tổng ≤ ngân sách suite**
   (finalize kiểm từng scenario). Kiểm **tổng cả suite** bằng `finalize --budget-only` (dựng đúng tập
   5 run-label từ `SUITE_ID`, không wildcard) từ START đầu tiên → END cuối cùng — xem B10.
4. `QueryUsagePerDay` custom quota (nếu dùng) là guard PHỤ: **chỉ on-demand pricing, gần đúng (không phải
   hard cap tuyệt đối), thay đổi mất vài phút mới hiệu lực, reset nửa đêm giờ Pacific**. → set quota cho
   **tổng suite**, **xác nhận đã effective** trước khi bắn, và **vẫn giữ `maximum_bytes_billed` làm guard chính**.

### B3. Chụp provenance revision (LẶP LẠI sau MỖI lần deploy/đổi config)
> Mỗi lần đổi config (vd bật/tắt BQ cache) tạo **revision mới** → phải chụp lại, nếu không report có
> provenance sai.
> **Unset trước** để nếu gcloud lỗi thì KHÔNG còn giá trị cũ (revision lần trước) treo lại trong môi
> trường → tránh finalize dùng nhầm provenance cũ (fail-open).
```bash
capture_provenance() {
  unset PERF_URL PERF_REVISION PERF_IMAGE_DIGEST
  local url revision digest
  url=$(gcloud run services describe "$PERF_SVC" --project "$PERF_PROJECT" \
    --region "$PERF_REGION" --format='value(status.url)') || return 5
  revision=$(gcloud run services describe "$PERF_SVC" --project "$PERF_PROJECT" \
    --region "$PERF_REGION" --format='value(status.latestReadyRevisionName)') || return 5
  [ -n "$url" ] && [ -n "$revision" ] || return 5
  digest=$(gcloud run revisions describe "$revision" --project "$PERF_PROJECT" \
    --region "$PERF_REGION" --format='value(status.imageDigest)') || return 5
  [ -n "$digest" ] || return 5
  PERF_URL="$url" PERF_REVISION="$revision" PERF_IMAGE_DIGEST="$digest"
  export PERF_URL PERF_REVISION PERF_IMAGE_DIGEST
}
capture_provenance
B3_RC=$?
if [ "$B3_RC" -eq 0 ]; then
  echo "url=$PERF_URL revision=$PERF_REVISION digest=$PERF_IMAGE_DIGEST"
else
  unset PERF_URL PERF_REVISION PERF_IMAGE_DIGEST
  echo "LỖI: không đọc được URL/revision/digest — dừng lại (B3_RC=$B3_RC)"
fi
```
Không tiếp tục khi `B3_RC != 0`. Hàm kiểm cả **exit status** lẫn giá trị rỗng; output không rỗng từ một
lệnh `gcloud` thất bại cũng không thể bị nhận nhầm là provenance hợp lệ.
Xác nhận revision này **nhận 100% traffic** (finalize cũng tự kiểm lại từng entry):
```bash
gcloud run services describe "$PERF_SVC" --project "$PERF_PROJECT" --region "$PERF_REGION" --format='value(status.traffic)'
```

### B4. Env hạ tầng cho scenario nghiệm thu (fail-fast nếu thiếu/không khớp)
```bash
export RUN_CONCURRENCY=40 PERF_CPU_LIMIT=1000m PERF_MEMORY_LIMIT=512Mi PERF_MIN_INSTANCES=1 PERF_MAX_INSTANCES=3 PERF_SERVICE_MIN_INSTANCES=0 PERF_SERVICE_MAX_INSTANCES=default PERF_SCALING_MODE=automatic PERF_QUERY_TIMEOUT_S=10 PERF_CACHE_TTL_SECONDS=300 PERF_RATE_LIMIT_PER_MINUTE=100000 PERF_RATE_LIMIT_BURST=100000 PERF_REGION="$PERF_REGION"
```
`PERF_BATCH_ID` và `PERF_BQ_MAXIMUM_BYTES_BILLED` từ B0/B2 cũng bắt buộc; CPU/RAM dùng đúng dạng revision
trả về (`1000m`, `512Mi`). Thiếu trường nào Locust dừng ngay exit 3. `as_of` được bắt từ response
pre-flight/request đầu và finalizer cũng bắt buộc có.
Hai biến `PERF_SERVICE_*` tách override service-level khỏi `PERF_MIN/MAX_INSTANCES` của revision. Track B
dùng revision min/max nên service-level phải là `0/default`; nếu còn override ẩn, pre-check dừng trước tải.
`PERF_SCALING_MODE=automatic` cũng bắt buộc: manual scaling bỏ qua revision min/max, nên không hợp lệ cho
bài capacity/scale-out này.
> `JOBS_API_CACHE_BACKEND` và `JOBS_API_BQ_USE_QUERY_CACHE` phải **KHỚP cấu hình service** và **đúng
> scenario** (locustfile fail-fast, exit 3 nếu sai): `realistic|ops_baseline|spike_recovery`→`true`+`≠none`; `bq_cold_search`→`false`;
> `market_coldmiss`→`false`+`none`.

### B5. Khai báo runner dùng chung rồi chạy `realistic`
Hàm dưới dựng **một `RUN_DIR` duy nhất** từ UUID và dùng cùng đường dẫn cho Locust, metadata và finalize;
không còn khả năng chạy scenario A nhưng finalize nhầm artifact scenario B. Finalize đọc `exit_code` Locust từ
metadata và gộp vào `suite-result.json`; đồng thời đối chiếu cấu hình revision Cloud Run thật.
Locust cũng tự pre-check revision/config/100% traffic bằng `gcloud` **trước khi phát tải**; finalizer kiểm
lại sau run để phát hiện thay đổi giữa chừng. `PERF_BATCH_ID` cũng được đối chiếu với pointer batch đang
publish trong đúng snapshot trước run; finalizer kiểm lại cả `batch_id` + `as_of` sau run. Mỗi lần đối
chiếu dùng một query audit nhỏ, có cùng `maximum_bytes_billed` guard và mang label audit riêng theo run:
bytes audit (kể cả query `INFORMATION_SCHEMA` per-scenario, có job-id riêng để không tự đếm khi đang RUNNING)
được cộng vào ngân sách scenario/suite nhưng
không làm nhiễu cache ratio hay số job workload. Query budget/aggregate cuối chạy sau cửa sổ suite và là
overhead báo cáo, không phải traffic kiểm thử.
Với `realistic` và `bq_cold_search`, metadata ghi cửa sổ từng pha; finalizer lấy `instance_count` riêng
cho Step 2/3 từ Cloud Monitoring. `peak_instances` và `scale_out_observed` là **report-only** vì plan chưa
đặt ngưỡng số instance tối thiểu; thiếu bằng chứng metric vẫn exit 5.
Các scenario có search còn chạy một pre-flight có nhãn `warmup` trước khi spawn user; response phải đúng
contract và có `next_page_token`, nếu không dừng ngay exit 3 thay vì tiêu hết ngân sách rồi mới phát hiện
workload 80/20 không hợp lệ.
```bash
unset SUITE_ID SUITE_START_UTC SUITE_END_UTC
declare -A SCENARIO_RESULTS=()
REPO="E:/FIS Intern/DE/W2/jobs-serving-api-week2/jobs-serving-api"

run_scenario() {
  local scenario="$1" cache_backend="$2" bq_cache="$3"
  if [ -z "${SUITE_ID:-}" ]; then
    SUITE_ID=$(cd "$REPO" && .venv/Scripts/python.exe -c 'import uuid; print(uuid.uuid4().hex)') || return 5
    SUITE_START_UTC=$(cd "$REPO" && .venv/Scripts/python.exe -c 'from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())') || return 5
  fi
  local run_id="${SUITE_ID}-${scenario}"
  local run_dir="$REPO/reports/${run_id}"
  local result="$run_dir/suite-result.json"
  unset 'SCENARIO_RESULTS[$scenario]'
  (
    set -e
    cd "$REPO" || exit 5
    mkdir -p "$run_dir" || exit 5
    rm -f "$result" "$run_dir/run-metadata.json"
    AUTH_BEARER=$(gcloud auth print-identity-token) || exit 5
    [ -n "$AUTH_BEARER" ] || { echo "token rỗng"; exit 5; }
    export AUTH_BEARER RUN_ID="$run_id"
    export JOBS_API_CACHE_BACKEND="$cache_backend" JOBS_API_BQ_USE_QUERY_CACHE="$bq_cache"
    curl -fsS -H "Authorization: Bearer $AUTH_BEARER" "$PERF_URL/health" >/dev/null || exit 5
    set +e
    PYTHONUTF8=1 BASE_URL="$PERF_URL" API_KEY="$PERF_KEY" LOAD_SCENARIO="$scenario" REPORT_PREFIX="$run_dir/run" \
      .venv/Scripts/python.exe -m locust -f tests/load/locustfile.py --headless \
      --html "$run_dir/run.html" --csv "$run_dir/run" --csv-full-history
    LOCUST_RC=$?
    if [ -f "$run_dir/run-metadata.json" ]; then
      .venv/Scripts/python.exe -m tests.load.finalize --project "$PERF_PROJECT" \
        --location "$PERF_BQ_LOCATION" --service "$PERF_SVC" --region "$PERF_REGION" \
        --budget-gib "$PERF_BUDGET_GIB" --metadata "$run_dir/run-metadata.json" \
        --locust-process-exit "$LOCUST_RC" \
        --app-cache-check auto --out "$result"
      FINALIZE_RC=$?
    else
      FINALIZE_RC=5
    fi
    echo "scenario=$scenario LOCUST_RC=$LOCUST_RC FINALIZE_RC=$FINALIZE_RC result=$result"
    [ -f "$run_dir/run-metadata.json" ] && exit "$FINALIZE_RC"
    [ "$LOCUST_RC" -ne 0 ] && exit "$LOCUST_RC"
    exit 5
  )
  local rc=$?
  [ -f "$result" ] && SCENARIO_RESULTS["$scenario"]="$result"
  SUITE_END_UTC=$(cd "$REPO" && .venv/Scripts/python.exe -c 'from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())')
  echo "SCENARIO_RC=$rc"
  return "$rc"
}

run_scenario realistic memory true
```

### B6. `ops_baseline` và **B7.** `spike_recovery`
Hai scenario này dùng cùng revision/config warm như realistic:
```bash
run_scenario ops_baseline memory true
run_scenario spike_recovery memory true
```
*(ops_baseline gần như không tốn BQ; finalize chủ yếu xác nhận revision/config + zero-jobs guard.)*

### B8. `bq_cold_search` (BQ cache off) — deploy lại + chụp lại revision
1. Deploy lại service perf với `JOBS_API_BQ_USE_QUERY_CACHE=false`.
2. **Chạy lại B3** (revision/digest MỚI + xác nhận 100% traffic) — nếu không, provenance report sẽ sai.
3. Chạy:
   ```bash
   run_scenario bq_cold_search memory false
   ```

### B8b. `market_coldmiss` (app-cache none + BQ cache off)
1. Deploy lại service perf `JOBS_API_CACHE_BACKEND=none` **và** `JOBS_API_BQ_USE_QUERY_CACHE=false`
   (guard chặn `none` ở `env=prod` → service perf phải `env=staging`). **Chạy lại B3**.
2. Chạy. **App-cache là BẰNG CHỨNG, không phải cờ tự khai**: `--app-cache-check auto` (mặc định)
   khiến finalize đếm cả `hit|miss` trong Cloud Logging, lọc theo đúng revision **và `run_id`**. Chỉ PASS
   khi số log khớp chính xác `market_ok_total` và **0 hit**. Finalizer chờ ingestion tối đa 120 giây;
   sau đó log vẫn thiếu/thừa hoặc không đọc được → exit 5.
   Không có lối tắt manual để tạo PASS: `--app-cache-check pass` bị từ chối. `fail` + bằng chứng chỉ dùng
   để ghi nhận một thất bại rõ ràng; muốn PASS bắt buộc chế độ `auto` đọc đủ log.
   ```bash
   run_scenario market_coldmiss none false
   ```

> **QUAN TRỌNG:** finalize của mỗi scenario phải chạy **TRƯỚC** lần deploy kế tiếp (B8/B8b/B9 đều tạo
> revision mới). Nếu để dồn tới cuối, finalize sẽ kiểm nhầm revision hiện tại, không phải revision đã phục vụ.

### B9. Cold start — PHẢI đưa min-instances về 0 trước (làm SAU CÙNG)
> `min-instances=1` giữ instance warm → `cold_start.py` timeout. Service-level min (`--min`) và revision-level
> min (`--min-instances`) là **hai cơ chế riêng**; phải đưa **cả hai** về 0:
1. Đưa cả hai min về 0 (lệnh này tạo **revision mới** → chờ Ready/100% traffic rồi **chạy lại B3** để refresh `PERF_REVISION`):
   ```bash
   gcloud run services update "$PERF_SVC" --project "$PERF_PROJECT" --region "$PERF_REGION" --min=0 --min-instances=0
   ```
   Kiểm revision không còn ghim min>0:
   ```bash
   gcloud run revisions describe "$PERF_REVISION" --project "$PERF_PROJECT" --region "$PERF_REGION" --format='value(metadata.annotations["autoscaling.knative.dev/minScale"])'
   ```
2. **Gỡ traffic tag** trỏ revision cũ nếu tag đó giữ nó warm.
3. Chạy (**3 mẫu**; 5 mẫu có thể >1 giờ). Bọc **subshell** để `exit` không đóng terminal + kiểm token non-empty:
   ```bash
   ( set -e; cd "E:/FIS Intern/DE/W2/jobs-serving-api-week2/jobs-serving-api"; AUTH_BEARER=$(gcloud auth print-identity-token); [ -n "$AUTH_BEARER" ] || { echo "token rỗng"; exit 5; }; BASE_URL="$PERF_URL" AUTH_BEARER="$AUTH_BEARER" .venv/Scripts/python.exe -m tests.load.cold_start --refresh-auth --samples 3 --service "$PERF_SVC" --project "$PERF_PROJECT" --region "$PERF_REGION" --revision "$PERF_REVISION" --out "reports/${SUITE_ID}-coldstart.json" ); echo "COLDSTART_RC=$?"
   ```
   Script chỉ đo khi xác nhận **2 điểm metric zero TƯƠI liên tiếp** (chống stale/độ trễ tới 120s); `--out`
   lưu median/max + provenance **theo từng mẫu**. Mỗi mẫu chỉ được bắn khi xác minh đúng một revision
   đang nhận 100% traffic; không xác minh được → bỏ mẫu và cold-start exit 5.

   Không dùng deploy revision mới làm lối tắt thay cho scale-to-zero. Cloud Run mặc định khởi động một
   instance để health-check deployment; vì vậy request đầu của revision mới có thể đã warm. Script từ
   chối `--mode manual`; không thu đủ 3 mẫu zero-tươi thì ghi `INSUFFICIENT_SAMPLE` thay vì kết luận giả.

### B10. Kết luận suite
- `suite-result.json` của mỗi scenario đã gộp Locust + BigQuery + app-cache (market) + cấu hình Cloud Run
  thật. Tuy nhiên **không kết luận bằng các dòng `SCENARIO_RC` riêng lẻ**: chạy block dưới để kiểm đủ 5
  scenario và ngân sách tổng, rồi tạo một artifact duy nhất.
  ```bash
  BUDGET_RESULT="$REPO/reports/${SUITE_ID}-suite-budget.json"
  FINAL_RESULT="$REPO/reports/${SUITE_ID}-final-suite-result.json"
  rm -f "$BUDGET_RESULT" "$FINAL_RESULT"

  (
    cd "$REPO" || exit 5
    .venv/Scripts/python.exe -m tests.load.finalize \
      --project "$PERF_PROJECT" --location "$PERF_BQ_LOCATION" \
      --budget-gib "$PERF_SUITE_BUDGET_GIB" \
      --start "$SUITE_START_UTC" --end "$SUITE_END_UTC" \
      --suite-id "$SUITE_ID" \
      --budget-only --out "$BUDGET_RESULT"
  )
  BUDGET_RC=$?

  AGG_ARGS=()
  for scenario in realistic ops_baseline spike_recovery bq_cold_search market_coldmiss; do
    if [ -n "${SCENARIO_RESULTS[$scenario]:-}" ]; then
      AGG_ARGS+=(--scenario-result "${SCENARIO_RESULTS[$scenario]}")
    fi
  done
  (
    cd "$REPO" || exit 5
    .venv/Scripts/python.exe -m tests.load.aggregate \
      --suite-id "$SUITE_ID" "${AGG_ARGS[@]}" \
      --budget-result "$BUDGET_RESULT" --out "$FINAL_RESULT"
  )
  FINAL_SUITE_RC=$?
  echo "BUDGET_RC=$BUDGET_RC FINAL_SUITE_RC=$FINAL_SUITE_RC result=$FINAL_RESULT"
  ```
- **Nguồn kết luận duy nhất:** `final-suite-result.json`; suite chỉ PASS khi `FINAL_SUITE_RC=0`.
  Thiếu/trùng scenario, artifact cũ không có verdict Locust, budget không đọc được, hoặc bất kỳ thành phần
  nào fail đều không thể PASS. Aggregate còn kiểm budget window bao phủ toàn bộ scenario, tổng job/bytes
  khớp chính xác tổng từng scenario, cùng project/service/region/BQ location/**dataset** và cùng published
  `batch_id + as_of`; đồng thời cùng image digest, corpus hash, load seed và Locust version. Budget query
  còn buộc cùng CPU/RAM/concurrency/min/max-instances và BigQuery byte guard (chỉ cache mode được phép
  đổi theo scenario), dùng **đúng tập 5 run label** của suite, không dùng wildcard/prefix mơ hồ.

### B11. Teardown (bắt buộc)
- Xóa service perf + snapshot dataset.
- **Khôi phục `QueryUsagePerDay` về giá trị gốc** (không "xóa quota").
- Đưa env service về mặc định an toàn (`JOBS_API_BQ_USE_QUERY_CACHE=true`, `JOBS_API_CACHE_BACKEND=memory|redis`).

---

## PowerShell (thay cho Git Bash)
Đặt từng biến rồi gọi, ví dụ chạy smoke local:
```bash
$env:PYTHONUTF8="1"; $env:BASE_URL="http://127.0.0.1:8010"; $env:API_KEY="dev-local-key-team-ai"; $env:LOAD_SCENARIO="smoke"; $env:REPORT_PREFIX="reports/local1-smoke/run"; .venv\Scripts\python.exe -m locust -f tests/load/locustfile.py --headless --csv reports/local1-smoke/run
```
