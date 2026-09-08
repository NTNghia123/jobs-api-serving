"""Port: hợp đồng giới hạn tốc độ theo client.

[FILE MỚI - TUẦN 6]  ·  Hướng dẫn §4  ·  Quyết định: docs/adr/ADR-013

Chỉ khai báo CÁI GÌ (client này còn quota không). Impl (token bucket in-process /
Redis sau này) nằm ở app/infrastructure/ratelimit/. Cùng khuôn với CacheBackend.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class RateLimiter(ABC):
    @abstractmethod
    def allow(self, client_id: str) -> bool:
        """True nếu request được phép; False nếu client đã vượt hạn mức."""
