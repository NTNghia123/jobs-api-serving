"""Lấy company_name từ `raw` — KHÔNG đổi schema scraper (ADR-019).

JobCompanyInfo không có `name`, nên tên công ty chỉ nằm trong payload `raw`:
  - TopDev : raw.company_detail.display_name → raw.company.display_name
  - VNW    : raw.companyName
Không có → None (KHÔNG 'Unknown'). Tìm trong detail.raw trước, list.raw sau.
"""
from __future__ import annotations

from collections.abc import Mapping


def _clean(v: object) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _nested(raw: Mapping[str, object] | None, *path: str) -> str | None:
    cur: object = raw
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return _clean(cur)


def _from_raw(source: str, raw: Mapping[str, object] | None) -> str | None:
    if not isinstance(raw, Mapping):
        return None
    if source == "topdev":
        return _nested(raw, "company_detail", "display_name") or _nested(raw, "company", "display_name")
    if source == "vietnamworks":
        return _clean(raw.get("companyName"))
    return None


def extract_company_name(
    source: str,
    detail_raw: Mapping[str, object] | None,
    list_raw: Mapping[str, object] | None = None,
) -> str | None:
    return _from_raw(source, detail_raw) or _from_raw(source, list_raw)
