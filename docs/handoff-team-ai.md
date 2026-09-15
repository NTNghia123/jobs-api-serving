# Bàn giao Jobs Serving API cho team AI

> API chỉ-đọc phục vụ dữ liệu tin tuyển dụng (TopDev + VietnamWorks) đã chuẩn hoá. Tài liệu này +
> `examples/client_demo.py` + một test token là đủ để gọi API mà không cần hỏi team Data.

## 1. Base URL & tài liệu

| Môi trường | Base URL |
| --- | --- |
| Local | `http://localhost:8080` |
| Cloud Run | `https://<service>-<hash>.<region>.run.app` (team Data cấp) |

- OpenAPI/Swagger tương tác: `GET {base}/docs` — nguồn contract chính, luôn khớp code.
- Máy đọc: `GET {base}/openapi.json`.

## 2. Xác thực

- Mọi endpoint `/v1/*` yêu cầu header **`X-API-Key: <key-thô>`**. `/health` mở (không cần key).
- Sai/thiếu/hết hạn key → **401**. Vượt hạn mức → **429**.
- Key do team Data cấp riêng cho từng client (xem §7). **Không commit key vào git**; đặt qua biến môi
  trường (client mẫu đọc `JOBS_API_API_KEY`).

## 3. Endpoint

| Method | Path | Mục đích |
| --- | --- | --- |
| `GET` | `/health` | Liveness (không auth, không query kho) |
| `GET` | `/v1/metadata` | Tự mô tả: filter/dimension/window/sort/limit hợp lệ |
| `POST` | `/v1/jobs/search` | Tìm tin theo filter (allowlist) |
| `GET` | `/v1/market/metrics` | Benchmark lương theo `source \| seniority \| category` |

### 3.1 `POST /v1/jobs/search`

Request:

```json
{ "filters": { "posted_after": "2026-01-01", "source": "topdev", "seniority": "senior" },
  "sort": "posted_desc", "limit": 20, "page_token": null }
```

- `filters.posted_after` **BẮT BUỘC** (ISO date) — chặn quét toàn lịch sử / prune partition BigQuery.
- Filter khác (tuỳ chọn): `posted_before`, `source`, `seniority`, `category`, `salary_min`,
  `experience_max`. **Field lạ → 400** (request `extra=forbid`).
- `sort`: `posted_desc` (mặc định) · `salary_max_desc` · `salary_min_asc` · `experience_asc`.

Response (envelope):

```json
{ "items": [ /* JobItem */ ], "next_page_token": null,
  "total_estimated": null, "as_of": "2026-09-01T00:00:00Z", "request_id": "req_…" }
```

`next_page_token` là `null` khi hết trang, hoặc một chuỗi token mờ khi còn trang sau.

`JobItem` gồm: `job_id, source, external_id, title, company_name, location_text, seniority,
experience_min_years, experience_max_years, salary_min_vnd_month, salary_max_vnd_month, salary_currency,
salary_period, categories[], posted_at, effective_posted_date, deadline_date, url`.
**Không** có PII (liên hệ nhà tuyển dụng, dữ liệu ứng viên).

### 3.2 `GET /v1/market/metrics`

Query: `dimension=source|seniority|category` · `window=90d|all_time`.
Mỗi row: `posting_count ≥ salary_disclosed_count ≥ salary_sample_count`, `median_salary_vnd_month`
(**null** khi `salary_sample_count < 5` — k-anonymity).

## 4. Phân trang (keyset)

- Trang sau: **gửi LẠI y nguyên** `filters` cũ + `page_token` từ response trước (token đã ký, không tự
  tạo/sửa). Đổi filter giữa chừng → API từ chối (fingerprint không khớp).
- Token sai/bị sửa/hết hạn/không đọc được → **400 `INVALID_PAGE_TOKEN`** (field `page_token`).
- Hết trang khi `next_page_token = null`.

## 5. Độ tươi dữ liệu (`as_of`)

Mọi **successful response của endpoint dữ liệu `/v1`** (`/v1/jobs/search`, `/v1/market/metrics`) có
`as_of` = thời điểm chốt dữ liệu của batch đang phục vụ (KHÔNG phải lúc gọi). (`/health` và response lỗi
không có `as_of`.) LLM/consumer phải kiểm `as_of` trước khi trình bày cho người dùng cuối — dữ liệu có
thể trễ vài ngày.

## 6. Lỗi

Một envelope thống nhất cho mọi lỗi:

```json
{ "error": { "code": "INVALID_FILTER", "message": "…", "field": "filters.posted_before",
             "request_id": "req_…" } }
```

- `code` ổn định (dành cho máy): `INVALID_REQUEST` · `INVALID_FILTER` · `MISSING_REQUIRED_FILTER` ·
  `LIMIT_EXCEEDED` · `INVALID_PAGE_TOKEN` (mọi trường hợp trên đều **400**) · `UNAUTHORIZED` (401) ·
  `RATE_LIMIT_EXCEEDED` (429) · `NOT_FOUND` (404) · `QUERY_TIMEOUT` (504) · `UPSTREAM_UNAVAILABLE` (503) ·
  `INTERNAL` (500).
- Khi báo lỗi cho team Data, **gửi kèm `request_id`** (có cả trong header `X-Request-ID`).

## 7. Test token (team Data cấp — luồng phát & nạp)

> Server chỉ lưu **hash SHA-256** của key; key thô hiện đúng **một lần** lúc phát. Mất thì phát lại.

```bash
# 1) Phát key cho một client (chạy trong terminal TIN CẬY, KHÔNG trong CI, không tee/redirect ra log)
python -m scripts.issue_key team-ai --days 90
#    → in: client_id, API key (thô, ĐƯA CHO CONSUMER), và JSON {"team-ai": {"key_sha256": …}}
```

- **Local**: gộp JSON đó vào biến `JOBS_API_API_KEYS` (là **map nhiều client** — phải **MERGE**, không
  thay toàn bộ, nếu không sẽ xoá key các client khác).
- **Cloud Run**: cập nhật secret `jobs-api-keys-{env}` theo quy trình **đọc map hiện hữu → merge entry mới
  → nạp version secret MỚI**, rồi **deploy lại** (revision pin version secret lúc deploy). Không `echo`
  map/key ra log; chỉ log `client_id` + version mới.

Đưa **key thô** cho consumer qua kênh an toàn.

## 8. Bắt đầu nhanh (client mẫu)

```bash
JOBS_API_BASE_URL=https://<service>.run.app \
JOBS_API_API_KEY=<key-thô> \
python examples/client_demo.py --limit 5
```

Client minh hoạ: `GET /v1/metadata`, `POST /v1/jobs/search` (phân trang), `GET /v1/market/metrics`, đọc
`as_of`, và xử lý lỗi theo envelope (`error.code`/`request_id`). Xem `examples/client_demo.py`.
