# Jobs Serving API

API chỉ-đọc phục vụ dữ liệu tin tuyển dụng cho team AI.
Trạng thái: **Migration Mongo → BigQuery — Phase 4 (khôi phục DuckDB backend + parity fake/duckdb/BQ) hoàn thành trong source; 3 backend khả dụng: `fake` (test, mặc định), `bigquery` (prod), `duckdb` (dev). Integration BQ guard `RUN_BQ_INTEGRATION=1` (skip mặc định) — test suite xanh.**
> ⚠️ `HUONG-DAN-CHAY-VA-DOC-CODE.md` là tài liệu **LEGACY (W3–W6)**, KHÔNG áp dụng cho migration hiện tại (schema/`country`/không `posted_after` cũ). Contract & cách chạy hiện hành: **README này** + `docs/migration-mongo-bigquery-plan.md`.

Báo cáo triển khai: [Phase 1 — GCP foundation](migrate-report/phase-1/README.md) ·
[Phase 2 — ELT MongoDB → BigQuery](migrate-report/phase-2/README.md) ·
[Phase 3 — BigQuery read adapter](migrate-report/phase-3/README.md).

---

## 1. Chạy trong 3 phút

```bash
cd jobs-serving-api
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8080
```

Mở http://localhost:8080/docs — đây là Swagger UI sinh tự động từ model Pydantic.
Bấm **Try it out** trên `POST /v1/jobs/search` để gọi thử ngay trên trình duyệt.

Chạy test:

```bash
pytest -q    # test suite xanh (backend fake; test Redis skip nếu không có Redis)
make test          # tương đương
```

Xuất file hợp đồng OpenAPI:

```bash
make openapi       # ghi ra openapi.json
```

---

## 2. Thử bằng curl

```bash
# 1. Dịch vụ sống chưa?
curl -s localhost:8080/health | python -m json.tool

# 2. API tự mô tả — filter/dimension/window hợp lệ (mọi /v1/* cần X-API-Key)
curl -s -H 'X-API-Key: dev-local-key-team-ai' localhost:8080/v1/metadata | python -m json.tool

# 3. Tìm việc: senior, lương >= 30tr VND/tháng, đăng từ 2026-06-01 (posted_after BẮT BUỘC)
curl -s -X POST localhost:8080/v1/jobs/search \
  -H 'Content-Type: application/json' -H 'X-API-Key: dev-local-key-team-ai' \
  -d '{"filters":{"posted_after":"2026-06-01","seniority":"senior","salary_min":30000000},"limit":3}' \
  | python -m json.tool

# 4. Benchmark lương theo cấp bậc, cửa sổ 90 ngày
curl -s -H 'X-API-Key: dev-local-key-team-ai' \
  'localhost:8080/v1/market/metrics?dimension=seniority&window=90d' | python -m json.tool

# 5. Thiếu posted_after (hoặc filter lạ 'city') -> 400
curl -s -X POST localhost:8080/v1/jobs/search \
  -H 'Content-Type: application/json' -H 'X-API-Key: dev-local-key-team-ai' \
  -d '{"filters":{}}' | python -m json.tool
```

## 3. Cấu trúc thư mục

```
app/
├── main.py                khởi tạo app, middleware, xử lý lỗi, gắn router
├── settings.py            cấu hình từ biến môi trường (không hard-code)
├── errors.py              cây lỗi + mã lỗi ổn định
│
├── api/                   controller mỏng + composition root
│   ├── deps.py            ráp interface ↔ impl (nơi DUY NHẤT biết adapter cụ thể)
│   ├── health.py          GET  /health
│   ├── metadata.py        GET  /v1/metadata
│   ├── jobs.py            POST /v1/jobs/search
│   └── market.py          GET  /v1/market/metrics
│
├── domain/                LÕI: quy tắc + hợp đồng (không phụ thuộc bên ngoài)
│   ├── catalog.py         ★ nguồn sự thật cho filter/metric
│   ├── validator.py       ★ QueryValidator (allowlist, k-anonymity)
│   ├── pagination.py      ★ page_token ký HMAC
│   └── ports/             interface + DTO đi kèm
│       ├── job_repository.py      JobRepository + SearchResult
│       ├── metrics_repository.py  MetricsRepository + MetricRow + BatchRef
│       └── cache.py               CacheBackend
│
├── infrastructure/        RÌA: adapter nói chuyện với thế giới ngoài
│   ├── cache/
│   │   ├── memory.py       InMemoryCache (cachetools)
│   │   └── redis.py        RedisCache
│   └── warehouse/
│       ├── read_mapping.py        map Row→JobItem/MetricRow + cursor — DÙNG CHUNG BQ & DuckDB
│       ├── bigquery_read_sql.py   builder SQL BQ THUẦN (keyset, filter, metadata) — test được
│       ├── bigquery_exec.py       BigQueryExecutor (ADC, timeout→cancel→504, cost log)
│       ├── bigquery_jobs.py       BigQueryJobRepository (prod)
│       ├── bigquery_metrics.py    BigQueryMetricsRepository (prod)
│       ├── duckdb_read_sql.py     builder SQL DuckDB THUẦN (mirror ngữ nghĩa BQ, dialect $param)
│       ├── duckdb_exec.py         DuckDBReader (read_only, timeout→504, resolve batch)
│       ├── duckdb_jobs.py         DuckDBJobRepository (dev — keyset trong SQL)
│       ├── duckdb_metrics.py      DuckDBMetricsRepository (dev)
│       ├── fake_jobs.py           FakeJobRepository (test double)
│       ├── fake_metrics.py        FakeMetricsRepository
│       └── sql/search_jobs.sql
│
├── models/                DTO Pydantic (hợp đồng đối ngoại)
│   ├── enums.py
│   ├── common.py
│   ├── metadata.py
│   ├── jobs.py
│   └── market.py
│
├── elt/                   job offline: build_silver.py, build_gold.py
└── observability/
    └── logging.py         log JSON + request_id
tests/                     hợp đồng, phân trang, validator, market, metadata, settings, auth, rate limit, query timeout
```

---

## 4. Cấu hình

Mọi biến đều có tiền tố `JOBS_API_`. Xem `.env.example`.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `JOBS_API_ENV` | `local` | `local` / `staging` / `prod` |
| `JOBS_API_LOG_FORMAT` | `json` | `text` khi dev cho dễ đọc |
| `JOBS_API_WAREHOUSE_BACKEND` | `fake` | `fake` (test) · `bigquery` (prod) · `duckdb` (dev). App **từ chối boot** nếu giá trị lạ. |
| `JOBS_API_DUCKDB_PATH` | `analytics.duckdb` | File kho DuckDB (đọc read-only). Chỉ dùng khi `backend=duckdb`. |
| `JOBS_API_BQ_PROJECT` | *(rỗng)* | Project BigQuery API đọc. **Bắt buộc** khi `backend=bigquery` (thiếu → không boot). |
| `JOBS_API_BQ_DATASET` | *(rỗng)* | Dataset API đọc, vd `jobs_prod`. **Bắt buộc** khi `backend=bigquery`. |
| `JOBS_API_BQ_LOCATION` | `asia-southeast1` | Location BigQuery (khớp dataset). |
| `JOBS_API_BQ_MAXIMUM_BYTES_BILLED` | `2000000000` | Trần byte mỗi truy vấn (cost guard); vượt → BQ từ chối job. |
| `JOBS_API_CACHE_BACKEND` | `memory` | `redis` cần Memorystore/Redis đang chạy. |
| `JOBS_API_PAGE_TOKEN_SECRET` | *(dev)* | Khoá HMAC ký `page_token`. **Prod phải lấy từ Secret Manager.** App từ chối khởi động nếu `env != local` mà vẫn dùng giá trị mặc định. |

> Tầng API **chỉ đọc BigQuery**, không chạm Mongo — không có `JOBS_API_MONGO_URL`. Config ELT (Mongo → BigQuery) ở ENV riêng `JOBS_MONGO_*`/`JOBS_BQ_*` (xem ADR-020, `docs/migration-mongo-bigquery-plan.md`).

### Quy ước tỷ giá cho Mongo → BigQuery

Trong giai đoạn tích hợp dữ liệu thật, pipeline dùng **tỷ giá cố định 1 USD = 25.500 VND**
(`USD_TO_VND_RATE = 25_500`) để chuẩn hoá lương về VND/tháng. Đây là giả định ổn định
cho demo và kiểm thử tái lập, **không phải tỷ giá thời gian thực** và không dùng cho quyết
định tài chính.

- Lương nguồn bằng VND đang lưu theo đơn vị triệu VND/tháng: nhân `1_000_000`.
- Lương nguồn bằng USD/tháng: nhân `25_500`.
- Giữ cả giá trị/currency/period gốc và các cột đã chuẩn hoá; không ghi đè dữ liệu nguồn.
- Mỗi dòng đã chuẩn hoá phải lưu `fx_rate_to_vnd = 25_500`, nguồn tỷ giá
  `fixed_project_assumption` và phiên bản quy tắc. Khi đổi tỷ giá, tăng phiên bản và build
  lại silver/gold để kết quả vẫn truy vết được.
- Khoảng lương thiếu một cận giữ cận còn lại và để cận thiếu là `NULL`; lương thoả thuận
  giữ cả hai cận là `NULL`.

### Cửa sổ thời gian của market metrics

`GET /v1/market/metrics` mặc định tính trên các job có `effective_posted_date` thuộc
**90 ngày gần nhất** (`window=90d`) và cho phép yêu cầu toàn bộ corpus bằng
`window=all_time`. Giai đoạn này không hỗ trợ khoảng ngày tuỳ ý để gold table vẫn nhỏ,
nhanh và cho kết quả median chính xác mà không phải gộp median của nhiều ngày.

Mỗi nhóm metric phải phân biệt:

- `posting_count`: tổng số job trong nhóm và cửa sổ thời gian.
- `salary_disclosed_count`: số job công khai ít nhất một cận lương.
- `salary_sample_count`: số job có đủ min/max để tính lương đại diện và median.

Ngưỡng k-anonymity áp dụng theo `salary_sample_count`, không theo `posting_count`.
Response luôn trả `window` và `as_of` để consumer biết phạm vi và độ tươi dữ liệu.

### Xác thực khi triển khai Cloud Run

Cloud Run được mở public ở tầng nền tảng để mentor và AI client ngoài GCP gọi được,
nhưng mọi endpoint `/v1/*` vẫn bắt buộc header `X-API-Key`; chỉ `/health` được mở.
API key và khoá ký page token lấy từ Secret Manager, không đưa vào image hoặc source.
Cloud Run dùng service account `sa-api-reader` riêng để đọc BigQuery qua ADC và không
dùng file JSON credential.

Đây là lựa chọn cho giai đoạn demo/staging. Khi toàn bộ consumer chạy trong GCP,
production có thể nâng lên Cloud Run IAM/OIDC; `X-API-Key` vẫn có thể giữ để nhận diện
và rate-limit từng client nếu cần.

---

## 5. Quy tắc bất di bất dịch của dự án

1. **Không trả PII.** Không trường nào trong `JobItem` chứa liên hệ nhà tuyển dụng hay dữ liệu ứng viên. Có test canh việc này.
2. **Log tên filter, không log giá trị filter.**
3. **Filter bám dữ liệu thật (Mongo → BigQuery).** `posted_after` (BẮT BUỘC — chặn quét cả kho), `posted_before`, `source`, `seniority` (suy ra, có `unknown`), `category` (source-qualified), `salary_min` (VND/tháng), `experience_max`. Metrics theo `source | seniority | category`, window `90d | all_time`.
4. **Mọi lỗi ra ngoài đều dùng chung một envelope**, kể cả lỗi 500.
5. **`page_token` phải được ký và validate.** Nó là input của người gọi.

---

## 6. Lộ trình còn lại

| Tuần | Việc | Ảnh hưởng tới code này |
|---|---|---|
| 7 | Docker + Cloud Run + CI/CD | Thêm `Dockerfile`, workflow |
| 8 | Tracing + bàn giao | Thêm OpenTelemetry, client Python mẫu, test token |
