# Migration Report — Phase 0

## 1. Trạng thái

**Phase 0 — Contract & normalized source: hoàn thành, sẵn sàng commit.**

Phase này chuyển Jobs Serving API từ contract demo MySQL/DuckDB cũ sang contract mục tiêu cho kiến trúc:

```text
MongoDB (TopDev + VietnamWorks)
        ↓ Phase 2 — ELT
BigQuery silver/gold
        ↓ Phase 3 — warehouse adapters
Jobs Serving API
        ↓ Phase 6
Cloud Run
```

Phase 0 chưa đọc MongoDB hoặc BigQuery thật và chưa tạo tài nguyên GCP. Mục tiêu của phase là khóa data contract, cập nhật API chạy hoàn chỉnh trên backend `fake`, ghi lại mapping từ dữ liệu nguồn và tạo bộ test bảo vệ contract trước khi xây ELT/BigQuery adapter.

Kết quả kiểm chứng gần nhất:

```text
pytest -q -rs
58 passed, 3 skipped

ruff check app tests
All checks passed!

docker compose config -q
PASS

docker compose config --services
redis
api
```

Ba test bị skip là Redis integration tests khi không có Redis local. Đây là hành vi có chủ đích; test contract mặc định dùng memory backend và không phụ thuộc dịch vụ bên ngoài.

---

## 2. Kiến trúc và phạm vi đã khóa

### 2.1. Nguồn dữ liệu

- Nguồn hiện tại: `topdev` và `vietnamworks`.
- API phục vụ toàn bộ corpus job đã crawl và lưu, không tuyên bố job đang `active`.
- Không suy ra `is_active` từ `lastSeenAt` vì dữ liệu hiện tại gần như có `lastSeenAt == firstSeenAt`.
- MongoDB source-of-record gồm `jobs`, `job_details` và taxonomy `job_categories`.
- Phase 2 sẽ extract `jobs LEFT JOIN job_details` theo `(platformId, externalId)` để không làm mất job thiếu detail.

Nguồn quyết định: [ADR-019](../docs/adr/ADR-019-mongo-source-data-contract.md).

### 2.2. Job identity

`job_id` đổi từ numeric ID sang composite string:

```text
<source>:<external_id>
```

Ví dụ:

```text
topdev:1001
vietnamworks:2003
```

`external_id` vẫn được giữ thành field riêng. Composite ID ngăn collision giữa ID của các nền tảng.

### 2.3. Company name

Quyết định cuối cùng là **không thay đổi schema scraper** và không thêm `JobCompanyInfo.name`.

Phase 2 ELT lấy `company_name` từ raw payload:

- TopDev: `company_detail.display_name`, fallback `company.display_name`.
- VietnamWorks: `companyName`.
- Không tìm được: `null`, không tự tạo giá trị `"Unknown"`.

Raw payload chỉ được đọc trong ELT; raw không được đưa vào silver serving hoặc API response.

### 2.4. Categories

- Một job có thể có nhiều category nhưng silver phải giữ invariant `1 job = 1 dòng`.
- BigQuery dùng `ARRAY<STRUCT<...>>`, mode `REPEATED`.
- `category_key` source-qualified theo group path, ví dụ `topdev:g14~j22`.
- Job không có category lưu `categories=[]`.
- Khi build market metrics, job thiếu category đi vào `<source>:unknown`.
- Search không hỗ trợ `category=<source>:unknown` trong v1 hiện tại.
- Khi Phase 2 UNNEST category để build gold, phải deduplicate theo job và dùng `COUNT(DISTINCT job_id)`.

### 2.5. Salary normalization

Phạm vi đã khóa:

- Currency hợp lệ: `VND`, `USD`.
- Period hợp lệ: `month`.
- Currency hoặc period khác: `salary_normalization_status='invalid'`.
- Không tự đặt hệ số cho `year`, `day` hoặc `hour`.

Tỷ giá cố định của dự án:

```text
1 USD = 25.500 VND
```

Quy tắc normalize:

- VND trong Mongo đang theo đơn vị triệu VND/tháng: nhân `1_000_000`.
- USD/tháng: nhân `25_500`.
- Giữ giá trị/currency/period gốc và các field normalized riêng.
- Ghi `fx_rate_to_vnd`, nguồn tỷ giá và version của quy tắc để có thể truy vết/rebuild.

Quy tắc salary một phía:

- Có min, thiếu max: giữ normalized min; max là `NULL`.
- Thiếu min, có max: min là `NULL`; giữ normalized max.
- Negotiable: cả hai cận `NULL`, status `negotiable`.
- Min lớn hơn max, số âm hoặc currency-period không hỗ trợ: cả hai cận `NULL`, status `invalid`.

### 2.6. Experience và seniority

- Giữ `experience_raw`.
- Parse thành `experience_min_years` và `experience_max_years`.
- Ghi `experience_parse_status` để phân biệt parsed/missing/unparsed.
- Seniority được normalize thành `junior | mid | senior | unknown`.
- Không suy seniority từ title nếu không đủ chắc chắn.
- Silver có thể giữ giá trị chưa map; market/search trình bày qua bucket `unknown`.

### 2.7. Date normalization

Các field chính:

- `posted_at`
- `effective_posted_date`
- `deadline_date`
- `posted_date_parse_status`
- `deadline_date_parse_status`

`effective_posted_date`:

```text
COALESCE(DATE(posted_at), DATE(first_seen_at))
```

`posted_date_parse_status`:

- `parsed`: parse được `posted_at`; effective date lấy từ `posted_at`.
- `fallback_first_seen`: posted date thiếu/lỗi nhưng `first_seen_at` hợp lệ.
- `invalid`: cả posted date và first-seen đều thiếu/lỗi; record bị quarantine.

`deadline_date_parse_status`:

- `parsed`: parse deadline thành công.
- `missing`: không có deadline; `deadline_date=null`.
- `invalid`: có giá trị nhưng parse lỗi; `deadline_date=null`.
- Deadline không gây quarantine.

---

## 3. API contract đã triển khai

### 3.1. `POST /v1/jobs/search`

Filter hợp lệ:

| Filter | Bắt buộc | Semantics |
| --- | --- | --- |
| `posted_after` | Có | `effective_posted_date >= posted_after`; bảo vệ partition/cost |
| `posted_before` | Không | `effective_posted_date <= posted_before` |
| `source` | Không | `topdev | vietnamworks` |
| `seniority` | Không | `junior | mid | senior | unknown` |
| `category` | Không | Source-qualified category key |
| `salary_min` | Không | VND/tháng; lấy job có `salary_max_vnd_month >= salary_min` |
| `experience_max` | Không | Lọc theo `experience_min_years` |

Các filter cũ như `country`, `city`, `job_function`, `employment_type` và `work_mode` không còn trong contract.

Request dùng `extra='forbid'`, vì vậy filter/field lạ trả lỗi 400 thay vì bị bỏ qua im lặng.

Khoảng ngày đảo ngược bị từ chối tại biên HTTP:

```text
posted_before < posted_after
→ HTTP 400
→ error.field = "filters.posted_before"
```

Sort options:

- `salary_max_desc`
- `salary_min_asc`
- `experience_asc`
- `posted_desc`

Mọi sort dùng `job_id` làm tie-breaker để hỗ trợ keyset pagination có thứ tự xác định.

### 3.2. `JobItem`

Response item hiện gồm:

- `job_id`
- `source`
- `external_id`
- `title`
- `company_name`
- `location_text`
- `seniority`
- `experience_min_years`
- `experience_max_years`
- `salary_min_vnd_month`
- `salary_max_vnd_month`
- `salary_currency`
- `salary_period`
- `categories`
- `posted_at`
- `effective_posted_date`
- `deadline_date`
- `url`

Không đưa vào response:

- Raw payload.
- Contact information.
- Session data.
- Applicant/candidate data.
- Description/benefit/requirement text lớn.
- Skills/knowledge/tags trong v1 hiện tại.

Search response envelope:

- `items`
- `next_page_token`
- `total_estimated`
- `as_of`
- `request_id`

`total_estimated` được phép `null` để BigQuery adapter tương lai không phải chạy `COUNT(*)` trên mỗi request.

### 3.3. `GET /v1/market/metrics`

Dimensions:

- `source`
- `seniority`
- `category`

Windows:

- `90d`, mặc định.
- `all_time`.

Window `90d` inclusive:

```text
[as_of_date - 89 ngày, as_of_date]
```

Mỗi metric group phân biệt ba count:

- `posting_count`: số job distinct trong group/window.
- `salary_disclosed_count`: số job có ít nhất một cận lương hợp lệ.
- `salary_sample_count`: số job có đủ min/max để tính midpoint và median.

Invariant:

```text
salary_sample_count <= salary_disclosed_count <= posting_count
```

Median là median của midpoint `(min + max) / 2`.

K-anonymity:

```text
salary_sample_count < JOBS_API_METRICS_MIN_SAMPLE_SIZE
→ median_salary_vnd_month = null
```

Ngưỡng mặc định là 5. Suppression dựa trên sample count, không dựa trên posting count.

### 3.4. `GET /v1/metadata`

Metadata công bố:

- API version.
- `as_of` của batch đang phục vụ.
- Filter names, types, required flags và allowed values.
- Metrics và suppression rule.
- Dimensions.
- Windows.
- `metrics_min_sample_size`.
- Sort options.
- Query limits.
- `request_id`.

Catalog, validator và metadata dùng chung nguồn cấu hình để tránh lệch contract.

### 3.5. Authentication và lỗi

- `/health` public.
- Mọi endpoint `/v1/*` yêu cầu `X-API-Key`.
- Cloud Run dự kiến public ở tầng hạ tầng; API vẫn xác thực ở application layer.
- Production có thể nâng lên IAM/OIDC ở phase sau.
- Error response tiếp tục dùng một envelope thống nhất.
- API không trả PII và validator dùng allowlist/default-deny cho output columns.

---

## 4. Backend và runtime Phase 0

### 4.1. Backend availability

Phase 0 chỉ cho phép:

```text
warehouse_backend=fake
```

Settings fail-fast khi boot:

- `duckdb`: tạm tắt, khôi phục Phase 4.
- `bigquery`: chưa có adapter, triển khai Phase 3.
- Backend lạ: unsupported.

Dependency layer cũng giữ nhánh phòng thủ cho DuckDB, nhưng không import DuckDB repositories trong runtime Phase 0.

### 4.2. Local quick-start

`.env.example` mặc định:

```env
JOBS_API_WAREHOUSE_BACKEND=fake
JOBS_API_CACHE_BACKEND=memory
JOBS_API_RATE_LIMITER_BACKEND=memory
```

Nhờ đó chạy local không cần DuckDB hoặc Redis.

### 4.3. Docker Compose

Default Compose chạy:

```text
redis
api(fake)
```

- API expose tại `http://localhost:8082`.
- API dùng Redis cho cache/rate-limit khi chạy Compose.
- API không phụ thuộc `elt-init` và không mount DuckDB volume.

Pipeline MySQL → DuckDB cũ được giữ dưới profile `legacy-duckdb` để tham khảo/dựng lại kho cũ:

```bash
docker compose --profile legacy-duckdb up --build mysql elt-init
```

Profile này không phục vụ API bằng DuckDB; DuckDB serving bị tắt đến Phase 4.

---

## 5. Fake contract fixtures

### 5.1. Fake jobs

Fake repository đã được chuyển sang contract mới và chứa:

- Hai nguồn TopDev/VietnamWorks.
- Composite job ID.
- Seniority `unknown`.
- Salary normalized theo VND/tháng.
- Record negotiable.
- Categories dạng repeated list.
- Job có nhiều category.
- Job không có category.
- Ngày đăng/effective date/deadline.
- Filter/sort semantics tương ứng adapter BigQuery tương lai.

Category filter dùng semantics kiểu `EXISTS`, không explode và không trả trùng job.

### 5.2. Fake metrics

Fake metrics có rows theo:

- Source.
- Seniority.
- Category.

Fixtures bao gồm:

- Ba loại count.
- Nhóm đủ mẫu để trả median.
- Nhóm thiếu mẫu để kiểm tra k-anonymity.
- Seniority `unknown`.
- `topdev:unknown`.
- `vietnamworks:unknown`.

Mongo-like raw fixtures chưa được tạo trong Phase 0. Chúng được chuyển sang Phase 2, nơi mapper/ELT mới thực sự tiêu thụ chúng.

---

## 6. Cache và batch watch-item

Market metrics hiện dùng `as_of_date` làm surrogate cho batch trong cache key của fake backend:

```text
{env}:metrics:v1:{as_of_date}:{window}:{dimension}
```

Điều này chỉ phù hợp với fake một-batch. Phase 3 phải thay `as_of_date` bằng `batch_id` thật để tránh collision khi publish hai batch trong cùng ngày.

Page token/batch snapshot, immutable batch metadata và atomic publication đã được khóa ở migration plan nhưng chưa triển khai trong Phase 0.

---

## 7. Files thay đổi trong Phase 0

### 7.1. Models và domain contract

| File | Thay đổi chính |
| --- | --- |
| [`app/models/enums.py`](../app/models/enums.py) | Job source, seniority có `unknown`, sort options, metric dimensions/windows |
| [`app/models/jobs.py`](../app/models/jobs.py) | Search filters mới, `posted_after` bắt buộc, inverted-date validation, `CategoryItem`, `JobItem` mới |
| [`app/models/market.py`](../app/models/market.py) | Ba count, median VND/tháng, window boundaries, display name |
| [`app/models/metadata.py`](../app/models/metadata.py) | Dimensions, windows, k-anonymity threshold và metadata contract mới |
| [`app/domain/catalog.py`](../app/domain/catalog.py) | Single source of truth cho filters, metrics, dimensions, windows, sort và limits |
| [`app/domain/validator.py`](../app/domain/validator.py) | Required `posted_after`, allowlists mới, PII/default-deny, k-anonymity theo sample count |
| [`app/domain/ports/metrics_repository.py`](../app/domain/ports/metrics_repository.py) | Metric DTO/port mới với ba count, median và `as_of` |

### 7.2. API layer

| File | Thay đổi chính |
| --- | --- |
| [`app/main.py`](../app/main.py) | OpenAPI description, auth note, filter/market docs và tag metadata mới |
| [`app/api/metadata.py`](../app/api/metadata.py) | Công bố filters, metrics, dimensions, windows và threshold từ catalog/settings |
| [`app/api/market.py`](../app/api/market.py) | Window `90d/all_time`, unknown display, ba count, k-anonymity và cache-key surrogate |
| [`app/api/deps.py`](../app/api/deps.py) | Phase 0 chỉ ráp fake adapters; bỏ import DuckDB; giữ fail-fast/defensive branches |

`app/api/jobs.py` không cần thay đổi lớn; handler tiếp tục làm việc qua domain models và repository port.

### 7.3. Infrastructure fixtures

| File | Thay đổi chính |
| --- | --- |
| [`app/infrastructure/warehouse/fake_jobs.py`](../app/infrastructure/warehouse/fake_jobs.py) | Fake corpus theo contract Mongo/BigQuery mới, multi-category và unknown cases |
| [`app/infrastructure/warehouse/fake_metrics.py`](../app/infrastructure/warehouse/fake_metrics.py) | Metrics rows theo source/seniority/category, unknown buckets và k-anonymity samples |

### 7.4. Settings và runtime

| File | Thay đổi chính |
| --- | --- |
| [`app/settings.py`](../app/settings.py) | Backend allowlist `{fake}`, fail-fast messages và `metrics_min_sample_size` |
| [`.env.example`](../.env.example) | Local mặc định fake + memory; ghi phase availability của BigQuery/DuckDB |
| [`docker-compose.yml`](../docker-compose.yml) | Default `redis + api(fake)`; topology DuckDB cũ chuyển vào legacy profile |

### 7.5. Documentation và ADR

| File | Thay đổi chính |
| --- | --- |
| [`README.md`](../README.md) | Quick-start, curl có API key/posted-after, salary, metrics, Cloud Run và backend phase status |
| [`HUONG-DAN-CHAY-VA-DOC-CODE.md`](../HUONG-DAN-CHAY-VA-DOC-CODE.md) | Gắn banner LEGACY và trỏ về tài liệu migration hiện hành |
| [`docs/adr/ADR-018-bigquery-warehouse.md`](../docs/adr/ADR-018-bigquery-warehouse.md) | Quyết định BigQuery production, fake test, DuckDB tạm tắt |
| [`docs/adr/ADR-019-mongo-source-data-contract.md`](../docs/adr/ADR-019-mongo-source-data-contract.md) | Mongo source contract và schema mapping matrix |
| [`docs/adr/README.md`](../docs/adr/README.md) | Thêm ADR-018/019, đánh dấu ADR-020..026 planned |
| [`docs/migration-mongo-bigquery-plan.md`](../docs/migration-mongo-bigquery-plan.md) | Kế hoạch Phase 0–8, invariants và Definition of Done |

### 7.6. Tests

| File | Phạm vi kiểm tra |
| --- | --- |
| [`tests/conftest.py`](../tests/conftest.py) | Test hermetic: fake warehouse, memory cache/rate-limit, base request có `posted_after` |
| [`tests/test_contract.py`](../tests/test_contract.py) | Search shape, filters, PII, multi-category, salary và inverted dates |
| [`tests/test_market.py`](../tests/test_market.py) | Metrics shape, windows, counts, k-anonymity và unknown buckets |
| [`tests/test_metadata.py`](../tests/test_metadata.py) | Filters/dimensions/windows/threshold và loại bỏ filters cũ |
| [`tests/test_settings.py`](../tests/test_settings.py) | Fail-fast cho fake/DuckDB/BigQuery/backend lạ |
| [`tests/test_auth.py`](../tests/test_auth.py) | Auth requests được cập nhật theo required date filter |
| [`tests/test_pagination.py`](../tests/test_pagination.py) | Pagination không trùng/sót và filter fingerprint với request hợp lệ |
| [`tests/test_openapi.py`](../tests/test_openapi.py) | OpenAPI generation, operation IDs và seniority enum mới |
| [`tests/test_validator.py`](../tests/test_validator.py) | Required filter, allowlists, return-column safety và k-anonymity |

Hai test suite DuckDB cũ bị xoá có chủ đích:

- `tests/test_duckdb_repo.py`
- `tests/test_injection_safety.py`

Chúng phụ thuộc schema cũ (`country`, numeric `job_id`, `years_exp`, salary cũ). Phase 4 sẽ viết lại DuckDB repository/tests theo schema mới và thêm lại injection/parity tests.

---

## 8. Definition of Done đạt được

- [x] Contract nguồn Mongo được ghi lại và có schema mapping matrix.
- [x] Composite `job_id` và source enums.
- [x] Categories repeated; multi-category không nhân dòng.
- [x] Unknown bucket không làm mất posting.
- [x] Salary VND/tháng, tỷ giá 25.500, one-sided và invalid semantics.
- [x] Experience/seniority/date semantics được khóa.
- [x] `posted_after` bắt buộc; inverted range trả 400 đúng field.
- [x] Market metrics có `90d/all_time`, ba count và k-anonymity theo sample count.
- [x] Models, catalog, metadata, README và OpenAPI description đồng bộ.
- [x] Fake search, metrics, metadata và authentication hoạt động.
- [x] Backend chưa khả dụng bị từ chối ngay khi boot.
- [x] Local quick-start không phụ thuộc Redis.
- [x] Default Compose chỉ chạy Redis + API fake.
- [x] Contract tests và lint gate xanh.
- [x] Fake API fixtures thuộc Phase 0; Mongo raw fixtures chuyển Phase 2.
- [x] Không tạo tài nguyên GCP trong Phase 0.

---

## 9. Nội dung cố ý để các phase sau

### Phase 1

- Báo cáo tổng quan và checklist xác minh: [`phase-1/README.md`](phase-1/README.md).
- GCP project/APIs/datasets.
- Service accounts, WIF, Secret Manager và cost guard.
- Memorystore/Direct VPC egress.

### Phase 2

- Báo cáo tổng quan, cách chạy và checklist xác minh: [`phase-2/README.md`](phase-2/README.md).
- Sanitized Mongo-like raw fixtures.
- Mongo → BigQuery extract/transform/load.
- Salary/date/experience/category mapper thật.
- Quarantine, reconciliation và quality checks.
- Silver/gold tables và atomic publication.

### Phase 3

- Báo cáo tổng quan, vận hành và checklist tích hợp: [`phase-3/README.md`](phase-3/README.md).
- BigQuery job/metrics repositories.
- Named query parameters, keyset SQL và `LIMIT + 1`.
- Batch-aware metadata/page token/cache key.
- `maximum_bytes_billed`, timeout/cancel và query observability.

### Phase 4

- Báo cáo tổng quan, parity và hướng dẫn kiểm chứng: [`phase-4/README.md`](phase-4/README.md).
- Khôi phục DuckDB theo schema mới.
- Fake/DuckDB/BigQuery parity tests.
- Injection tests theo filters mới.

### Phase 5

- Dagster orchestration (Plan A wrapper, concurrency, run_metric) — repo scraper `job-scraper-1`.

### Phase 6

- Báo cáo tổng quan + runbook staging/prod: [`phase-6/README.md`](phase-6/README.md).
- Cloud Run deploy script (staging/prod), image tag = commit SHA → Artifact Registry.
- CI/CD GitHub Actions + WIF (deploy staging tự động), smoke cổng cứng qua identity token.
- Public access tối thiểu quyền (`allow-public.sh`), memory-first + toggle Redis/VPC egress.

### Phase 7

- Báo cáo tổng quan + runbook VM/backup: [`phase-7/README.md`](phase-7/README.md).
- Provisioning VM (Mongo container, Dagster 2 systemd unit), compose tách + hardening bí mật.
- Backup mongodump→GCS (writer objectCreator bất biến, restore identity riêng) + restore test.

### Phase 8

- Nguồn tuyển dụng và tính năng mở rộng sau (TopCV/ITviec/JobsGo).

---

## 10. Checklist trước commit

```bash
pytest -q
ruff check app tests
docker compose config -q
git diff --check
git status --short
```

Đảm bảo stage cả các file mới:

- `docs/adr/ADR-018-bigquery-warehouse.md`
- `docs/adr/ADR-019-mongo-source-data-contract.md`
- `docs/migration-mongo-bigquery-plan.md`
- `tests/test_market.py`
- `tests/test_settings.py`
- `migrate-report/README.md`

Đảm bảo hai deletion DuckDB cũ cũng được stage có chủ đích.

Commit message gợi ý:

```text
feat: lock phase 0 Mongo-to-BigQuery serving contract
```
