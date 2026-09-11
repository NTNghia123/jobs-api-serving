"""Port: hợp đồng đọc chỉ số thị trường (gold table).

Interface + DTO thô (chưa áp k-anonymity — handler áp). Impl: Fake/DuckDB/BigQuery ở
app/infrastructure/warehouse/. Xem docs/adr/ADR-020.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class MetricRow:
    """Một dòng chỉ số THÔ đọc từ gold (chưa áp k-anonymity)."""
    dimension_value: str
    posting_count: int              # distinct job_id trong nhóm/cửa sổ
    salary_disclosed_count: int     # có >=1 cận lương sau normalize
    salary_sample_count: int        # có đủ min&max (mẫu tính median)
    median_salary_vnd_month: float | None


class MetricsRepository(ABC):
    @abstractmethod
    def market_metrics(self, dimension: str, window: str) -> list[MetricRow]:
        """Trả mọi nhóm của một chiều trong một cửa sổ. dimension/window đã validate ở API."""

    @abstractmethod
    def as_of(self) -> datetime:
        """data cutoff của batch đang phục vụ (dùng để neo window 90d + cache key)."""
