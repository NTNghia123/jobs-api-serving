"""Adapter cache no-op — LOAD-TEST ONLY.

Mọi get() trả None (luôn miss); set() không lưu gì (không ném lỗi). Dùng cho "market cache-miss
microtest": vô hiệu app-cache một cách TẤT ĐỊNH để đo đường lạnh đầy đủ của /market/metrics, thay vì
dựa vào TTL=0 (phụ thuộc độ phân giải thời gian). Guard ở settings cấm backend này khi env=prod.
"""
from __future__ import annotations

from app.domain.ports.cache import CacheBackend


class NoOpCache(CacheBackend):
    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str) -> None:
        # Cố ý không lưu: mọi lần đọc sau vẫn là miss.
        return None
