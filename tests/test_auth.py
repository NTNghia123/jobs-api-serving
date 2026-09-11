"""Test xác thực & rate-limit ở tầng HTTP.

[FILE MỚI - TUẦN 6]  ·  Hướng dẫn §6

Kiểm 4 điều: /health mở; thiếu/sai key → 401; key dev đúng → 200; vượt quota → 429.
"""
from __future__ import annotations

from app.api.deps import get_rate_limiter
from app.infrastructure.auth.config_key_store import DEV_API_KEY
from app.infrastructure.ratelimit.memory import InMemoryRateLimiter


def test_health_khong_can_key(client):
    # client fixture có gắn sẵn key, nhưng /health vốn không kiểm → vẫn 200.
    assert client.get("/health").status_code == 200


def test_thieu_key_bi_401(client):
    r = client.post("/v1/jobs/search",
                    json={"filters": {"posted_after": "2020-01-01"}, "limit": 5},
                    headers={"X-API-Key": ""})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_sai_key_bi_401(client):
    # Key ASCII (HTTP header không nhận ký tự non-ASCII).
    r = client.get("/v1/metadata", headers={"X-API-Key": "sai-hoan-toan"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_key_dev_dung_thi_200(client):
    r = client.get("/v1/metadata", headers={"X-API-Key": DEV_API_KEY})
    assert r.status_code == 200


def test_vuot_quota_bi_429(client):
    # Ép limiter chỉ cho 1 request rồi chặn, bằng dependency_overrides.
    # LƯU Ý: tạo MỘT instance dùng chung — nếu lambda tạo mới mỗi request thì xô
    # token reset mỗi lần và không bao giờ chặn.
    limiter = InMemoryRateLimiter(rate_per_sec=0.0, capacity=1)
    app = client.app
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    try:
        h = {"X-API-Key": DEV_API_KEY}
        assert client.get("/v1/metadata", headers=h).status_code == 200   # token đầu
        r = client.get("/v1/metadata", headers=h)                          # hết token
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    finally:
        app.dependency_overrides.pop(get_rate_limiter, None)
