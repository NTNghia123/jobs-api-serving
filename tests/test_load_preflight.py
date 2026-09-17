from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from requests import exceptions as rex

from tests.load.preflight import check_pagination


def _body(token="token"):
    return {
        "items": [], "next_page_token": token, "total_estimated": None,
        "as_of": datetime(2026, 9, 1, tzinfo=UTC).isoformat(), "request_id": "r",
    }


def test_preflight_requires_next_page_token_and_labels_request():
    captured = {}

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return SimpleNamespace(status_code=200, json=lambda: _body())

    ok, reason, _ = check_pagination(
        "https://svc.example/", {"X-API-Key": "k"}, "suite-realistic", post=post)
    assert ok is True and reason == ""
    assert captured["headers"]["X-Request-ID"] == (
        "lt_suite-realistic:warmup:preflight-pagination")
    assert captured["json"]["limit"] == 20

    ok, reason, _ = check_pagination(
        "https://svc.example", {}, "run", post=lambda *a, **k: SimpleNamespace(
            status_code=200, json=lambda: _body(None)))
    assert ok is False and "next_page_token" in reason


def test_preflight_fails_closed_on_http_schema_or_network_error():
    cases = [
        lambda *a, **k: SimpleNamespace(status_code=403, json=lambda: {}),
        lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"items": []}),
    ]
    for post in cases:
        assert check_pagination("https://svc.example", {}, "run", post=post)[0] is False

    def network_error(*args, **kwargs):
        raise rex.ConnectionError("down")

    assert check_pagination("https://svc.example", {}, "run", post=network_error)[0] is False
