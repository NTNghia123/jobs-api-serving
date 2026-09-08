"""Adapter: RateLimiter in-process bằng thuật toán token bucket.

[FILE MỚI - TUẦN 6]  ·  Hướng dẫn §4  ·  Quyết định: docs/adr/ADR-013

Mỗi client một 'xô token': token hồi đầy theo thời gian (rate/giây), mỗi request tốn 1.
Hết token → chặn (429). In-process → CHỈ đúng khi 1 instance; nhiều instance cần Redis
(ADR-013), giống hạn chế của cache in-process (ADR-009).
"""
from __future__ import annotations

import time

from app.domain.ports.rate_limiter import RateLimiter


class InMemoryRateLimiter(RateLimiter):
    def __init__(self, rate_per_sec: float, capacity: float, *, now=time.monotonic):
        self._rate = rate_per_sec         # token hồi mỗi giây
        self._cap = capacity              # sức chứa tối đa (cho phép burst)
        self._now = now                   # tiêm được để test (đồng hồ giả)
        self._state: dict[str, tuple[float, float]] = {}   # client -> (tokens, last_ts)

    def allow(self, client_id: str) -> bool:
        now = self._now()
        tokens, last = self._state.get(client_id, (self._cap, now))
        # hồi token theo thời gian trôi qua, trần là capacity
        tokens = min(self._cap, tokens + (now - last) * self._rate)
        if tokens < 1.0:
            self._state[client_id] = (tokens, now)   # cập nhật mốc, vẫn từ chối
            return False
        self._state[client_id] = (tokens - 1.0, now)
        return True
