"""Adapter: đọc tin tuyển dụng THẬT từ DuckDB (hiện thực JobRepository).

Trước refactor: app/warehouse/duckdb_repo.py. Xem docs/adr/ADR-003, ADR-006, ADR-011.

An toàn — hai nguyên tắc cốt lõi:
  1. IDENTIFIER TỪ ALLOWLIST: tên cột/toán tử/ORDER BY lấy từ dict cứng dưới đây,
     KHÔNG lấy từ user. (Tham số SQL không thay được tên cột.)
  2. VALUE TỪ THAM SỐ: mọi giá trị lọc đi qua tham số $tên, không bao giờ nối chuỗi.

API mở kho read_only=True: không thể ghi vào kho (least privilege ở tầng kết nối).
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb

from app.domain.ports.job_repository import JobRepository, SearchResult
from app.infrastructure.warehouse._duckdb_timeout import execute_with_timeout  # ★ TUẦN 7
from app.models.enums import SortOption
from app.models.jobs import JobItem, SearchRequest

# ALLOWLIST 1: filter (do user gửi) → (cột trong silver_jobs, toán tử). Cột là HẰNG SỐ.
# 'salary_min' → ('salary_max','>='): giữ ĐÚNG ngữ nghĩa FakeJobRepository để hai backend
# cho cùng kết quả (bộ test hợp đồng chạy chung).
_FILTER_TO_SQL: dict[str, tuple[str, str]] = {
    "seniority":      ("seniority",  "="),
    "experience_max": ("years_exp",  "<="),
    "salary_min":     ("salary_max", ">="),
    "country":        ("country",    "="),
}

# ALLOWLIST 2: sort → ORDER BY cố định, luôn kèm job_id để thứ tự TẤT ĐỊNH (ADR-006).
_SORT_TO_SQL: dict[SortOption, str] = {
    SortOption.SALARY_MAX_DESC: "ORDER BY salary_max DESC, job_id ASC",
    SortOption.SALARY_MIN_ASC:  "ORDER BY salary_min ASC,  job_id ASC",
    SortOption.EXPERIENCE_ASC:  "ORDER BY years_exp  ASC,  job_id ASC",
}

# sql/ nằm CẠNH file này (đã dời cùng nhau khi refactor) → đường dẫn tương đối vẫn đúng.
_SQL_PATH = Path(__file__).parent / "sql" / "search_jobs.sql"

_AS_OF = datetime(2025, 8, 17, 0, 0, tzinfo=UTC)


def _build_where(req: SearchRequest) -> tuple[str, dict]:
    clauses: list[str] = []
    params: dict = {}

    for name, value in req.filters.model_dump().items():
        if value is None or name not in _FILTER_TO_SQL:
            continue

        column, op = _FILTER_TO_SQL[name]
        clauses.append(f"{column} {op} ${name}")
        params[name] = value.value if hasattr(value, "value") else value

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


class DuckDBJobRepository(JobRepository):
    """Hiện thực JobRepository đọc từ file DuckDB. Handler không biết lớp này tồn tại —
    nó chỉ thấy interface JobRepository (nhờ Depends)."""

    def __init__(self, path: str, query_timeout_s: int = 30):   # ★ TUẦN 7: trần thời gian truy vấn
        self._con = duckdb.connect(path, read_only=True)
        self._template = _SQL_PATH.read_text(encoding="utf-8")
        self._timeout_s = query_timeout_s

    def as_of(self) -> datetime:
        return _AS_OF

    def _fetch(self, req: SearchRequest) -> list[JobItem]:
        where, params = _build_where(req)
        order = _SORT_TO_SQL[req.sort]
        # .format chỉ chèn where/order — cả hai ráp 100% từ allowlist, không từ user.
        sql = self._template.format(where=where, order=order)
        cur = self._con.cursor()                      # cursor riêng mỗi request (an toàn thread)
        # ★ TUẦN 7: chạy có trần thời gian — quá hạn thì interrupt + ném QueryTimeoutError (504).
        desc, rows = execute_with_timeout(cur, sql, params, self._timeout_s)
        cols = [d[0] for d in desc]
        # JobItem có extra='forbid' → nếu SELECT lỡ trả cột lạ (PII) thì Pydantic NÉM LỖI.
        return [JobItem(**dict(zip(cols, row, strict=True))) for row in rows]

    def _sort_key(self, req: SearchRequest):
        if req.sort == SortOption.SALARY_MIN_ASC:
            return lambda r: [r.salary_min or 0, r.job_id]
        if req.sort == SortOption.EXPERIENCE_ASC:
            return lambda r: [r.years_exp, r.job_id]
        return lambda r: [-(r.salary_max or 0), r.job_id]   # SALARY_MAX_DESC

    def search(self, req: SearchRequest, cursor: dict | None) -> SearchResult:
        """Lọc + sắp xếp trong SQL; cắt trang ở Python để khớp TUYỆT ĐỐI với Fake (ADR-006)."""
        rows = self._fetch(req)
        total = len(rows)
        key = self._sort_key(req)
        rows.sort(key=key)
        if cursor:
            last = cursor.get("k")
            rows = [r for r in rows if key(r) > last]   # keyset: chỉ lấy sau con trỏ
        page = rows[: req.limit]
        next_cursor = {"k": key(page[-1])} if len(rows) > req.limit else None
        return SearchResult(
            items=page,
            next_cursor=next_cursor,
            total_estimated=total,
            as_of=_AS_OF,
        )
