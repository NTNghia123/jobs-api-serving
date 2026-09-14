# Looker Studio dashboard — jobs-serving-api

Dashboard BI nối **trực tiếp BigQuery** (dataset `jobs_reporting`, các view `rpt_*`), phục vụ 4 mục đích + showcase: **thị trường việc làm · pipeline ELT · chất lượng dữ liệu · API serving**.

> Lớp reporting views là read-only, tách khỏi bảng do ELT sở hữu. Nguồn sự thật tên cột: `infra/gcp/reporting/views_core.sql` và `views_api.sql`. Kiến trúc pipeline: [../architecture/README.md](../architecture/README.md).

## 0. Thiết lập hạ tầng (chạy 1 lần, theo thứ tự)

```bash
cd infra/gcp
cp config.example.sh config.sh   # điền PROJECT_ID + kiểm DATASET_REPORTING/LOGS, METRICS_MIN_SAMPLE_SIZE
bash 85-reporting-views.sh        # dataset jobs_reporting + view core + authorize đọc jobs_prod
# ... dựng dashboard trang 1–5 ...
bash 86-logging-sink.sh           # dataset jobs_prod_logs + Cloud Logging→BQ sink (API metrics)
# ... sinh traffic tới API prod (search / market / vượt hạn mức 429) ...
bash 87-api-reporting-views.sh    # view rpt_api_* + authorize đọc jobs_prod_logs
```

`METRICS_MIN_SAMPLE_SIZE` trong `config.sh` **phải khớp** app setting `metrics_min_sample_size` (mặc định 5) — đối chiếu với `/v1/metadata`.

`API_REPORT_LOOKBACK_DAYS` (cửa sổ hiển thị view API) **phải ≤** `LOG_PARTITION_EXPIRATION_DAYS` (retention log) — `87` sẽ `die` nếu vi phạm (tránh dashboard hiển thị tối đa = retention gây hiểu nhầm).

## 1. Kết nối Looker Studio

- **Data source**: BigQuery connector → project `${JOBS_API_BQ_PROJECT}` → dataset **`jobs_reporting`** → chọn từng view `rpt_*`.
- **Credentials: Owner's Credentials.** Người xem (viewer) **không cần IAM BigQuery**. Chỉ **owner** của data source cần: `roles/bigquery.jobUser` trên execution project + `roles/bigquery.dataViewer` trên `jobs_reporting`. Owner **không** cần quyền trực tiếp trên `jobs_prod`/`jobs_prod_logs` — truy cập qua **authorized dataset** (script 85/87 đã cấu hình).
- **Freshness cấu hình theo DATA SOURCE** (không theo page):
  - Market (Trang 1–3): **12 giờ**.
  - Ops/API (Trang 4–6): **5 phút**.
  - `rpt_pipeline_batches` dùng ở **cả Trang 1 và Trang 4** → tạo **2 data source** cùng trỏ view này: một freshness 12h (Trang 1), một 5m (Trang 4). (Hoặc chấp nhận 5m cho cả hai.)
- **KHÔNG dùng Extract Data** cho Trang 4–6 (extract là snapshot tĩnh).
- Calculated field đã làm ở tầng view → Looker chỉ format/aggregate.
- Đặt **date-range control** mặc định (vd 12 tháng gần nhất) để tránh quét toàn partition.

## 2. Các trang & biểu đồ

Ký hiệu: **nguồn** · dimension · metric · loại chart.

### Trang 1 — Tổng quan (showcase)
| Chart | Chi tiết |
|---|---|
| KPI scorecards | `rpt_overview_kpis` · — · `job_count`, `source_count`, `median_salary_trieu`, `salary_disclosed_rate`, `as_of_date` |
| Cơ cấu nguồn | `rpt_jobs` · `source` · COUNT · donut |
| Theo cấp bậc | `rpt_jobs` · `seniority` · COUNT · bar |
| Job qua batch | `rpt_pipeline_batches`\@12h · `published_at` · `silver_rows` · line |
| Kiến trúc | ảnh `../architecture/jobs-serving-api-pipeline-preview.png` + mô tả |

### Trang 2 — Thị trường (một data source: `rpt_jobs_by_category`)
Controls: `source`, `seniority`, **`category_label`** (hoặc `category_key`), date range trên `effective_posted_date`. Mọi count = `COUNT_DISTINCT(job_id)`.
| Chart | Chi tiết |
|---|---|
| Top 15 ngành | `category_label` · COUNT_DISTINCT(job_id) · horizontal bar |
| Cấp bậc × nguồn | `seniority` × `source` · COUNT_DISTINCT(job_id) · stacked bar |
| Xu hướng đăng | `effective_posted_date` (theo tháng) · COUNT_DISTINCT(job_id) · time series *(mật độ tin đăng, không phải "job active")* |
| Top công ty | `company_name` · COUNT_DISTINCT(job_id) · bar |
| Bảng chi tiết | title/company/source/seniority/salary/`effective_posted_date`/`deadline_date` · table (link `source_url`) *(view exploded 1 dòng/job×category → job đa ngành lặp dòng; nếu muốn 1 dòng/job dùng `COUNT_DISTINCT` hoặc bảng riêng từ `rpt_jobs`)* |

### Trang 3 — Lương (đơn vị triệu VND/tháng)
Control cửa sổ `window` (90d ↔ all_time) cho chart gold.
| Chart | Chi tiết |
|---|---|
| Median theo ngành | `rpt_market_metrics` (dimension='category') · `dimension_label` · `median_salary_trieu` · bar. Filter nguồn **chỉ chart này** qua `taxonomy_source` (không page-wide) |
| Median theo cấp bậc | `rpt_market_metrics` (dimension='seniority') · `dimension_value` · `median_salary_trieu` · bar |
| Median theo nguồn | `rpt_market_metrics` (dimension='source') · `dimension_value` · `median_salary_trieu` · bar |
| % công khai lương | `rpt_market_metrics` (**filter `dimension='category'`**) · `dimension_label` · `disclosed_rate` · bar (nếu không filter sẽ trộn cả source/seniority/category) |
| Khoảng lương | `rpt_jobs` (`has_salary_range=true`) · `seniority` · AVG(`salary_min_trieu`), AVG(`salary_max_trieu`) · bar *(luôn all_time)* |

Ghi chú trên trang: median = NULL khi `salary_sample_count < k` (k-anonymity).

### Trang 4 — Pipeline ELT
| Chart | Chi tiết |
|---|---|
| Scorecards batch hiện tại | `rpt_current_pipeline_batch` · — · `silver_rows`, `quarantined_rows`, `quarantine_rate`, `mapping_version`, `salary_fx_version`, `gold_rows` (nhãn "aggregate rows") |
| Funnel qua batch | `rpt_pipeline_batches`\@5m · `published_at` · stacked bars `silver_rows`+`quarantined_rows` + line tham chiếu `source_jobs_total` |
| Quarantine rate | `rpt_pipeline_batches` · `published_at` · `quarantine_rate` (line) trên nền số row (bar) |
| Nguồn theo batch | `rpt_pipeline_batches` · `published_at` · `topdev_source_jobs` vs `vietnamworks_source_jobs` · stacked bar |
| gold_rows | `rpt_pipeline_batches` · `published_at` · `gold_rows` · line riêng |
| Lineage | `rpt_pipeline_batches` · table (`batch_id`, `published_at`, `as_of_date`, counts, `dagster_run_id`, `crawl_batch_id`) |

### Trang 5 — Chất lượng dữ liệu (`rpt_quarantine`)
Caveat trên trang: chỉ phản ánh quarantine của **batch publish thành công**; run fail quality-gate không xuất hiện (cần Dagster run events — out of scope).
| Chart | Chi tiết |
|---|---|
| Theo lý do | `reason_code` · COUNT · bar |
| Theo stage | `stage` · COUNT · bar |
| Theo nguồn | `source` · COUNT · donut |
| Theo batch | `published_at` · COUNT · time series |
| Chi tiết | `reason_detail_sanitized` gần đây · table |

### Trang 6 — API serving (sau 86+87; freshness 5m)
| Chart | Chi tiết |
|---|---|
| Request count | `rpt_api_requests` · `event_ts` · COUNT · time series |
| Latency p50/p95 | `rpt_api_latency_hourly` · `hour_ts` · `p50_latency_ms`, `p95_latency_ms` · time series |
| Status code | `rpt_api_requests` · `event_ts` × **`status`** · COUNT · stacked bar (breakdown theo mã cụ thể để thấy rõ **429**/**504**; `status_class` chỉ dùng làm filter/nhóm tổng quát) |
| Cache hit/miss | `rpt_api_cache_events` · `cache` · COUNT · donut |
| BQ bytes billed | `rpt_api_bq_queries_daily` · `day` · `total_gib_billed` · time series (cost-control) |
| Top endpoint | `rpt_api_requests` · `path` · COUNT + AVG(`latency_ms`) · table |
| 429 theo client | `rpt_api_429_by_client` · `client_id` · `rate_limited_count` · bar |

## 3. Tham chiếu view (tên cột chính)

- **rpt_overview_kpis** (1 dòng): job_count, source_count, company_count, salary_disclosed_count, salary_sample_count, salary_disclosed_rate, median_salary_vnd_month, median_salary_trieu, as_of_date.
- **rpt_current_batch** / **rpt_current_pipeline_batch** (1 dòng): batch_id, as_of_date, published_at, *_source_jobs, silver_rows, gold_rows, quarantined_rows, quarantine_rate, mapping_version, salary_fx_version, dagster_run_id, crawl_batch_id.
- **rpt_jobs** (1 dòng/job): job_id, source, title, company_name, location_text, seniority, salary_*_trieu, salary_*_vnd_month, has_salary_disclosed, has_salary_range, effective_posted_date, deadline_date, source_url, categories.
- **rpt_jobs_by_category** (1 dòng/job×category): + category_key, category_name, category_label.
- **rpt_category_dict** (1 dòng/key): category_key, source, category_name, dimension_label.
- **rpt_market_metrics**: window, dimension, dimension_value, dimension_label, taxonomy_source, posting_count, salary_disclosed_count, salary_sample_count, disclosed_rate, median_salary_vnd_month (đã che k-anon), median_salary_trieu.
- **rpt_pipeline_batches**: (warehouse_batches) + ingest_accounted_rows, quarantine_rate.
- **rpt_quarantine**: batch_id, source, external_id, stage, reason_code, reason_detail_sanitized, created_at, published_at, as_of_date.
- **rpt_api_***: xem `views_api.sql`.

## 4. Verification (bq query)

```sql
-- rpt_jobs khớp batch đang serve (bằng CHÍNH XÁC)
SELECT (SELECT COUNT(*) FROM `PROJECT.jobs_reporting.rpt_jobs`) AS jobs,
       (SELECT silver_rows FROM `PROJECT.jobs_reporting.rpt_current_batch`) AS silver_rows;
-- LEFT JOIN category không mất job
SELECT (SELECT COUNT(DISTINCT job_id) FROM `PROJECT.jobs_reporting.rpt_jobs_by_category`) AS by_cat,
       (SELECT COUNT(*) FROM `PROJECT.jobs_reporting.rpt_jobs`) AS jobs;
-- config 1 dòng, k khớp API
SELECT metrics_min_sample_size FROM `PROJECT.jobs_reporting.rpt_reporting_config`;
-- invariant
SELECT COUNTIF(seniority IS NULL) AS null_seniority FROM `PROJECT.jobs_reporting.rpt_jobs`;          -- = 0
SELECT COUNT(*) AS bad_keys FROM (                                                                    -- = 0
  SELECT category_key FROM `PROJECT.jobs_reporting.rpt_jobs_by_category`
  GROUP BY category_key HAVING COUNT(DISTINCT source) > 1 OR COUNT(DISTINCT category_name) > 1);
```

## 5. Caveats

- **Skills** chưa có trong serving → không có chart skills.
- **location_text** chưa parse tỉnh/thành → bản đồ địa lý để phase sau.
- **Category taxonomy chưa canonical** giữa TopDev/VietnamWorks → nhãn luôn gồm nguồn ("TopDev — Backend"); không gọi "toàn thị trường".
- **API metrics** chỉ có **từ lúc bật sink** (không hồi tố).
- **Median gold** = median của midpoint `(min+max)/2`; đã che k-anon trong view (khớp API).
