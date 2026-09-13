# Runbook hiện tại — chạy Jobs Serving API

> Cập nhật và kiểm chứng ngày 2026-09-14. Các README theo phase là báo cáo lịch sử tại thời điểm
> hoàn thành phase; số lượng test và trạng thái backend trong các báo cáo cũ không phải trạng thái
> runtime mới nhất.

## Chọn cách chạy

| Mục tiêu | Backend | Phụ thuộc ngoài | Cách nên dùng |
| --- | --- | --- | --- |
| Mở API và thử ngay | `fake` | Không | Mục 1 |
| Chạy bằng dữ liệu fixture trong file | `duckdb` | Không | Mục 3 |
| Chạy container local | `fake` | Docker Desktop | Mục 4 |
| Đọc dữ liệu staging thật | `bigquery` | GCP project, ADC, warehouse đã publish | Mục 5 |
| Chạy MongoDB → BigQuery ELT | Không phải API backend | MongoDB, BigQuery, ADC | Phase 2 |

## 1. Chạy ngay trên Windows PowerShell — khuyến nghị

Các lệnh phải chạy tại thư mục chứa `pyproject.toml` và `app/`:

```powershell
Set-Location 'E:\FIS Intern\DE\W2\jobs-serving-api-week2\jobs-serving-api'

# Chỉ cần hai lệnh này nếu .venv chưa tồn tại.
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# Ghi đè cấu hình của riêng phiên PowerShell này; không sửa .env.
$env:JOBS_API_ENV = 'local'
$env:JOBS_API_WAREHOUSE_BACKEND = 'fake'
$env:JOBS_API_CACHE_BACKEND = 'memory'
$env:JOBS_API_RATE_LIMITER_BACKEND = 'memory'

.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8080
```

Nếu `.venv` đã tồn tại thì bỏ qua hai lệnh tạo môi trường và cài dependency. Không cần activate
virtual environment vì mọi lệnh gọi thẳng `.venv\Scripts\python.exe`.

Mở <http://localhost:8080/docs>, hoặc dùng một PowerShell khác để smoke-test:

```powershell
Invoke-RestMethod -Uri 'http://localhost:8080/health'

$headers = @{ 'X-API-Key' = 'dev-local-key-team-ai' }
$body = @{
  filters = @{ posted_after = '2026-01-01' }
  sort = 'posted_desc'
  limit = 2
} | ConvertTo-Json -Depth 4

Invoke-RestMethod `
  -Method Post `
  -Uri 'http://localhost:8080/v1/jobs/search' `
  -Headers $headers `
  -ContentType 'application/json' `
  -Body $body
```

Kỳ vọng: `/health` trả `status = ok`; search trả 2 item và item đầu là
`topdev:1004` với fixture hiện tại.

### Lưu ý về `.env` đang có trong workspace này

Tại lần kiểm tra 2026-09-14, `.env` local đang chọn:

```dotenv
JOBS_API_WAREHOUSE_BACKEND=duckdb
JOBS_API_CACHE_BACKEND=redis
JOBS_API_DUCKDB_PATH=analytics.duckdb
```

`analytics.duckdb` là schema legacy, thiếu `warehouse_state`; vì vậy chạy `uvicorn` mà không ghi đè
biến môi trường sẽ boot nhưng search trả HTTP 500. Các biến ở lệnh PowerShell phía trên tránh lỗi này
mà không ghi đè file cấu hình cá nhân. Nếu muốn sửa lâu dài, đổi warehouse/cache/rate-limiter trong
`.env` thành `fake`/`memory`/`memory` như `.env.example`.

## 2. Chạy kiểm thử

PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
```

Nếu pytest báo `PermissionError` ở `%TEMP%\pytest-of-...`, đặt thư mục tạm trong repo:

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
.\.venv\Scripts\python.exe -m pytest -q --basetemp .tmp\pytest-current -p no:cacheprovider
```

Không dùng `pytest tests/test_elt_*.py` trong PowerShell vì wildcard này không được shell mở rộng.
Dùng:

```powershell
$eltTests = Get-ChildItem tests\test_elt_*.py | ForEach-Object FullName
.\.venv\Scripts\python.exe -m pytest -q $eltTests
```

Kết quả kiểm chứng gần nhất:

```text
251 passed, 12 skipped, 1 warning
ruff: All checks passed!
```

12 test skip là integration tùy môi trường (BigQuery/Redis); chúng không chứng minh GCP IAM,
network, quota hoặc dataset thật đã sẵn sàng.

## 3. Chạy local bằng DuckDB schema mới

Không dùng `analytics.duckdb` legacy. Tạo fixture mới bằng một tên file chưa tồn tại:

```powershell
.\.venv\Scripts\python.exe -c "from tests.fixtures.duckdb_fixture import load_duckdb; load_duckdb('phase4-dev.duckdb')"
$env:JOBS_API_ENV = 'local'
$env:JOBS_API_WAREHOUSE_BACKEND = 'duckdb'
$env:JOBS_API_DUCKDB_PATH = 'phase4-dev.duckdb'
$env:JOBS_API_CACHE_BACKEND = 'memory'
$env:JOBS_API_RATE_LIMITER_BACKEND = 'memory'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8080
```

Loader tạo schema từ đầu nên lần chạy sau phải dùng tên file mới hoặc chủ động xóa file fixture cũ.
Đây chỉ là dữ liệu demo 9 job, không phải bản sao BigQuery production.

## 4. Chạy bằng Docker Compose

Tại repo root, sau khi Docker Desktop đã chạy:

```powershell
docker compose config -q
docker compose up --build
```

Compose mặc định chạy `redis` và API backend `fake`; API ở <http://localhost:8082/docs>. Dừng bằng:

```powershell
docker compose down
```

Pipeline `legacy-duckdb` chỉ dựng lại kho cũ và không phải đường chạy API hiện tại.

## 5. Chạy staging thật với BigQuery

Chỉ làm sau khi:

- Phase 1 đã tạo project, dataset và IAM;
- Phase 2 đã publish một batch vào `jobs_staging`;
- `warehouse_state` có pointer `serving`;
- máy đã có ADC (`gcloud auth application-default login`);
- có page-token secret và JSON hash API key cho staging.

Dùng hướng dẫn Bash/Cloud Shell ở [Phase 3](phase-3/README.md#11-cách-chạy). Biến của API có tiền
tố `JOBS_API_`; biến của ELT ở Phase 2 dùng `JOBS_MONGO_` và `JOBS_BQ_`. Không trộn hai nhóm.

## 6. Phạm vi đã và chưa xác minh

| Report | Kết luận hiện tại |
| --- | --- |
| Phase 0 | Đúng như snapshot lịch sử, nhưng câu “chỉ fake khả dụng” không mô tả code sau Phase 4 |
| Phase 1 | Tên file/luồng chạy khớp repo; script Bash qua `bash -n`; chưa chạy trên GCP thật |
| Phase 2 | CLI và biến môi trường khớp code; unit test xanh; Mongo/BQ thật chưa được xác minh ở máy này |
| Phase 3 | Fake runtime đã smoke thành công; BigQuery cần ADC và warehouse thật |
| Phase 4 | DuckDB parity test xanh; đã sửa typo tên biến integration thành `JOBS_BQ_DATASET_STAGING` |
| Phase 5 | Thuộc repo scraper/orchestration, không có report riêng trong repo serving này |
| Phase 6 | File và workflow tồn tại, toàn bộ shell script qua syntax check; deploy GCP chưa thực hiện |
| Phase 7 | Là quy trình hai repo; không thể chạy chỉ từ repo serving-api, cần thêm `job-scraper-1` và VM |

