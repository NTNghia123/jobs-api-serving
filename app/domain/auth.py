"""Logic xác thực API key — THUẦN (không I/O, không FastAPI).

[FILE MỚI - TUẦN 6]  ·  Hướng dẫn §3  ·  Quyết định: docs/adr/ADR-012

Nằm ở domain vì đây là CHÍNH SÁCH (hash + so khớp + hạn dùng), không phụ thuộc hạ tầng.
Việc lấy record ở đâu là của port ApiKeyStore (adapter lo).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.domain.ports.api_key_store import ApiKeyStore, ClientRecord
from app.errors import UnauthorizedError


def hash_key(raw_key: str) -> str:
    """SHA-256 hex của key thô. Server CHỈ lưu giá trị này, không lưu key thô."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def authenticate(raw_key: str, store: ApiKeyStore, now: datetime | None = None) -> ClientRecord:
    """Trả ClientRecord nếu key hợp lệ & chưa hết hạn; ngược lại ném 401.

    Tiêm `now` để test hạn dùng bằng đồng hồ giả.
    """
    now = now or datetime.now(timezone.utc)
    record = store.lookup(hash_key(raw_key))     # so khớp hằng-thời-gian nằm trong store
    if record is None:
        raise UnauthorizedError("API key không hợp lệ")
    if record.expires_at is not None and now > record.expires_at:
        raise UnauthorizedError("API key đã hết hạn")
    return record
