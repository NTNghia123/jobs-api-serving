# Load & Latency Test

Bộ đo **latency** + **khả năng chịu tải** cho jobs-serving-api. Có hai cách chạy: dùng tài nguyên perf/staging
tách biệt, hoặc dùng chính project/service/dataset hiện tại khi dự án chưa phục vụ người dùng. Mô hình
**closed-model** (Locust): báo cáo theo concurrency (VU) + achieved RPS, KHÔNG đồng nhất VU với RPS.
Kế hoạch đầy đủ: [`docs/load-test-plan.md`](../../docs/load-test-plan.md).

## Thành phần
| File | Vai trò |
|---|---|
| `config.py` | Scenario/phase, spawn rate, SLA matrix, ngưỡng abort, đường report. |
| `corpus.py` | Corpus payload **cố định** + PRNG mỗi user + hash (tái lập). |
| `metrics.py` | Histogram success-only theo (phase,endpoint), rolling-60s abort, reference/recovery, cửa sổ UTC từng pha, exit code. |
| `planner.py` | Máy trạng thái pha THUẦN (không import locust) — gate scale-up/down, "_transition". |
| `shapes.py` | `ScenarioShape` (vỏ mỏng bọc `PhasePlanner`; một concrete, chọn theo `LOAD_SCENARIO`). |
| `locustfile.py` | User dispatch 1 request/iteration, pagination 80/20, phân loại lỗi, ghi report. |
| `response_validation.py` | Validate đầy đủ response 200 bằng đúng Pydantic contract; sai schema không vào percentile success. |
| `preflight.py` | Trước run có search: xác nhận payload đại diện trả token, nếu không dừng workload 80/20 ngay. |
| `dryrun.py` | BigQuery dry-run trên corpus → size `maximum_bytes_billed`. |
| `cold_start.py` | Đo cold start (script riêng, median/max; cần min-instances=0). |
| `finalize.py` | Verdict một scenario: gộp Locust + BigQuery + app-cache evidence + cấu hình/instance Cloud Run thật + batch snapshot thật → `suite-result.json`. |
| `aggregate.py` | Verdict duy nhất: đủ mọi scenario, cùng target, budget bao phủ cửa sổ và tổng usage → `final-suite-result.json`. |

Chọn **một** runbook và không trộn lệnh giữa hai bản:

- [`RUNBOOK_CURRENT_PROJECT.md`](RUNBOOK_CURRENT_PROJECT.md): dùng chính project/service/dataset hiện tại;
  sao lưu rồi khôi phục service, không tạo hoặc xóa project/service/dataset.
- [`RUNBOOK.md`](RUNBOOK.md): dùng tài nguyên perf/staging tách biệt.

Ở cả hai cách, Locust exit 0 chỉ là SLA client-side **sơ bộ**; suite chỉ PASS khi `aggregate.py` exit 0
và đủ mọi scenario cùng budget-only.

## Scenario (`LOAD_SCENARIO`)
`realistic` · `bq_cold_search` · `market_coldmiss` · `spike_recovery` · `ops_baseline` · `smoke` (validate DuckDB/fake local, KHÔNG nghiệm thu).

## Exit code
Locust (client-side SLA): `0 PASS` · `2 SLA_FAIL` · `3 INVALID_TEST / INSUFFICIENT_SAMPLE` · `4 SAFETY_ABORT`.
finalize (kết quả scenario đã gộp Locust): `0 PASS` · `2 SLA/BUDGET_FAIL` · `3 INVALID` · `4 SAFETY_ABORT` · `5 KHÔNG ĐỌC ĐƯỢC`.
aggregate (toàn suite): cùng mã trên; thiếu/trùng/artifact cũ hoặc sai schema → `3`.

## Report mỗi run (theo `REPORT_PREFIX`)
`-success-metrics.{json,csv}` (p95/p99 success-only theo pha + `sla_status`) · `-error-metrics.{json,csv}`
(latency lỗi để chẩn đoán) · `-metadata.json` (run_id, corpus_hash, seed, revision/digest, cache state,
region/scaling, thời gian UTC) · `suite-result.json` (verdict scenario) · `final-suite-result.json` (verdict toàn suite).

## Đọc & diễn giải
- **Nghiệm thu latency:** `-success-metrics.json` — p95/p99 **chỉ trên response thành công**, theo ma trận pha.
- **Chẩn đoán lỗi:** `-error-metrics.json` — 504/timeout/5xx mất bao lâu.
- **Pagination:** `-metadata.json` `next_page.first/next` phải trong 75–85% / 15–25%, nếu không → `WORKLOAD_INVALID`.
- **Chi phí + cache invariant + revision:** `suite-result.json` (finalize). App-cache-hit của `market_coldmiss`
  **không có trong BigQuery** → finalizer tự đếm log service của đúng revision/run; manual không thể tạo PASS.
- **Scale-out Step 2/3:** `suite-result.json.instance_evidence.phases` lấy từ Cloud Monitoring, report-only;
  thiếu điểm metric làm finalizer exit 5.

---

Validate miễn phí trên máy (backend `fake`, scenario `smoke`) có trong Track A của cả hai runbook.
