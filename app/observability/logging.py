"""Log JSON + request_id.

Tuần 8 mới làm tracing/metric đầy đủ, nhưng request_id và log có cấu trúc phải
có từ bây giờ: chúng là công cụ debug cho chính 6 tuần tới.

QUY TẮC PII: log ghi TÊN filter được dùng, KHÔNG ghi GIÁ TRỊ filter.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

REQUEST_ID_HEADER = "X-Request-ID"
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """Mỗi dòng log là một object JSON — query được bằng Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "request_id": get_request_id(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn có logger riêng, cho nó dùng chung handler
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False


def log_event(logger: logging.Logger, level: int, message: str, **fields) -> None:
    """Helper để log kèm field có cấu trúc."""
    logger.log(level, message, extra={"extra_fields": fields})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Gắn request_id cho mỗi request và log một dòng khi request kết thúc.

    Nhận X-Request-ID từ client nếu có (giúp nối trace xuyên dịch vụ),
    nếu không thì tự sinh.
    """

    def __init__(self, app, logger_name: str = "api.access"):
        super().__init__(app)
        self.logger = logging.getLogger(logger_name)

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex[:16]}"
        token = _request_id.set(rid)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = rid
            return response
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            log_event(
                self.logger,
                logging.INFO,
                "http_request",
                method=request.method,
                path=request.url.path,
                status=status,
                latency_ms=elapsed_ms,
                # client_id sẽ được thêm ở Tuần 6 sau khi có auth
            )
            _request_id.reset(token)
