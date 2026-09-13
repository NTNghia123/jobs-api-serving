"""API key có expires_at NAIVE (không tz) không được gây 500.

Trước fix: JSON "2030-01-01T00:00:00" → datetime naive → so với now() aware-UTC ở
domain/auth.py ném TypeError → 500. Validator ở ApiKeyEntry chuẩn hoá naive→UTC.
"""
from __future__ import annotations

import pytest

from app.domain.auth import authenticate, hash_key
from app.errors import UnauthorizedError
from app.infrastructure.auth.config_key_store import ConfigApiKeyStore
from app.settings import Settings

RAW = "some-raw-key-for-test"


def _store(expires_at: str) -> ConfigApiKeyStore:
    s = Settings(
        _env_file=None, env="local",
        api_keys={"c": {"key_sha256": hash_key(RAW), "expires_at": expires_at}},
    )
    return ConfigApiKeyStore.from_settings(s)


def test_naive_future_expiry_khong_500():
    rec = authenticate(RAW, _store("2999-01-01T00:00:00"))   # naive, tương lai → KHÔNG TypeError
    assert rec.client_id == "c"


def test_naive_past_expiry_tra_401():
    with pytest.raises(UnauthorizedError):                    # naive, quá khứ → hết hạn (không 500)
        authenticate(RAW, _store("2000-01-01T00:00:00"))
