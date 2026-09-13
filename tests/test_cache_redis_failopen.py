"""RedisCache phải FAIL-OPEN khi Redis lỗi (invariant plan: "Cache fail-open").

Không cần Redis thật: tiêm một client giả LUÔN ném lỗi, rồi khẳng định get→None (coi như miss)
và set→không ném. Nhờ vậy handler /market/metrics vẫn tính lại từ kho thay vì 500 khi Redis chết.
"""
from __future__ import annotations

import logging

from app.infrastructure.cache.redis import RedisCache


class _BoomClient:
    """Giả lập redis client hỏng: mọi thao tác đều ném (như ConnectionError khi Redis chết)."""

    def get(self, key):
        raise RuntimeError("boom: redis down")

    def setex(self, key, ttl, value):
        raise RuntimeError("boom: redis down")


def _cache_with_boom() -> RedisCache:
    # Bỏ qua __init__ (khỏi cần thư viện/where server redis) rồi gắn client giả.
    c = RedisCache.__new__(RedisCache)
    c._r = _BoomClient()
    c._ttl = 300
    return c


def test_get_fail_open_tra_none(caplog):
    c = _cache_with_boom()
    with caplog.at_level(logging.WARNING, logger="cache.redis"):
        assert c.get("k") is None            # KHÔNG ném → coi như cache miss
    assert any("fail-open" in r.message for r in caplog.records)


def test_set_fail_open_khong_nem(caplog):
    c = _cache_with_boom()
    with caplog.at_level(logging.WARNING, logger="cache.redis"):
        c.set("k", "v")                      # KHÔNG ném → ghi cache thất bại được bỏ qua
    assert any("fail-open" in r.message for r in caplog.records)


def test_client_co_bounded_timeout():
    # Bounded socket timeout: fail-open chỉ hữu ích khi lỗi trả NHANH (không treo blackhole > 25s).
    c = RedisCache("redis://localhost:6379/0")   # KHÔNG kết nối tới khi dùng
    kw = c._r.connection_pool.connection_kwargs
    assert kw.get("socket_connect_timeout") is not None
    assert kw.get("socket_timeout") is not None
