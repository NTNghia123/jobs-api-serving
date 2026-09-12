"""Executor BigQuery DÙNG CHUNG cho read adapter (jobs + metrics).

Gom một chỗ phần I/O lặp: dựng QueryJobConfig (trần byte + params typed), chạy có trần
thời gian, timeout → cancel job → 504, log chi phí (bq_job_id/total_bytes_billed/cache_hit/
elapsed_ms). SQL + params thuần đến từ bigquery_read_sql.py. Xem docs/adr/ADR-020.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import TimeoutError as FutureTimeoutError

from google.cloud import bigquery

from app.errors import QueryTimeoutError
from app.infrastructure.warehouse.bigquery_read_sql import QueryParam, ReadTarget, SqlAndParams
from app.observability.logging import log_event

logger = logging.getLogger("warehouse.bigquery")


def to_bq_params(params: list[QueryParam]) -> list[bigquery.ScalarQueryParameter]:
    """QueryParam thuần → ScalarQueryParameter typed (STRING/INT64/FLOAT64/DATE/TIMESTAMP)."""
    return [bigquery.ScalarQueryParameter(p.name, p.bq_type, p.value) for p in params]


class BigQueryExecutor:
    """Giữ client (ADC) + trần thời gian; chạy SqlAndParams có kiểm soát chi phí + timeout."""

    def __init__(
        self,
        target: ReadTarget,
        *,
        query_timeout_s: int = 10,
        client: bigquery.Client | None = None,
    ):
        self.target = target
        self._timeout_s = query_timeout_s
        # ADC — SA reader gắn ở Cloud Run; không JSON key.
        self.client = client or bigquery.Client(project=target.project, location=target.location)

    def run(self, sp: SqlAndParams, *, op: str) -> list[bigquery.table.Row]:
        cfg = bigquery.QueryJobConfig(
            maximum_bytes_billed=self.target.maximum_bytes_billed,
            query_parameters=to_bq_params(sp.params),
        )
        t0 = time.perf_counter()
        job = self.client.query(sp.sql, job_config=cfg)
        try:
            rows = list(job.result(timeout=self._timeout_s))
        except FutureTimeoutError as exc:
            job.cancel()   # ngừng job phía BQ, không để chạy tốn tiền sau khi ta bỏ
            log_event(logger, logging.WARNING, "bq_query_timeout", op=op,
                      bq_job_id=job.job_id, timeout_s=self._timeout_s)
            raise QueryTimeoutError("Truy vấn kho quá hạn") from exc
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        log_event(logger, logging.INFO, "bq_query", op=op, bq_job_id=job.job_id,
                  total_bytes_billed=job.total_bytes_billed, cache_hit=job.cache_hit,
                  elapsed_ms=elapsed_ms, rows=len(rows))
        return rows
