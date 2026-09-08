"""Port: hợp đồng tra cứu API key.

[FILE MỚI - TUẦN 6]  ·  Hướng dẫn §2  ·  Quyết định: docs/adr/ADR-012

Chỉ khai báo CÁI GÌ (tra key_hash → client). Impl cụ thể (đọc từ config/DB) nằm ở
app/infrastructure/auth/. Store KHÔNG bao giờ giữ key thô — chỉ giữ HASH.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ClientRecord:
    """Danh tính một client đã xác thực (KHÔNG chứa key thô)."""
    client_id: str
    expires_at: datetime | None = None   # None = không hết hạn


class ApiKeyStore(ABC):
    @abstractmethod
    def lookup(self, key_hash: str) -> ClientRecord | None:
        """Trả ClientRecord nếu key_hash khớp một key đã đăng ký; None nếu không.

        So khớp phải dùng hằng-thời-gian (compare_digest) — xem impl.
        """
