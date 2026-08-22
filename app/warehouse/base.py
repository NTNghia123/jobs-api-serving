"""Interface của lớp truy cập dữ liệu.

Tuần 2 chỉ có bản Fake. Tuần 3 thêm DuckDB (dev) và BigQuery (prod) mà KHÔNG
phải sửa bất kỳ dòng nào trong app/api/. Đó là mục đích của interface này.
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
