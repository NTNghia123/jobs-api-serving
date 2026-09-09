"""Adapter: ApiKeyStore đọc key từ config (settings).

[FILE MỚI - TUẦN 6]  ·  Hướng dẫn §3

Giữ danh sách (client_id, key_hash, expires_at) trong bộ nhớ. So khớp bằng
hmac.compare_digest (hằng-thời-gian, chống timing attack — giống app/domain/pagination.py).
KHÔNG giữ key thô. Prod nạp key qua env JOBS_API_API_KEYS (JSON); local có key dev sẵn.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime

from app.domain.auth import hash_key
from app.domain.ports.api_key_store import ApiKeyStore, ClientRecord

# Key DEV cho môi trường local — TIỆN cho /docs và test. KHÔNG dùng ở prod.
DEV_CLIENT_ID = "team-ai-dev"
DEV_API_KEY = "dev-local-key-team-ai"   # plaintext chỉ để dev; server vẫn chỉ lưu hash


@dataclass(frozen=True)
class _Entry:
    client_id: str
    key_hash: str
    expires_at: datetime | None


class ConfigApiKeyStore(ApiKeyStore):
    def __init__(self, entries: list[_Entry]):
        self._entries = entries

    @classmethod
    def from_settings(cls, settings) -> ConfigApiKeyStore:
        """Dựng store từ settings.api_keys; nếu rỗng và env=local thì seed key dev."""
        entries = [
            _Entry(client_id=cid, key_hash=rec.key_sha256, expires_at=rec.expires_at)
            for cid, rec in settings.api_keys.items()
        ]
        if not entries and settings.env == "local":
            entries.append(_Entry(DEV_CLIENT_ID, hash_key(DEV_API_KEY), None))
        return cls(entries)

    def lookup(self, key_hash: str) -> ClientRecord | None:
        for e in self._entries:
            # compare_digest: so khớp hằng-thời-gian, không rẽ nhánh sớm theo ký tự.
            if hmac.compare_digest(e.key_hash, key_hash):
                return ClientRecord(client_id=e.client_id, expires_at=e.expires_at)
        return None
