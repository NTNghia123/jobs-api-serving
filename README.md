# Jobs Serving API

API chỉ-đọc phục vụ dữ liệu tin tuyển dụng cho team AI.
Trạng thái: **Tuần 2 / 8 — hợp đồng API đã xong, dữ liệu còn là giả lập.**

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
pytest -q          # 23 test
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
├── api/
│   ├── deps.py            dependency injection (đổi repository không sửa handler)
│   ├── health.py          GET  /health
│   ├── metadata.py        GET  /v1/metadata
│   └── jobs.py            POST /v1/jobs/search
├── models/                hợp đồng đối ngoại (Pydantic) — thứ consumer nhìn thấy
│   ├── enums.py           giá trị hợp lệ cho filter
│   ├── common.py          envelope lỗi, health
│   ├── metadata.py
│   └── jobs.py
├── domain/
│   ├── catalog.py         ★ nguồn sự thật cho filter/metric (tài liệu + validate)
│   └── pagination.py      ★ page_token ký HMAC
├── warehouse/
│   ├── base.py            interface JobRepository
│   └── fake.py            hiện thực giả lập (Tuần 3 thêm DuckDB/BigQuery)
└── observability/
    └── logging.py         log JSON + request_id
tests/                     23 test: hợp đồng, phân trang, lỗi, OpenAPI
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
| 3 | Nối kho dữ liệu thật | Thêm `warehouse/duckdb.py`, `warehouse/bigquery.py`; sửa `deps.py`. Handler không đổi. |
| 4 | QueryValidator + kiểm soát chi phí | Thêm `domain/validator.py`, gọi trước `repo.search()` |
| 5 | Gold table + cache | Thêm `api/market.py`, lớp cache |
| 6 | Auth + rate limit | Thêm dependency `require_client`; thêm `client_id` vào log |
| 7 | Docker + Cloud Run + CI/CD | Thêm `Dockerfile`, workflow |
| 8 | Tracing + bàn giao | Thêm OpenTelemetry, client Python mẫu, test token |
