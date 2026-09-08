"""/v1/jobs/search — hoạt động nghiệp vụ chính.

[FILE SỬA]  Đích thật: app/api/jobs.py
So với bản Tuần 3, chỉ THÊM: 1 import + 1 instance validator + 1 dòng gọi
validator.validate_search(req) ở ĐẦU handler (đánh dấu ★). Đây là lần ĐẦU TIÊN được
phép sửa jobs.py — đúng như comment đã báo trước từ Tuần 2.

Tuần 2: hợp đồng đầy đủ, dữ liệu từ FakeJobRepository.
Tuần 3: đổi repository sang DuckDB — file này KHÔNG đổi.
Tuần 4: chèn QueryValidator vào trước lời gọi repo.   ← TUẦN NÀY
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.deps import get_repository
from app.domain.pagination import decode_page_token, encode_page_token, filters_fingerprint
from app.domain.validator import QueryValidator          # ★ THÊM Ở TUẦN 4
from app.models.common import ErrorResponse
from app.models.jobs import SearchRequest, SearchResponse
from app.observability.logging import get_request_id, log_event
from app.settings import get_settings
from app.domain.ports.job_repository import JobRepository

router = APIRouter(tags=["jobs"])
logger = logging.getLogger("api.jobs")
validator = QueryValidator()                              # ★ THÊM Ở TUẦN 4 (stateless, dùng chung)


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

    # ★ THÊM Ở TUẦN 4 — CỔNG KIỂM SOÁT: chạy TRƯỚC khi chạm repository/SQL.
    # GIẢI THÍCH: Pydantic đã chặn phần lớn ở biên; đây là lớp chính sách + phòng thủ
    # theo chiều sâu, và là chỗ chính sách tương lai (k-anonymity, dimension/metric) sống.
    validator.validate_search(req)

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
