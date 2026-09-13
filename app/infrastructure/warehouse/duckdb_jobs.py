"""Adapter: đọc tin tuyển dụng từ DuckDB (hiện thực JobRepository) — schema silver MỚI.

DuckDB = backend 'dev' + LƯỚI PARITY chạy-được cho ngữ nghĩa SQL Phase 3: thực thi CÙNG
filter/sort/keyset/EXISTS/limit+1 như BigQuery, ở local, phải cho kết quả GIỐNG fake
(test_duckdb_repo). SQL dialect DuckDB ở duckdb_read_sql.py; map Row→JobItem + cursor dùng
CHUNG với BQ ở read_mapping.py. Đọc theo BATCH như BQ. Xem docs/adr/ADR-003, ADR-020.

Khôi phục ở Phase 4 (đã tạm tắt từ Phase 0 khi schema silver đổi sang BigQuery).
"""
from __future__ import annotations

from datetime import datetime

import duckdb

from app.domain.ports.job_repository import JobRepository, SearchResult
from app.infrastructure.warehouse.duckdb_exec import DuckDBReader
from app.infrastructure.warehouse.duckdb_read_sql import build_search_sql
from app.infrastructure.warehouse.read_mapping import build_next_cursor, to_job_item
from app.models.jobs import SearchRequest


class DuckDBJobRepository(JobRepository):
    """Đọc silver_jobs từ file DuckDB. Handler chỉ thấy interface JobRepository (Depends)."""

    def __init__(
        self,
        path: str,
        *,
        query_timeout_s: int = 30,
        connection: duckdb.DuckDBPyConnection | None = None,
        reader: DuckDBReader | None = None,
    ):
        self._reader = reader or DuckDBReader(
            path, query_timeout_s=query_timeout_s, connection=connection,
        )

    def as_of(self) -> datetime:
        return self._reader.current_batch()[1]

    def search(self, req: SearchRequest, cursor: dict | None) -> SearchResult:
        if cursor:
            batch_id = cursor["batch_id"]
            as_of = datetime.fromisoformat(cursor["as_of"])   # snapshot từ trang 1
            last_sort = cursor.get("last_sort")
            last_job_id = cursor.get("last_job_id")
        else:
            batch_id, as_of = self._reader.current_batch()
            last_sort = last_job_id = None

        sql, params = build_search_sql(
            req, batch_id=batch_id, last_sort=last_sort, last_job_id=last_job_id,
        )
        rows = self._reader.run(sql, params)

        # limit+1: có dòng thứ (limit+1) ⇒ còn trang sau. Cắt về đúng limit.
        has_next = len(rows) > req.limit
        page_rows = rows[: req.limit]
        items = [to_job_item(r) for r in page_rows]
        next_cursor = (
            build_next_cursor(req.sort, batch_id, as_of, page_rows[-1]) if has_next else None
        )
        # total_estimated=None: mirror BQ (không COUNT(*) mỗi request).
        return SearchResult(items=items, next_cursor=next_cursor, total_estimated=None, as_of=as_of)
