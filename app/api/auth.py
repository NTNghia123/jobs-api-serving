"""Inbound adapter: dependency FastAPI xác thực + rate-limit mỗi request.

[FILE MỚI - TUẦN 6]  ·  Hướng dẫn §5

Gắn vào router /v1 (main.py). /health KHÔNG gắn → luôn mở. Thứ tự: xác thực (401) →
rate-limit (429) → ghi client_id vào request.state (access-log) + ContextVar (log trong handler).

BẪY ContextVar + threadpool: dependency/handler khai báo `def` (sync) được FastAPI chạy trong
THREADPOOL. ContextVar set trong 1 thread KHÔNG quay về async context, và mỗi lần run_sync là
một context COPY → nếu set_client_id() chạy trong `authorize` (threadpool của dependency) thì
handler (threadpool khác) đọc lại get_client_id() = "-". Vì vậy `require_client` là ASYNC: chạy
phần blocking (authenticate + limiter.allow có thể chạm Redis) qua run_in_threadpool, rồi gọi
set_client_id() Ở ASYNC CONTEXT — giá trị này được copy sang context của handler sync → log trong
handler (market_metrics, bq_query) có client_id thật. request.state thì luôn OK vì là object chia sẻ.
"""
from __future__ import annotations

from fastapi import Depends, Header, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_api_key_store, get_rate_limiter
from app.domain.auth import authenticate
from app.domain.ports.api_key_store import ApiKeyStore
from app.domain.ports.rate_limiter import RateLimiter
from app.errors import RateLimitExceededError, UnauthorizedError
from app.observability.logging import set_client_id


def authorize(request: Request, raw_key: str | None,
              store: ApiKeyStore, limiter: RateLimiter) -> str:
    """Lõi thuần BLOCKING (test được trực tiếp). Trả client_id hoặc ném lỗi.

    KHÔNG set ContextVar ở đây — require_client làm việc đó trong async context (xem docstring module).
    request.state.client_id gán TRƯỚC limiter.allow() để request bị 429 vẫn có client_id thật ở access-log.
    """
    if not raw_key:
        raise UnauthorizedError("Thiếu header X-API-Key")     # 401
    record = authenticate(raw_key, store)                     # 401 nếu sai/hết hạn
    request.state.client_id = record.client_id                # request.state chia sẻ qua thread → access-log 429 OK
    if not limiter.allow(record.client_id):
        raise RateLimitExceededError("Vượt giới hạn tốc độ")  # 429
    return record.client_id


async def require_client(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    store: ApiKeyStore = Depends(get_api_key_store),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> str:
    """Dependency gắn cho mọi endpoint /v1. ASYNC để set_client_id() truyền được sang handler."""
    client_id = await run_in_threadpool(authorize, request, x_api_key, store, limiter)
    set_client_id(client_id)   # async context → context copy sang handler sync → log trong handler có id
    return client_id
