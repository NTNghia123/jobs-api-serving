# Jobs Serving API

API chỉ-đọc phục vụ dữ liệu tin tuyển dụng cho team AI.
Trạng thái: **Tuần 6 / 8 — Gold table, cache, API key và rate limit đã hoàn thành (50 test xanh).**
> Bản hoàn thiện W3–W6. Hướng dẫn chạy & đọc code: **HUONG-DAN-CHAY-VA-DOC-CODE.md**

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
pytest tests -q    # 50 test
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

# 2. API tự mô tả — filter nào hợp lệ? (seniority, experience_max, salary_min, country)
curl -s localhost:8080/v1/metadata | python -m json.tool

# 3. Tìm việc: cấp senior, lương tối thiểu 60000, ở Denmark
curl -s -X POST localhost:8080/v1/jobs/search \
  -H 'Content-Type: application/json' \
  -d '{"filters":{"seniority":"senior","salary_min":60000},"limit":3}' \
  | python -m json.tool

# 4. Gửi filter đã bị loại (city) -> 400 INVALID_FILTER
curl -s -X POST localhost:8080/v1/jobs/search \
  -H 'Content-Type: application/json' \
  -d '{"filters":{"city":"HN"}}' | python -m json.tool
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
│       ├── metrics_repository.py  MetricsRepository + MetricRow
│       └── cache.py               CacheBackend
│
├── infrastructure/        RÌA: adapter nói chuyện với thế giới ngoài
│   ├── cache/
│   │   ├── memory.py       InMemoryCache (cachetools)
│   │   └── redis.py        RedisCache
│   └── warehouse/
│       ├── duckdb_jobs.py      DuckDBJobRepository
│       ├── duckdb_metrics.py   DuckDBMetricsRepository
│       ├── fake_jobs.py        FakeJobRepository (test double)
│       ├── fake_metrics.py     FakeMetricsRepository
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
tests/                     50 test: hợp đồng, phân trang, validator, injection, market, auth, rate limit
```

---

## 4. Cấu hình

Mọi biến đều có tiền tố `JOBS_API_`. Xem `.env.example`.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `JOBS_API_ENV` | `local` | `local` / `staging` / `prod` |
| `JOBS_API_LOG_FORMAT` | `json` | `text` khi dev cho dễ đọc |
| `JOBS_API_WAREHOUSE_BACKEND` | `fake` | Tuần 3: `duckdb` / `bigquery` |
| `JOBS_API_PAGE_TOKEN_SECRET` | *(dev)* | Khoá HMAC ký `page_token`. **Prod phải lấy từ Secret Manager.** App từ chối khởi động nếu `env != local` mà vẫn dùng giá trị mặc định. |

---

## 5. Quy tắc bất di bất dịch của dự án

1. **Không trả PII.** Không trường nào trong `JobItem` chứa liên hệ nhà tuyển dụng hay dữ liệu ứng viên. Có test canh việc này.
2. **Log tên filter, không log giá trị filter.**
3. **Filter bám schema thật.** Chỉ seniority (suy ra) / experience_max / salary_min / country (JOIN company). Không có city/job_function/ngày.
4. **Mọi lỗi ra ngoài đều dùng chung một envelope**, kể cả lỗi 500.
5. **`page_token` phải được ký và validate.** Nó là input của người gọi.

---

## 6. Lộ trình còn lại

| Tuần | Việc | Ảnh hưởng tới code này |
|---|---|---|
| 7 | Docker + Cloud Run + CI/CD | Thêm `Dockerfile`, workflow |
| 8 | Tracing + bàn giao | Thêm OpenTelemetry, client Python mẫu, test token |
