"""Map row (dict/Row) → DTO + dựng cursor — THUẦN, dùng CHUNG cho BigQuery & DuckDB.

Cả hai backend SELECT cùng bộ cột (alias `url`←source_url, `seniority` đã COALESCE, categories
là list struct 3 field) nên map GIỐNG HỆT → JobItem/MetricRow parity. Tách khỏi *_jobs.py để
DuckDB khỏi kéo google-cloud-bigquery. cursor_sort_value ở bigquery_read_sql (thuần) — dùng lại.
Xem docs/adr/ADR-020, ADR-026.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from app.domain.ports.metrics_repository import MetricRow
from app.infrastructure.warehouse.bigquery_read_sql import cursor_sort_value
from app.models.enums import SortOption
from app.models.jobs import CategoryItem, JobItem


def to_job_item(row: Mapping[str, object]) -> JobItem:
    """Một Row/dict (cột theo SELECT) → JobItem. Map TƯỜNG MINH → cột lạ không lọt ra
    (PII chặn ở SELECT allowlist cố định)."""
    categories = [
        CategoryItem(
            category_key=c["category_key"],
            category_name=c["category_name"],
            category_path=c["category_path"],
        )
        for c in row["categories"]
    ]
    return JobItem(
        job_id=row["job_id"],
        source=row["source"],
        external_id=row["external_id"],
        title=row["title"],
        company_name=row["company_name"],
        location_text=row["location_text"],
        seniority=row["seniority"],           # đã COALESCE →'unknown' trong SQL
        experience_min_years=row["experience_min_years"],
        experience_max_years=row["experience_max_years"],
        salary_min_vnd_month=row["salary_min_vnd_month"],
        salary_max_vnd_month=row["salary_max_vnd_month"],
        salary_currency=row["salary_currency"],
        salary_period=row["salary_period"],
        categories=categories,
        posted_at=row["posted_at"],
        effective_posted_date=row["effective_posted_date"],
        deadline_date=row["deadline_date"],
        url=row["url"],                        # alias source_url
    )


def to_metric_row(row: Mapping[str, object]) -> MetricRow:
    """Một Row/dict gold → MetricRow THÔ (chưa che median — k-anon áp ở handler)."""
    return MetricRow(
        dimension_value=row["dimension_value"],
        posting_count=row["posting_count"],
        salary_disclosed_count=row["salary_disclosed_count"],
        salary_sample_count=row["salary_sample_count"],
        median_salary_vnd_month=row["median_salary_vnd_month"],
    )


def build_next_cursor(
    sort: SortOption, batch_id: str, as_of: datetime, last_row: Mapping[str, object],
) -> dict:
    """Cursor trang sau: gắn batch (bất biến) + as_of snapshot + vị trí keyset (sort, job_id).

    as_of snapshot vì batch bất biến → trang 2+ khỏi query lại metadata. last_sort đã COALESCE
    khớp ORDER BY; date → ISO khi encode token (json default=str).
    """
    return {
        "batch_id": batch_id,
        "as_of": as_of.isoformat(),
        "last_sort": cursor_sort_value(sort, last_row),
        "last_job_id": last_row["job_id"],
    }
