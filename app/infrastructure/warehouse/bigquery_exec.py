"""Executor BigQuery DÙNG CHUNG cho read adapter (jobs + metrics).

Gom một chỗ phần I/O lặp: dựng QueryJobConfig (trần byte + params typed), chạy có trần
thời gian, timeout → cancel job → 504, log chi phí (bq_job_id/total_bytes_billed/cache_hit/
elapsed_ms). SQL + params thuần đến từ bigquery_read_sql.py. Xem docs/adr/ADR-020.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import TimeoutError as FutureTimeoutError

from google.cloud import bigquery
from opentelemetry import trace  # ★ THÊM Ở TUẦN 8

from app.errors import QueryTimeoutError
from app.infrastructure.warehouse.bigquery_read_sql import QueryParam, ReadTarget, SqlAndParams
from app.observability.logging import get_request_id, log_event

logger = logging.getLogger("warehouse.bigquery")

# ★ TUẦN 8 — cancel là best-effort; GIỚI HẠN thời gian để lỗi timeout không treo response 504
# thêm (mặc định API BigQuery không đặt trần → có thể chờ rất lâu). Giữ nhỏ để tổng vẫn < Cloud Run 25s.
_CANCEL_TIMEOUT_S = 5


def load_test_run_label(request_id: str) -> str | None:
    """Lấy run-id từ ``lt_<run-id>:<phase>:<request>`` và chuẩn hóa thành BigQuery label.

    Marker riêng ``lt_`` tránh biến request-id bình thường có dấu ``:`` thành instrumentation load-test.
    """
    if not request_id.startswith("lt_"):
        return None
    run_id, sep, _rest = request_id.partition(":")
    run_id = run_id[3:]
    if not sep or not run_id:
        return None
    value = re.sub(r"[^a-z0-9_-]", "_", run_id.lower()).strip("_-")[:63]
    return value or None


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
        tracer: trace.Tracer | None = None,   # ★ TUẦN 8 — inject để test không đụng global
    ):
        self.target = target
        self._timeout_s = query_timeout_s
        # ADC — SA reader gắn ở Cloud Run; không JSON key.
        self.client = client or bigquery.Client(project=target.project, location=target.location)
        # ★ TUẦN 8 — prod: get_tracer trỏ về global provider (configure_tracing set một lần);
        # test truyền provider.get_tracer(...) để đọc span mà không phụ thuộc global.
        self._tracer = tracer or trace.get_tracer(__name__)

    def run(self, sp: SqlAndParams, *, op: str) -> list[bigquery.table.Row]:
        run_label = load_test_run_label(get_request_id())
        cfg_args = {
            "maximum_bytes_billed": self.target.maximum_bytes_billed,
            "query_parameters": to_bq_params(sp.params),
            "use_query_cache": self.target.use_query_cache,
        }
        if run_label:
            cfg_args["labels"] = {"load_test_run": run_label}
        cfg = bigquery.QueryJobConfig(
            **cfg_args,
        )
        # ★ TUẦN 8 — span bọc CẢ vòng đời (submit + result). CM mặc định tự record exception
        # + set status ERROR khi lỗi thoát ra → KHÔNG record thủ công (tránh ghi hai lần).
        with self._tracer.start_as_current_span("bq.query") as span:
            span.set_attribute("op", op)               # đặt ngay khi mở span
            t0 = time.perf_counter()
            job = self.client.query(sp.sql, job_config=cfg)
            if job.job_id:                             # bq.job_id NGAY sau submit — để trace timeout vẫn có
                span.set_attribute("bq.job_id", job.job_id)
            try:
                rows = list(job.result(timeout=self._timeout_s))
            except FutureTimeoutError as exc:
                span.set_attribute("timeout_s", self._timeout_s)   # đặt TRƯỚC cancel
                try:
                    # best-effort, chặn TỔNG thời gian: retry=None (một lần, không retry theo deadline
                    # ~10 phút mặc định) + timeout per-request → không treo response 504 khi BQ chậm.
                    job.cancel(retry=None, timeout=_CANCEL_TIMEOUT_S)
                except Exception:  # noqa: BLE001 — cancel gọi mạng có thể lỗi/quá hạn; KHÔNG được che 504
                    logger.warning("bq job.cancel() thất bại/quá hạn (bỏ qua)", exc_info=True)
                log_event(logger, logging.WARNING, "bq_query_timeout", op=op,
                          bq_job_id=job.job_id, timeout_s=self._timeout_s)
                raise QueryTimeoutError("Truy vấn kho quá hạn") from exc
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            # attribute kết quả — chỉ đặt khi khác None.
            if job.total_bytes_billed is not None:
                span.set_attribute("bq.total_bytes_billed", job.total_bytes_billed)
            if job.cache_hit is not None:
                span.set_attribute("bq.cache_hit", job.cache_hit)
            span.set_attribute("bq.rows", len(rows))
            span.set_attribute("bq.elapsed_ms", elapsed_ms)
            log_event(logger, logging.INFO, "bq_query", op=op, bq_job_id=job.job_id,
                      total_bytes_billed=job.total_bytes_billed, cache_hit=job.cache_hit,
                      elapsed_ms=elapsed_ms, rows=len(rows))
            return rows
