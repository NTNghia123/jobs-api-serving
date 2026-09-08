"""Adapter cache Redis (hiện thực CacheBackend) — chia sẻ giữa NHIỀU instance.

Trước refactor: một phần của app/domain/cache.py. Cần một Redis server đang chạy;
bật bằng JOBS_API_CACHE_BACKEND=redis. Xem docs/adr/ADR-009.
"""
from __future__ import annotations

from app.domain.ports.cache import CacheBackend


class RedisCache(CacheBackend):
    def __init__(self, url: str, ttl_seconds: int = 300):
        # Import TRỄ: chỉ nạp thư viện redis khi thực sự dùng backend này, để dev chạy
        # in-memory không cần cài/chạy Redis.
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True)  # get() trả str, không phải bytes
        self._ttl = ttl_seconds

    def get(self, key: str) -> str | None:
        return self._r.get(key)

    def set(self, key: str, value: str) -> None:
        self._r.setex(key, self._ttl, value)   # SET kèm hạn sống (giây) trong một lệnh
