"""Client Python tối giản — bản nháp của thứ sẽ bàn giao cho team AI ở Tuần 8.

Chạy: python examples/client_demo.py  (cần server đang chạy ở cổng 8080)
Đã căn chỉnh theo schema thật: filter seniority / salary_min / country.
"""
from __future__ import annotations

import json
import urllib.request

BASE = "http://localhost:8080"


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def search_all_pages(filters: dict, page_size: int = 3) -> list[dict]:
    """Duyệt hết kết quả bằng page_token."""
    items, token = [], None
    while True:
        body = {"filters": filters, "limit": page_size}
        if token:
            body["page_token"] = token
        data = _post("/v1/jobs/search", body)
        items.extend(data["items"])
        token = data["next_page_token"]
        if not token:
            print(f"Dữ liệu tính đến: {data['as_of']}")
            return items


if __name__ == "__main__":
    jobs = search_all_pages({"seniority": "senior"})
    for j in jobs:
        print(f"#{j['job_id']:<3} {j['title'][:34]:<34} {j['company_name']:<16} "
              f"{j['country'] or '-':<10} {j['salary_min']}-{j['salary_max']}")
