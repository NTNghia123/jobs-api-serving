"""Dependency injection.

[FILE SỬA]  Đích thật: app/api/deps.py
Composition root: chỗ DUY NHẤT ráp adapter cho các port. Migration Phase 0: chỉ backend
'fake' khả dụng; 'bigquery' (Phase 3) / 'duckdb' (Phase 4) bị từ chối fail-fast ở settings
(và nhánh phòng thủ dưới đây). Handler/models/domain không phụ thuộc adapter cụ thể (ADR-003/018).

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
from app.domain.ports.api_key_store import ApiKeyStore  # ★ THÊM Ở TUẦN 6
from app.domain.ports.cache import CacheBackend
from app.domain.ports.job_repository import JobRepository
from app.domain.ports.metrics_repository import MetricsRepository
from app.domain.ports.rate_limiter import RateLimiter  # ★ THÊM Ở TUẦN 6

# Adapters (implementation) — chỉ composition root này được import infrastructure
from app.infrastructure.auth.config_key_store import ConfigApiKeyStore  # ★ THÊM Ở TUẦN 6
from app.infrastructure.cache.memory import InMemoryCache
from app.infrastructure.cache.redis import RedisCache
from app.infrastructure.ratelimit.memory import InMemoryRateLimiter  # ★ THÊM Ở TUẦN 6
from app.infrastructure.ratelimit.redis import RedisRateLimiter  # ★ THÊM Ở TUẦN 7
from app.infrastructure.warehouse.fake_jobs import FakeJobRepository
from app.infrastructure.warehouse.fake_metrics import FakeMetricsRepository
from app.settings import get_settings

# DuckDB tạm tắt trong migration (schema silver đổi sang BigQuery). Phase 4 khôi phục parity.
_DUCKDB_DISABLED = (
    "warehouse_backend='duckdb' tạm không hỗ trợ trong migration; dùng 'fake' "
    "('bigquery' khả dụng ở Phase 3, 'duckdb' khôi phục ở Phase 4)."
)


# Import bigquery lazily (chỉ khi backend=bigquery) — tránh kéo google-cloud-bigquery/ADC
# khi chạy 'fake' (test). target_key = tuple hashable khớp thứ tự tham số ReadTarget.
@lru_cache
def _build_repository(backend: str, target_key: tuple, query_timeout_s: int) -> JobRepository:
    if backend == "fake":
        return FakeJobRepository()
    if backend == "bigquery":                                # ★ Phase 3
        from app.infrastructure.warehouse.bigquery_jobs import BigQueryJobRepository
        from app.infrastructure.warehouse.bigquery_read_sql import ReadTarget
        return BigQueryJobRepository(ReadTarget(*target_key), query_timeout_s=query_timeout_s)
    if backend == "duckdb":                                  # ★ TẠM TẮT trong migration (Phase 4 khôi phục)
        raise RuntimeError(_DUCKDB_DISABLED)
    raise RuntimeError(f"warehouse_backend chưa được hỗ trợ: {backend}")


def get_repository() -> JobRepository:
    s = get_settings()
    # target_key là tuple hashable (cho lru_cache) khớp thứ tự tham số ReadTarget.
    target_key = (s.bq_project, s.bq_dataset, s.bq_location, s.bq_maximum_bytes_billed)
    return _build_repository(s.warehouse_backend, target_key, s.query_timeout_s)


# ★ THÊM Ở TUẦN 5 — repository đọc gold table chỉ số thị trường.
@lru_cache
def _build_metrics_repository(backend: str, target_key: tuple, query_timeout_s: int) -> MetricsRepository:
    if backend == "fake":
        return FakeMetricsRepository()
    if backend == "bigquery":                                # ★ Phase 3
        from app.infrastructure.warehouse.bigquery_metrics import BigQueryMetricsRepository
        from app.infrastructure.warehouse.bigquery_read_sql import ReadTarget
        return BigQueryMetricsRepository(ReadTarget(*target_key), query_timeout_s=query_timeout_s)
    if backend == "duckdb":
        raise RuntimeError(_DUCKDB_DISABLED)   # ★ TẠM TẮT trong migration (Phase 4 khôi phục)
    raise RuntimeError(f"warehouse_backend chưa được hỗ trợ: {backend}")


def get_metrics_repository() -> MetricsRepository:
    s = get_settings()
    target_key = (s.bq_project, s.bq_dataset, s.bq_location, s.bq_maximum_bytes_billed)
    return _build_metrics_repository(s.warehouse_backend, target_key, s.query_timeout_s)


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


# ★ THÊM Ở TUẦN 6 — kho API key (đọc từ config; local tự seed key dev).
@lru_cache
def get_api_key_store() -> ApiKeyStore:
    return ConfigApiKeyStore.from_settings(get_settings())


# ★ THÊM Ở TUẦN 6, MỞ RỘNG TUẦN 7 — rate limiter dùng chung (singleton nhờ lru_cache).
# Giữ NGUYÊN hàm này là @lru_cache (conftest gọi .cache_clear() giữa các test). Tuần 7 chỉ
# thêm nhánh chọn adapter: memory (1 instance) | redis (chia sẻ đa instance) qua rate_limiter_backend.
@lru_cache
def get_rate_limiter() -> RateLimiter:
    s = get_settings()
    capacity = s.rate_limit_burst or s.rate_limit_per_minute
    rate_per_sec = s.rate_limit_per_minute / 60.0
    if s.rate_limiter_backend == "memory":
        return InMemoryRateLimiter(rate_per_sec=rate_per_sec, capacity=capacity)
    if s.rate_limiter_backend == "redis":                    # ★ THÊM Ở TUẦN 7
        return RedisRateLimiter(s.redis_url, rate_per_sec=rate_per_sec, capacity=capacity)
    raise RuntimeError(f"rate_limiter_backend chưa được hỗ trợ: {s.rate_limiter_backend}")
