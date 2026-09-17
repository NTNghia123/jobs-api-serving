"""Validate đầy đủ response 200 mà không import Locust/gevent."""
from __future__ import annotations

from pydantic import ValidationError

from app.models.common import HealthResponse
from app.models.jobs import SearchResponse
from app.models.market import MarketMetricsResponse
from app.models.metadata import MetadataResponse
from tests.load import config as C

_MODELS = {
    C.EP_SEARCH: SearchResponse,
    C.EP_MARKET: MarketMetricsResponse,
    C.EP_HEALTH: HealthResponse,
    C.EP_METADATA: MetadataResponse,
}


def valid_success_body(endpoint: str, body: object) -> bool:
    """Chỉ True khi body khớp toàn bộ Pydantic contract (kể cả kiểu và extra=forbid)."""
    model = _MODELS.get(endpoint)
    if model is None or not isinstance(body, dict) or set(body) != set(model.model_fields):
        return False
    try:
        model.model_validate(body)
        return True
    except (ValidationError, TypeError, ValueError):
        return False


def enforce_duration_limit(ok: bool, err_class: str | None,
                           response_time_ms: float) -> tuple[bool, str | None]:
    """Response đúng contract nhưng tổng wall-time >25s vẫn là lỗi zero-tolerance theo plan."""
    if response_time_ms > C.MAX_REQUEST_DURATION_MS:
        # Giữ lớp lỗi gốc để report chẩn đoán vẫn phân biệt được 504/5xx chậm.
        return False, f"{err_class or 'client'}_over_25s"
    return ok, err_class


def base_error_class(err_class: str | None) -> str | None:
    """Lấy lớp lỗi gốc để control-flow vẫn nhận 400/401/429 khi response đồng thời quá 25s."""
    suffix = "_over_25s"
    if err_class and err_class.endswith(suffix):
        return err_class[:-len(suffix)]
    return err_class
