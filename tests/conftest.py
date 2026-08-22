import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def base_body() -> dict:
    """Request hợp lệ tối thiểu — không còn field bắt buộc nào."""
    return {"limit": 5}
