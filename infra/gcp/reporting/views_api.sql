-- =============================================================================
-- Reporting views (API metrics) cho Looker Studio — dataset jobs_reporting.
-- Nguồn: bảng log của Cloud Logging sink (jobs_prod_logs.run_googleapis_com_stdout).
-- Render + chạy bởi infra/gcp/87-api-reporting-views.sh (SAU khi bảng log đã có).
--
-- Placeholder:
--   __PROJECT__          → PROJECT_ID
--   __DS_LOGS__          → DATASET_LOGS (jobs_prod_logs)
--   __DS_RPT__           → DATASET_REPORTING (jobs_reporting)
--   __LOG_LOOKBACK_DAYS__ → API_REPORT_LOOKBACK_DAYS (cửa sổ hiển thị; ≤ retention của log)
--
-- Ghi chú:
--  - Dùng cột `timestamp` NATIVE của Cloud Logging làm event time + partition prune (KHÔNG parse jsonPayload.ts).
--  - Giới hạn __LOG_LOOKBACK_DAYS__ ngày để prune partition.
--  - jsonPayload numeric → FLOAT64 → CAST khi cần INT64 (status, bytes billed).
--  - App ghi 3 loại event riêng: http_request / market_metrics / bq_query(+bq_query_timeout).
-- =============================================================================

-- 1) Access log per-request (http_request). 1 dòng / request.
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_api_requests` AS
SELECT
  timestamp                                   AS event_ts,
  jsonPayload.method                          AS method,
  jsonPayload.path                            AS path,
  CAST(jsonPayload.status AS INT64)           AS status,
  CAST(jsonPayload.status AS INT64) DIV 100   AS status_class,   -- 2/4/5
  jsonPayload.latency_ms                      AS latency_ms,
  jsonPayload.client_id                       AS client_id,
  jsonPayload.request_id                      AS request_id
FROM `__PROJECT__.__DS_LOGS__.run_googleapis_com_stdout`
WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL __LOG_LOOKBACK_DAYS__ DAY)
  AND jsonPayload.message = 'http_request';

-- 2) Latency p50/p95 theo giờ (APPROX_QUANTILES precompute). Verify p95 theo tolerance.
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_api_latency_hourly` AS
SELECT
  TIMESTAMP_TRUNC(timestamp, HOUR)                                   AS hour_ts,
  COUNT(*)                                                           AS request_count,
  APPROX_QUANTILES(jsonPayload.latency_ms, 100)[OFFSET(50)]          AS p50_latency_ms,
  APPROX_QUANTILES(jsonPayload.latency_ms, 100)[OFFSET(95)]          AS p95_latency_ms,
  MAX(jsonPayload.latency_ms)                                        AS max_latency_ms
FROM `__PROJECT__.__DS_LOGS__.run_googleapis_com_stdout`
WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL __LOG_LOOKBACK_DAYS__ DAY)
  AND jsonPayload.message = 'http_request'
GROUP BY hour_ts;

-- 3) Cache hit/miss của /market/metrics (market_metrics event).
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_api_cache_events` AS
SELECT
  timestamp                       AS event_ts,
  jsonPayload.dimension           AS dimension,
  jsonPayload.window              AS window,
  jsonPayload.cache               AS cache,        -- 'hit' | 'miss'
  CAST(jsonPayload.groups AS INT64) AS groups,
  jsonPayload.client_id           AS client_id
FROM `__PROJECT__.__DS_LOGS__.run_googleapis_com_stdout`
WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL __LOG_LOOKBACK_DAYS__ DAY)
  AND jsonPayload.message = 'market_metrics';

-- 4) BigQuery cost/latency tầng đọc theo NGÀY (bq_query event) — cost-control.
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_api_bq_queries_daily` AS
SELECT
  DATE(timestamp)                                                    AS day,
  COUNT(*)                                                           AS query_count,
  SUM(CAST(jsonPayload.total_bytes_billed AS INT64))                 AS total_bytes_billed,
  SAFE_DIVIDE(SUM(CAST(jsonPayload.total_bytes_billed AS INT64)), POW(1024, 3)) AS total_gib_billed,
  COUNTIF(jsonPayload.cache_hit)                                     AS bq_cache_hits,
  SAFE_DIVIDE(COUNTIF(jsonPayload.cache_hit), COUNT(*))              AS bq_cache_hit_rate,
  APPROX_QUANTILES(jsonPayload.elapsed_ms, 100)[OFFSET(95)]          AS p95_query_ms
FROM `__PROJECT__.__DS_LOGS__.run_googleapis_com_stdout`
WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL __LOG_LOOKBACK_DAYS__ DAY)
  AND jsonPayload.message = 'bq_query'
GROUP BY day;

-- 5) 429 (rate limit) theo client_id — khả thi nhờ auth.py gán client_id trước limiter.
CREATE OR REPLACE VIEW `__PROJECT__.__DS_RPT__.rpt_api_429_by_client` AS
SELECT
  jsonPayload.client_id           AS client_id,
  COUNT(*)                        AS rate_limited_count,
  MIN(timestamp)                  AS first_seen,
  MAX(timestamp)                  AS last_seen
FROM `__PROJECT__.__DS_LOGS__.run_googleapis_com_stdout`
WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL __LOG_LOOKBACK_DAYS__ DAY)
  AND jsonPayload.message = 'http_request'
  AND CAST(jsonPayload.status AS INT64) = 429
GROUP BY client_id;
