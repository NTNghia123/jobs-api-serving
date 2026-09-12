"""/v1/market/metrics — benchmark lương theo source | seniority | category.

Đọc gold đã tổng hợp sẵn. k-anonymity dựa trên salary_sample_count (ADR-020).
window 90d neo theo as_of_date của batch (data cutoff). Cache key gắn batch+env+window+
dimension để publish batch mới không trả cache cũ (ADR-026). Xem docs/adr/ADR-020, ADR-026.
"""
from __future__ import annotations

import logging
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends

from app.api.deps import get_cache, get_metrics_repository
from app.domain.ports.cache import CacheBackend
from app.domain.ports.metrics_repository import MetricsRepository
from app.domain.validator import QueryValidator
from app.models.common import ErrorResponse
from app.models.enums import MetricDimension, MetricWindow
from app.models.market import MarketMetricRow, MarketMetricsResponse
from app.observability.logging import get_request_id, log_event
from app.settings import get_settings

router = APIRouter(tags=["market"])
logger = logging.getLogger("api.market")
validator = QueryValidator()      # stateless, dùng chung

_VN_TZ = timezone(timedelta(hours=7))   # Asia/Ho_Chi_Minh: UTC+7 cố định (không DST) — khỏi cần tzdata


def _display_name(value: str) -> str | None:
    return "Unknown" if value == "unknown" or value.endswith(":unknown") else None


@router.get(
    "/market/metrics",
    response_model=MarketMetricsResponse,
    summary="Chỉ số lương theo source | seniority | category",
    description=(
        "Đọc từ gold đã tổng hợp sẵn. median_salary_vnd_month = null khi salary_sample_count "
        "< ngưỡng k-anonymity. window: 90d (neo theo as_of_date) hoặc all_time. Đơn vị VND/tháng."
    ),
    operation_id="getMarketMetrics",
    responses={400: {"model": ErrorResponse, "description": "dimension/window không hợp lệ"}},
)
def market_metrics(
    dimension: MetricDimension = MetricDimension.SENIORITY,
    window: MetricWindow = MetricWindow.D90,
    repo: MetricsRepository = Depends(get_metrics_repository),
    cache: CacheBackend = Depends(get_cache),
) -> MarketMetricsResponse:
    settings = get_settings()
    dim, win = dimension.value, window.value
    validator.validate_dimension(dim)   # phòng thủ theo chiều sâu (Pydantic enum đã chặn ở biên)

    # Đọc batch đang phục vụ MỘT lần: batch_id (cache key) + as_of (neo window). Dùng cùng
    # batch_id cho cả cache key lẫn market_metrics → không lệch nếu batch mới publish giữa chừng.
    batch = repo.current_batch()
    as_of = batch.as_of
    as_of_date = as_of.astimezone(_VN_TZ).date()   # neo cửa sổ 90d theo giờ VN (tránh lệch ngày)
    if win == MetricWindow.D90.value:
        window_end, window_start = as_of_date, as_of_date - timedelta(days=89)
    else:
        window_end = window_start = None

    # Cache key gắn batch_id (invariant 5, ADR-026): env + batch_id + window + dimension.
    # batch_id duy nhất mỗi batch → publish batch mới không trả cache cũ; hai batch cùng ngày
    # KHÔNG còn collision (khác hẳn as_of_date surrogate cũ).
    cache_key = f"{settings.env}:metrics:v1:{batch.batch_id}:{win}:{dim}"
    cached = cache.get(cache_key)
    if cached is not None:
        resp = MarketMetricsResponse.model_validate_json(cached)
        log_event(logger, logging.INFO, "market_metrics", dimension=dim, window=win, cache="hit")
        return resp.model_copy(update={"request_id": get_request_id()})

    rows = repo.market_metrics(dim, win, batch.batch_id)
    items = [
        MarketMetricRow(
            dimension_value=r.dimension_value,
            display_name=_display_name(r.dimension_value),
            posting_count=r.posting_count,
            salary_disclosed_count=r.salary_disclosed_count,
            salary_sample_count=r.salary_sample_count,
            # k-anonymity: che median khi SỐ MẪU lương < ngưỡng (không dựa posting_count).
            median_salary_vnd_month=validator.suppress_if_small(
                r.salary_sample_count, r.median_salary_vnd_month,
                min_size=settings.metrics_min_sample_size,
            ),
        )
        for r in rows
    ]

    resp = MarketMetricsResponse(
        dimension=dim, window=win, window_start=window_start, window_end=window_end,
        items=items, as_of=as_of, request_id=get_request_id(),
    )
    cache.set(cache_key, resp.model_dump_json())
    log_event(logger, logging.INFO, "market_metrics", dimension=dim, window=win,
              cache="miss", groups=len(items))
    return resp
