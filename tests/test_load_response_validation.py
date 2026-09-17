from __future__ import annotations

from datetime import UTC, datetime

from tests.load import config as C
from tests.load.response_validation import (
    base_error_class,
    enforce_duration_limit,
    valid_success_body,
)


def test_search_response_requires_full_contract_not_just_items():
    partial = {"items": [], "request_id": "r"}
    assert valid_success_body(C.EP_SEARCH, partial) is False
    complete = {
        "items": [], "next_page_token": None, "total_estimated": None,
        "as_of": datetime(2026, 9, 1, tzinfo=UTC).isoformat(), "request_id": "r",
    }
    assert valid_success_body(C.EP_SEARCH, complete) is True


def test_market_response_rejects_wrong_item_shape_and_extra_fields():
    body = {
        "dimension": "seniority", "window": "90d", "window_start": "2026-06-04",
        "window_end": "2026-09-01", "items": [{"dimension_value": "senior"}],
        "as_of": "2026-09-01T00:00:00Z", "request_id": "r",
    }
    assert valid_success_body(C.EP_MARKET, body) is False
    body["items"] = []
    body["unexpected"] = True
    assert valid_success_body(C.EP_MARKET, body) is False


def test_unknown_endpoint_is_not_a_valid_success():
    assert valid_success_body("GET /unknown", {}) is False


def test_success_over_25_seconds_becomes_zero_tolerance_error():
    assert enforce_duration_limit(True, None, 25_000) == (True, None)
    assert enforce_duration_limit(True, None, 25_000.1) == (False, "client_over_25s")
    assert enforce_duration_limit(False, "server_5xx", 30_000) == (
        False, "server_5xx_over_25s")


def test_slow_error_keeps_base_class_for_control_flow():
    for original in ("bad_request", "auth_denied", "rate_limited", "server_504"):
        _ok, decorated = enforce_duration_limit(False, original, 30_000)
        assert base_error_class(decorated) == original
    assert base_error_class(None) is None
