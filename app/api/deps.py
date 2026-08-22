"""Dependency injection.

FastAPI's Depends cho phép đổi hiện thực mà không sửa handler — Tuần 3 chỉ cần
sửa hàm _build_repository, còn app/api/jobs.py giữ nguyên.

BẪY ĐÃ GẶP (giữ lại làm ghi chú): nếu hàm dependency có tham số kiểu BaseModel
(ví dụ `settings: Settings = None`), FastAPI sẽ hiểu đó là MỘT THAM SỐ BODY nữa.
Khi đó handler có 2 body param, FastAPI chuyển sang chế độ "embed" và bắt client
gửi {"req": {...}} thay vì {...} — hợp đồng gãy mà không có lỗi lúc khởi động.
Vì vậy dependency ở đây không nhận tham số nào.
"""
from __future__ import annotations

from functools import lru_cache

from app.settings import get_settings
from app.warehouse.base import JobRepository
from app.warehouse.fake import FakeJobRepository


@lru_cache
def _build_repository(backend: str) -> JobRepository:
    if backend == "fake":
        return FakeJobRepository()
    # Tuần 3:
    # if backend == "duckdb":   return DuckDBJobRepository(...)
    # if backend == "bigquery": return BigQueryJobRepository(...)
    raise RuntimeError(f"warehouse_backend chưa được hỗ trợ: {backend}")


def get_repository() -> JobRepository:
    return _build_repository(get_settings().warehouse_backend)
