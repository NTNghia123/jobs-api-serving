"""/v1/market/metrics — benchmark lương theo cấp bậc / quốc gia (Tuần 5).

Đây là chỗ lớp QueryValidator của Tuần 4 phát huy giá trị THẬT:
  - validate_dimension(): chỉ nhận seniority|country (allowlist).
  - suppress_if_small(): che median của nhóm quá nhỏ (k-anonymity).
Caching qua interface CacheBackend (in-memory hoặc Redis, chọn bằng config — ADR-009).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_cache, get_metrics_repository
from app.domain.ports.cache import CacheBackend
from app.domain.ports.metrics_repository import MetricsRepository
from app.domain.validator import QueryValidator
from app.models.common import ErrorResponse
from app.models.market import MarketMetricRow, MarketMetricsResponse
from app.observability.logging import get_request_id, log_event

router = APIRouter(tags=["market"])
logger = logging.getLogger("api.market")

validator = QueryValidator()      # stateless, dùng chung


@router.get(
    "/market/metrics",
    response_model=MarketMetricsResponse,
    summary="Chỉ số lương theo cấp bậc hoặc quốc gia",
    description=(
        "Đọc từ gold table đã tổng hợp sẵn (nhanh). Nhóm nhỏ hơn ngưỡng k-anonymity "
        "sẽ có median_salary = null. Đơn vị lương không xác định trong nguồn."
    ),
    operation_id="getMarketMetrics",
    responses={400: {"model": ErrorResponse, "description": "dimension không hợp lệ"}},
)
def market_metrics(
    dimension: str = Query("seniority", description="seniority | country"),
    repo: MetricsRepository = Depends(get_metrics_repository),
    cache: CacheBackend = Depends(get_cache),
) -> MarketMetricsResponse:
    # 1) CỔNG KIỂM SOÁT: chỉ nhận dimension trong allowlist (W4).
    validator.validate_dimension(dimension)

    # 2) CACHE: cache lưu CHUỖI JSON → parse lại thành model khi hit.
    cache_key = f"metrics:{dimension}"
    cached = cache.get(cache_key)
    if cached is not None:
        resp = MarketMetricsResponse.model_validate_json(cached)
        log_event(logger, logging.INFO, "market_metrics", dimension=dimension, cache="hit")
        # Dữ liệu tái dùng, nhưng request_id phải là của REQUEST HIỆN TẠI (không lấy id cũ).
        return resp.model_copy(update={"request_id": get_request_id()})

    # 3) Đọc gold (đã nướng sẵn) rồi ÁP k-anonymity từng nhóm.
    rows = repo.market_metrics(dimension)
    items = [
        MarketMetricRow(
            dimension_value=r.dimension_value,
            median_salary=validator.suppress_if_small(r.posting_count, r.median_salary),
            posting_count=r.posting_count,
        )
        for r in rows
    ]
    as_of = rows[0].as_of if rows else datetime.now(UTC)

    resp = MarketMetricsResponse(
        dimension=dimension, items=items, as_of=as_of, request_id=get_request_id()
    )
    # 4) Ghi cache dạng JSON (dùng chung cho memory lẫn Redis).
    cache.set(cache_key, resp.model_dump_json())
    log_event(logger, logging.INFO, "market_metrics", dimension=dimension,
              cache="miss", groups=len(items))
    return resp
