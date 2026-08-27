"""Adapter cache in-process dùng cachetools.TTLCache (hiện thực CacheBackend).

Trước refactor: một phần của app/domain/cache.py. Hợp dev / 1 instance, không cần server.
Xem docs/adr/ADR-009.
"""
from __future__ import annotations

from cachetools import TTLCache

from app.domain.ports.cache import CacheBackend


class InMemoryCache(CacheBackend):
    def __init__(self, ttl_seconds: int = 300, maxsize: int = 512):
        # TTLCache tự đuổi phần tử hết hạn và phần tử cũ nhất khi đầy.
        self._c: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_seconds)

    def get(self, key: str) -> str | None:
        return self._c.get(key)

    def set(self, key: str, value: str) -> None:
        self._c[key] = value
