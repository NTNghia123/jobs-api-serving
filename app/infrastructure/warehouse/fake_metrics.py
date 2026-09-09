"""Adapter giả lập cho MetricsRepository — dữ liệu mẫu để test không cần DuckDB.

Trước refactor: một phần của app/warehouse/metrics.py. country 'Andorra' cố ý chỉ 2 tin
để thấy k-anonymity kích hoạt (nhóm < 5 → median bị che ở tầng API).
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.domain.ports.metrics_repository import MetricRow, MetricsRepository

_AS_OF = datetime(2025, 8, 17, 0, 0, tzinfo=UTC)


class FakeMetricsRepository(MetricsRepository):
    _SAMPLE: dict[str, list[MetricRow]] = {
        "seniority": [
            MetricRow("junior", 45000.0, 25, _AS_OF),
            MetricRow("mid", 52000.0, 26, _AS_OF),
            MetricRow("senior", 60000.0, 25, _AS_OF),
        ],
        "country": [
            MetricRow("Andorra", 48000.0, 2, _AS_OF),   # nhóm nhỏ → sẽ bị che
            MetricRow("Denmark", 58000.0, 8, _AS_OF),
        ],
    }

    def market_metrics(self, dimension: str) -> list[MetricRow]:
        return list(self._SAMPLE.get(dimension, []))
