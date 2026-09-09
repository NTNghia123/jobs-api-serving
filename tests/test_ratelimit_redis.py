"""Test RedisRateLimiter (token bucket Lua) — chạy KHI có Redis, ngược lại tự SKIP.

[FILE MỚI - TUẦN 7]  ·  Xem docs/adr/ADR-017

Bật Redis để chạy thật:  docker run --rm -p 6379:6379 redis:7-alpine
(hoặc đặt JOBS_API_REDIS_URL trỏ tới Redis của bạn). Không có Redis → skip, không fail.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

redis = pytest.importorskip("redis")   # bỏ qua nếu chưa cài thư viện redis

from app.infrastructure.ratelimit.redis import RedisRateLimiter

_URL = os.environ.get("JOBS_API_REDIS_URL", "redis://localhost:6379/0")


def _redis_up() -> bool:
    try:
        redis.Redis.from_url(_URL).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_up(), reason="Redis không sẵn sàng (đặt JOBS_API_REDIS_URL)")


def _limiter(rate, cap):
    # Prefix ngẫu nhiên → mỗi lần chạy một xô sạch, không dính state lần trước còn trong Redis.
    return RedisRateLimiter(_URL, rate_per_sec=rate, capacity=cap, key_prefix=f"test:{uuid.uuid4()}:")


def test_cho_phep_toi_suc_chua_roi_chan():
    rl = _limiter(rate=1.0, cap=3)
    assert [rl.allow("c1") for _ in range(4)] == [True, True, True, False]


def test_moi_client_mot_xo_rieng():
    rl = _limiter(rate=1.0, cap=1)
    assert rl.allow("c1") is True
    assert rl.allow("c2") is True     # client khác không bị ảnh hưởng
    assert rl.allow("c1") is False


def test_token_hoi_lai_theo_thoi_gian():
    rl = _limiter(rate=10.0, cap=1)   # 10 token/giây → ~0.1s hồi 1 token
    assert rl.allow("c1") is True
    assert rl.allow("c1") is False
    time.sleep(0.25)                  # đủ để hồi > 1 token
    assert rl.allow("c1") is True
