"""/v1/metadata — API tự mô tả chính nó.

Vì sao endpoint này tồn tại: consumer (đặc biệt là agent LLM) cần biết được
phép gửi filter nào và giá trị nào, mà không phải hỏi team Data. Nó cũng làm
giảm áp lực "xin thêm filter" — thứ dễ biến allowlist thành nút thắt.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_repository
from app.domain.catalog import (
    DEFAULT_LIMIT,
    FILTERS,
    MAX_EXPERIENCE_YEARS,
    MAX_LIMIT,
    METRICS,
    SORT_OPTIONS,
)
from app.models.metadata import FilterInfo, LimitsInfo, MetadataResponse, MetricInfo
from app.observability.logging import get_request_id
from app.settings import get_settings
from app.warehouse.base import JobRepository

router = APIRouter(tags=["discovery"])


@router.get(
    "/metadata",
    response_model=MetadataResponse,
    summary="Liệt kê filter, metric và giới hạn hợp lệ",
    operation_id="getMetadata",
)
def metadata(repo: JobRepository = Depends(get_repository)) -> MetadataResponse:
    return MetadataResponse(
        version=get_settings().api_version,
        as_of=repo.as_of(),
        filters=[FilterInfo(**f.__dict__) for f in FILTERS],
        metrics=[MetricInfo(**m.__dict__) for m in METRICS],
        sort_options=list(SORT_OPTIONS),
        limits=LimitsInfo(
            default_limit=DEFAULT_LIMIT,
            max_limit=MAX_LIMIT,
            max_experience_years=MAX_EXPERIENCE_YEARS,
        ),
        request_id=get_request_id(),
    )
