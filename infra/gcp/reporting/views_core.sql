-- =============================================================================
-- Reporting views (CORE) cho Looker Studio — dataset jobs_reporting.
-- Render + chạy bởi infra/gcp/85-reporting-views.sh (KHÔNG chạy trực tiếp file này).
--
-- Placeholder (script thay bằng sed):
--   __PROJECT__   → PROJECT_ID
--   __DS_PROD__   → DATASET_PROD  (nguồn: silver_jobs, gold_market_metrics, warehouse_*)
--   __DS_RPT__    → DATASET_REPORTING (đích: các view rpt_*)
--   (placeholder k) → METRICS_MIN_SAMPLE_SIZE (k-anon) — literal chỉ đặt Ở rpt_reporting_config, ĐÚNG 1 LẦN.
--
-- Thứ tự CREATE theo dependency (view tham chiếu view khác phải tạo trước).
-- Tất cả tên bảng/ view đều fully-qualified `project.dataset.name`.
-- =============================================================================

-- 0) Config dùng chung — 1 view 1 dòng. Placeholder k CHỈ ở đây → 2 view dưới không thể lệch threshold.
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_reporting_config` AS
SELECT __K__ AS metrics_min_sample_size;

-- 1) Batch đang serve = pointer (warehouse_state) × catalog (warehouse_batches).
--    0 dòng lúc bootstrap (chưa publish batch nào).
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_current_batch` AS
SELECT
  b.batch_id,
  b.as_of_date,
  b.data_as_of_at,
  b.published_at,
  b.source_jobs_total,
  b.topdev_source_jobs,
  b.vietnamworks_source_jobs,
  b.silver_rows,
  b.gold_rows,
  b.quarantined_rows,
  b.mapping_version,
  b.salary_fx_version,
  b.crawl_batch_id,
  b.dagster_run_id
FROM `__PROJECT__.__DS_PROD__.warehouse_state` AS s
JOIN `__PROJECT__.__DS_PROD__.warehouse_batches` AS b
  ON b.batch_id = s.published_batch_id
WHERE s.warehouse_name = 'serving';

-- 2) 1 dòng/job của batch đang serve.
--    Floor '0001-01-01' thoả require_partition_filter mà không bỏ sót dòng (khớp bigquery_writer).
--    seniority = COALESCE(...,'unknown') khớp serving layer + gold (tránh nhóm null tách khỏi unknown).
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_jobs` AS
SELECT
  job_id,
  source,
  external_id,
  source_url,
  title,
  company_name,
  location_text,
  COALESCE(seniority_normalized, 'unknown') AS seniority,
  seniority_normalized,
  experience_min_years,
  experience_max_years,
  salary_currency,
  salary_period,
  salary_min_vnd_month,
  salary_max_vnd_month,
  SAFE_DIVIDE(salary_min_vnd_month, 1e6) AS salary_min_trieu,
  SAFE_DIVIDE(salary_max_vnd_month, 1e6) AS salary_max_trieu,
  SAFE_DIVIDE(salary_min_vnd_month + salary_max_vnd_month, 2e6) AS salary_mid_trieu,
  (salary_min_vnd_month IS NOT NULL OR salary_max_vnd_month IS NOT NULL) AS has_salary_disclosed,
  (salary_min_vnd_month IS NOT NULL AND salary_max_vnd_month IS NOT NULL) AS has_salary_range,
  posted_at,
  effective_posted_date,
  deadline_date,
  batch_id,
  categories
FROM `__PROJECT__.__DS_PROD__.silver_jobs`
WHERE effective_posted_date >= DATE '0001-01-01'
  AND batch_id = (
    SELECT published_batch_id
    FROM `__PROJECT__.__DS_PROD__.warehouse_state`
    WHERE warehouse_name = 'serving'
  );

-- 3) KPI tổng quan — LUÔN 1 dòng.
--    counts trên toàn rpt_jobs (0 job → COUNT=0 vẫn 1 dòng); median = scalar subquery (0 mẫu → NULL);
--    as_of_date = scalar subquery từ rpt_current_batch (bootstrap → NULL, KHÔNG làm mất dòng).
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_overview_kpis` AS
WITH job_stats AS (
  SELECT
    COUNT(*)                                                    AS job_count,
    COUNT(DISTINCT source)                                      AS source_count,
    COUNT(DISTINCT company_name)                               AS company_count,
    COUNTIF(has_salary_disclosed)                             AS salary_disclosed_count,
    COUNTIF(has_salary_range)                                 AS salary_sample_count,
    SAFE_DIVIDE(COUNTIF(has_salary_disclosed), COUNT(*))      AS salary_disclosed_rate
  FROM `__PROJECT__.__DS_RPT__.rpt_jobs`
),
sample AS (
  SELECT PERCENTILE_CONT((salary_min_vnd_month + salary_max_vnd_month) / 2.0, 0.5) OVER () AS median_all
  FROM `__PROJECT__.__DS_RPT__.rpt_jobs`
  WHERE has_salary_range
  LIMIT 1
)
SELECT
  js.job_count,
  js.source_count,
  js.company_count,
  js.salary_disclosed_count,
  js.salary_sample_count,
  js.salary_disclosed_rate,
  IF(js.salary_sample_count < cfg.metrics_min_sample_size,
     NULL,
     (SELECT median_all FROM sample))                          AS median_salary_vnd_month,
  SAFE_DIVIDE(
    IF(js.salary_sample_count < cfg.metrics_min_sample_size, NULL, (SELECT median_all FROM sample)),
    1e6)                                                        AS median_salary_trieu,
  (SELECT as_of_date FROM `__PROJECT__.__DS_RPT__.rpt_current_batch` LIMIT 1) AS as_of_date
FROM job_stats AS js
CROSS JOIN `__PROJECT__.__DS_RPT__.rpt_reporting_config` AS cfg;

-- 4) 1 dòng/(job × category). LEFT JOIN UNNEST giữ job không category → bucket '<source>:unknown'
--    (khớp gold). category_label gồm nguồn để Looker không gộp 2 taxonomy trùng tên.
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_jobs_by_category` AS
SELECT
  j.job_id,
  j.source,
  j.seniority,
  j.company_name,
  j.title,
  j.source_url,
  j.effective_posted_date,
  j.deadline_date,
  j.salary_min_trieu,
  j.salary_max_trieu,
  j.has_salary_disclosed,
  j.has_salary_range,
  COALESCE(c.category_key, CONCAT(j.source, ':unknown')) AS category_key,
  COALESCE(c.category_name, 'Unknown')                  AS category_name,
  CONCAT(
    CASE j.source
      WHEN 'topdev' THEN 'TopDev'
      WHEN 'vietnamworks' THEN 'VietnamWorks'
      ELSE j.source
    END,
    ' — ', COALESCE(c.category_name, 'Unknown')
  )                                                       AS category_label
FROM `__PROJECT__.__DS_RPT__.rpt_jobs` AS j
LEFT JOIN UNNEST(j.categories) AS c;

-- 5) Từ điển key → nhãn (1 dòng/key). Xây từ rpt_jobs_by_category (tự có bucket unknown).
--    GROUP BY category_key, source: source phụ thuộc hàm vào key nên vẫn 1 dòng/key
--    (script verify COUNT(*) = COUNT(DISTINCT category_key) để bắt bất thường).
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_category_dict` AS
SELECT
  category_key,
  source,
  MIN(category_name) AS category_name,
  CONCAT(
    CASE source
      WHEN 'topdev' THEN 'TopDev'
      WHEN 'vietnamworks' THEN 'VietnamWorks'
      ELSE source
    END,
    ' — ', MIN(category_name)
  ) AS dimension_label
FROM `__PROJECT__.__DS_RPT__.rpt_jobs_by_category`
GROUP BY category_key, source;

-- 6) Gold market metrics của batch đang serve, đã che k-anon + gắn nhãn ngành.
--    k lấy từ rpt_reporting_config (CROSS JOIN) → không lệch với KPI.
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_market_metrics` AS
WITH masked AS (
  SELECT
    m.window,
    m.dimension,
    m.dimension_value,
    CASE
      WHEN m.dimension = 'category' THEN COALESCE(d.dimension_label, m.dimension_value)
      ELSE m.dimension_value
    END                                                          AS dimension_label,
    IF(m.dimension = 'category', SPLIT(m.dimension_value, ':')[SAFE_OFFSET(0)], NULL) AS taxonomy_source,
    m.posting_count,
    m.salary_disclosed_count,
    m.salary_sample_count,
    IF(m.salary_sample_count < cfg.metrics_min_sample_size, NULL, m.median_salary_vnd_month) AS median_salary_vnd_month,
    SAFE_DIVIDE(m.salary_disclosed_count, m.posting_count)       AS disclosed_rate
  FROM `__PROJECT__.__DS_PROD__.gold_market_metrics` AS m
  CROSS JOIN `__PROJECT__.__DS_RPT__.rpt_reporting_config` AS cfg
  LEFT JOIN `__PROJECT__.__DS_RPT__.rpt_category_dict` AS d
    ON d.category_key = m.dimension_value
   AND m.dimension = 'category'
  WHERE m.batch_id = (
    SELECT published_batch_id
    FROM `__PROJECT__.__DS_PROD__.warehouse_state`
    WHERE warehouse_name = 'serving'
  )
)
SELECT
  *,
  SAFE_DIVIDE(median_salary_vnd_month, 1e6) AS median_salary_trieu
FROM masked;

-- 7) Lịch sử batch (pipeline ELT). quarantine_rate = SAFE_DIVIDE; gold_rows giữ riêng (KHÔNG vào funnel).
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_pipeline_batches` AS
SELECT
  batch_id,
  crawl_batch_id,
  dagster_run_id,
  data_as_of_at,
  as_of_date,
  published_at,
  source_jobs_total,
  topdev_source_jobs,
  vietnamworks_source_jobs,
  silver_rows,
  gold_rows,
  quarantined_rows,
  (silver_rows + quarantined_rows)                       AS ingest_accounted_rows,  -- phải == source_jobs_total
  SAFE_DIVIDE(quarantined_rows, source_jobs_total)       AS quarantine_rate,
  mapping_version,
  salary_fx_version
FROM `__PROJECT__.__DS_PROD__.warehouse_batches`;

-- 8) Batch đang serve (1 dòng) cho scorecard Trang 4 — theo POINTER, KHÔNG phải latest-by-timestamp.
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_current_pipeline_batch` AS
SELECT
  b.batch_id,
  b.published_at,
  b.as_of_date,
  b.source_jobs_total,
  b.topdev_source_jobs,
  b.vietnamworks_source_jobs,
  b.silver_rows,
  b.gold_rows,
  b.quarantined_rows,
  SAFE_DIVIDE(b.quarantined_rows, b.source_jobs_total)   AS quarantine_rate,
  b.mapping_version,
  b.salary_fx_version,
  b.dagster_run_id,
  b.crawl_batch_id
FROM `__PROJECT__.__DS_PROD__.warehouse_batches` AS b
JOIN `__PROJECT__.__DS_PROD__.warehouse_state` AS s
  ON s.published_batch_id = b.batch_id
WHERE s.warehouse_name = 'serving';

-- 9) Data quality — bản ghi bị loại, gắn published_at của batch (chỉ batch publish THÀNH CÔNG).
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_quarantine` AS
SELECT
  q.batch_id,
  q.source,
  q.external_id,
  q.stage,
  q.reason_code,
  q.reason_detail_sanitized,
  q.created_at,
  b.published_at,
  b.as_of_date
FROM `__PROJECT__.__DS_PROD__.warehouse_quarantine` AS q
LEFT JOIN `__PROJECT__.__DS_PROD__.warehouse_batches` AS b
  ON b.batch_id = q.batch_id;
