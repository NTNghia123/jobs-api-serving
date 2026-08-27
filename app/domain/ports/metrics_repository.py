"""Port: hợp đồng đọc chỉ số thị trường (gold table).

Interface + DTO thô (chưa áp k-anonymity). Impl cụ thể (DuckDB/Fake) nằm ở
app/infrastructure/warehouse/. Xem docs/adr/ADR-011.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class MetricRow:
    """Một dòng chỉ số THÔ đọc từ gold (chưa áp k-anonymity)."""
    dimension_value: str
    median_salary: float | None
    posting_count: int
    as_of: datetime


class MetricsRepository(ABC):
    @abstractmethod
    def market_metrics(self, dimension: str) -> list[MetricRow]:
        """Trả mọi nhóm của một chiều. 'dimension' đã được validate ở tầng API."""
