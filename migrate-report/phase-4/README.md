# Migration Report — Phase 4: DuckDB parity và kiểm chứng BigQuery

## 1. Tổng quan

Phase 4 đưa DuckDB trở lại làm read backend local theo đúng schema warehouse mới, đồng thời tạo lớp
kiểm chứng để ba backend `fake`, `duckdb` và `bigquery` giữ cùng hành vi nghiệp vụ.

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
        ├────────► fake      — fixture nhanh cho unit/contract test
        ├────────► duckdb    — SQL thật, chạy local, không cần GCP
        └────────► bigquery  — warehouse production
                       │
                       ▼
              cùng domain DTO và page-token contract
```

Mục tiêu quan trọng nhất không chỉ là “chạy được DuckDB”, mà là dùng DuckDB như một executable
specification cho các quy tắc khó của BigQuery: filter, sort, `NULL`, keyset pagination,
`LIMIT + 1`, category dạng repeated/list và snapshot theo `batch_id`.

### Trạng thái đã xác minh

| Phạm vi | Trạng thái | Bằng chứng/ý nghĩa |
| --- | --- | --- |
| DuckDB backend theo schema mới | **Hoàn thành trong source** | Job và metrics repositories chạy SQL thật trên file DuckDB |
| Chọn backend bằng config | **Hoàn thành trong source** | `fake`, `duckdb`, `bigquery` đều được composition root hỗ trợ |
| Parity fake ↔ DuckDB | **Xanh** | Sort, filter, paging, category, metrics và snapshot được kiểm tra bằng fixture chung |
| BigQuery SQL/unit tests | **Xanh** | Builder, typed params, repository mapping và safety guard được kiểm tra không cần GCP |
| BigQuery integration thật | **Có test, chưa chạy trong lần xác minh này** | 9 test skip mặc định vì chưa bật `RUN_BQ_INTEGRATION=1` |
| Focused suite Phase 4 | **107 pass, 9 skip** | Chạy ngày 2026-09-13 |
| Toàn bộ test suite | **239 pass, 12 skip** | Chạy ngày 2026-09-13; integration tùy chọn tiếp tục skip |
| Cursor snapshot | **Đã áp dụng** | Token khóa `batch_id`, `as_of`, sort key và fingerprint của filter |
| Cursor TTL/HTTP 410 | **Chưa kích hoạt** | ADR-026 đã thiết kế, retention/cleanup awareness chưa được hiện thực |

> Phase 4 hoàn thành ở mức source và local executable parity. Integration trên một BigQuery project
> thật vẫn là bước xác minh môi trường có điều kiện, không được tính là đã chạy chỉ vì test tồn tại.

---

## 2. Vì sao Phase 4 cần DuckDB?

Unit test cho SQL builder có thể phát hiện tên bảng sai, thiếu parameter hoặc câu SQL không an toàn,
nhưng không chứng minh câu SQL thực sự trả đúng thứ tự và đúng trang dữ liệu. Ngược lại, chạy mọi test
trên BigQuery thật sẽ chậm, cần credential và có thể phát sinh chi phí.

DuckDB lấp khoảng trống đó:

- chạy SQL thật trong CI/local mà không cần cloud;
- tái hiện schema silver/gold và batch publication của warehouse;
- kiểm tra nhiều trang liên tiếp để phát hiện trùng hoặc sót job;
- so sánh kết quả với fake repository có fixture xác định;
- giữ BigQuery integration test cho khác biệt chỉ môi trường thật mới phát hiện được.

DuckDB là backend đọc dành cho development/test. Nó không thay thế BigQuery production và không phải
bản sao tự động của warehouse.

---

## 3. Các thành phần chính

### 3.1. SQL builder riêng cho DuckDB

`app/infrastructure/warehouse/duckdb_read_sql.py` dựng ba nhóm query:

| Nhóm query | Mục đích |
| --- | --- |
| Current batch metadata | Lấy batch đang publish và `data_as_of_at` theo state/catalog |
| Job search | Filter, sort, keyset, category và `LIMIT + 1` trên silver jobs |
| Market metrics | Đọc gold metrics theo batch, window và dimension |

BigQuery và DuckDB có cùng semantics nhưng không dùng chung chuỗi SQL vì dialect khác nhau:

| Vấn đề | BigQuery | DuckDB |
| --- | --- | --- |
| Named parameter | `@name` | `$name` |
| Category repeated/list | `EXISTS ... UNNEST(...)` | `list_contains(list_transform(...))` |
| Từ khóa `window` | dùng theo dialect BigQuery | được quote thành `"window"` |
| Query safety | typed query parameters | parameter dictionary, không nối input vào SQL |

Tên bảng/cột là hằng số nội bộ; giá trị từ request luôn đi qua bound parameters. Builder projection
rõ các cột cần dùng, không dùng `SELECT *` và không đưa PII vào read model.

### 3.2. DuckDB reader

`app/infrastructure/warehouse/duckdb_exec.py` chịu trách nhiệm thực thi:

- mở database với `read_only=True`;
- tạo cursor riêng cho mỗi lần đọc;
- áp dụng query timeout và interrupt khi hết thời gian;
- chuẩn hóa timestamp không timezone từ DuckDB thành UTC;
- trả lỗi upstream `503` nếu chưa có batch nào được publish.

Thiết kế read-only giúp API không vô tình sửa warehouse local. Việc tạo/populate file DuckDB thuộc
tooling hoặc pipeline riêng, không thuộc request serving path.

### 3.3. Job và metrics repositories

`duckdb_jobs.py` và `duckdb_metrics.py` hiện thực đúng domain ports mà handler đang dùng. API route,
validation và response contract vì vậy không cần biết backend đang là DuckDB hay BigQuery.

Job repository:

- resolve batch hiện hành cho trang đầu;
- dùng `batch_id` trong token cho các trang tiếp theo;
- thực thi filter/sort/keyset ở SQL;
- lấy `limit + 1` dòng để xác định có trang sau;
- trả `total_estimated=null`, tránh count query đắt tiền.

Metrics repository đọc `gold_market_metrics` cùng batch đã publish và map các dimension như
`category`, `location`, `seniority`, bao gồm bucket `unknown`.

### 3.4. Shared row mapping

`app/infrastructure/warehouse/read_mapping.py` gom logic dùng chung:

- map row warehouse thành `JobItem`;
- map row gold thành metric DTO;
- tạo cursor kế tiếp từ dòng cuối cùng.

Việc dùng chung mapping giảm nguy cơ BigQuery và DuckDB trả JSON khác nhau dù SQL tương đương, đồng
thời DuckDB không phải import BigQuery client chỉ để tái sử dụng mapper.

### 3.5. Dependency injection và settings

`app/settings.py` chấp nhận `fake | duckdb | bigquery`. `app/api/deps.py` lazy-load adapter tương ứng:

- `fake`: không cần warehouse bên ngoài;
- `duckdb`: cần `JOBS_API_DUCKDB_PATH` trỏ tới file đúng schema;
- `bigquery`: cần project/dataset và Application Default Credentials phù hợp.

---

## 4. Contract parity được bảo vệ như thế nào?

### 4.1. Filter và dữ liệu NULL

Test bao phủ category, location, seniority, salary tối thiểu, ngày đăng, keyword và các điều kiện
đang có trong API contract. Salary/date/experience `NULL` không được coi là thỏa điều kiện range;
seniority trống được quy về `unknown` theo contract hiện hành.

### 4.2. Bốn kiểu sort và keyset

Các sort option được chạy qua SQL DuckDB và đối chiếu với fake repository. Test lấy liên tục đến hết
dữ liệu rồi xác minh:

- không trùng hoặc bỏ sót job giữa các trang;
- thứ tự ổn định khi nhiều dòng có cùng sort value;
- `job_id` là tie-breaker và có thể là chuỗi;
- `NULL` giữ đúng quy tắc sắp xếp/keyset đã thống nhất.

### 4.3. Category không nhân bản dòng

Một job có nhiều category chỉ xuất hiện một lần khi match. BigQuery dùng `EXISTS` trên `UNNEST`;
DuckDB kiểm tra list. Test xác minh filter này không tạo duplicate.

### 4.4. `LIMIT + 1`

```text
0..limit dòng     → không có next_page_token
limit + 1 dòng    → trả limit dòng và tạo next_page_token
```

Cách này tránh chạy `COUNT(*)` chỉ để biết còn trang kế tiếp. Vì vậy `total_estimated` có chủ đích là
`null` trên warehouse repositories.

### 4.5. Metrics

Parity test kiểm tra tổng số job, dimension/bucket `unknown`, invariant của metric, window `90d` và
`data_as_of` nhất quán với batch đã publish.

---

## 5. Snapshot phân trang và ADR-026

Page token được ký HMAC và chứa thông tin giữ snapshot:

```json
{
  "batch_id": "...",
  "as_of": "...",
  "last_sort": "...",
  "last_job_id": "..."
}
```

Token còn ràng buộc với fingerprint của filter/sort. Nếu client đổi filter nhưng tái sử dụng token
cũ, API trả `400` thay vì trả một trang không cùng tập kết quả.

```text
Trang 1: resolve current batch A → token chứa batch A
Batch B được publish
Trang 2 với token cũ        → tiếp tục query batch A
```

`docs/adr/ADR-026-pagination-snapshot.md` thiết kế trường hợp batch cũ đã bị cleanup: token phải trả
`410 CURSOR_EXPIRED`. Tuy nhiên repository chưa có retention/TTL check và domain error 410, vì vậy
nhánh này **chưa được kích hoạt**. Hiện tại cần giữ batch cũ đủ lâu hơn vòng đời token thực tế.

Cache metrics cũng phải gồm `batch_id` trong key. Khi publish batch mới, request mới dùng key mới mà
không cần xóa đồng loạt cache của batch trước.

---

## 6. DuckDB fixture local

`tests/fixtures/duckdb_fixture.py` tạo warehouse nhỏ gồm 9 job, silver/gold tables, batch catalog và
publication state. Dữ liệu đồng bộ với fake fixture để parity test có expected result xác định.

Tạo một file development mới:

```bash
python -c "from tests.fixtures.duckdb_fixture import load_duckdb; load_duckdb('phase4-dev.duckdb')"
```

Cấu hình `.env`:

```dotenv
JOBS_API_WAREHOUSE_BACKEND=duckdb
JOBS_API_DUCKDB_PATH=phase4-dev.duckdb
```

Chạy API:

```bash
uvicorn app.main:app --reload
```

Lưu ý:

- dùng tên file mới hoặc xóa fixture cũ trước khi tạo lại vì loader tạo schema từ đầu;
- fixture chỉ dành cho test/demo local;
- `analytics.duckdb` cũ có thể dùng schema legacy và không tương thích adapter Phase 4;
- Phase 4 chưa có pipeline production để đồng bộ BigQuery sang DuckDB.

---

## 7. BigQuery integration test có điều kiện

`tests/test_bq_integration.py` skip mặc định để suite thường không cần credential và không phát sinh
cloud cost. Khi bật, test sẽ:

1. dùng dataset có hậu tố `_test`;
2. tạo schema cần thiết;
3. publish fixture 9 job bằng writer Phase 2;
4. đọc lại qua adapter BigQuery;
5. kiểm tra bốn sort, pagination, category, metrics và `as_of`;
6. kiểm tra cost guard bằng maximum-bytes budget rất nhỏ;
7. xóa các bảng test khi teardown, nhưng giữ dataset.

Biến môi trường cần thiết:

```dotenv
RUN_BQ_INTEGRATION=1
JOBS_BQ_PROJECT=<gcp-project-id>
JOBS_BQ_STAGING_DATASET=<dataset-goc>   # test dùng <dataset-goc>_test
JOBS_BQ_LOCATION=asia-southeast1
```

Runtime cần Application Default Credentials có quyền tạo/xóa bảng, ghi dữ liệu và query trong
project test. Chạy riêng bằng:

```bash
pytest -q tests/test_bq_integration.py
```

> Test ghi dữ liệu thật và có thể phát sinh chi phí nhỏ. Chỉ chạy với project/dataset test, không
> trỏ vào production.

Trong lần xác minh này, `RUN_BQ_INTEGRATION` không được bật nên 9 trường hợp BigQuery integration đã
skip. Báo cáo chưa xác nhận IAM, location, quota hoặc schema trên một GCP project cụ thể.

---

## 8. Cách kiểm thử Phase 4

### Focused suite

```bash
pytest -q \
  tests/test_duckdb_sql.py \
  tests/test_duckdb_repo.py \
  tests/test_bigquery_sql.py \
  tests/test_bigquery_jobs.py \
  tests/test_bigquery_metrics.py \
  tests/test_bq_integration.py \
  tests/test_settings.py \
  tests/test_contract.py \
  tests/test_pagination.py \
  tests/test_market.py
```

Kết quả đã chạy: **107 passed, 9 skipped**.

### Toàn bộ suite

```bash
pytest -q
```

Kết quả đã chạy: **239 passed, 12 skipped**. Có một warning deprecation từ Starlette/TestClient,
không phải test failure. Trong sandbox/CI hạn chế thư mục temp, hãy chỉ định `--basetemp` nằm trong
workspace có quyền ghi.

---

## 9. Definition of Done

| Tiêu chí | Kết quả |
| --- | --- |
| DuckDB dùng schema silver/gold mới | Đạt |
| DuckDB jobs và metrics chạy SQL thật | Đạt |
| Backend switch hỗ trợ đủ 3 backend | Đạt |
| Shared mapping tránh drift DTO | Đạt |
| Filter/sort/keyset/NULL parity local | Đạt |
| Category filter không duplicate | Đạt |
| `LIMIT + 1`, `total_estimated=null` | Đạt |
| Batch-aware cursor và stable `as_of` | Đạt |
| Integration harness BigQuery có guard | Đạt |
| BigQuery integration chạy trên GCP trong lần này | Chưa xác minh |
| Batch-flip integration test trên BigQuery thật | Chưa có |
| Cursor TTL và HTTP 410 | Chưa triển khai |
| Pipeline đồng bộ production vào DuckDB | Ngoài phạm vi |

---

## 10. Giới hạn và rủi ro còn lại

1. **Khác biệt dialect vẫn tồn tại.** DuckDB parity giảm rủi ro nhưng không thay thế hoàn toàn query
   trên BigQuery thật.
2. **Integration test chưa mô phỏng publish giữa hai request.** Batch pinning có unit/local coverage,
   nhưng chưa có bài test BigQuery thật thực hiện batch flip giữa trang 1 và trang 2.
3. **Token chưa có TTL/410.** Nếu batch bị cleanup quá sớm, hành vi chưa đạt thiết kế cuối ADR-026.
4. **DuckDB không tự được xây ở production.** Fixture là test/dev tooling, không phải ELT/replication.
5. **Contract test nằm ở nhiều tầng.** HTTP contract dùng fake; parity warehouse được kiểm tra ở
   repository/SQL level. Chưa phải mọi HTTP case đều parameterize qua cả ba backend.
6. **Cloud integration phụ thuộc môi trường.** ADC, IAM, dataset location, quota và billing chỉ được
   xác nhận khi thực sự bật test BigQuery.

---

## 11. Handoff sang Phase 5

Phase 5 nên tập trung vào orchestration:

- đưa extract/transform/load/reconciliation/publish vào Dagster assets/jobs;
- thêm retry, sensor/schedule và partition phù hợp;
- chỉ publish khi quality gates đạt;
- giữ `batch_id` xuyên suốt logs, metadata và observability;
- bổ sung retention policy trước khi kích hoạt cursor expiration/HTTP 410;
- chạy BigQuery integration trên project CI/staging có budget và IAM giới hạn.

Phase 4 đã tạo safety net để thay đổi pipeline ở Phase 5 mà vẫn phát hiện sớm drift giữa dữ liệu
warehouse và API contract.
