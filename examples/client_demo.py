"""Client Python mẫu bàn giao cho team AI — Tuần 8.

Gọi Jobs Serving API chỉ bằng thư viện chuẩn (urllib). Minh hoạ: xác thực X-API-Key,
filter đúng contract (posted_after BẮT BUỘC), phân trang page_token, đọc as_of (độ tươi),
và /v1/metadata + /v1/market/metrics.

Chạy:
    # key đưa qua env (khuyến nghị) — KHÔNG in key ra log
    JOBS_API_API_KEY=<key-thô> python examples/client_demo.py
    # hoặc chỉ định tường minh + trỏ tới Cloud Run
    python examples/client_demo.py --api-key <key> --base-url https://<service>.run.app

Biến môi trường:
    JOBS_API_BASE_URL  (mặc định http://localhost:8080)
    JOBS_API_API_KEY   (bắt buộc nếu không truyền --api-key)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_TIMEOUT = 30  # giây — bất biến timeout lồng nhau: client 30 > Cloud Run 25 > request 20 > query 10


def default_posted_after() -> str:
    """posted_after mặc định = hôm nay (UTC) trừ 90 ngày — không hard-code ngày cố định."""
    return (datetime.now(UTC).date() - timedelta(days=90)).isoformat()


# ---- Hàm THUẦN (được test dùng lại, không I/O) ----------------------------------------

def parse_search_response(data: dict) -> dict:
    """Rút các trường ổn định của envelope /v1/jobs/search."""
    return {
        "items": data["items"],
        "next_page_token": data.get("next_page_token"),
        "as_of": data.get("as_of"),
        "total_estimated": data.get("total_estimated"),
    }


def _fmt_salary(smin, smax) -> str:
    """Hiển thị lương, xử lý cả trường hợp MỘT PHÍA (chỉ min hoặc chỉ max)."""
    if smin is None and smax is None:
        return "thoa thuan"
    if smax is None:
        return f">= {smin} VND/thang"
    if smin is None:
        return f"<= {smax} VND/thang"
    return f"{smin}-{smax} VND/thang"


def format_job(job: dict) -> str:
    """Hiển thị một JobItem theo contract HIỆN TẠI (location_text, salary_*_vnd_month...)."""
    cats = ",".join(c["category_key"] for c in job.get("categories") or []) or "-"
    salary = _fmt_salary(job.get("salary_min_vnd_month"), job.get("salary_max_vnd_month"))
    return (
        f"{job['job_id']:<20} {job['title'][:32]:<32} "
        f"{(job.get('company_name') or '-'):<18} {(job.get('location_text') or '-'):<16} "
        f"{(job.get('seniority') or '-'):<8} {salary:<26} "
        f"posted={job.get('effective_posted_date')} cats={cats}"
    )


def describe_error(exc: Exception) -> str:
    """Thông báo lỗi AN TOÀN — không bao giờ chứa header/API key/request object."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = json.loads(exc.read())
        except (ValueError, json.JSONDecodeError):
            # 502/503/HTML từ Cloud Run/proxy — không phải JSON.
            return f"HTTP {exc.code} {exc.reason}"
        # Body có thể là JSON nhưng KHÔNG phải envelope (list/scalar, hoặc thiếu 'error').
        err = body.get("error") if isinstance(body, dict) else None
        if not isinstance(err, dict):
            return f"HTTP {exc.code} {exc.reason}"
        return (
            f"[{err.get('code', 'HTTP_' + str(exc.code))}] "
            f"{err.get('message', exc.reason)} (request_id={err.get('request_id', '-')})"
        )
    if isinstance(exc, urllib.error.URLError):
        return f"Loi ket noi: {exc.reason}"
    if isinstance(exc, OSError):   # gồm TimeoutError (read-phase timeout không bọc trong URLError)
        return f"Loi ket noi: {exc}"
    return f"Loi khong xac dinh: {exc.__class__.__name__}"


# ---- I/O ------------------------------------------------------------------------------

def _do_request(method: str, url: str, api_key: str, payload: dict | None = None,
                timeout: float = DEFAULT_TIMEOUT) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"X-API-Key": api_key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL do người dùng cấu hình)
        return json.loads(resp.read())


def search_all_pages(base_url: str, api_key: str, filters: dict, limit: int = 3,
                     timeout: float = DEFAULT_TIMEOUT,
                     max_pages: int = 10) -> tuple[list[dict], str | None, bool]:
    """Duyệt kết quả bằng page_token (gửi LẠI token trang trước). CÓ TRẦN trang.

    Trả (items, as_of, truncated). max_pages chặn tải vô hạn trên corpus lớn — client demo
    chỉ minh hoạ vài trang, không kéo hết ~chục nghìn job.
    """
    if max_pages < 1:
        raise ValueError("max_pages phải >= 1")
    items: list[dict] = []
    token: str | None = None
    as_of: str | None = None
    for _ in range(max_pages):
        body: dict = {"filters": filters, "limit": limit}
        if token:
            body["page_token"] = token
        parsed = parse_search_response(
            _do_request("POST", f"{base_url}/v1/jobs/search", api_key, payload=body, timeout=timeout)
        )
        items.extend(parsed["items"])
        as_of = parsed["as_of"]
        token = parsed["next_page_token"]
        if not token:
            return items, as_of, False
    return items, as_of, True   # chạm trần max_pages, còn token → truncated


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Client mẫu gọi Jobs Serving API.")
    ap.add_argument("--base-url", default=os.environ.get("JOBS_API_BASE_URL", DEFAULT_BASE_URL))
    ap.add_argument("--api-key", default=os.environ.get("JOBS_API_API_KEY"))
    ap.add_argument("--posted-after", default=None, help="mặc định = UTC hôm nay - 90 ngày")
    ap.add_argument("--source", default=None, help="topdev | vietnamworks")
    ap.add_argument("--seniority", default=None, help="junior | mid | senior | unknown")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--max-pages", type=int, default=10, help="trần số trang (chống tải vô hạn)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = ap.parse_args(argv)

    if not args.api_key:  # fail-fast TRƯỚC khi gọi mạng
        print("Thiếu API key: đặt env JOBS_API_API_KEY hoặc truyền --api-key.", file=sys.stderr)
        return 2
    if args.max_pages < 1:
        print("--max-pages phải >= 1.", file=sys.stderr)
        return 2
    # urlopen(timeout <=0 / NaN / inf) ném ValueError (không phải OSError) → chặn sớm.
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print("--timeout phải là số hữu hạn > 0.", file=sys.stderr)
        return 2
    # base_url rỗng/không scheme → urlopen ném ValueError (unknown url type) → chặn sớm.
    parsed = urlparse(args.base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        print("--base-url phải là URL http(s) hợp lệ, vd http://localhost:8080.", file=sys.stderr)
        return 2

    base_url = args.base_url.rstrip("/")
    filters: dict = {"posted_after": args.posted_after or default_posted_after()}
    if args.source:
        filters["source"] = args.source
    if args.seniority:
        filters["seniority"] = args.seniority

    try:
        meta = _do_request("GET", f"{base_url}/v1/metadata", args.api_key, timeout=args.timeout)
        filter_names = [f["name"] for f in meta.get("filters", [])]
        print("Filter hợp lệ:", ", ".join(filter_names) or "(xem /docs)")

        jobs, as_of, truncated = search_all_pages(
            base_url, args.api_key, filters, args.limit, args.timeout, args.max_pages,
        )
        note = f" (dừng ở trần {args.max_pages} trang — còn dữ liệu)" if truncated else ""
        print(f"\n{len(jobs)} tin (dữ liệu tính đến {as_of}){note}:")
        for j in jobs:
            print(" ", format_job(j))

        metrics = _do_request(
            "GET", f"{base_url}/v1/market/metrics?dimension=source&window=90d",
            args.api_key, timeout=args.timeout,
        )
        print("\nMarket metrics (source, 90d):")
        for row in metrics.get("items", []):
            print(" ", row)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        # TimeoutError (read-phase) không bọc trong URLError → phải bắt riêng, khỏi crash.
        print("Lỗi gọi API:", describe_error(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
