import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_api_key_store, get_rate_limiter  # ★ THÊM Ở TUẦN 6
from app.infrastructure.auth.config_key_store import DEV_API_KEY  # ★ THÊM Ở TUẦN 6
from app.main import create_app


@pytest.fixture(scope="session")
def client() -> TestClient:
    # ★ Tuần 6: gắn sẵn key dev cho MỌI request → test cũ không phải đổi từng file.
    return TestClient(create_app(), headers={"X-API-Key": DEV_API_KEY})


@pytest.fixture(autouse=True)
def _reset_auth_state():
    """★ Tuần 6: xoá state token bucket + store giữa các test.

    get_rate_limiter/get_api_key_store dùng @lru_cache (singleton) → state tích luỹ
    qua các test, dễ gây 429 chéo nhau. cache_clear() để mỗi test bắt đầu sạch.
    """
    get_rate_limiter.cache_clear()
    get_api_key_store.cache_clear()
    yield


@pytest.fixture
def base_body() -> dict:
    """Request hợp lệ tối thiểu — không còn field bắt buộc nào."""
    return {"limit": 5}
