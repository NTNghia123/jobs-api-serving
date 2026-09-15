"""Cấu hình test dùng chung.

Ép backend HERMETIC cho test: warehouse=fake, cache/rate-limit=memory — không phụ thuộc
.env (duckdb/redis) hay file analytics.duckdb. Contract test chạy trên `fake`; parity
duckdb/bigquery ở Phase 4.
"""
import os

# Phải set TRƯỚC khi app đọc settings (pydantic-settings: env var > .env).
os.environ["JOBS_API_WAREHOUSE_BACKEND"] = "fake"
os.environ["JOBS_API_CACHE_BACKEND"] = "memory"
os.environ["JOBS_API_RATE_LIMITER_BACKEND"] = "memory"
os.environ["JOBS_API_ENV"] = "local"
# ★ TUẦN 8 — hermetic: ép tắt tracing để test KHÔNG chạm google.auth/mạng dù máy dev đặt otlp.
os.environ["JOBS_API_OTEL_TRACES_EXPORTER"] = "none"

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_api_key_store, get_rate_limiter
from app.infrastructure.auth.config_key_store import DEV_API_KEY
from app.main import create_app
from app.settings import get_settings

get_settings.cache_clear()   # bỏ cache nếu settings đã bị đọc lúc import


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(create_app(), headers={"X-API-Key": DEV_API_KEY})


@pytest.fixture(autouse=True)
def _reset_auth_state():
    """Xoá state token bucket + store giữa các test (tránh 429 chéo)."""
    get_rate_limiter.cache_clear()
    get_api_key_store.cache_clear()
    yield


@pytest.fixture
def base_body() -> dict:
    """Request hợp lệ tối thiểu — posted_after BẮT BUỘC (lấy đủ xa để phủ toàn corpus mẫu)."""
    return {"filters": {"posted_after": "2020-01-01"}, "limit": 5}
