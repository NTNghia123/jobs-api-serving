"""Adapter: đọc gold_market_metrics từ DuckDB (hiện thực MetricsRepository) — schema MỚI.

Mirror BigQueryMetricsRepository: current_batch() (state × batches) + market_metrics theo
batch/window/dimension. Gold giữ SỐ THẬT; k-anon che median áp ở handler. Map row dùng chung
read_mapping. Khôi phục ở Phase 4. Xem docs/adr/ADR-011, ADR-020.
"""
from __future__ import annotations

import duckdb

from app.domain.ports.metrics_repository import BatchRef, MetricRow, MetricsRepository
from app.infrastructure.warehouse.duckdb_exec import DuckDBReader
from app.infrastructure.warehouse.duckdb_read_sql import build_metrics_sql
from app.infrastructure.warehouse.read_mapping import to_metric_row


class DuckDBMetricsRepository(MetricsRepository):
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

    def current_batch(self) -> BatchRef:
        batch_id, as_of = self._reader.current_batch()
        return BatchRef(batch_id=batch_id, as_of=as_of)

    def market_metrics(self, dimension: str, window: str, batch_id: str) -> list[MetricRow]:
        sql, params = build_metrics_sql(dimension, window, batch_id)
        return [to_metric_row(r) for r in self._reader.run(sql, params)]
