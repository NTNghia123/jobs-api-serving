"""Adapter giả lập cho MetricsRepository — dữ liệu mẫu, không cần BigQuery/DuckDB.

Mô phỏng gold sau ELT: 3 loại count + median VND/tháng, theo dimension source|seniority|
category. Có nhóm 'unknown' và nhóm salary_sample_count < 5 để thấy k-anonymity kích hoạt
(median bị che ở handler). Window để nhất quán interface; fake trả cùng dữ liệu cho 90d/all_time.
Xem docs/adr/ADR-020.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.domain.ports.metrics_repository import BatchRef, MetricRow, MetricsRepository

_AS_OF = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
_FAKE_BATCH_ID = "fake-batch-2026-09-01"   # prod lấy động từ warehouse_state (ADR-025)


def _row(value, posting, disclosed, sample, median) -> MetricRow:
    return MetricRow(
        dimension_value=value,
        posting_count=posting,
        salary_disclosed_count=disclosed,
        salary_sample_count=sample,
        median_salary_vnd_month=median,
    )


_SAMPLE: dict[str, list[MetricRow]] = {
    "seniority": [
        _row("junior", 40, 32, 28, 16_000_000.0),
        _row("mid", 35, 30, 26, 28_000_000.0),
        _row("senior", 30, 24, 20, 45_000_000.0),
        _row("unknown", 8, 5, 3, 38_000_000.0),   # sample_count 3 < 5 → median bị che
    ],
    "source": [
        _row("topdev", 60, 48, 40, 35_000_000.0),
        _row("vietnamworks", 53, 43, 37, 30_000_000.0),
    ],
    "category": [
        _row("topdev:g1~j5", 22, 20, 18, 40_000_000.0),
        _row("topdev:g14~j22", 15, 10, 9, 20_000_000.0),
        _row("vietnamworks:g8~j51", 12, 8, 4, 33_000_000.0),   # sample 4 < 5 → che
        _row("topdev:unknown", 9, 6, 3, 30_000_000.0),         # job thiếu category → sample 3 < 5 → che
        _row("vietnamworks:unknown", 7, 5, 5, 26_000_000.0),   # unknown vẫn giữ posting (không biến mất)
    ],
}


class FakeMetricsRepository(MetricsRepository):
    def current_batch(self) -> BatchRef:
        return BatchRef(batch_id=_FAKE_BATCH_ID, as_of=_AS_OF)

    def market_metrics(self, dimension: str, window: str, batch_id: str) -> list[MetricRow]:
        # fake một-batch: bỏ qua batch_id/window, trả cùng dữ liệu mẫu.
        return list(_SAMPLE.get(dimension, []))
