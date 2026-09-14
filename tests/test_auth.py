"""Test xác thực & rate-limit ở tầng HTTP.

[FILE MỚI - TUẦN 6]  ·  Hướng dẫn §6

Kiểm 4 điều: /health mở; thiếu/sai key → 401; key dev đúng → 200; vượt quota → 429.
"""
from __future__ import annotations

import io
import json
import logging
from types import SimpleNamespace

import pytest

from app.api.auth import authorize
from app.api.deps import get_rate_limiter
from app.domain.auth import hash_key
from app.errors import RateLimitExceededError, UnauthorizedError
from app.infrastructure.auth.config_key_store import (
    DEV_API_KEY,
    DEV_CLIENT_ID,
    ConfigApiKeyStore,
    _Entry,
)
from app.infrastructure.ratelimit.memory import InMemoryRateLimiter
from app.observability.logging import JsonFormatter


def _dev_store() -> ConfigApiKeyStore:
    return ConfigApiKeyStore([_Entry(DEV_CLIENT_ID, hash_key(DEV_API_KEY), None)])


class _DenyLimiter:
    def allow(self, client_id: str) -> bool:  # noqa: ARG002 — luôn chặn để giả lập 429
        return False


class _AllowLimiter:
    def allow(self, client_id: str) -> bool:  # noqa: ARG002 — luôn cho qua
        return True


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


# --- authorize() core: client_id gán TRƯỚC limiter → access-log của 429 có client_id thật ---
# (phục vụ chart "429 theo client_id" ở dashboard Looker Studio)

def test_429_van_gan_client_id_vao_request_state():
    req = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(RateLimitExceededError):
        authorize(req, DEV_API_KEY, _dev_store(), _DenyLimiter())
    # dù bị 429, request.state.client_id vẫn là client thật (không phải "-")
    assert req.state.client_id == DEV_CLIENT_ID


def test_cho_qua_thi_tra_client_id_va_gan_state():
    req = SimpleNamespace(state=SimpleNamespace())
    cid = authorize(req, DEV_API_KEY, _dev_store(), _AllowLimiter())
    assert cid == DEV_CLIENT_ID
    assert req.state.client_id == DEV_CLIENT_ID


def test_401_khong_gan_client_id():
    # Sai key → raise TRƯỚC khi có record → KHÔNG gán client_id (access-log sẽ là "-").
    req = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(UnauthorizedError):
        authorize(req, "sai-key", _dev_store(), _AllowLimiter())
    assert not hasattr(req.state, "client_id")


def test_market_metrics_log_co_client_id(client):
    # ContextVar client_id (set trong require_client async) PHẢI truyền sang handler sync →
    # log 'market_metrics' có client_id thật, không phải "-". (Chống hồi quy bug threadpool.)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    lg = logging.getLogger("api.market")
    lg.addHandler(handler)
    prev_level = lg.level
    lg.setLevel(logging.INFO)
    try:
        r = client.get("/v1/market/metrics", headers={"X-API-Key": DEV_API_KEY})
        assert r.status_code == 200
    finally:
        lg.removeHandler(handler)
        lg.setLevel(prev_level)
    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    mm = [e for e in events if e.get("message") == "market_metrics"]
    assert mm, "không thấy log market_metrics"
    assert mm[-1]["client_id"] == DEV_CLIENT_ID
