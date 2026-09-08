"""Adapter: đọc gold table chỉ số thị trường từ DuckDB (hiện thực MetricsRepository).

Trước refactor: một phần của app/warehouse/metrics.py. Xem docs/adr/ADR-011.
"""
from __future__ import annotations

from datetime import timezone

import duckdb

from app.domain.ports.metrics_repository import MetricRow, MetricsRepository


class DuckDBMetricsRepository(MetricsRepository):
    # Chỉ SELECT cột cần (không SELECT *). 'dimension' là GIÁ TRỊ → đi qua tham số.
    _SQL = (
        "SELECT dimension_value, median_salary, posting_count, as_of "
        "FROM gold_market_metrics WHERE dimension = $dimension "
        "ORDER BY dimension_value"
    )

    def __init__(self, path: str):
        self._con = duckdb.connect(path, read_only=True)   # API chỉ đọc kho

    def market_metrics(self, dimension: str) -> list[MetricRow]:
        cur = self._con.cursor()
        cur.execute(self._SQL, {"dimension": dimension})   # tham số hoá, chống injection
        rows = []
        for dv, ms, pc, ao in cur.fetchall():
            # gold lưu as_of dạng naive; gắn UTC để response nhất quán với các endpoint khác.
            if ao is not None and ao.tzinfo is None:
                ao = ao.replace(tzinfo=timezone.utc)
            rows.append(MetricRow(dv, ms, pc, ao))
        return rows
