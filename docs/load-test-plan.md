# Plan: Load & Latency Test cho jobs-serving-api (perf/staging + BigQuery)

## Context
Kiểm thử **latency** và **khả năng chịu tải** của serving API (backend BigQuery). Chạy trên
**service perf/staging riêng** (không đụng production), mô hình tải **closed-model bằng Locust** —
báo cáo theo concurrency (VU) + achieved RPS, **không đồng nhất VU với RPS**.

Đặc thù đã xác minh trong code (ảnh hưởng mô hình chi phí):
- `/v1/market/metrics`: [market.py:58](app/api/market.py:58) gọi `repo.current_batch()` **trước**
  khi check cache (dòng 70) → **mọi** request (kể cả cache hit) tốn ≥1 BQ job `batch_meta`
  ([bigquery_metrics.py:42](app/infrastructure/warehouse/bigquery_metrics.py:42)).
- `/v1/jobs/search`: **trang đầu** = batch_meta + search ≈ 2 BQ jobs; **trang tiếp** (từ page token)
  = chỉ search ≈ 1 BQ job.
- On-demand BQ: phí tối thiểu 10MB/query; `maximum_bytes_billed` chỉ **từ chối** query vượt ước tính;
  bảng clustered có thể ước tính cao → query hợp lệ vẫn bị từ chối.
- Cloud Run `concurrency=40` = request đồng thời tối đa/instance, KHÔNG phải 40 req/s.
- Auth `X-API-Key` ([auth.py:47](app/api/auth.py:47)); private cần `Authorization: Bearer`.

## Môi trường (perf/staging — KHÔNG sửa production)
- Cloud Run service perf riêng, cấu hình giống prod (CPU=1, RAM=512Mi, concurrency=40), `max-instances=3`;
  dùng **automatic scaling**. Service-level min/max phải là `0/default` để không âm thầm ghi đè giới hạn
  revision-level dùng trong bài test.
- **Dataset snapshot** giữ nguyên **partitioning, clustering, `require_partition_filter`** (không chỉ giống số dòng).
- API key riêng; billing project + **custom `QueryUsagePerDay` quota** (guardrail server-side, **gần đúng** — không phải chặn cứng tuyệt đối).
- `rate_limit_per_minute` đủ cao để không cản tải. **Ghi rõ cache backend app** (memory | redis); `memory` → mỗi instance cache riêng, scale-out tạo thêm cache miss.
- Khai tường minh query timeout, cache TTL, rate-limit/minute + burst trên revision perf; finalizer đối
  chiếu cấu hình thật và aggregate buộc các giá trị này giữ nguyên giữa các scenario.
- `min-instances=1` cho pha warm; **cold start đo riêng**.

## Mục tiêu đo & workload
- Mix endpoint: **65% `/jobs/search`, 35% `/market/metrics`**.
- Trong `/jobs/search`: **80% trang đầu / 20% trang tiếp** (dùng `next_page_token` từ response trước).
  *Scope: nếu không kiểm thử pagination → đổi thành "first-page-only" và ghi rõ (công thức BQ jobs đổi theo).*
- `/health`, `/metadata`: đo riêng ở warm-up/baseline.

## Ngưỡng nghiệm thu & pha áp dụng
Latency tính trên **response THÀNH CÔNG (2xx + đúng cấu trúc)**:

| Endpoint | p95 | p99 |
|---|---|---|
| /health | ≤ 300 ms | — |
| /v1/metadata | ≤ 500 ms | — |
| /v1/jobs/search | ≤ 2 s | ≤ 5 s |
| /v1/market/metrics | ≤ 2 s | ≤ 5 s |

- **Error rate (định nghĩa THỐNG NHẤT toàn plan):** `unexpected_error_rate < 1%`, `429 = 0%`,
  `client timeout / request > 25s = 0%`. `unexpected_error_rate` gồm: **4xx ngoài kịch bản, 5xx (gồm 504),
  2xx sai schema, read-timeout, connection-fail** (504 vẫn báo riêng để chẩn đoán).
- **Recovery (đồng thời, TỪNG endpoint):** `p95 ≤ pre-spike-reference p95 × 1.2` **VÀ** `p95 ≤ SLA`
  — reference đo **cùng tải 10 VU ngay trong spike scenario**.

**Ma trận nghiệm thu theo pha** (giải quyết mâu thuẫn p99 vs số mẫu — baseline 2 VU/180s ≈ 360 request
→ không đủ 1.000 mẫu cho p99):

| Pha | p95 | p99 | Error SLA |
|---|---|---|---|
| Ops baseline | Bắt buộc | Không áp dụng | Bắt buộc |
| Baseline 2 VU | Bắt buộc | **Chỉ báo cáo** | Bắt buộc |
| Step 1 | Bắt buộc | **Chỉ đánh giá nếu đủ mẫu** | Bắt buộc |
| Step 2–3 | Bắt buộc | Bắt buộc | Bắt buộc |
| Spike | Chỉ báo cáo | Chỉ báo cáo | Bắt buộc |
| Recovery | Bắt buộc | Không áp dụng | Bắt buộc |
| Market cold-miss | **p95 REPORT_ONLY** (đường lạnh nhân tạo, đắt) | Không áp dụng | Bắt buộc |
| Cold start | Median/max | Không áp dụng | Báo cáo riêng |

- **Quy tắc INSUFFICIENT_SAMPLE:** metric **bắt buộc** thiếu mẫu → **fail**; metric **chỉ báo cáo** thiếu mẫu
  → `NOT_EVALUATED`, **không** làm fail toàn run. (Ngưỡng: p95 ≥ 100, p99 ≥ 1.000 success/endpoint/phase.)

## Load profile (closed-model, `wait_time = constant_pacing(1)`)
Số request không khẳng định trước; trần lý thuyết `≈ VU × thời_gian / pacing_interval × request/iteration`
(xem mục Chi phí); số thực + achieved RPS + BQ jobs lấy từ báo cáo. Shape dùng `get_current_user_count()`
để **không bắt đầu hold trước khi đủ user mục tiêu**. Mỗi step ghi rõ spawn rate.

| Pha | Tải (VU) | Thời gian | Spawn rate | SLA |
|---|---:|---:|---:|---|
| Warm-up | 2 | 2 phút | 2/s | không (mẫu bị loại khỏi histogram SLA) |
| Baseline | 2 | 3 phút | 2/s | theo ma trận nghiệm thu |
| Step 1 | 10 | 5 phút | 5/s | theo ma trận nghiệm thu |
| Step 2 | 20 | 5 phút | 5/s | theo ma trận nghiệm thu |
| Step 3 | 40 | 5 phút | 5/s | theo ma trận (tiếp nếu cost/error an toàn) |
| Cold start | min-inst=0 | riêng | — | chỉ báo cáo |

**Spike + Recovery (liên tục 1 shape) — reference & recovery CÙNG 10 VU để so công bằng:**
| Đoạn | User | Thời gian | Spawn rate |
|---|---:|---:|---:|
| Pre-spike reference 1 | 10 | 60s | 10/s |
| Pre-spike reference 2 | 10 | 60s | — |
| Ramp spike | 10 → 100 | 10s | 9/s |
| Giữ spike | 100 | 60s | — |
| Hạ tải | 100 → 10 | 10s | 9/s |
| Recovery 1 | 10 | 60s | — |
| Recovery 2 | 10 | 60s | — |

Tiêu chí recovery (từng endpoint): `recovery p95 ≤ pre-spike-reference p95 × 1.2` **VÀ** `≤ SLA`; cả **Recovery 1 và 2** đều phải thỏa.

- Step 2/3: **đo số instance thực & kiểm scale-out**, không khẳng định ép N instance.
- **Cold start**: set min-inst=0 **không** giết instance đang chạy ngay; chờ scale-to-zero có thể lâu.
  Lần đầu lấy **3–5 mẫu**, **timeout tối đa/mẫu**; quá hạn → ghi "không thu được cold-start" (không chờ vô hạn);
  không dùng deploy revision mới thay cho bằng chứng scale-to-zero (Cloud Run có thể đã khởi động instance
  để health-check deployment). Chỉ nhận mẫu sau **2 điểm zero tươi khác timestamp**; báo **median/max**.

## Warm-up sau mỗi lần đổi revision/config (BẮT BUỘC)
Mỗi lần đổi cấu hình (`realistic → BQ-cache off → cache backend none`) tạo **Cloud Run revision mới** →
chạy baseline ngay sẽ dính cold start + init BigQuery client/ADC + connection setup + import. Trước **mỗi**
scenario, theo trình tự:
1. Chờ revision mới `Ready` và **nhận 100% traffic**.
2. Chờ `min-instances=1` có hiệu lực.
3. Gọi `/health`.
4. Chạy **warm-up data request 1–2 phút**.
5. **Loại mẫu warm-up** khỏi histogram SLA (không ghi vào success-only).
6. Mới bắt đầu baseline.

Không phá điều kiện cache của từng chế độ: realistic → warm-up làm nóng cache như prod; BQ-cold → BQ cache
đã tắt nên warm-up không phá cold-query; market cold-miss → backend `none` nên warm-up không tạo app-cache hit.

## Đo lường (bắt buộc tự quản)
- **Percentile success-only:** Locust **không** tự loại failure khỏi histogram latency. → Event listener
  lưu response-time **thành công** vào histogram riêng theo `(phase, endpoint)`. Báo cáo đồng thời:
  (1) p95/p99 success-only → nghiệm thu; (2) latency của lỗi → chẩn đoán; (3) tổng error rate.
- **Output riêng cho custom histogram** (HTML/CSV mặc định Locust không chứa): xuất
  `report-success-metrics.json` + `.csv`, mỗi dòng: `run_id, phase, endpoint, success_count,
  failure_count, p50, p95, p99, sla_status`. Listener **đặt exit code ≠ 0 nếu SLA fail** (dùng cho CI/nghiệm thu).
- **Cửa sổ auto-abort ≠ cửa sổ recovery** (mặc định percentile Locust ~10s → phải tự tính):
  - **Auto-abort:** rolling window **60s** (trượt).
  - **Recovery:** hai cửa sổ **cố định, không chồng nhau** — `giây 0–59` và `giây 60–119`; **cả hai** đều phải thỏa điều kiện recovery.
- **Ngưỡng số mẫu:** p95 ≥ **100**, p99 ≥ **1.000** success/endpoint/phase → áp dụng theo
  **Ma trận nghiệm thu** (metric bắt buộc thiếu mẫu = fail; metric báo cáo thiếu mẫu = `NOT_EVALUATED`).
- **`reference_p95` (recovery):** = p95 của **toàn bộ success samples trong Reference 1 + Reference 2 gộp**.
  Cả Reference 1 và 2 **đều phải đạt SLA**; nếu một reference window không đạt → spike scenario **không có
  baseline ổn định → fail trước khi đánh giá recovery**. Recovery 1 và Recovery 2 **so riêng** với cùng `reference_p95`.

## Pagination — quản lý state (mỗi Locust user)
Mỗi user giữ state `{page_token, original_filters, original_sort}` (page_token gắn filter+sort ban đầu qua fingerprint):
- **Mỗi iteration chỉ gửi ĐÚNG 1 request**; page_token dùng ở iteration **sau** → giữ đúng pacing + tỉ lệ endpoint.
- Chọn next-page nhưng **chưa có token** → thực hiện first-page, **ghi nhận là first-page**.
- Token trả về **`null`** → kết thúc chuỗi, tạo search mới.
- Next-page **400 do fingerprint** → **lỗi script**, KHÔNG tính lỗi hiệu năng API.
- Tỉ lệ **80/20 đo trên số HTTP request THỰC**, không phải số lần task được chọn.
- **Pre-flight dữ liệu (bắt buộc trước khi chạy):** chọn filter chắc chắn trả `next_page_token` + `limit`
  đủ nhỏ; **kiểm tra token tồn tại trước khi bắt đầu**. **Tolerance thực tế:** first-page **75–85%**,
  next-page **15–25%**; ngoài khoảng → **workload không hợp lệ**, không kết luận hiệu năng.

## Phân tách cache — 3 chế độ (app-cache ≠ BQ-cache)
`JOBS_API_BQ_USE_QUERY_CACHE=false` chỉ tắt **BQ result cache**; **app-cache** của `/market/metrics` vẫn
chạy (batch_meta → app-cache hit → không query gold). Nên tách:
1. **Realistic** (full profile): app-cache như prod, BQ-cache **bật**.
2. **BQ-cold search** (baseline + Step 1/2): BQ-cache **tắt**, chủ yếu đo `/jobs/search`.
   Hợp lệ khi **BQ cache-hit < 5%**.
3. **Market cache-miss microtest** (tải nhỏ, đắt): app-cache bị **vô hiệu** + BQ-cache tắt → đo đường
   lạnh đầy đủ của metrics. Hợp lệ khi **app cache-hit = 0% VÀ BQ cache-hit < 5%**.
   - Vô hiệu app-cache: **ưu tiên thêm cache backend `none`** (no-op, `get()` luôn `None`) — tất định.
     `cache_ttl_seconds=0` trên `TTLCache` phụ thuộc độ phân giải thời gian (fragile), chỉ dùng nếu không thêm backend.
     **Unit test bắt buộc** xác nhận mỗi request là miss ở chế độ này.
- Wiring `bq_use_query_cache`: `Settings → dependency construction → BigQuery repo/executor → QueryJobConfig.use_query_cache`.

## Mô hình chi phí & kiểm soát
- **Dry-run** payload đại diện → `total_bytes_processed` (dry-run KHÔNG trả bytes_billed).
- **Query thật** → `total_bytes_billed` (chi phí thực).
- `maximum_bytes_billed = max(total_bytes_processed) + margin 20–30%`.
- **Trần request (closed-model, `constant_pacing(1)`):**
  `iterations_max ≈ VU × thời_gian / pacing_interval`; `http_requests_max ≈ iterations_max × request/iteration`.
  Thiết kế 1 request/iteration → requests ≈ iterations.
- **Dự toán BQ jobs (80/20 pagination):**
  `BQ jobs ≈ N_metrics + 2×N_search_first + N_search_next + N_market_cache_miss`
  ≡ `N_data_requests + N_search_first + N_market_cache_miss`
  (trong đó `N_data_requests` = search + metrics, **không** gồm /health, /metadata).

## Điều kiện tự dừng (abort) & định nghĩa lỗi
**`unexpected_error_rate`** gồm: 4xx ngoài kịch bản; **429**; 5xx & 504; read-timeout; connection-fail;
**2xx nhưng sai cấu trúc**.
- **Locust listener (client-side, tự động):** `429 trong pha năng lực → abort NGAY`;
  `unexpected_error_rate > 5% trong 60s (rolling) → abort`; p95 > 10s/60s; **max requests**; **max time**.
  Khi kết luận cuối, chỉ cần một `429`, client timeout hoặc request wall-time >25s ở bất kỳ pha đo nào
  cũng làm SLA fail (zero-tolerance), kể cả khi tổng error rate vẫn <1%.
  **Giá trị theo scenario:** `max_requests = theoretical_request_max × 1.1`; `max_time = planned_duration + 120s`.
  Listener **đếm HTTP request thực**, không đếm task iteration.
- **Server-side:** `QueryUsagePerDay` quota.
- **Tổng BQ bytes/cache-hit:** mỗi query của load test được gắn BigQuery label `load_test_run`; finalizer
  lọc `INFORMATION_SCHEMA.JOBS_BY_PROJECT` theo đúng label (không cộng nhầm job khác), kiểm bytes và
  cache invariant theo từng scenario. Ngân sách tổng lọc theo đúng tập run-label sinh từ `suite_id`
  (không wildcard/prefix mơ hồ).
- **Instance:** collector ghi cửa sổ UTC từng pha; finalizer đọc Cloud Monitoring
  `run.googleapis.com/container/instance_count` theo đúng service/location/revision cho Step 2/3 và lưu
  peak + `scale_out_observed`. Chỉ **report-only** vì plan không đặt ngưỡng instance tối thiểu; thiếu metric
  làm run không đọc được (exit 5). **Queue** vẫn theo dõi Cloud Monitoring trong lần đầu.
- *Ngưỡng nghiệm thu cuối vẫn là lỗi bất thường < 1%.*

**`INVALID_TEST` (lỗi script/cấu hình — dừng NGAY, KHÔNG tính vào error rate hiệu năng):** pagination
fingerprint 400; API key 401/403; không lấy được page token ở pre-flight; sai cấu hình scenario; thiếu env bắt buộc.

**Guard an toàn:** từ chối khởi động nếu `env=prod` và (`cache_backend=none` hoặc
`bq_use_query_cache=false`) để tránh vô hiệu cache production → đội chi phí BQ.

## Kết quả & tái lập (report metadata)
- **Payload corpus cố định** (không dựa mỗi global seed — greenlet chạy khác thứ tự không tái lập hoàn toàn):
  chuẩn bị corpus payload cố định; mỗi user chọn theo **round-robin hoặc PRNG riêng** (seed từ `LOAD_SEED`);
  lưu **hash của corpus** trong metadata → realistic và BQ-cold dùng **cùng phân phối truy vấn**.
- Mỗi report (`<REPORT_PREFIX>-metadata.json`) lưu: `LOAD_SEED`, **corpus hash**, **Locust version pin**,
  Cloud Run **revision/image digest**, dataset **batch_id**, cache backend + trạng thái BQ cache,
  `maximum_bytes_billed`, query timeout + cache TTL + rate-limit, region + CPU/RAM + concurrency +
  revision/service min/max instances + scaling mode,
  thời gian bắt đầu/kết thúc và
  **cửa sổ từng pha** theo UTC.
- Trước run và trong finalizer, đối chiếu `PERF_BATCH_ID` với **published batch thật** của đúng snapshot;
  sau run còn đối chiếu `as_of` từ response với metadata batch BigQuery để phát hiện snapshot đổi giữa
  chừng. Query audit (gồm cả query `INFORMATION_SCHEMA` per-scenario, gắn nhãn report và tự loại job hiện
  tại khỏi câu đếm để không tạo vòng lặp) có label riêng theo run: bytes được
  cộng vào budget nhưng không vào cache ratio/job count workload. Query tổng hợp verdict cuối chạy sau cửa
  sổ suite và được coi là overhead báo cáo. Finalizer cũng đối chiếu metadata với **service/revision Cloud Run thật** (URL, digest, cache
  config, concurrency, min/max instances, automatic scaling, 100% traffic) và gộp exit code Locust vào
  `suite-result.json`.
- `aggregate.py` từ chối thiếu/trùng/artifact cũ và tạo `final-suite-result.json` — nguồn verdict duy nhất;
  mọi scenario phải cùng project/service/region/BQ location/dataset và cùng published `batch_id + as_of`,
  nên không thể ghép run từ hai snapshot khác nhau thành một PASS.

## Exit code (chuẩn hóa, cho CI/nghiệm thu)
`0 = PASS` · `2 = SLA_FAIL` · `3 = INVALID_TEST hoặc INSUFFICIENT_SAMPLE (metric BẮT BUỘC)` · `4 = SAFETY_ABORT`.
- Thiếu mẫu ở metric **bắt buộc** → exit `3`. Thiếu mẫu ở metric **report-only** → ghi `NOT_EVALUATED`, **không** đổi exit code.
- `market_coldmiss` (report-only) **không** làm cả suite fail chỉ vì thiếu 1.000 mẫu.
- Finalizer/aggregate dùng thêm `5 = KHÔNG ĐỌC ĐƯỢC`; suite chỉ PASS khi aggregate exit `0`.
- Finalizer bắt buộc nhận exit code thật của process Locust và đối chiếu với exit code trong metadata;
  RUNBOOK xóa artifact verdict/metadata cũ trước khi rerun cùng ID để lỗi tiến trình không thể tái dùng PASS cũ.

## Deliverables (file sẽ tạo khi thực thi)
1. `tests/load/locustfile.py` — closed-model, `constant_pacing(1)`, **1 request/iteration**; task search
   (65, state pagination 80/20 như mục trên) + market (35); **validate cấu trúc response**; request name
   gắn phase; listener tách **success/failure histogram** theo `(phase, endpoint)`, tính **rolling-60s**
   (auto-abort) + **cửa sổ cố định 0–59/60–119** (recovery), **exit code ≠ 0 khi SLA fail**;
   `timeout=(5,25)` phân biệt 504/read-timeout/conn-fail và bảo đảm request >25s không được tính success;
   marker riêng `lt_` + `run_id` + `phase` vào `X-Request-ID` (request-id thường không bị gắn nhãn BQ);
   payload từ **corpus cố định** (round-robin/PRNG riêng theo user); xuất theo `REPORT_PREFIX`:
   `<prefix>-success-metrics.{json,csv}` + `<prefix>-metadata.json` (không ghi đè run khác).
   Trước khi spawn tải ở scenario có search, chạy pre-flight payload đại diện; thiếu `next_page_token` hoặc
   sai contract → INVALID_TEST ngay.
2. `tests/load/shapes.py` — **một concrete shape duy nhất**, chọn phase list qua `LOAD_SCENARIO`
   (`realistic` | `bq_cold_search` | `market_coldmiss` | `spike_recovery` | `ops_baseline`) —
   **chỉ 1 concrete active/run**; step shape ghi rõ spawn rate; spike→recovery dùng `get_current_user_count()`.
3. **`ops_baseline` scenario/user class** đo riêng `/health` + `/metadata` (**≥100 request/endpoint**), **không** vào mix 65/35.
4. `tests/load/cold_start.py` — **script riêng**: chờ instance count → 0 (có timeout) → gửi 1 request → ghi latency
   → lặp **3–5 lần** → xuất **median/max** hoặc `INSUFFICIENT_SAMPLE`.
5. `tests/load/dryrun.py` — dry-run, in `total_bytes_processed`, đề xuất guard.
6. **App change:** (a) setting `bq_use_query_cache` (env `JOBS_API_BQ_USE_QUERY_CACHE`, default `true`) wiring
   `Settings → deps → BQ executor → QueryJobConfig.use_query_cache` trong `bigquery_exec.py`; (b) cache backend
   `none` (no-op) cho market-coldmiss + **guard từ chối `env=prod AND cache_backend=none`**; (c) gắn label
   `load_test_run` cho BigQuery job phát sinh từ request load-test. **Unit test:**
   default `true`; env `false` parse đúng; `QueryJobConfig.use_query_cache` nhận đúng; backend `none` →
   `set("k","v")` **không ném lỗi** và `get("k")` vẫn trả `None`; guard prod raise khi khởi động.
7. `tests/load/finalize.py` + `aggregate.py` — verdict fail-closed từng scenario và toàn suite; instance
   evidence đúng Step 2/3; budget toàn suite lọc đúng tập run-label, bao phủ window/usage và cùng target.
8. `tests/load/RUNBOOK.md` — nguồn hướng dẫn duy nhất cho dựng perf, chạy, finalize và teardown.
9. `docs/load-test-plan.md` — bản plan này; `requirements-dev.txt` pin Locust/Monitoring client.

## Điều chỉnh vận hành
- Máy phát tải **cùng region** Cloud Run (đo backend) hoặc **region người dùng** (đo UX).
- Giám sát **CPU máy chạy Locust** để không thành nút thắt.
- **Identity token hết hạn:** realistic/cold-query/cold-start có thể kéo dài gần/quá hạn token →
  **lấy `AUTH_BEARER` mới trước MỖI run**, không tái sử dụng một token cho toàn quy trình.

## Verification
1. **Validate script miễn phí trên backend fake local** (chạy smoke shape, kiểm listener/abort/histogram/
   rolling+cửa-sổ-cố-định/exit-code) + **unit test `bq_use_query_cache`**.
2. **Dry-run** trên perf → size guard + set `QueryUsagePerDay`.
3. Chạy tuần tự theo RUNBOOK (lấy token mới **trước mỗi run**): nhóm revision warm
   `realistic → ops_baseline → spike_recovery`, rồi deploy BQ-cache off chạy `bq_cold_search`, deploy
   cache backend `none` chạy `market_coldmiss`, cuối cùng **cold-start**. Report ghi theo
   `reports/<suite_id>-<scenario>/` (không ghi đè run trước):
   ```bash
   AUTH_BEARER=$(gcloud auth print-identity-token)
   export BASE_URL=https://<perf-svc>.run.app API_KEY=<perf-key> RUN_ID=<id> LOAD_SCENARIO=realistic
   REPORT_PREFIX="reports/${RUN_ID}/run"
   locust -f tests/load/locustfile.py --headless \
     --html "${REPORT_PREFIX}.html" --csv "${REPORT_PREFIX}" --csv-full-history
   # → cùng prefix: ${REPORT_PREFIX}-success-metrics.{json,csv}, ${REPORT_PREFIX}-metadata.json
   ```
   Warm-up (chờ revision Ready + 100% traffic + min-inst=1 → /health → 1–2 phút data) chạy **trước mỗi** scenario, mẫu warm-up bị loại.
4. Chạy finalizer ngay sau từng scenario, budget-only từ đầu đến cuối suite, rồi aggregate. Chỉ
   `final-suite-result.json` exit `0` mới là PASS.
5. **Đọc kết quả**: p95/p99 success-only (theo phase), latency lỗi, error tách mã, achieved RPS +
   concurrency, instance count (Cloud Run), `total_bytes_billed` (INFORMATION_SCHEMA), cache-hit ratio (app + BQ).
   Xác nhận **cache invariant** mỗi chế độ (realistic / bq-cold <5% / market-coldmiss app=0% & BQ<5%) + **80/20 tolerance**.
6. **Đối chiếu ngưỡng theo pha** (đủ mẫu: p95≥100, p99≥1.000/endpoint/phase); recovery **hai cửa sổ cố định (Recovery 1 & 2)**, cả hai đều thỏa `p95 ≤ pre-spike-reference×1.2` VÀ `≤ SLA`, tính từng endpoint.
7. **Teardown**: xoá service perf + snapshot dataset; **khôi phục quota override** (về giá trị gốc, không "xoá quota"); đưa env `JOBS_API_BQ_USE_QUERY_CACHE` về `true` (không cần revert code — config có sẵn, mặc định `true`); không chạm production.
