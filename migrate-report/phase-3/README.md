# Migration Report — Phase 3: BigQuery Read Adapter

## 1. Tổng quan

Phase 3 nối Jobs Serving API với các bảng silver/gold đã publish trong BigQuery. Đây là phase chuyển
từ contract chạy trên dữ liệu `fake` sang khả năng phục vụ dữ liệu warehouse thật, nhưng vẫn giữ
nguyên domain ports và HTTP contract.

```text
Consumer / Team AI
        │
        ▼
FastAPI handlers
├── POST /v1/jobs/search
└── GET  /v1/market/metrics
        │
        ▼
JobRepository / MetricsRepository
        │ composition root chọn backend
        ├──────────────► Fake repositories (test/local mặc định)
        │
        └──────────────► BigQuery read adapters
                           ├── SQL builder thuần
                           ├── typed query parameters
                           ├── maximum_bytes_billed
                           ├── timeout → cancel
                           └── ADC của service account reader
                                      │
                                      ▼
                         warehouse_state + warehouse_batches
                                      │ batch_id đã publish
                         ┌────────────┴────────────┐
                         ▼                         ▼
                    silver_jobs          gold_market_metrics
```

Mọi truy vấn dữ liệu đều gắn `batch_id`. Trang đầu đọc batch đang publish; các trang tiếp theo tiếp
tục đọc batch nằm trong page token, nên việc publish batch mới không làm kết quả phân trang bị trộn.

### Trạng thái

| Phạm vi | Trạng thái | Ý nghĩa |
| --- | --- | --- |
| BigQuery SQL builder | **Hoàn thành trong source** | Filter, sort, keyset, metadata và metrics SQL đã được hiện thực |
| Job/metrics repositories | **Hoàn thành trong source** | Hai adapter hiện thực domain ports và map BigQuery Row sang DTO |
| Backend selection/config | **Hoàn thành trong source** | `warehouse_backend=bigquery` đã khả dụng và fail-fast khi thiếu config |
| Unit/contract tests liên quan | **Xanh** | 56 test tập trung; toàn suite 201 pass, 3 skip |
| BigQuery integration thật | **Cần xác minh** | I/O với ADC, IAM, schema và dataset thật để Phase 4 kiểm chứng có điều kiện |
| Cursor TTL và HTTP 410 | **Chưa triển khai** | Token đã ký và gắn batch; expiration/cleanup awareness thuộc ADR-026/Phase 4 |
| DuckDB parity | **Chưa triển khai lại** | DuckDB vẫn bị tắt đến Phase 4 |

> Phase 3 hoàn thành khả năng đọc BigQuery trong source. Muốn chạy thật, dataset phải có ít nhất một
> batch do Phase 2 publish và runtime phải dùng service account reader đúng môi trường.

---

## 2. Phase 3 giải quyết vấn đề gì?

Đọc BigQuery khác đọc fixture hoặc một database nhỏ ở bốn điểm quan trọng:

1. **Byte quét tạo ra chi phí:** không thể kéo toàn bộ bảng về Python rồi mới filter/sort.
2. **Dữ liệu được publish theo batch:** API phải đọc snapshot nhất quán, không chỉ lấy “dòng mới nhất”.
3. **Pagination phải ổn định:** batch mới có thể được publish giữa trang 1 và trang 2.
4. **Warehouse là upstream có độ trễ:** query cần timeout, cancel và log chi phí/trạng thái.

Phase 3 đẩy filter, sort và keyset xuống SQL; chỉ chọn các cột API cần; giới hạn byte quét; đồng thời
giữ handler độc lập với BigQuery thông qua `JobRepository` và `MetricsRepository`.

---

## 3. Các thành phần chính

### 3.1. SQL builder thuần

`app/infrastructure/warehouse/bigquery_read_sql.py` chỉ dựng SQL và danh sách `QueryParam`, không
khởi tạo BigQuery client. Nhờ vậy có thể kiểm tra an toàn truy vấn và pagination mà không cần GCP.

Builder cung cấp ba nhóm query:

| Hàm | Mục đích |
| --- | --- |
| `build_current_batch_meta_sql` | JOIN state với catalog để lấy batch đang publish và `data_as_of_at` |
| `build_search_sql` | Query `silver_jobs` với filters, keyset và `LIMIT + 1` |
| `build_metrics_sql` | Query `gold_market_metrics` theo batch, window và dimension |

Kết quả builder gồm:

```text
SqlAndParams
├── sql: câu lệnh dùng @named_parameter
└── params: tên + BigQuery type + value
```

### 3.2. BigQuery executor dùng chung

`BigQueryExecutor` chịu trách nhiệm I/O cho cả jobs và metrics:

- tạo `QueryJobConfig`;
- gắn typed scalar parameters;
- áp `maximum_bytes_billed`;
- gọi `job.result(timeout=...)`;
- timeout thì gọi `job.cancel()` và trả lỗi API 504;
- log `bq_job_id`, `total_bytes_billed`, `cache_hit`, `elapsed_ms` và số row.

Executor dùng Application Default Credentials. Trên Cloud Run, credential đến từ runtime service
account; source code không đọc file JSON key.

### 3.3. BigQuery job repository

`BigQueryJobRepository` hiện thực `JobRepository`:

1. Trang đầu gọi `_current_batch()` để lấy `batch_id` và `data_as_of_at`.
2. Dựng search SQL cho đúng batch.
3. Lấy `limit + 1` dòng để biết có trang sau hay không.
4. Map từng row sang `JobItem` bằng allowlist tường minh.
5. Nếu còn trang, dựng cursor gồm batch snapshot và vị trí keyset.
6. Trả `total_estimated=None` để tránh một truy vấn `COUNT(*)` tốn byte cho mỗi request.

Nếu warehouse chưa có batch được publish, repository trả `503 UPSTREAM_UNAVAILABLE`, không giả vờ
đó là một kết quả rỗng hợp lệ.

### 3.4. BigQuery metrics repository

`BigQueryMetricsRepository` hiện thực `MetricsRepository`:

- `current_batch()` lấy `BatchRef(batch_id, as_of)`;
- `market_metrics(dimension, window, batch_id)` đọc đúng gold rows của batch đã chốt;
- adapter giữ nguyên median thật;
- handler API mới áp k-anonymity theo `salary_sample_count`.

Tách việc che median khỏi SQL giúp warehouse giữ số liệu audit và cho phép thay đổi ngưỡng API mà
không rebuild gold.

### 3.5. Composition root

`app/api/deps.py` là nơi duy nhất chọn adapter:

```text
warehouse_backend=fake     → FakeJobRepository + FakeMetricsRepository
warehouse_backend=bigquery → BigQueryJobRepository + BigQueryMetricsRepository
warehouse_backend=duckdb   → bị từ chối đến Phase 4
```

BigQuery modules được import lazy, nên test/local dùng `fake` không khởi tạo ADC client hoặc phụ
thuộc kết nối GCP.

---

## 4. Snapshot theo batch

### 4.1. Trang đầu

Trang đầu không có page token. Adapter JOIN:

```text
warehouse_state.published_batch_id
                 =
warehouse_batches.batch_id
```

Kết quả cung cấp:

- `batch_id`: snapshot dữ liệu cần query;
- `data_as_of_at`: data cutoff trả ra trong response dưới tên `as_of`;
- `as_of_date`: metadata dùng để xây các cửa sổ metric ở warehouse.

### 4.2. Trang tiếp theo

Cursor nội bộ mang:

```json
{
  "batch_id": "batch-20260913T010000Z",
  "as_of": "2026-09-13T01:00:00+00:00",
  "last_sort": 50000000,
  "last_job_id": "topdev:1001"
}
```

Handler ký cursor bằng HMAC và gắn fingerprint của `filters + sort`. Người gọi:

- không sửa được `batch_id`, `as_of` hoặc vị trí keyset;
- không thể dùng token cũ với bộ filter/sort khác;
- có thể tiếp tục đọc batch A dù batch B vừa trở thành current.

Trang tiếp theo không query lại current metadata; `as_of` được lấy từ snapshot đã ký của trang đầu.

### 4.3. Phần chưa có trong Phase 3

Page token chưa có thời điểm hết hạn. Nếu batch trong token đã bị cleanup, Phase 3 chưa trả
`410 CURSOR_EXPIRED`; truy vấn có thể trả trang rỗng. TTL, retention coupling và mã lỗi 410 thuộc
ADR-026/Phase 4.

---

## 5. Search SQL và keyset pagination

### 5.1. Filters

Mọi search query luôn có:

```sql
batch_id = @batch_id
AND effective_posted_date >= @posted_after
```

`posted_after` bắt buộc vừa giới hạn nghiệp vụ vừa thỏa `require_partition_filter` của
`silver_jobs`.

| API filter | SQL semantics |
| --- | --- |
| `posted_after` | `effective_posted_date >= @posted_after` |
| `posted_before` | `effective_posted_date <= @posted_before` |
| `source` | `source = @source` |
| `seniority` | `COALESCE(seniority_normalized, 'unknown') = @seniority` |
| `category` | `EXISTS` trên repeated `categories` |
| `salary_min` | `salary_max_vnd_month >= @salary_min`; loại row salary null |
| `experience_max` | `experience_min_years <= @experience_max`; loại row experience null |

Category dùng `EXISTS`, không `CROSS JOIN UNNEST` ở search, nên một job có nhiều category vẫn chỉ
trả một dòng.

### 5.2. Sort allowlist

| Sort option | ORDER BY chính | Hướng keyset | NULL khi sort |
| --- | --- | --- | --- |
| `salary_max_desc` | `COALESCE(salary_max_vnd_month, 0) DESC` | `< last_sort` | `0` |
| `salary_min_asc` | `COALESCE(salary_min_vnd_month, 0) ASC` | `> last_sort` | `0` |
| `experience_asc` | `COALESCE(experience_min_years, 0.0) ASC` | `> last_sort` | `0.0` |
| `posted_desc` | `effective_posted_date DESC` | `< last_sort` | Không null |

Mọi sort đều thêm `job_id ASC` làm tie-breaker. Điều này tạo thứ tự tất định khi nhiều job có cùng
lương, kinh nghiệm hoặc ngày đăng.

### 5.3. Keyset predicate

Với sort giảm dần:

```sql
(sort_expr < @last_sort
 OR (sort_expr = @last_sort AND job_id > @last_job_id))
```

Với sort tăng dần, toán tử chính đổi thành `>`; tie-breaker `job_id > @last_job_id` giữ nguyên.

Adapter dùng `LIMIT @limit_plus_one`. Nếu BigQuery trả nhiều hơn `limit`, dòng dư chứng minh còn
trang sau; API cắt về đúng `limit` và tạo `next_page_token`.

### 5.4. Vì sao không dùng OFFSET?

OFFSET khiến BigQuery phải đọc và bỏ lại các dòng của trang trước, chi phí tăng theo số trang. Nó
cũng dễ bị trùng/sót khi dataset thay đổi. Keyset dùng vị trí cuối của trang trước, kết hợp batch
snapshot, nên ổn định và phù hợp hơn cho warehouse.

---

## 6. An toàn dữ liệu và chống injection

Phase 3 áp hai lớp riêng biệt:

### Identifier

- Project và dataset phải khớp `^[A-Za-z0-9_-]+$`.
- Tên bảng đến từ hằng trong schema.
- Tên cột, toán tử và biểu thức sort đến từ allowlist trong code.
- Table ID luôn fully-qualified và đặt trong backtick.

### Value

- Filter values, cursor values, batch ID, limit, dimension và window đều qua typed
  `ScalarQueryParameter`.
- Không nối chuỗi giá trị người dùng vào SQL.

Search dùng column projection tường minh, không `SELECT *`. Chỉ ba category subfield cần cho API
được chọn. Row được map thủ công sang `JobItem`, nên cột lạ như email liên hệ không lọt ra response.

---

## 7. Cost control và timeout

### 7.1. Cost guard

Mỗi query có `maximum_bytes_billed`, mặc định 2 GB:

```text
JOBS_API_BQ_MAXIMUM_BYTES_BILLED=2000000000
```

Các cơ chế giảm byte quét gồm:

- bắt buộc partition filter `posted_after`;
- chỉ chọn cột cần thiết;
- filter và keyset chạy trong BigQuery;
- không `COUNT(*)` cho `total_estimated`;
- metrics đọc gold table nhỏ thay vì scan silver;
- cache metrics theo batch.

Nếu ước lượng của BigQuery vượt trần, query bị từ chối. Phase 3 hiện chưa map lỗi vượt cost cap sang
mã ứng dụng riêng; lỗi đó có thể đi qua error envelope dưới dạng lỗi server.

### 7.2. Timeout

```text
client timeout > request budget > BigQuery query timeout
```

Mặc định hiện tại:

```text
client:              nên >= 30 giây
request budget:      20 giây
BigQuery query:      10 giây
```

Khi `job.result()` quá hạn, executor gọi `job.cancel()` để query không tiếp tục tiêu thụ tài nguyên,
sau đó raise `QueryTimeoutError` và API trả 504.

`request_timeout_s` hiện là ngân sách/cấu hình cho edge và observability; Phase 3 không tạo một
middleware cưỡng chế timeout toàn request trong process.

### 7.3. Query log

Một query hoàn thành ghi các field:

```text
event=bq_query
op=batch_meta|search|metrics
bq_job_id=...
total_bytes_billed=...
cache_hit=true|false
elapsed_ms=...
rows=...
```

Không log giá trị filter hoặc secret.

---

## 8. Market metrics và cache

Handler `/v1/market/metrics` thực hiện theo thứ tự:

1. Validate `dimension` và `window`.
2. Đọc current batch đúng một lần.
3. Dùng cùng `batch_id` cho cache key và gold query.
4. Nếu cache miss, đọc `gold_market_metrics`.
5. Che median khi `salary_sample_count < metrics_min_sample_size`.
6. Ghi response vào cache.

Cache key:

```text
{env}:metrics:v1:{batch_id}:{window}:{dimension}
```

Dùng `batch_id` thay vì `as_of_date` ngăn hai batch publish cùng ngày dùng nhầm cache. Publish batch
mới tự tạo namespace cache mới mà không cần xoá đồng bộ cache cũ ngay lập tức.

Window `90d` trả `window_start/window_end` neo theo `as_of` ở timezone Việt Nam. `all_time` trả hai
mốc này là `null`.

---

## 9. Cấu trúc file Phase 3

| File | Chức năng |
| --- | --- |
| `app/infrastructure/warehouse/bigquery_read_sql.py` | SQL builder, target validation, sort/keyset allowlist |
| `app/infrastructure/warehouse/bigquery_exec.py` | BigQuery client, params, cost cap, timeout/cancel và query log |
| `app/infrastructure/warehouse/bigquery_jobs.py` | `JobRepository`, batch snapshot, Row → JobItem và cursor |
| `app/infrastructure/warehouse/bigquery_metrics.py` | `MetricsRepository`, batch ref và Row → MetricRow |
| `app/api/deps.py` | Chọn fake/BigQuery adapter bằng settings |
| `app/api/market.py` | Cache key gắn batch và k-anonymity |
| `app/settings.py` | Cấu hình BigQuery và fail-fast backend |
| `app/domain/ports/metrics_repository.py` | `BatchRef` và interface metrics theo batch |
| `docs/adr/ADR-020-bigquery-serving.md` | Quyết định kiến trúc BigQuery serving |
| `tests/test_bigquery_sql.py` | Filter, sort, keyset, params và identifier safety |
| `tests/test_bigquery_jobs.py` | Row mapping, cursor và typed parameter mapping |
| `tests/test_bigquery_metrics.py` | Gold Row → MetricRow |

---

## 10. Cấu hình chạy BigQuery backend

### 10.1. Biến bắt buộc

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `JOBS_API_WAREHOUSE_BACKEND` | `fake` | Đặt `bigquery` để bật adapter thật |
| `JOBS_API_BQ_PROJECT` | Rỗng | Project chứa warehouse; bắt buộc với BigQuery |
| `JOBS_API_BQ_DATASET` | Rỗng | Dataset staging hoặc prod; bắt buộc với BigQuery |
| `JOBS_API_BQ_LOCATION` | `asia-southeast1` | Phải khớp location dataset |
| `JOBS_API_BQ_MAXIMUM_BYTES_BILLED` | `2000000000` | Trần byte mỗi query |
| `JOBS_API_QUERY_TIMEOUT_S` | `10` | Timeout query trước khi cancel |
| `JOBS_API_REQUEST_TIMEOUT_S` | `20` | Request budget, phải lớn hơn query timeout |

Với `env != local`, API còn bắt buộc page-token secret và API-key config hợp lệ.

### 10.2. Điều kiện warehouse

Trước khi bật BigQuery backend:

- dataset đã tồn tại ở đúng location;
- Phase 2 đã tạo đúng schema;
- `warehouse_state` có pointer `serving` khác null;
- `warehouse_batches` có metadata cho pointer đó;
- silver và gold có rows thuộc cùng batch;
- runtime SA có `dataViewer` trên đúng dataset và `jobUser` ở project.

### 10.3. Authentication

Trên Cloud Run, gắn `sa-api-reader-staging` hoặc `sa-api-reader-prod` làm runtime identity.

Nếu kiểm tra từ máy phát triển:

```bash
gcloud auth application-default login
```

Không đặt `GOOGLE_APPLICATION_CREDENTIALS` trỏ tới JSON key nếu workload có thể dùng service
identity/ADC.

---

## 11. Cách chạy

### 11.1. Backend fake

Backend mặc định không cần GCP:

```bash
cp .env.example .env
uvicorn app.main:app --reload --port 8080
```

### 11.2. BigQuery staging

Ví dụ biến môi trường cho một phiên Bash:

```bash
export JOBS_API_ENV=staging
export JOBS_API_WAREHOUSE_BACKEND=bigquery
export JOBS_API_BQ_PROJECT='<gcp-project-id>'
export JOBS_API_BQ_DATASET='jobs_staging'
export JOBS_API_BQ_LOCATION='asia-southeast1'
export JOBS_API_BQ_MAXIMUM_BYTES_BILLED='2000000000'
export JOBS_API_QUERY_TIMEOUT_S='10'
export JOBS_API_REQUEST_TIMEOUT_S='20'
export JOBS_API_PAGE_TOKEN_SECRET='<secret-tu-secret-manager>'
export JOBS_API_API_KEYS='<json-hash-api-key>'

uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Nếu thiếu project/dataset khi chọn backend BigQuery, Settings từ chối boot ngay.

### 11.3. Gọi API

```bash
curl -s -X POST http://localhost:8080/v1/jobs/search \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <api-key>' \
  -d '{
    "filters": {
      "posted_after": "2026-06-01",
      "source": "topdev",
      "seniority": "senior"
    },
    "sort": "posted_desc",
    "limit": 20
  }'
```

Response BigQuery có `total_estimated: null`. Dùng `next_page_token` để biết và lấy trang tiếp theo:

```bash
curl -s -X POST http://localhost:8080/v1/jobs/search \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <api-key>' \
  -d '{
    "filters": {
      "posted_after": "2026-06-01",
      "source": "topdev",
      "seniority": "senior"
    },
    "sort": "posted_desc",
    "limit": 20,
    "page_token": "<next_page_token>"
  }'
```

Filters và sort phải được gửi lại giống trang đầu. Thay chúng nhưng giữ token cũ sẽ trả
`400 INVALID_PAGE_TOKEN`.

Metrics:

```bash
curl -s \
  -H 'X-API-Key: <api-key>' \
  'http://localhost:8080/v1/market/metrics?dimension=category&window=90d'
```

---

## 12. Cách xác minh BigQuery integration

### 12.1. Xác minh current batch

```bash
bq query --use_legacy_sql=false \
  "SELECT s.published_batch_id, b.data_as_of_at, b.as_of_date
   FROM \`<project>.<dataset>.warehouse_state\` s
   JOIN \`<project>.<dataset>.warehouse_batches\` b
     ON b.batch_id = s.published_batch_id
   WHERE s.warehouse_name = 'serving'"
```

Kỳ vọng đúng một row và `published_batch_id` có dữ liệu.

### 12.2. Smoke test search

Gọi trang đầu với `limit=2`, lưu token, sau đó gọi trang hai bằng cùng filters/sort. Kiểm tra:

- không trùng `job_id` giữa hai trang;
- `as_of` hai trang giống nhau;
- token trang sau kết thúc, không lặp vô hạn;
- publish batch mới giữa hai request không làm trang hai đổi sang batch mới.

### 12.3. Smoke test metrics

Gọi đủ:

```text
dimension = source | seniority | category
window    = 90d | all_time
```

Kiểm tra `salary_sample_count <= salary_disclosed_count <= posting_count`, median bị che đúng theo
ngưỡng API, và unknown bucket không bị mất.

### 12.4. Kiểm tra log và cost

Sau mỗi request BigQuery, log phải có `bq_job_id`, bytes billed và elapsed time. Xác minh query search
có partition filter trong BigQuery job details và không vượt cost cap.

### 12.5. Timeout

Trong dataset test, đặt query timeout đủ thấp hoặc dùng một query integration có kiểm soát để xác
minh adapter gọi cancel và API trả error envelope với `QUERY_TIMEOUT`/HTTP 504. Không thử nghiệm bằng
truy vấn không giới hạn trên dataset production.

---

## 13. Kiểm thử

Chạy nhóm test tập trung cho Phase 3:

```bash
pytest -q \
  tests/test_bigquery_sql.py \
  tests/test_bigquery_jobs.py \
  tests/test_bigquery_metrics.py \
  tests/test_settings.py \
  tests/test_market.py \
  tests/test_pagination.py
```

Kết quả kiểm chứng khi viết báo cáo:

```text
Phase 3 focused tests: 56 passed
Full suite:            201 passed, 3 skipped
```

Test đang bảo vệ:

- identifier validation và fully-qualified table names;
- không `SELECT *`, values dùng typed parameters;
- mọi filter mới và category `EXISTS`;
- bốn sort option, NULL semantics và keyset predicates;
- `LIMIT + 1` và cursor sort value;
- current batch metadata SQL;
- metrics query theo batch/window/dimension;
- mapping BigQuery Row sang `JobItem`/`MetricRow`;
- cột lạ không lọt vào API model;
- fail-fast config khi thiếu project/dataset;
- token HMAC và filter fingerprint;
- metrics window, unknown bucket và k-anonymity trên fake backend.

Các test này không tạo BigQuery client thật. ADC, IAM, network, cost reporting và timeout/cancel thật
phải được xác minh bằng integration test có guard ở Phase 4.

---

## 14. Definition of Done

### Source code và kiểm thử cục bộ

- [x] Có SQL builder thuần cho current batch, search và metrics.
- [x] Mọi identifier đến từ allowlist/validated target; mọi value dùng named parameters.
- [x] Search có projection tường minh, partition filter và category `EXISTS`.
- [x] Keyset pagination chạy trong SQL với bốn sort option và `job_id` tie-breaker.
- [x] Query dùng `LIMIT + 1`; BigQuery response không chạy `COUNT(*)`.
- [x] Page cursor gắn `batch_id`, `as_of`, `last_sort`, `last_job_id` và filter fingerprint.
- [x] Trang đầu đọc current batch; trang tiếp theo giữ batch snapshot.
- [x] Metrics cache key gắn environment + schema version + batch + window + dimension.
- [x] Có `maximum_bytes_billed`, timeout/cancel và structured query log.
- [x] `warehouse_backend=bigquery` được ráp qua composition root và fail-fast khi thiếu config.
- [x] ADR-020 ghi lại quyết định và trade-off.
- [x] Focused tests và full suite xanh.

### Trên BigQuery thật

- [ ] API boot thành công bằng reader SA của đúng môi trường.
- [ ] Trang đầu lấy đúng current batch và `data_as_of_at`.
- [ ] Tất cả filter/sort chạy đúng trên schema do Phase 2 tạo.
- [ ] Phân trang không trùng/sót và giữ nguyên batch khi current pointer đổi.
- [ ] Metrics đọc đúng gold rows và cache key không collision giữa hai batch cùng ngày.
- [ ] Log ghi được BigQuery job ID, bytes billed, cache hit và elapsed time.
- [ ] Timeout thật cancel query và trả HTTP 504.
- [ ] Reader staging không đọc được dataset prod; reader prod không đọc staging ngoài chủ đích.
- [ ] Cost cap từ chối query vượt ngưỡng trên dataset test.

---

## 15. Giới hạn và việc để Phase 4+

- Chưa có BigQuery I/O integration suite được bật bằng `RUN_BQ_INTEGRATION=1` và dataset `_test`.
- Chưa parameterize contract tests giữa fake, DuckDB và BigQuery SQL/integration.
- DuckDB repositories vẫn theo schema cũ và bị tắt; Phase 4 sẽ khôi phục parity.
- Page token chưa có `issued_at`/TTL; batch bị cleanup chưa trả 410.
- ADR-026 về pagination snapshot/retention chưa được tạo.
- Lỗi vượt `maximum_bytes_billed` chưa có mã ứng dụng riêng.
- `request_timeout_s` chưa được cưỡng chế trong process.
- API search không cache; đây là quyết định hiện tại để tránh cache invalidation/phân trang phức tạp.
- BigQuery phù hợp curriculum và analytics serving nhưng có thể không đạt low-latency/full-text ranking;
  Postgres/OpenSearch vẫn là phương án sau nếu SLO thực tế yêu cầu.

---

## 16. Bàn giao sang Phase 4

Phase 4 cần tập trung vào parity và failure modes thực tế:

1. Khôi phục DuckDB repositories theo schema silver/gold mới.
2. Chạy cùng contract test trên fake, DuckDB và BigQuery builder/integration.
3. Tạo dataset `_test` có fixture batch versioned để test I/O có điều kiện.
4. Kiểm tra injection qua category/filter chuỗi.
5. Kiểm tra pagination khi publish batch mới giữa hai page request.
6. Bổ sung cursor TTL, `410 CURSOR_EXPIRED` và retention rule tương ứng.
7. Xác minh cost cap, timeout/cancel, IAM staging/prod và query stats trên BigQuery thật.

Tài liệu liên quan:

- BigQuery serving decision: [`ADR-020`](../../docs/adr/ADR-020-bigquery-serving.md)
- BigQuery warehouse: [`ADR-018`](../../docs/adr/ADR-018-bigquery-warehouse.md)
- Mongo data contract: [`ADR-019`](../../docs/adr/ADR-019-mongo-source-data-contract.md)
- Atomic publication: [`ADR-025`](../../docs/adr/ADR-025-atomic-publication.md)
- Migration plan: [`migration-mongo-bigquery-plan.md`](../../docs/migration-mongo-bigquery-plan.md)
- Phase 2 report: [`phase-2/README.md`](../phase-2/README.md)
