"""Adapter: đọc gold table chỉ số thị trường từ DuckDB (hiện thực MetricsRepository).

Trước refactor: một phần của app/warehouse/metrics.py. Xem docs/adr/ADR-011.
"""
from __future__ import annotations

from datetime import UTC

import duckdb

from app.domain.ports.metrics_repository import MetricRow, MetricsRepository
from app.infrastructure.warehouse._duckdb_timeout import execute_with_timeout  # ★ TUẦN 7


class DuckDBMetricsRepository(MetricsRepository):
    # Chỉ SELECT cột cần (không SELECT *). 'dimension' là GIÁ TRỊ → đi qua tham số.
    _SQL = (
        "SELECT dimension_value, median_salary, posting_count, as_of "
        "FROM gold_market_metrics WHERE dimension = $dimension "
        "ORDER BY dimension_value"
    )

    def __init__(self, path: str, query_timeout_s: int = 30):   # ★ TUẦN 7: trần thời gian truy vấn
        self._con = duckdb.connect(path, read_only=True)   # API chỉ đọc kho
        self._timeout_s = query_timeout_s

    def market_metrics(self, dimension: str) -> list[MetricRow]:
        cur = self._con.cursor()
        # ★ TUẦN 7: chạy có trần thời gian (interrupt khi quá hạn -> QueryTimeoutError 504).
        _desc, fetched = execute_with_timeout(cur, self._SQL, {"dimension": dimension}, self._timeout_s)
        rows = []
        for dv, ms, pc, ao in fetched:
            # gold lưu as_of dạng naive; gắn UTC để response nhất quán với các endpoint khác.
            if ao is not None and ao.tzinfo is None:
                ao = ao.replace(tzinfo=UTC)
            rows.append(MetricRow(dv, ms, pc, ao))
        return rows
