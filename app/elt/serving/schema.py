"""Schema BigQuery cho warehouse serving — khai báo thuần (không import bigquery).

Loader (2.6d) convert `Field` → `bigquery.SchemaField`. Tách khai báo khỏi client để
test được mà không cần cài google-cloud-bigquery, và để drift-test khớp với dataclass
(`SilverRow`/`GoldMetricRow`/`QuarantineRecord`) bắt lệch schema sớm.

Hai biến thể mỗi bảng dữ liệu:
  - bảng PUBLISHED (silver_jobs, gold_market_metrics): ELT APPEND, bất biến theo batch_id,
    partition + cluster + require_partition_filter (serving).
  - bảng CANDIDATE (*_candidate): work table, WRITE_TRUNCATE mỗi lần load, KHÔNG
    require_partition_filter (để verify/đọc tự do trước khi APPEND sang published).
Bảng metadata: warehouse_state (pointer singleton), warehouse_batches (catalog bất biến),
warehouse_quarantine.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    mode: str = "NULLABLE"
    fields: tuple[Field, ...] = ()  # cho RECORD


def field_names(fields: tuple[Field, ...]) -> list[str]:
    """Tên field cấp 1 (cho drift-test khớp to_bq_row())."""
    return [f.name for f in fields]


# --- tên bảng ---
TABLE_SILVER = "silver_jobs"
TABLE_SILVER_CANDIDATE = "silver_jobs_candidate"
TABLE_GOLD = "gold_market_metrics"
TABLE_GOLD_CANDIDATE = "gold_market_metrics_candidate"
TABLE_STATE = "warehouse_state"
TABLE_BATCHES = "warehouse_batches"
TABLE_QUARANTINE = "warehouse_quarantine"
TABLE_QUARANTINE_CANDIDATE = "warehouse_quarantine_candidate"

# --- partition / cluster (serving) ---
SILVER_PARTITION_FIELD = "effective_posted_date"   # DATE → partition prune theo posted_after
SILVER_CLUSTER = ("batch_id", "source", "seniority_normalized", "job_id")
SILVER_REQUIRE_PARTITION_FILTER = True             # published: buộc filter (kiểm soát chi phí)
GOLD_CLUSTER = ("batch_id", "dimension", "window")

# --- warehouse_state: tên cố định của pointer singleton (CAS theo cột này) ---
WAREHOUSE_STATE_NAME = "serving"


_CATEGORY_FIELDS = (
    Field("category_key", "STRING", "REQUIRED"),
    Field("category_name", "STRING", "REQUIRED"),
    Field("category_code", "STRING"),
    Field("category_path", "STRING"),
    Field("level1_id", "STRING"),
    Field("level2_id", "STRING"),
    Field("level3_id", "STRING"),
)

SILVER_FIELDS: tuple[Field, ...] = (
    Field("job_id", "STRING", "REQUIRED"),
    Field("source", "STRING", "REQUIRED"),
    Field("external_id", "STRING", "REQUIRED"),
    Field("source_url", "STRING"),
    Field("detail_status", "STRING", "REQUIRED"),
    Field("title", "STRING", "REQUIRED"),
    Field("company_name", "STRING"),
    Field("location_text", "STRING"),
    Field("seniority_raw", "STRING"),
    Field("seniority_normalized", "STRING"),
    Field("seniority_mapping_version", "STRING"),
    Field("experience_raw", "STRING"),
    Field("experience_min_years", "FLOAT64"),
    Field("experience_max_years", "FLOAT64"),
    Field("experience_parse_status", "STRING", "REQUIRED"),
    Field("salary_raw", "STRING"),
    Field("salary_min_original", "FLOAT64"),
    Field("salary_max_original", "FLOAT64"),
    Field("salary_currency", "STRING"),
    Field("salary_period", "STRING"),
    Field("salary_min_vnd_month", "INT64"),
    Field("salary_max_vnd_month", "INT64"),
    Field("fx_rate_to_vnd", "FLOAT64"),
    Field("salary_fx_version", "STRING"),
    Field("salary_normalization_status", "STRING", "REQUIRED"),
    Field("posted_at", "TIMESTAMP"),
    Field("effective_posted_date", "DATE", "REQUIRED"),
    Field("deadline_date", "DATE"),
    Field("posted_date_parse_status", "STRING", "REQUIRED"),
    Field("deadline_date_parse_status", "STRING", "REQUIRED"),
    Field("first_seen_at", "TIMESTAMP", "REQUIRED"),
    Field("last_seen_at", "TIMESTAMP"),
    Field("batch_id", "STRING", "REQUIRED"),
    Field("categories", "RECORD", "REPEATED", _CATEGORY_FIELDS),
)

GOLD_FIELDS: tuple[Field, ...] = (
    Field("batch_id", "STRING", "REQUIRED"),
    Field("window", "STRING", "REQUIRED"),
    Field("dimension", "STRING", "REQUIRED"),
    Field("dimension_value", "STRING", "REQUIRED"),
    Field("posting_count", "INT64", "REQUIRED"),
    Field("salary_disclosed_count", "INT64", "REQUIRED"),
    Field("salary_sample_count", "INT64", "REQUIRED"),
    Field("median_salary_vnd_month", "FLOAT64"),
)

# pointer singleton: 1 dòng; CAS WHERE published_batch_id=@expected_prev.
WAREHOUSE_STATE_FIELDS: tuple[Field, ...] = (
    Field("warehouse_name", "STRING", "REQUIRED"),
    Field("published_batch_id", "STRING"),            # NULL lúc bootstrap
    Field("updated_at", "TIMESTAMP"),
)

# catalog BẤT BIẾN: có mặt ở đây = đã publish (state machine dựa vào điều này).
WAREHOUSE_BATCHES_FIELDS: tuple[Field, ...] = (
    Field("batch_id", "STRING", "REQUIRED"),
    Field("crawl_batch_id", "STRING"),                # lineage
    Field("dagster_run_id", "STRING"),                # lineage
    Field("data_as_of_at", "TIMESTAMP", "REQUIRED"),  # as_of phục vụ API
    Field("as_of_date", "DATE", "REQUIRED"),          # neo window 90d
    Field("published_at", "TIMESTAMP", "REQUIRED"),   # lúc flip pointer
    Field("source_jobs_total", "INT64", "REQUIRED"),
    Field("topdev_source_jobs", "INT64", "REQUIRED"),
    Field("vietnamworks_source_jobs", "INT64", "REQUIRED"),
    Field("silver_rows", "INT64", "REQUIRED"),
    Field("gold_rows", "INT64", "REQUIRED"),
    Field("quarantined_rows", "INT64", "REQUIRED"),
    Field("mapping_version", "STRING"),
    Field("salary_fx_version", "STRING"),
)

QUARANTINE_FIELDS: tuple[Field, ...] = (
    Field("batch_id", "STRING", "REQUIRED"),
    Field("source", "STRING", "REQUIRED"),
    Field("external_id", "STRING"),
    Field("stage", "STRING", "REQUIRED"),
    Field("reason_code", "STRING", "REQUIRED"),
    Field("reason_detail_sanitized", "STRING"),
    Field("created_at", "TIMESTAMP", "REQUIRED"),
)
