"""Điểm khởi tạo ứng dụng.

Đọc file này trước khi đọc phần còn lại: nó cho thấy toàn bộ hình dạng của
dịch vụ — middleware nào chạy, router nào được gắn, lỗi được xử lý ra sao.
"""
from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Request  # ★ THÊM Depends (Tuần 6)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import health, jobs, market, metadata  # ★ THÊM 'market' (Tuần 5)
from app.api.auth import require_client  # ★ THÊM Ở TUẦN 6
from app.errors import AppError
from app.models.common import ErrorDetail, ErrorResponse
from app.observability.logging import (
    RequestContextMiddleware,
    configure_logging,
    get_request_id,
    log_event,
)
from app.settings import get_settings

logger = logging.getLogger("api.error")

API_DESCRIPTION = """
API chỉ-đọc phục vụ dữ liệu tin tuyển dụng cho các hệ thống nội bộ (team AI).

**Xác thực**: mọi endpoint `/v1` cần header `X-API-Key`. `/health` không cần. Vượt hạn mức → 429.

**Nguyên tắc sử dụng**
* Chỉ dùng filter và giá trị có trong `GET /v1/metadata` (seniority, experience_max, salary_min, country).
* Chỉ dùng filter và giá trị có trong `GET /v1/metadata`.
* `page_token` là chuỗi mờ đã ký: lấy nguyên văn từ response trước, không tự tạo, không sửa.
* Mọi response đều có `as_of` — hãy kiểm tra độ tươi trước khi hiển thị cho người dùng cuối.
* API không trả về bất kỳ thông tin cá nhân nào (liên hệ nhà tuyển dụng, dữ liệu ứng viên).

**Khi gặp lỗi**: đọc `error.code` (ổn định, dành cho máy) và gửi kèm `error.request_id` khi báo lỗi.
"""

TAGS_METADATA = [
    {"name": "operations", "description": "Vận hành: kiểm tra sống."},
    {"name": "discovery", "description": "Tự mô tả: filter và metric hợp lệ."},
    {"name": "jobs", "description": "Tìm kiếm tin tuyển dụng."},
    {"name": "market", "description": "Benchmark lương theo cấp bậc/quốc gia."},   # ★ Tuần 5
]


def _error_response(status: int, code: str, message: str, field: str | None = None) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, field=field, request_id=get_request_id())
    )
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="Jobs Serving API",
        version="1.0.0",
        description=API_DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        docs_url="/docs",
        openapi_url="/openapi.json",
        contact={"name": "Team Data", "email": "data@example.com"},
    )

    app.add_middleware(RequestContextMiddleware)

    # ---- Xử lý lỗi: mọi lỗi ra ngoài đều cùng một envelope ----
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError):
        return _error_response(exc.status_code, exc.code, exc.message, exc.field)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError):
        """Đổi 422 mặc định của FastAPI thành 400 theo envelope của ta.

        Vì sao: consumer chỉ nên phải học MỘT cấu trúc lỗi. 422 với schema riêng
        của FastAPI là chi tiết cài đặt bị rò ra ngoài hợp đồng.
        """
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body") or None
        msg = first.get("msg", "Request không hợp lệ")
        code = "INVALID_FILTER" if loc and loc.startswith("filters.") else "INVALID_REQUEST"
        return _error_response(400, code, msg, loc)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception):
        """Không bao giờ để chi tiết nội bộ (stack trace, tên bảng) lọt ra ngoài."""
        log_event(logger, logging.ERROR, "unhandled_exception", path=request.url.path)
        logger.exception("unhandled")
        return _error_response(500, "INTERNAL", "Lỗi nội bộ. Hãy gửi kèm request_id khi báo lỗi.")

    # ---- Router ----
    # ★ Tuần 6: /health KHÔNG có auth (luôn mở). Ba router /v1 gắn require_client ở cấp
    # router → mọi endpoint dữ liệu đều sau lớp xác thực + rate-limit. /docs và
    # /openapi.json vẫn mở (là tài liệu hợp đồng cho team AI ở Tuần 8).
    auth = [Depends(require_client)]                                       # ★ THÊM Ở TUẦN 6
    app.include_router(health.router)                                      # /health (mở)
    app.include_router(metadata.router, prefix="/v1", dependencies=auth)   # ★ /v1/metadata
    app.include_router(jobs.router, prefix="/v1", dependencies=auth)       # ★ /v1/jobs/search
    app.include_router(market.router, prefix="/v1", dependencies=auth)     # ★ /v1/market/metrics

    return app


app = create_app()
