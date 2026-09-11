# Kế hoạch: Serving API trên dữ liệu thật (Mongo → BigQuery → Cloud Run) + Dagster ELT

> Bản kế hoạch triển khai đã duyệt (đối chiếu code thật `jobs-serving-api` + `job-scraper-1` + dữ liệu Mongo, qua nhiều vòng review). Dùng làm tài liệu theo dõi tiến độ; acceptance criteria của mỗi phase = Definition of Done.

## Context
`jobs-serving-api` đang phục vụ dữ liệu demo (MySQL 76 dòng → DuckDB). Mục tiêu: phục vụ **dữ liệu việc làm THẬT** do `job-scraper-1` crawl vào **MongoDB `job_crawler`** (~14.485 job hiện tại: TopDev + VietnamWorks), dùng **BigQuery** làm kho (đúng curriculum gốc mentor — hoàn toàn BigQuery + Cloud Run), **Dagster** orchestrate ELT, **serving API lên Cloud Run**. Việc chính là biến mô tả thành **invariant kiểm thử được**.

4 trụ: (1) data contract khớp dữ liệu thật; (2) published-batch **nguyên tử + versioned + metadata bất biến theo batch**; (3) pagination đẩy xuống BigQuery **gắn batch**; (4) orchestration đúng theo crawl batch.

## Quyết định đã chốt
- **Nguồn:** TopDev + VietnamWorks. [SAU] TopCV/ITviec/JobsGo (cần login). Bỏ Facebook.
- **Phạm vi API:** phục vụ **toàn bộ corpus job đã quan sát & lưu** (KHÔNG "active"; `lastSeenAt==firstSeenAt`). `as_of` = **data cutoff của batch** (không phải lúc publish).
- **Kho:** BigQuery (prod) · DuckDB (dev) · fake (test). `warehouse_backend=fake|duckdb|bigquery`, cùng contract. **(Phase 0: chỉ `fake` khả dụng; bigquery bật Phase 3, duckdb Phase 4.)**
- **Deploy:** API **Cloud Run trực tiếp**. VM: Mongo + scraper + Dagster + ELT (gắn thẳng writer SA; bỏ impersonation; VM = trusted compute boundary — ADR).
- **Cache/rate-limit:** **Memorystore for Redis** (không thuộc Always Free, phát sinh chi phí khi idle; giai đoạn thử nghiệm dùng trial credits → tier nhỏ nhất + teardown khi không demo). Cloud Run→Redis **ưu tiên Direct VPC egress** (connector fallback), cùng region, private IP. Cache key chứa `batch_id`; rate-limit key = client_id/hash. **Cache fail-open**; rate-limiter fail-open+log (demo). `max-instances` đặt trần.
- **Auth:** Cloud Run public hạ tầng; `/v1/*` bắt buộc `X-API-Key`; `/health` mở (liveness nhẹ, KHÔNG query BQ/Redis). IAM/OIDC [SAU].
- **Salary:** `1 USD=25.500 VND` (versioned). VND Mongo theo **triệu** →×1e6; USD →×25.500. Giữ original + normalized. Negotiable→null; one-sided KHÔNG tự điền; min>max/âm/currency-period lạ → invalid.
- **`job_id` composite:** `"<source>:<external_id>"`, source canonical slug lowercase; dựng từ `(platformId, externalId)`; giữ `external_id` field riêng.
- **`/v1/jobs/search`:** `posted_after` **BẮT BUỘC**. Trang sau: client **gửi lại filters**, API check fingerprint (token KHÔNG âm thầm đổi params).
- **`/v1/market/metrics`:** `dimension = source | seniority | category` ([SAU] province); `window = 90d | all_time`. **Unknown bucket**: seniority `unknown`; category `topdev:unknown`/`vietnamworks:unknown`. k-anon `JOBS_API_METRICS_MIN_SAMPLE_SIZE=5`.
- **Timeout:** BQ query 15s (enforce adapter `result(timeout)`+`job.cancel()`→504) · Cloud Run 25s (hạ tầng) · client ≥30s. App-timeout KHÔNG enforce trong process → `JOBS_API_REQUEST_TIMEOUT_S` đổi tên **`request_budget_s`** (budget/observability).
- **cursor lỗi:** **400 INVALID_CURSOR** · **410 CURSOR_EXPIRED**.
- **Thời gian:** `data_as_of_at` (chốt sau crawl, trước transform) · `as_of_date = DATE(data_as_of_at,"Asia/Ho_Chi_Minh")` · `published_at` (lúc flip). Response `as_of = data_as_of_at`.
- **CI:** **GitHub Actions + Workload Identity Federation** (không JSON key).

## INVARIANT bắt buộc [NGAY] (kiểm thử được)
1. **Versioned + metadata bất biến (hai bảng):**
   - `silver_jobs`/`gold_market_metrics` mang cột `batch_id`, ELT **APPEND**; `WRITE_TRUNCATE` chỉ cho **bảng candidate/work** (`silver_jobs_candidate`/`gold_market_metrics_candidate`), KHÔNG phải dataset **môi trường** staging. Giữ **≥2 batch**. ("staging" = môi trường `jobs_staging`/`jobs_prod`; "candidate" = bảng ETL tạm.)
   - **`warehouse_state`** (singleton pointer): `warehouse_name, published_batch_id`. Bootstrap seed 1 row `published_batch_id=NULL`.
   - **`warehouse_batches`** (immutable catalog): `batch_id, crawl_batch_id, dagster_run_id, data_as_of_at, as_of_date, published_at, source_jobs_total, topdev_source_jobs, vietnamworks_source_jobs, silver_rows, gold_rows, quarantined_rows, mapping_version, salary_fx_version`.
   - API đọc metadata **theo `batch_id` đang query** (không current state) → trang 2 batch A trả đúng `as_of` A; rollback chỉ đổi pointer.
2. **Idempotency + concurrency + publish nguyên tử:** logical key silver `(batch_id, job_id)`, gold `(batch_id, window, dimension, dimension_value)`. **Retry = state machine theo "có mặt trong `warehouse_batches`" (= đã publish, BẤT BIẾN):**
   - có & `==current` → no-op; có & `!=current` → **`BATCH_ALREADY_PUBLISHED`**, không sửa gì (chỉ rollback/promote tường minh đổi pointer);
   - chưa có nhưng silver/gold có rows → dở dang → xoá/thay riêng batch rồi load lại;
   - chưa có & không rows → batch mới.
   **`warehouse_batches` INSERT + `warehouse_state` CAS trong CÙNG transaction** (`BEGIN…COMMIT`); `published_at` sinh trong transaction; CAS `WHERE published_batch_id=@expected_prev` (đầu tiên `IS NULL`); **assert affected==1 TRONG script trước COMMIT** (≠1→ROLLBACK). Silver/gold append ngoài transaction OK. CLI: `BATCH_ALREADY_PUBLISHED`→exit non-zero có kiểm soát, không auto-retry vô hạn. Transaction abort do concurrent mutation → đọc lại state, áp state machine, không retry mù `expected_prev` mới.
3. **API chỉ đọc batch đã publish** (hoặc batch trong token). commit metadata+pointer **sau khi** silver&gold pass check.
4. **Page token gắn batch:** `{batch_id, sort, last_sort_value, last_job_id, filter_fingerprint, resolved_posted_after/before}`. Query đúng batch token; cleaned→410; TTL ≤ thời gian giữ batch; không cleanup batch còn trong TTL.
5. **Cache key gắn batch+env+schema version:** `{env}:metrics:v1:{batch_id}:{window}:{dimension}`; rate-limit `{env}:ratelimit:v1:{client_id_hash}:{window}`. v1-API không cache search.
6. **window 90d neo `as_of_date`:** `effective_posted_date ∈ [as_of_date-89, as_of_date]` (inclusive). Response `window, window_start, window_end, as_of(=data_as_of_at)`. `all_time` = toàn corpus batch.
7. **Counts & median:** `posting_count` (distinct job_id) ≥ `salary_disclosed_count` ≥ `salary_sample_count`. `median` = median của **midpoint** `(min+max)/2`. **k-anon theo `salary_sample_count`**. Gold uniqueness `(batch_id, window, dimension, dimension_value)`.
8. **effective_posted_date:** `COALESCE(parsed_posted_date, DATE(first_seen_at))`; cả hai invalid → **quarantine** (reason).
9. **Reconciliation:** `source_jobs_at_extract = silver_rows + quarantined_rows` (theo source); `silver_rows = COUNT(*) = COUNT(DISTINCT job_id)`. Count Mongo thực tế cùng extraction run (không dùng count Dagster crawl output). 14.485/8 = baseline/fixture.

## Kế hoạch theo phase ([NGAY] · [SAU])

### Phase 0 — Contract & normalized source  [NGAY]
- Đọc `src/` scraper chốt mapping. **Deliverable: schema mapping matrix** (ADR-019): silver field → BQ type · nullable · nguồn TopDev · nguồn VNW · fallback. + field **KHÔNG ra API** (raw payload, contact, session).
- `company_name`: **KHÔNG đổi schema scraper** — ELT luôn lấy từ `raw` (TopDev display_name; VNW companyName); không có → null. `company_name: str|None`.
- **categories:** silver **repeated STRUCT** `<category_key,category_name,category_code,category_path,level1_id,level2_id,level3_id>` (không explode). Nguồn `job_details.categories` OR fallback `jobs.category`. **Key = source + group-path** (`topdev:g14~j22`). Không category → silver `categories=[]`; bucket `<source>:unknown` chỉ ở metrics (search không hỗ trợ `category=<source>:unknown`; [SAU] `has_category=false`).
- experience: `experience_raw, experience_min_years, experience_max_years, experience_parse_status`; seniority: `seniority_raw, seniority_normalized` (null nếu không chắc; không suy từ title), `seniority_mapping_version`.
- date parse theo source → `posted_at, effective_posted_date, deadline_date, posted_date_parse_status, deadline_date_parse_status` (semantics theo ADR-019).
- **Fake API contract fixtures** (hình dạng JobItem/response: composite id, categories repeated + multi-category, salary VND/tháng + negotiable, seniority/category `unknown`) — chạy trên `fake`. *(Sanitized Mongo-like RAW fixtures cho mapper → Phase 2, nơi mới có consumer.)*
- Cập nhật `app/models/*` + `/v1/metadata`; đồng bộ **README + OpenAPI description** (docs cũng là contract); contract tests trên `fake` xanh.
- **DuckDB tạm tắt** (schema silver cũ) — settings/deps từ chối rõ `warehouse_backend=duckdb`; khôi phục Phase 4.
- **DoD:** models+metadata+README/OpenAPI đồng bộ; mapping matrix trong ADR-019; fake-contract fixtures + contract tests xanh trên fake; contract khoá. Không tạo tài nguyên GCP.

### Phase 1 — GCP foundation  [NGAY] (script gcloud idempotent, commit; [SAU] Terraform)
- Project + APIs; dataset **staging & prod** cùng region.
- **SA tách môi trường (IAM enforce):** `sa-api-reader-staging`/`sa-api-reader-prod` (`dataViewer` dataset tương ứng + `jobUser` **PROJECT** + `secretAccessor` chỉ secret môi trường) · `sa-dagster-elt` (`dataEditor`+`jobUser`) · `sa-ci-deployer-staging` (WIF tự động) + `sa-ci-deployer-prod` (manual) — AR write + Cloud Run deploy + `serviceAccountUser` trên runtime SA. Không JSON key.
- Artifact Registry (+image lifecycle); **secret riêng môi trường** `jobs-api-keys-{staging,prod}`, `jobs-api-page-token-secret-{staging,prod}` (không dùng chung); pin version; budget alert.
- **Memorystore Redis** (tier nhỏ nhất) cùng region + **Direct VPC egress**.
- **Cost guard:** `max-instances` trần + CPU/mem/concurrency; `maximum_bytes_billed`; teardown script (staging + Memorystore); image lifecycle.

### Phase 2 — ELT Mongo → BigQuery (CLI repo serving-api)  [NGAY]
- **Sanitized Mongo-like raw fixtures** (TopDev/VNW: thiếu detail, negotiable, VND/USD, chỉ-max, experience range, invalid date, missing title/company, multi-category, duplicate key) cho mapper + quality-check.
- Chốt `data_as_of_at`. Extract `jobs` **LEFT JOIN** `job_details` (count Mongo thực tế); remap; parse date/experience/salary; normalize company/salary/categories.
- Load **candidate tables** (`WRITE_TRUNCATE`) → **quality checks** → **build gold TỪ validated candidate** (90d + all_time; `CROSS JOIN UNNEST(categories)` + dedupe `(batch_id,job_id,category_key)` + `COUNT(DISTINCT job_id)`; unknown bucket) → APPEND silver & gold (`batch_id`) → verify → **transaction: INSERT `warehouse_batches` + CAS `warehouse_state`**. Gold build từ candidate để né `require_partition_filter` + cùng snapshot.
- **Writer guard:** dataset allowlist theo env; CLI prod cần `--environment prod`; không nhận dataset tùy ý; log project/dataset trước khi load; staging test không dùng prod writer creds.
- **Quarantine table/artifact:** `batch_id, source, external_id, stage, reason_code, reason_detail_sanitized, created_at`.
- `run_metric` + lineage `crawl_batch_id, dagster_run_id, warehouse_batch_id`.
- [SAU] cleanup/retention tự động (giữ batch trong token TTL), `elt_batches` đầy đủ, incremental MERGE, tombstone, canonical category mapping.

### Phase 3 — BigQuery read adapter  [NGAY]
- `bigquery_jobs.py` + `bigquery_metrics.py` (impl port; DuckDB/fake giữ). ADC.
- **Keyset trong SQL** + `LIMIT @limit_plus_one`; NULL ordering khớp fake/DuckDB; query **batch trong token**; đọc metadata theo `batch_id` từ `warehouse_batches`. Category filter `WHERE EXISTS (SELECT 1 FROM UNNEST(categories) c WHERE c.category_key=@k)`.
- Named params `@x`; identifier validate+fully-qualified+backtick. `maximum_bytes_billed`; log `bq_job_id, total_bytes_billed, cache_hit, elapsed_ms`. Timeout→cancel→504. `total_estimated=null`.
- `settings.py`: `bq_project, bq_dataset, bq_location, bq_maximum_bytes_billed, mongo_url, metrics_min_sample_size`.

### Phase 4 — API contract + search date filter + tests  [NGAY]
- `posted_after` bắt buộc; partition BY `effective_posted_date`, cluster `batch_id, source, seniority_normalized, job_id`; `require_partition_filter=true`.
- **KHÔI PHỤC DuckDB backend (đã tạm tắt ở Phase 0):** dựng lại `DuckDBJobRepository`/`DuckDBMetricsRepository` theo schema silver MỚI (composite job_id, source, salary VND/tháng, experience min/max, categories repeated, `POSTED_DESC`, keyset trong SQL, `market_metrics(dimension, window)`, as_of); **bỏ chặn** `warehouse_backend=duckdb` ở `settings.py` + `deps.py`; thêm lại **test injection qua `category`/filter chuỗi** (thay `test_injection_safety` cũ dùng `country`) + `test_duckdb_repo` mới.
- Contract test parameterize **fake + duckdb + BQ SQL-builder** (filter, sort, null ordering, cursor, limit+1, không trùng/sót, salary, date boundary, string job_id, category filter không duplicate, publish batch giữa 2 page request).
- BQ integration guard `RUN_BQ_INTEGRATION=1` + dataset suffix `_test`; cost test.

### Phase 5 — Dagster orchestration  [NGAY: Plan A + lock/idempotency]
- **Plan A wrapper daily batch job:** `crawl_batch_id` → crawl TopDev+VNW → chờ tất cả → lỗi→không ELT → ok→`run_serving_elt`. Giữ partitioned assets cho manual/backfill.
- **Concurrency key** phủ wrapper + manual/backfill crawler + ELT extract.
- `runner.py`: giết process Linux (`start_new_session=True`+`os.killpg`+graceful rồi force). Bỏ `assets_facebook`.
- **Asset check cốt lõi:** silver distinct==row count; `source_jobs==silver+quarantine`; source count khớp; row đúng batch_id; silver&gold+`warehouse_batches` tồn tại trước flip; `sample≤disclosed≤posting`; median null khi <k; pointer giữ nguyên nếu check fail; policy null title/url.
- [SAU] sensor + run_key + full asset-check + freshness policy.

### Phase 6 — Cloud Run + CI/CD + staging/prod  [NGAY cơ bản]
- Image tag commit SHA → AR → deploy **staging** (WIF); smoke params hợp lệ: `/health` · `/v1/metadata` · `/v1/jobs/search?posted_after=2026-01-01&limit=5` · `/v1/market/metrics?dimension=source&window=90d` + `?dimension=category&window=all_time`.
- Cloud Run: SA reader theo env; secrets Secret Manager theo env (pin); không `GOOGLE_APPLICATION_CREDENTIALS`; Direct VPC egress → Memorystore; `max-instances` trần.
- **staging/prod NGAY:** 2 config, 2 dataset, SA tách môi trường, script deploy prod thủ công lặp lại, một lần prod deploy/rehearsal ghi nhận. [SAU] promotion/approval/canary/rollback.

### Phase 7 — VM ops + backup  [NGAY]
- Provisioning script commit; Dagster **systemd**. Compose **chỉ `mongo`**; tách file compose (dev/VM-mongo/minio); bỏ hard-code mật khẩu `mongo-init.js` (fail-fast `${MONGO_ROOT_PASSWORD:?required}`); bỏ/secret-hoá `metabase_reader`.
- `.gitignore`: `.env`,`.env.*`,`!.env.example`; unignore compose/provisioning/systemd templates.
- Firewall: không expose Mongo/Dagster UI; session TopCV/ITviec ngoài git, login lại thủ công (không vượt anti-bot).
- Backup: `mongodump`→GCS (tên immutable, writer SA chỉ-tạo-object, restore identity riêng, retention/lifecycle, encryption) + disk snapshot. **Restore test lần đầu NGAY.** [SAU] drill định kỳ.

### Phase 8 — Nguồn sau  [SAU]
TopCV/ITviec/JobsGo (+asset crawl, login monitoring); sửa normalized model upstream thay vì hack raw.

## ADR (docs/adr/)
018 BigQuery warehouse · 019 Mongo source & data contract (mapping matrix, LEFT JOIN, composite key, category repeated + source-qualified + unknown bucket) · 020 BigQuery serving (keyset, limit+1, total_estimated=null, window neo as_of_date, count defs, dimension allowlist + unknown bucket, posted_after bắt buộc, cursor 400/410, timeout, category EXISTS, metadata theo batch) · 021 Dagster (Plan A, CAS+lock+idempotent+bootstrap, sequencing≠deps) · 022 Deployment (Cloud Run + service identity, GitHub Actions+WIF, SA tách môi trường, VM trusted boundary, staging/prod, cost guard) · 023 Session mgmt · 024 Salary · 025 Atomic publication (hai bảng, INSERT+CAS cùng transaction, ≥2 batch, bootstrap, rollback, lineage) · 026 Pagination snapshot (token gắn batch, metadata từ `warehouse_batches`, 410 expired, cache key gắn batch).

## Files chính
- serving-api (mới): `warehouse/bigquery_jobs.py`, `bigquery_metrics.py`, `sql/search_jobs_bq.sql`, `app/elt/*_bq.py`, `docs/adr/ADR-018..026`, tests.
- serving-api (sửa): `app/models/*`, `app/domain/pagination.py`, `app/api/deps.py`, `app/settings.py`, `app/domain/validator.py`, `requirements.txt`, `README.md`, tách `docker-compose*.yml`.
- job-scraper-1: `orchestration/.../runner.py`, `assets_serving.py`, `__init__.py` (bỏ facebook), `mongo-init.js`, `.gitignore`, provisioning/systemd/`.env.example`. *(KHÔNG đổi schema scraper — company_name đọc từ raw ở ELT.)*

## Verification / Acceptance
Xem đầy đủ trong bản plan gốc (`~/.claude/plans/`). Điểm cốt lõi: pytest fake xanh (duckdb tạm tắt tới Phase 4); reconciliation `source==silver+quarantine`; VND/USD/negotiable/one-sided đúng; 3 count + k-anon theo sample_count; category 1 job=1 row + search không duplicate; unknown bucket không mất posting; keyset trong SQL; `maximum_bytes_billed` reject; timeout→cancel+504; **page 2 đọc đúng batch + as_of của trang 1**; CAS fail→rollback; retry batch lịch sử→`BATCH_ALREADY_PUBLISHED`; SA staging không đọc prod; secrets ngoài git; mongodump restore thử OK.

## Hoãn [SAU]
arbitrary date-range metrics · /v2 · job-detail endpoint · incremental MERGE + tombstone · elt_batches/cleanup-retention tự động · writer staging/prod tách riêng · `has_category=false` · sensor+run_key + full asset-check · Terraform · promotion/rollback tự động · restore drill định kỳ · TopCV/ITviec/JobsGo · Postgres/OpenSearch · province + location parser · canonical category mapping · rate-limiter fail-closed.
