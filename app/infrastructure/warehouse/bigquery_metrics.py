"""Adapter: đọc gold_market_metrics từ BigQuery (hiện thực MetricsRepository).

Tầng I/O đọc chỉ số thị trường. SQL thuần ở bigquery_read_sql.py; execute + timeout +
log ở BigQueryExecutor (dùng chung với jobs). Gold giữ SỐ THẬT; k-anonymity che median
áp ở TẦNG API (handler), KHÔNG ở SQL — ADR-020.

Đọc theo BATCH: current_batch() cho batch đang publish; market_metrics(dim, win, batch_id)
đọc đúng batch handler đã chốt (cùng batch với cache key → không lệch). Xem docs/adr/ADR-020.
Phần I/O test thật lên BQ ở Phase 4 (RUN_BQ_INTEGRATION=1).
"""
from __future__ import annotations

from collections.abc import Mapping

from google.cloud import bigquery

from app.domain.ports.metrics_repository import BatchRef, MetricRow, MetricsRepository
from app.errors import UpstreamUnavailableError
from app.infrastructure.warehouse.bigquery_exec import BigQueryExecutor
from app.infrastructure.warehouse.bigquery_read_sql import (
    ReadTarget,
    build_current_batch_meta_sql,
    build_metrics_sql,
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


class BigQueryMetricsRepository(MetricsRepository):
    """Đọc gold từ BigQuery. Handler chỉ thấy interface MetricsRepository (Depends)."""

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

    def current_batch(self) -> BatchRef:
        rows = self._exec.run(build_current_batch_meta_sql(self._exec.target), op="batch_meta")
        if not rows:
            raise UpstreamUnavailableError("Chưa có batch nào được publish trong kho")
        return BatchRef(batch_id=rows[0]["batch_id"], as_of=rows[0]["data_as_of_at"])

    def market_metrics(self, dimension: str, window: str, batch_id: str) -> list[MetricRow]:
        sp = build_metrics_sql(self._exec.target, dimension, window, batch_id)
        return [to_metric_row(r) for r in self._exec.run(sp, op="metrics")]
