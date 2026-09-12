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


@dataclass
class BatchRef:
    """Batch đang publish: định danh + data cutoff. batch_id gắn CACHE KEY (invariant 5,
    ADR-026) — không dùng as_of_date làm surrogate (hai batch cùng ngày sẽ đụng key).
    as_of dùng neo window 90d."""
    batch_id: str
    as_of: datetime


class MetricsRepository(ABC):
    @abstractmethod
    def current_batch(self) -> BatchRef:
        """Batch đang phục vụ (batch_id + data cutoff). Chưa publish batch nào → raise (503).

        Handler đọc MỘT lần rồi dùng batch_id đó cho cả cache key lẫn market_metrics(...) →
        cache key và số liệu luôn cùng một batch (không lệch nếu batch mới publish giữa chừng).
        """

    @abstractmethod
    def market_metrics(self, dimension: str, window: str, batch_id: str) -> list[MetricRow]:
        """Mọi nhóm của một chiều trong một cửa sổ, THUỘC batch_id. dim/window đã validate ở API."""
