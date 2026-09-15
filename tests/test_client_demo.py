"""Test client mẫu bàn giao (examples/client_demo.py) — TUẦN 8.

Ba nhóm:
  1. Phân trang: monkeypatch urlopen, kiểm URL/method/X-API-Key/body/timeout + token forwarding.
  2. Hiển thị: đưa JobItem THẬT (từ TestClient) qua format_job → chứng minh dùng field contract mới.
  3. Lỗi: HTTPError (envelope JSON / body không JSON) + URLError; và fail-fast khi thiếu key.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from examples import client_demo


class _FakeResp:
    def __init__(self, payload: dict):
        self._b = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self) -> bytes:
        return self._b


class _Recorder:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # (Request, timeout)

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        return _FakeResp(self._responses.pop(0))


def _header(req, name: str):
    for k, v in req.header_items():
        if k.lower() == name.lower():
            return v
    return None


# ---- 1) Phân trang --------------------------------------------------------------------

def test_search_all_pages_forwards_token(monkeypatch):
    rec = _Recorder([
        {"items": [{"job_id": "a"}], "next_page_token": "tok1", "as_of": "2026-01-01"},
        {"items": [{"job_id": "b"}], "next_page_token": None, "as_of": "2026-01-01"},
    ])
    monkeypatch.setattr(urllib.request, "urlopen", rec)

    items, as_of, truncated = client_demo.search_all_pages(
        "http://x", "KEY", {"posted_after": "2020-01-01"}, limit=1,
    )

    assert [i["job_id"] for i in items] == ["a", "b"]
    assert as_of == "2026-01-01"
    assert truncated is False

    req1, t1 = rec.calls[0]
    req2, _ = rec.calls[1]
    body1, body2 = json.loads(req1.data), json.loads(req2.data)
    # request 1: chưa có token; posted_after đúng
    assert "page_token" not in body1
    assert body1["filters"]["posted_after"] == "2020-01-01"
    # request 2: gửi LẠI đúng token của trang trước
    assert body2["page_token"] == "tok1"
    # cả hai: X-API-Key + POST + timeout mặc định 30
    for req, timeout in rec.calls:
        assert _header(req, "X-API-Key") == "KEY"
        assert req.get_method() == "POST"
        assert timeout == 30
    assert t1 == 30


# ---- 2) Hiển thị dùng field contract mới ---------------------------------------------

def test_format_job_dung_field_moi(client):
    r = client.post("/v1/jobs/search", json={"filters": {"posted_after": "2020-01-01"}, "limit": 1})
    item = r.json()["items"][0]

    line = client_demo.format_job(item)

    # Hiển thị phải dùng location_text + effective_posted_date (lỗi cũ dùng j["country"]).
    assert (item["location_text"] or "-") in line
    assert str(item["effective_posted_date"]) in line
    if item["categories"]:
        assert item["categories"][0]["category_key"] in line


# ---- 3) Xử lý lỗi ---------------------------------------------------------------------

def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "Reason", {}, io.BytesIO(body))


def test_describe_error_envelope_json():
    err = _http_error(400, json.dumps(
        {"error": {"code": "INVALID_FILTER", "message": "bad", "request_id": "req_1"}}
    ).encode())
    msg = client_demo.describe_error(err)
    assert "INVALID_FILTER" in msg
    assert "req_1" in msg


def test_describe_error_body_khong_json():
    err = _http_error(503, b"<html>Service Unavailable</html>")
    msg = client_demo.describe_error(err)
    assert "503" in msg          # fallback status/reason, không crash


def test_describe_error_json_khong_envelope():
    # Body là JSON hợp lệ nhưng KHÔNG phải envelope (list) → không crash, fallback status.
    err = _http_error(400, json.dumps(["not", "an", "envelope"]).encode())
    msg = client_demo.describe_error(err)
    assert "400" in msg


def test_describe_error_urlerror():
    msg = client_demo.describe_error(urllib.error.URLError("connection refused"))
    assert "ket noi" in msg.lower()


def test_describe_error_timeout():
    # read-phase timeout ném TimeoutError (không bọc URLError) → phải xử lý an toàn.
    msg = client_demo.describe_error(TimeoutError("timed out"))
    assert "ket noi" in msg.lower()


def test_format_job_salary_mot_phia():
    # Kiểm _fmt_salary trực tiếp: một phía KHÔNG được in "None".
    only_min = client_demo._fmt_salary(30_000_000, None)
    only_max = client_demo._fmt_salary(None, 50_000_000)
    nego = client_demo._fmt_salary(None, None)
    both = client_demo._fmt_salary(30_000_000, 50_000_000)
    assert only_min == ">= 30000000 VND/thang"
    assert only_max == "<= 50000000 VND/thang"
    assert nego == "thoa thuan"
    assert both == "30000000-50000000 VND/thang"


def test_search_all_pages_ton_trong_max_pages(monkeypatch):
    # Server luôn trả token → phải dừng ở max_pages và báo truncated.
    def always_more(req, timeout=None):
        return _FakeResp({"items": [{"job_id": "z"}], "next_page_token": "t", "as_of": "d"})
    monkeypatch.setattr(urllib.request, "urlopen", always_more)
    items, _as_of, truncated = client_demo.search_all_pages(
        "http://x", "KEY", {"posted_after": "2020-01-01"}, limit=1, max_pages=3,
    )
    assert truncated is True
    assert len(items) == 3


def test_search_all_pages_tu_choi_max_pages_khong_duong():
    with pytest.raises(ValueError):
        client_demo.search_all_pages("http://x", "KEY", {"posted_after": "2020-01-01"}, max_pages=0)


def test_main_tu_choi_max_pages_khong_duong(monkeypatch):
    monkeypatch.setenv("JOBS_API_API_KEY", "KEY")
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("không được gọi mạng khi arg sai"))
    assert client_demo.main(["--base-url", "http://x", "--max-pages", "0"]) == 2


@pytest.mark.parametrize("bad_timeout", ["-1", "0", "nan", "inf"])
def test_main_tu_choi_timeout_khong_duong(monkeypatch, bad_timeout):
    monkeypatch.setenv("JOBS_API_API_KEY", "KEY")
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("không được gọi mạng khi timeout sai"))
    assert client_demo.main(["--base-url", "http://x", "--timeout", bad_timeout]) == 2


@pytest.mark.parametrize("bad_url", ["", "localhost:8080", "ftp://x", "/v1"])
def test_main_tu_choi_base_url_khong_hop_le(monkeypatch, bad_url):
    monkeypatch.setenv("JOBS_API_API_KEY", "KEY")
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("không được gọi mạng khi base-url sai"))
    assert client_demo.main(["--base-url", bad_url]) == 2


def test_main_fail_fast_khi_thieu_key(monkeypatch):
    monkeypatch.delenv("JOBS_API_API_KEY", raising=False)
    # Không được gọi mạng khi thiếu key.
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("không được gọi mạng khi thiếu key"))
    assert client_demo.main(["--base-url", "http://x"]) == 2
