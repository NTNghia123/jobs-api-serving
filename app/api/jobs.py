"""/v1/jobs/search — hoạt động nghiệp vụ chính.

Tuần 2: hợp đồng đã đầy đủ, dữ liệu đến từ FakeJobRepository.
Tuần 3: đổi repository sang warehouse adapter thật — file này không đổi.
Tuần 4: chèn QueryValidator vào trước lời gọi repo.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.deps import get_repository
from app.domain.pagination import decode_page_token, encode_page_token, filters_fingerprint
from app.models.common import ErrorResponse
from app.models.jobs import SearchRequest, SearchResponse
from app.observability.logging import get_request_id, log_event
from app.settings import get_settings
from app.warehouse.base import JobRepository

router = APIRouter(tags=["jobs"])
logger = logging.getLogger("api.jobs")


@router.post(
    "/jobs/search",
    response_model=SearchResponse,
    summary="Tìm tin tuyển dụng theo bộ lọc",
    description=(
        "Chỉ đọc. Dùng POST vì bộ filter có cấu trúc lồng nhau và để giá trị filter "
        "không nằm trong URL/log (xem ADR-002). Endpoint KHÔNG thay đổi trạng thái — "
        "retry an toàn."
    ),
    operation_id="searchJobs",
    responses={
        400: {"model": ErrorResponse, "description": "Filter không hợp lệ hoặc thiếu"},
        504: {"model": ErrorResponse, "description": "Truy vấn kho quá hạn"},
    },
)
def search_jobs(
    req: SearchRequest,
    repo: JobRepository = Depends(get_repository),
) -> SearchResponse:
    settings = get_settings()

    # Vân tay của "hình dạng truy vấn" — token chỉ dùng lại được cho đúng truy vấn đó.
    fingerprint = filters_fingerprint(
        {
            "filters": req.filters.model_dump(mode="json"),
            "sort": req.sort.value,
        }
    )

    cursor = None
    if req.page_token:
        cursor = decode_page_token(
            req.page_token, fingerprint=fingerprint, secret=settings.page_token_secret
        )

    result = repo.search(req, cursor)

    next_token = (
        encode_page_token(result.next_cursor, fingerprint=fingerprint, secret=settings.page_token_secret)
        if result.next_cursor
        else None
    )

    # QUY TẮC PII: chỉ log TÊN filter, không log giá trị.
    log_event(
        logger,
        logging.INFO,
        "jobs_search",
        filters_used=req.filters.active_names(),
        limit=req.limit,
        sort=req.sort.value,
        returned=len(result.items),
        paged=bool(req.page_token),
    )

    return SearchResponse(
        items=result.items,
        next_page_token=next_token,
        total_estimated=result.total_estimated,
        as_of=result.as_of,
        request_id=get_request_id(),
    )
