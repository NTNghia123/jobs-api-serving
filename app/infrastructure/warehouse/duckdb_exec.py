"""Reader DuckDB dùng chung cho read adapter (jobs + metrics) — mirror BigQueryExecutor.

Giữ connection read-only + trần thời gian; chạy SQL qua execute_with_timeout (interrupt khi
quá hạn → 504). Phân giải batch đang publish (JOIN state × batches) như BQ. Xem docs/adr/ADR-020.
"""
from __future__ import annotations

from datetime import UTC, datetime

import duckdb

from app.errors import UpstreamUnavailableError
from app.infrastructure.warehouse._duckdb_timeout import execute_with_timeout
from app.infrastructure.warehouse.duckdb_read_sql import build_current_batch_meta_sql


class DuckDBReader:
    """API mở kho read_only=True — không thể ghi (least privilege ở tầng kết nối)."""

    def __init__(
        self,
        path: str,
        *,
        query_timeout_s: int = 30,
        connection: duckdb.DuckDBPyConnection | None = None,
    ):
        # connection tiêm vào cho test (mở sẵn trên file fixture); prod mở read_only từ path.
        self._con = connection or duckdb.connect(path, read_only=True)
        self._timeout_s = query_timeout_s

    def run(self, sql: str, params: dict) -> list[dict]:
        cur = self._con.cursor()   # cursor riêng mỗi request (interrupt an toàn khi chạy song song)
        desc, rows = execute_with_timeout(cur, sql, params, self._timeout_s)
        cols = [d[0] for d in desc]
        return [dict(zip(cols, r, strict=True)) for r in rows]

    def current_batch(self) -> tuple[str, datetime]:
        """(batch_id, data_as_of_at) của batch đang publish. Chưa có → 503."""
        sql, params = build_current_batch_meta_sql()
        rows = self.run(sql, params)
        if not rows:
            raise UpstreamUnavailableError("Chưa có batch nào được publish trong kho")
        as_of = rows[0]["data_as_of_at"]
        if as_of is not None and as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)   # DuckDB TIMESTAMP naive → gắn UTC (nhất quán BQ)
        return rows[0]["batch_id"], as_of
