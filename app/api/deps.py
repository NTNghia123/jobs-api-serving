"""Dependency injection.

[FILE SỬA]  Đích thật: app/api/deps.py
So với bản Tuần 2, chỉ THÊM 1 import + 1 nhánh 'duckdb' (đánh dấu ★). CHỖ DUY NHẤT
trong code cũ phải sửa để đổi từ dữ liệu giả sang dữ liệu thật — app/api/jobs.py,
app/models/, app/domain/ KHÔNG đổi. Đó là phép thử của adapter pattern (ADR-003).

FastAPI's Depends cho phép đổi hiện thực mà không sửa handler.

BẪY ĐÃ GẶP (giữ lại làm ghi chú): nếu hàm dependency có tham số kiểu BaseModel
(ví dụ `settings: Settings = None`), FastAPI sẽ hiểu đó là MỘT THAM SỐ BODY nữa.
Khi đó handler có 2 body param, FastAPI chuyển sang chế độ "embed" và bắt client
gửi {"req": {...}} thay vì {...} — hợp đồng gãy mà không có lỗi lúc khởi động.
Vì vậy dependency ở đây không nhận tham số nào.
"""
from __future__ import annotations

from functools import lru_cache

# Ports (interface) — thuộc lõi
from app.domain.ports.cache import CacheBackend
from app.domain.ports.job_repository import JobRepository
from app.domain.ports.metrics_repository import MetricsRepository

# Adapters (implementation) — chỉ composition root này được import infrastructure
from app.infrastructure.cache.memory import InMemoryCache
from app.infrastructure.cache.redis import RedisCache
from app.infrastructure.warehouse.duckdb_jobs import DuckDBJobRepository
from app.infrastructure.warehouse.duckdb_metrics import DuckDBMetricsRepository
from app.infrastructure.warehouse.fake_jobs import FakeJobRepository
from app.infrastructure.warehouse.fake_metrics import FakeMetricsRepository
from app.settings import get_settings


@lru_cache
def _build_repository(backend: str) -> JobRepository:
    if backend == "fake":
        return FakeJobRepository()
    if backend == "duckdb":                                  # ★ THÊM Ở TUẦN 3
        return DuckDBJobRepository(path=get_settings().duckdb_path)
    # Tuần 7 (tuỳ chọn):
    # if backend == "bigquery": return BigQueryJobRepository(...)
    raise RuntimeError(f"warehouse_backend chưa được hỗ trợ: {backend}")


def get_repository() -> JobRepository:
    return _build_repository(get_settings().warehouse_backend)


# ★ THÊM Ở TUẦN 5 — repository đọc gold table chỉ số thị trường.
@lru_cache
def _build_metrics_repository(backend: str) -> MetricsRepository:
    if backend == "fake":
        return FakeMetricsRepository()
    if backend == "duckdb":
        return DuckDBMetricsRepository(path=get_settings().duckdb_path)
    raise RuntimeError(f"warehouse_backend chưa được hỗ trợ: {backend}")


def get_metrics_repository() -> MetricsRepository:
    return _build_metrics_repository(get_settings().warehouse_backend)


# ★ THÊM Ở TUẦN 5 — cache đổi được giữa in-memory (cachetools) và Redis (ADR-009).
@lru_cache
def _build_cache(backend: str, redis_url: str, ttl: int) -> CacheBackend:
    if backend == "memory":
        return InMemoryCache(ttl_seconds=ttl)
    if backend == "redis":
        return RedisCache(redis_url, ttl_seconds=ttl)
    raise RuntimeError(f"cache_backend chưa được hỗ trợ: {backend}")


def get_cache() -> CacheBackend:
    s = get_settings()
    return _build_cache(s.cache_backend, s.redis_url, s.cache_ttl_seconds)
