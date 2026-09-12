"""Adapter: đọc tin tuyển dụng THẬT từ BigQuery (hiện thực JobRepository).

Tầng I/O — DUY NHẤT chạm google-cloud-bigquery ở phía đọc jobs. Dựng SQL + params ở
bigquery_read_sql.py (thuần, test được); ở đây: ADC client, execute có trần thời gian,
map Row → JobItem, phân trang keyset gắn batch. Xem docs/adr/ADR-020.

Đọc theo BATCH (invariant 1/3/4):
  - Trang 1 (không cursor): phân giải batch ĐANG publish + data_as_of_at từ
    warehouse_state × warehouse_batches. Chưa có batch → 503.
  - Trang 2+ (có cursor): batch_id + as_of lấy TỪ cursor (đã snapshot) — page 2 của
    batch A vẫn đọc batch A dù batch B vừa publish; KHÔNG query lại metadata.

An toàn/chi phí: identifier từ allowlist + backtick (read_sql); giá trị qua @param;
maximum_bytes_billed; timeout → cancel job → 504; total_estimated=None (không COUNT(*)).
Không unit-test phần I/O (cần BQ) → integration RUN_BQ_INTEGRATION=1 + dataset _test (Phase 4).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from google.cloud import bigquery

from app.domain.ports.job_repository import JobRepository, SearchResult
from app.errors import UpstreamUnavailableError
from app.infrastructure.warehouse.bigquery_exec import BigQueryExecutor
from app.infrastructure.warehouse.bigquery_read_sql import (
    ReadTarget,
    build_current_batch_meta_sql,
    build_search_sql,
    cursor_sort_value,
)
from app.models.enums import SortOption
from app.models.jobs import CategoryItem, JobItem, SearchRequest


def to_job_item(row: Mapping[str, object]) -> JobItem:
    """Một Row/dict (cột theo _SELECT) → JobItem. extra='forbid' → cột lạ (PII) sẽ NÉM LỖI."""
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


def build_next_cursor(
    sort: SortOption, batch_id: str, as_of: datetime, last_row: Mapping[str, object],
) -> dict:
    """Cursor trang sau: gắn batch (bất biến) + as_of snapshot + vị trí keyset (sort, job_id).

    as_of snapshot vì batch bất biến → trang 2+ khỏi query lại warehouse_batches.
    last_sort đã COALESCE khớp ORDER BY; date → ISO khi encode token (json default=str).
    """
    return {
        "batch_id": batch_id,
        "as_of": as_of.isoformat(),
        "last_sort": cursor_sort_value(sort, last_row),
        "last_job_id": last_row["job_id"],
    }


class BigQueryJobRepository(JobRepository):
    """Đọc silver_jobs từ BigQuery. Handler chỉ thấy interface JobRepository (Depends)."""

    def __init__(
        self,
        target: ReadTarget,
        *,
        query_timeout_s: int = 10,
        client: bigquery.Client | None = None,
        executor: BigQueryExecutor | None = None,
    ):
        self._exec = executor or BigQueryExecutor(
            target, query_timeout_s=query_timeout_s, client=client,
        )

    def _current_batch(self) -> tuple[str, datetime]:
        """(batch_id, data_as_of_at) của batch đang publish. Chưa có → 503 (không phải rỗng 200)."""
        rows = self._exec.run(build_current_batch_meta_sql(self._exec.target), op="batch_meta")
        if not rows:
            raise UpstreamUnavailableError("Chưa có batch nào được publish trong kho")
        return rows[0]["batch_id"], rows[0]["data_as_of_at"]

    def as_of(self) -> datetime:
        return self._current_batch()[1]

    def search(self, req: SearchRequest, cursor: dict | None) -> SearchResult:
        if cursor:
            batch_id = cursor["batch_id"]
            as_of = datetime.fromisoformat(cursor["as_of"])   # snapshot từ trang 1
            last_sort = cursor.get("last_sort")
            last_job_id = cursor.get("last_job_id")
        else:
            batch_id, as_of = self._current_batch()
            last_sort = last_job_id = None

        sp = build_search_sql(
            self._exec.target, req, batch_id=batch_id, last_sort=last_sort, last_job_id=last_job_id,
        )
        rows = self._exec.run(sp, op="search")

        # limit+1: có dòng thứ (limit+1) ⇒ còn trang sau. Cắt về đúng limit.
        has_next = len(rows) > req.limit
        page_rows = rows[: req.limit]
        items = [to_job_item(r) for r in page_rows]
        next_cursor = (
            build_next_cursor(req.sort, batch_id, as_of, page_rows[-1]) if has_next else None
        )
        # total_estimated=None: không COUNT(*) mỗi request (tốn byte); đừng dùng để phân trang.
        return SearchResult(items=items, next_cursor=next_cursor, total_estimated=None, as_of=as_of)
