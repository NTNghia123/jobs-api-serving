"""/health — endpoint duy nhất không yêu cầu xác thực (từ Tuần 6).

Ghi chú: đây là LIVENESS check — chỉ trả lời "tiến trình còn sống".
Nó KHÔNG gọi xuống kho dữ liệu. Lý do: nếu health check gọi BigQuery thì mỗi
lần Cloud Run probe là một truy vấn tính tiền, và một sự cố nhỏ ở kho sẽ khiến
toàn bộ instance bị khai tử và khởi động lại vô ích.
Readiness check (có gọi kho) sẽ tách riêng ở Tuần 7 nếu cần.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from app.models.common import HealthResponse
from app.settings import get_settings

router = APIRouter(tags=["operations"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Kiểm tra dịch vụ còn sống",
    operation_id="getHealth",
)
def health() -> HealthResponse:
    s = get_settings()
    return HealthResponse(
        status="ok",
        service=s.service_name,
        version=s.api_version,
        env=s.env,
        checked_at=datetime.now(UTC),
    )
