"""Cây lỗi của ứng dụng + envelope lỗi thống nhất.

Vì sao cần: consumer là máy. Một hệ thống trả lỗi mỗi chỗ một kiểu buộc phía
gọi phải viết code đoán lỗi. Ở đây mọi lỗi — kể cả lỗi validate của FastAPI và
lỗi 500 ngoài dự kiến — đều đi ra dưới cùng một cấu trúc JSON.
"""
from __future__ import annotations


class AppError(Exception):
    """Lỗi nghiệp vụ đã lường trước. Luôn có mã ổn định để máy đọc."""

    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


class InvalidFilterError(AppError):
    status_code = 400
    code = "INVALID_FILTER"


class MissingRequiredFilterError(AppError):
    status_code = 400
    code = "MISSING_REQUIRED_FILTER"


class InvalidRequestError(AppError):
    status_code = 400
    code = "INVALID_REQUEST"


class LimitExceededError(AppError):
    status_code = 400
    code = "LIMIT_EXCEEDED"


class InvalidPageTokenError(AppError):
    status_code = 400
    code = "INVALID_PAGE_TOKEN"


class UnauthorizedError(AppError):  # ★ THÊM Ở TUẦN 6 — thiếu/sai/hết hạn API key
    status_code = 401
    code = "UNAUTHORIZED"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class RateLimitExceededError(AppError):  # dùng từ Tuần 6
    status_code = 429
    code = "RATE_LIMIT_EXCEEDED"


class QueryTimeoutError(AppError):  # dùng từ Tuần 3
    status_code = 504
    code = "QUERY_TIMEOUT"


class UpstreamUnavailableError(AppError):  # dùng từ Tuần 3
    status_code = 503
    code = "UPSTREAM_UNAVAILABLE"
