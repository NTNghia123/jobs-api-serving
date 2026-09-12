"""Dựng repeated STRUCT categories cho silver — 1 job = 1 dòng (không explode).

`category_key = '<source>:<group-path>'` (dùng `group`, vd 'topdev:g14~j22' —
KHÔNG bare numeric id; ADR-019). Nguồn: `job_details.categories` (nhiều), fallback
`jobs.category` (một, StoredJobCategory từ phân vùng crawl). Thiếu cả hai → [].
Dedupe theo category_key, giữ thứ tự xuất hiện.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.elt.serving.silver import SilverCategory


def _make(source: str, cat: Mapping[str, object]) -> SilverCategory | None:
    """Một JobCategory (Mongo, camelCase) → SilverCategory; None nếu không dựng được key."""
    group = cat.get("group")
    code = cat.get("code")
    # ưu tiên group-path; fallback code; không có gì dùng được → bỏ (không tạo key bare-id).
    path = group if isinstance(group, str) and group else None
    key_part = path or (code if isinstance(code, str) and code else None)
    if key_part is None:
        return None

    name = cat.get("name")
    return SilverCategory(
        category_key=f"{source}:{key_part}",
        category_name=name if isinstance(name, str) and name else key_part,
        category_code=code if isinstance(code, str) and code else None,
        category_path=path,
        level1_id=_str_or_none(cat.get("level1Id")),
        level2_id=_str_or_none(cat.get("level2Id")),
        level3_id=_str_or_none(cat.get("level3Id")),
    )


def _str_or_none(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def build_categories(
    source: str,
    detail_categories: Sequence[Mapping[str, object]] | None,
    list_category: Mapping[str, object] | None = None,
) -> list[SilverCategory]:
    raw_list: list[Mapping[str, object]] = []
    if detail_categories:
        raw_list = [c for c in detail_categories if isinstance(c, Mapping)]
    if not raw_list and isinstance(list_category, Mapping):
        raw_list = [list_category]

    out: list[SilverCategory] = []
    seen: set[str] = set()
    for cat in raw_list:
        sc = _make(source, cat)
        if sc is not None and sc.category_key not in seen:
            seen.add(sc.category_key)
            out.append(sc)
    return out
