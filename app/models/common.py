"""Model dùng chung cho mọi endpoint."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Mã lỗi ổn định, dành cho máy đọc.", examples=["INVALID_FILTER"])
    message: str = Field(description="Mô tả cho người đọc log. Không dùng để so sánh trong code.")
    field: str | None = Field(default=None, description="Trường gây lỗi, nếu xác định được.")
    request_id: str = Field(description="Dùng khi báo lỗi cho team Data.")


class ErrorResponse(BaseModel):
    """Envelope lỗi duy nhất của toàn bộ API."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "error": {
                    "code": "INVALID_FILTER",
                    "message": "Filter 'company_email' không nằm trong danh sách cho phép",
                    "field": "filters.company_email",
                    "request_id": "req_01J9XKQ2R7",
                }
            }
        },
    )

    error: ErrorDetail


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="ok", examples=["ok"])
    service: str
    version: str
    env: str
    checked_at: datetime
