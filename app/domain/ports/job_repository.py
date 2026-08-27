"""Port: hợp đồng truy cập tin tuyển dụng (interface + DTO trả về).

Đây là 'cổng' của lõi: handler và các adapter đều phụ thuộc vào interface này, không
phụ thuộc lẫn nhau. Impl cụ thể (DuckDB/Fake) nằm ở app/infrastructure/warehouse/.
Xem docs/adr/ADR-003, ADR-011.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.models.jobs import JobItem, SearchRequest


@dataclass
class SearchResult:
    items: list[JobItem]
    next_cursor: dict | None
    total_estimated: int | None
    as_of: datetime


class JobRepository(ABC):
    @abstractmethod
    def search(self, req: SearchRequest, cursor: dict | None) -> SearchResult:
        """Trả về một trang kết quả. Không bao giờ trả trường PII."""

    @abstractmethod
    def as_of(self) -> datetime:
        """Thời điểm dữ liệu được cập nhật lần cuối."""
