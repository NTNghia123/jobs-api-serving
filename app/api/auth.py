"""Inbound adapter: dependency FastAPI xác thực + rate-limit mỗi request.

[FILE MỚI - TUẦN 6]  ·  Hướng dẫn §5

Gắn vào router /v1 (main.py). /health KHÔNG gắn → luôn mở. Thứ tự: xác thực (401) →
rate-limit (429) → ghi client_id vào context + request.state (cho log).
"""
from __future__ import annotations

from fastapi import Depends, Header, Request

from app.api.deps import get_api_key_store, get_rate_limiter
from app.domain.auth import authenticate
from app.domain.ports.api_key_store import ApiKeyStore
from app.domain.ports.rate_limiter import RateLimiter
from app.errors import RateLimitExceededError, UnauthorizedError
from app.observability.logging import set_client_id


def authorize(request: Request, raw_key: str | None,
              store: ApiKeyStore, limiter: RateLimiter) -> str:
    """Lõi thuần (test được trực tiếp, không cần server). Trả client_id hoặc ném lỗi."""
    if not raw_key:
        raise UnauthorizedError("Thiếu header X-API-Key")     # 401
    record = authenticate(raw_key, store)                     # 401 nếu sai/hết hạn
    if not limiter.allow(record.client_id):
        raise RateLimitExceededError("Vượt giới hạn tốc độ")  # 429
    # Ghi danh tính vào 2 nơi:
    set_client_id(record.client_id)                 # (a) ContextVar → log sự kiện trong handler
    request.state.client_id = record.client_id      # (b) request.state → dòng access-log (xem §5 bẫy)
    return record.client_id


def require_client(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    store: ApiKeyStore = Depends(get_api_key_store),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> str:
    """Dependency gắn cho mọi endpoint /v1. Trả client_id để handler dùng nếu cần."""
    return authorize(request, x_api_key, store, limiter)
