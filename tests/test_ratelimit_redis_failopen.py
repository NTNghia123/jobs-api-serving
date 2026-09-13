"""RedisRateLimiter phải FAIL-OPEN khi Redis lỗi (invariant plan: "rate-limiter fail-open+log").

Không cần Redis thật: gắn một script giả LUÔN ném lỗi (như khi Redis chết), rồi khẳng định
allow()→True (cho qua) + log WARN, để API không bị 500 hàng loạt trên đường /v1 khi Redis down.
"""
from __future__ import annotations

import logging

from app.infrastructure.ratelimit.redis import RedisRateLimiter


def _limiter_with_boom() -> RedisRateLimiter:
    # Bỏ qua __init__ (khỏi cần thư viện/server redis) rồi gắn script giả luôn ném.
    rl = RedisRateLimiter.__new__(RedisRateLimiter)
    rl._rate = 1.0
    rl._cap = 1.0
    rl._ttl = 60
    rl._prefix = "test:"

    def _boom(*args, **kwargs):
        raise RuntimeError("boom: redis down")

    rl._script = _boom
    return rl


def test_allow_fail_open_cho_qua(caplog):
    rl = _limiter_with_boom()
    with caplog.at_level(logging.WARNING, logger="ratelimit.redis"):
        assert rl.allow("c1") is True        # Redis chết → cho qua, KHÔNG ném
    assert any("fail-open" in r.message for r in caplog.records)


def test_client_co_bounded_timeout():
    # Rate limiter chạy trước mọi /v1: phải có timeout để không treo request khi Redis blackhole.
    rl = RedisRateLimiter("redis://localhost:6379/0", rate_per_sec=1.0, capacity=1.0)
    kw = rl._r.connection_pool.connection_kwargs
    assert kw.get("socket_connect_timeout") is not None
    assert kw.get("socket_timeout") is not None
