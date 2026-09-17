"""Pre-flight pagination trước khi phát tải có chi phí.

Không import Locust để test thuần. Dùng đúng một payload đại diện trong corpus; response phải đúng
contract và có ``next_page_token``. Nếu không, workload 80/20 không thể được chứng minh.
"""
from __future__ import annotations

from collections.abc import Callable

import requests

from tests.load import config as C
from tests.load.corpus import SEARCH_CORPUS
from tests.load.response_validation import valid_success_body


def check_pagination(base_url: str, headers: dict[str, str], run_id: str,
                     post: Callable = requests.post) -> tuple[bool, str, dict | None]:
    """Trả ``(ok, reason, body)``; request được gắn nhãn warmup để BQ cost vẫn truy vết được."""
    representative = SEARCH_CORPUS[0]
    payload = {
        "filters": dict(representative["filters"]),
        "sort": representative["sort"],
        "limit": C.SEARCH_LIMIT,
    }
    request_headers = {
        **headers,
        "X-Request-ID": f"lt_{run_id}:warmup:preflight-pagination",
    }
    try:
        response = post(
            f"{base_url.rstrip('/')}/v1/jobs/search",
            headers=request_headers,
            json=payload,
            timeout=C.HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, f"pre-flight search không kết nối được: {exc}", None
    if response.status_code != 200:
        return False, f"pre-flight search trả HTTP {response.status_code}", None
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return False, "pre-flight search trả JSON không hợp lệ", None
    if not valid_success_body(C.EP_SEARCH, body):
        return False, "pre-flight search trả response sai contract", body
    if not body.get("next_page_token"):
        return False, "payload pre-flight không trả next_page_token; không thể chạy workload 80/20", body
    return True, "", body
