# ADR-020: BigQuery serving — read adapter (keyset trong SQL, đọc theo batch, cost guard)

- Trạng thái: Đã chấp nhận
- Ngày: 2026-09-13
- Người quyết định: [Điền tên]
- Liên quan: ADR-018 (BigQuery warehouse), ADR-019 (Mongo source & contract), ADR-024 (salary),
  ADR-025 (atomic publication), ADR-026 (pagination snapshot); impl
  `app/infrastructure/warehouse/bigquery_read_sql.py` (SQL thuần), `bigquery_exec.py` (I/O chung),
  `bigquery_jobs.py`, `bigquery_metrics.py`; `app/settings.py`, `app/api/deps.py`, `app/api/market.py`.

## Bối cảnh
Phase 3 bật `warehouse_backend=bigquery` để API phục vụ dữ liệu THẬT đã publish (silver/gold ở
BigQuery). Trên BigQuery, **byte quét = tiền** và không có transaction đọc như DuckDB, nên adapter đọc
phải: đẩy filter/sort/keyset **xuống SQL** (không kéo cả bảng về Python như fake/duckdb), chặn trần chi
phí, đọc **đúng batch đã publish** (không "current state" cho trang cũ), và **khớp CHÍNH XÁC ngữ nghĩa
`FakeJobRepository`** (bộ contract test chạy chung fake ↔ BQ). Hình dạng bảng/metadata theo ADR-025.

## Quyết định

### Kiến trúc — tách SQL thuần khỏi I/O
- **`bigquery_read_sql.py` THUẦN** (không import `google-cloud-bigquery`): dựng chuỗi SQL + danh sách
  `QueryParam` → **unit-test được không cần client/ADC/mạng**. **`bigquery_exec.py`** giữ client (ADC)
  + execute; **`bigquery_jobs.py`/`bigquery_metrics.py`** map Row → DTO. Cùng mẫu ELT (publish.py thuần ↔
  bigquery_writer.py I/O). Phần I/O test thật ở Phase 4 (`RUN_BQ_INTEGRATION=1` + dataset `_test`).

### An toàn injection (như DuckDB adapter, ADR-003/006)
- **Identifier từ allowlist:** tên bảng (schema.py), cột, toán tử, ORDER BY là HẰNG trong code.
  `ReadTarget` validate charset `project`/`dataset` (`^[A-Za-z0-9_-]+$`) rồi backtick + fully-qualified.
- **Value luôn qua `@param`** (`ScalarQueryParameter` typed) — không nối chuỗi giá trị vào SQL.
- **Column projection tường minh** (không `SELECT *`, không cột PII); map Row → JobItem tường minh
  → cột lạ không lọt ra. `categories` chỉ lấy 3 subfield API cần qua `ARRAY(SELECT AS STRUCT …)`.

### Đọc theo BATCH (invariant 1/3/4, ADR-025/026)
- Mọi query silver/gold buộc `batch_id = @batch_id`. Trang 1: phân giải batch **đang publish** +
  `data_as_of_at` bằng JOIN `warehouse_state` × `warehouse_batches`. **Chưa publish batch nào → 503**
  (`UPSTREAM_UNAVAILABLE`), không phải 200-rỗng.
- **Metadata đọc theo `batch_id`** (không theo current state) → trang 2 của batch A trả đúng `as_of` A;
  rollback chỉ đổi pointer. Cursor mang `{batch_id, as_of, last_sort, last_job_id}`: `as_of` **snapshot**
  ở trang 1 (batch bất biến) → trang 2+ **không query lại metadata**, và luôn đọc đúng batch của token.
- `total_estimated = null` (không `COUNT(*)` mỗi request — tốn byte). Đừng dùng để phân trang.

### Keyset trong SQL + limit+1 (khớp fake — điểm dễ sai nhất)
- **Sort allowlist** → `(expr, direction)`, luôn `… , job_id ASC` để thứ tự TẤT ĐỊNH:
  `salary_max_desc`→`COALESCE(salary_max_vnd_month,0) DESC`; `salary_min_asc`→`COALESCE(…,0) ASC`;
  `experience_asc`→`COALESCE(experience_min_years,0.0) ASC`; `posted_desc`→`effective_posted_date DESC`.
- **Keyset predicate** (trang 2+): DESC dùng `(<expr> < @last_sort OR (<expr>=@last_sort AND job_id > @last_job_id))`;
  ASC dùng `>`. `LIMIT @limit_plus_one`; nhận đủ `limit+1` dòng ⇒ còn trang sau (cắt về `limit`).
- **NULL bất đối xứng khớp fake:** SORT/KEYSET **COALESCE NULL→0** (khớp `x or 0`); còn FILTER
  `salary_min` (`salary_max_vnd_month >= @x`) và `experience_max` (`experience_min_years <= @x`) **LOẠI NULL**
  (khớp `is not None`). Hai chỗ xử lý NULL ngược nhau — cố ý.

### Filter / dimension (ADR-019)
- `posted_after` **BẮT BUỘC** → cũng là partition filter (`effective_posted_date >= @posted_after`) thoả
  `require_partition_filter` của silver_jobs. `posted_before` optional.
- `seniority`: `COALESCE(seniority_normalized,'unknown') = @seniority` (JobItem.seniority bắt buộc, có
  `unknown` ↔ `seniority_normalized IS NULL`).
- `category`: `EXISTS (SELECT 1 FROM UNNEST(categories) c WHERE c.category_key=@k)` → job nhiều category
  KHÔNG bị nhân dòng. Search không hỗ trợ `<source>:unknown` (chỉ metrics).
- Metrics: đọc gold theo `batch_id`+`window`+`dimension`; gold giữ **số thật**, **k-anonymity che median
  áp ở TẦNG API** theo `salary_sample_count < JOBS_API_METRICS_MIN_SAMPLE_SIZE`, KHÔNG ở SQL. Count defs:
  `posting_count ≥ salary_disclosed_count ≥ salary_sample_count`. window `90d` neo `as_of_date` (giờ VN
  +07:00); unknown bucket giữ posting.

### Cache key metrics gắn batch_id (invariant 5, ADR-026)
- `/market/metrics` cache key `{env}:metrics:v1:{batch_id}:{window}:{dimension}` — **thay surrogate
  `as_of_date` cũ** (hai batch cùng ngày sẽ collision). Handler đọc `current_batch()` MỘT lần → dùng cùng
  `batch_id` cho cả cache key lẫn `market_metrics(...)` (không lệch nếu batch mới publish giữa chừng).
  Port `MetricsRepository` đổi: thêm `current_batch()→BatchRef`, `market_metrics(dim, window, batch_id)`.

### Cost / timeout / config
- `maximum_bytes_billed` mỗi query (`JOBS_API_BQ_MAXIMUM_BYTES_BILLED`, mặc định 2 GB); vượt → BQ từ
  chối job. Log `bq_job_id, total_bytes_billed, cache_hit, elapsed_ms` mỗi truy vấn.
- **Timeout → cancel → 504:** `job.result(timeout=query_timeout_s)` quá hạn → `job.cancel()` (ngừng job
  phía BQ) → `QueryTimeoutError` (504). Cursor lỗi: **400 `INVALID_PAGE_TOKEN`** / **410 khi TTL** (ADR-026).
- **Config API:** `JOBS_API_BQ_PROJECT`, `JOBS_API_BQ_DATASET`, `JOBS_API_BQ_LOCATION`,
  `JOBS_API_BQ_MAXIMUM_BYTES_BILLED`. Fail-fast: `warehouse_backend=bigquery` mà thiếu project/dataset →
  **không boot**. **KHÔNG có `mongo_url`** trong Settings API — tầng API chỉ đọc BigQuery, không chạm
  Mongo (config ELT ở ENV RIÊNG `JOBS_MONGO_*`/`JOBS_BQ_*`). *Ghi chú: plan doc §Phase 3 liệt kê
  `mongo_url` là tàn dư cũ — bị quyết định tách ENV ELT ghi đè.* Xác thực = ADC (SA reader tách môi
  trường, không JSON key — ADR-022).

## Phương án đã cân nhắc
- **OFFSET pagination** — quét lại từ đầu mỗi trang (tốn byte, chậm, kết quả lệch khi có batch mới);
  keyset đẩy xuống SQL rẻ và tất định (ADR-006).
- **Keyset ở Python (như fake/duckdb)** — kéo cả bảng về → tốn byte khủng trên BQ; buộc đẩy xuống SQL.
- **`COUNT(*)` cho `total`** — quét toàn phần mỗi request; `total_estimated=null` thay bằng `next_page_token`.
- **Cache key theo `as_of_date`** — hai batch cùng ngày collision (bug); dùng `batch_id` duy nhất.
- **k-anonymity trong SQL** — gold mất "số thật" để audit/parity; áp ở API linh hoạt hơn (ADR-024/025).
- **Gộp SQL builder vào class I/O** — mất seam unit-test không cần BQ; tách module thuần để test rẻ.

## Hệ quả
- Tích cực: chi phí kiểm soát (projection + partition prune + `maximum_bytes_billed` + keyset); parity
  fake ↔ BQ (contract test chung); page 2 đọc đúng batch + `as_of` trang 1; cache đúng theo batch;
  fail-fast config sai ngay ở boot; SQL builder test được không cần GCP.
- Đánh đổi: phần I/O (client/ADC) chỉ verify ở integration Phase 4; `duckdb` backend còn tạm tắt
  (khôi phục Phase 4, ADR-026 cho token TTL/cleanup); `maximum_bytes_billed` vượt hiện raise 500 (chưa
  map mã lỗi riêng) — chấp nhận cho giai đoạn này.
