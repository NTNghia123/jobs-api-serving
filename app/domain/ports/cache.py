"""Port: hợp đồng cache của tầng phục vụ.

Chỉ khai báo CÁI GÌ (get/set chuỗi). Impl cụ thể (in-memory / Redis) nằm ở
app/infrastructure/cache/. Xem docs/adr/ADR-009, ADR-011.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class CacheBackend(ABC):
    @abstractmethod
    def get(self, key: str) -> str | None:
        """Trả chuỗi đã cache, hoặc None nếu miss/hết hạn."""

    @abstractmethod
    def set(self, key: str, value: str) -> None:
        """Lưu chuỗi kèm TTL (do backend quản lý)."""
