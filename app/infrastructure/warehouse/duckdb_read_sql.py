"""Builder SQL THUẦN cho DuckDB read adapter — MIRROR ngữ nghĩa BigQuery (dialect DuckDB).

DuckDB = backend 'dev' + LƯỚI PARITY chạy-được cho ngữ nghĩa SQL Phase 3: nó thực thi
CÙNG logic filter/sort/keyset/EXISTS/limit+1 như BigQuery, ở local, và phải cho kết quả
GIỐNG FakeJobRepository (test_duckdb_repo). Khác BQ ở dialect:
  - tham số `$name` (không `@name`); giá trị bind qua dict (không typed ScalarQueryParameter).
  - category: `list_contains(list_transform(categories, c -> c.category_key), $k)` (không UNNEST).
  - identifier bảng KHÔNG fully-qualified/backtick (DuckDB một db); `"window"` phải quote (keyword).

Giữ CÙNG bất biến NULL như BQ (điểm dễ sai): SORT/KEYSET COALESCE NULL→0; FILTER salary_min/
experience_max LOẠI NULL; seniority COALESCE→'unknown'. Xem docs/adr/ADR-020, ADR-026.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models.enums import SortOption
from app.models.jobs import SearchRequest

# tên bảng (allowlist — hằng, không từ user). DuckDB dev load 1 batch qua fixture loader.
TABLE_SILVER = "silver_jobs"
TABLE_GOLD = "gold_market_metrics"
TABLE_STATE = "warehouse_state"
TABLE_BATCHES = "warehouse_batches"
WAREHOUSE_STATE_NAME = "serving"


@dataclass(frozen=True)
class _SortSpec:
    expr: str
    direction: str    # ASC | DESC


# expr GIỐNG BQ (COALESCE hợp lệ ở cả hai dialect) → parity ngữ nghĩa sort.
_SORT: dict[SortOption, _SortSpec] = {
    SortOption.SALARY_MAX_DESC: _SortSpec("COALESCE(salary_max_vnd_month, 0)", "DESC"),
    SortOption.SALARY_MIN_ASC:  _SortSpec("COALESCE(salary_min_vnd_month, 0)", "ASC"),
    SortOption.EXPERIENCE_ASC:  _SortSpec("COALESCE(experience_min_years, 0.0)", "ASC"),
    SortOption.POSTED_DESC:     _SortSpec("effective_posted_date", "DESC"),
}

# SELECT tường minh (không SELECT *, không cột PII). alias source_url→url, COALESCE seniority.
# categories: chỉ 3 subfield API cần (list_transform) → map thẳng CategoryItem như BQ.
_SELECT = """\
  job_id, source, external_id, title, company_name, location_text,
  COALESCE(seniority_normalized, 'unknown') AS seniority,
  experience_min_years, experience_max_years,
  salary_min_vnd_month, salary_max_vnd_month, salary_currency, salary_period,
  posted_at, effective_posted_date, deadline_date,
  source_url AS url,
  list_transform(categories, c -> {
      'category_key': c.category_key,
      'category_name': c.category_name,
      'category_path': c.category_path
  }) AS categories"""


def build_search_sql(
    req: SearchRequest,
    *,
    batch_id: str,
    last_sort: object | None = None,
    last_job_id: str | None = None,
) -> tuple[str, dict]:
    """SQL + dict params cho /jobs/search (DuckDB). Mirror build_search_sql của BQ."""
    f = req.filters
    spec = _SORT[req.sort]

    where: list[str] = ["batch_id = $batch_id", "effective_posted_date >= $posted_after"]
    params: dict = {"batch_id": batch_id, "posted_after": f.posted_after}

    if f.posted_before is not None:
        where.append("effective_posted_date <= $posted_before")
        params["posted_before"] = f.posted_before
    if f.source is not None:
        where.append("source = $source")
        params["source"] = f.source.value
    if f.seniority is not None:
        where.append("COALESCE(seniority_normalized, 'unknown') = $seniority")
        params["seniority"] = f.seniority.value
    if f.category is not None:
        # EXISTS tương đương: có category_key khớp → giữ 1 dòng (không nhân dòng).
        where.append("list_contains(list_transform(categories, c -> c.category_key), $category)")
        params["category"] = f.category
    if f.salary_min is not None:
        where.append("salary_max_vnd_month >= $salary_min")   # LOẠI NULL (khớp fake is not None)
        params["salary_min"] = f.salary_min
    if f.experience_max is not None:
        where.append("experience_min_years <= $experience_max")
        params["experience_max"] = float(f.experience_max)

    if last_job_id is not None:
        lv = last_sort
        if req.sort == SortOption.POSTED_DESC and isinstance(lv, str):
            lv = date.fromisoformat(lv)   # cursor lưu date ISO → khôi phục date
        cmp = "<" if spec.direction == "DESC" else ">"
        where.append(
            f"({spec.expr} {cmp} $last_sort "
            f"OR ({spec.expr} = $last_sort AND job_id > $last_job_id))"
        )
        params["last_sort"] = lv
        params["last_job_id"] = last_job_id

    params["limit_plus_one"] = req.limit + 1

    sql = (
        f"SELECT\n{_SELECT}\n"
        f"FROM {TABLE_SILVER}\n"
        f"WHERE {' AND '.join(where)}\n"
        f"ORDER BY {spec.expr} {spec.direction}, job_id ASC\n"
        f"LIMIT $limit_plus_one"
    )
    return sql, params


def build_current_batch_meta_sql() -> tuple[str, dict]:
    """Batch đang publish + as_of (JOIN pointer × catalog) — mirror BQ. 0 dòng → 503."""
    sql = (
        "SELECT b.batch_id, b.data_as_of_at, b.as_of_date\n"
        f"FROM {TABLE_STATE} s\n"
        f"JOIN {TABLE_BATCHES} b ON b.batch_id = s.published_batch_id\n"
        "WHERE s.warehouse_name = $warehouse_name\n"
        "LIMIT 1"
    )
    return sql, {"warehouse_name": WAREHOUSE_STATE_NAME}


def build_metrics_sql(dimension: str, window: str, batch_id: str) -> tuple[str, dict]:
    """Đọc gold theo batch/window/dimension (DuckDB). `window` là keyword → phải quote."""
    sql = (
        "SELECT dimension_value, posting_count, salary_disclosed_count,\n"
        "       salary_sample_count, median_salary_vnd_month\n"
        f"FROM {TABLE_GOLD}\n"
        'WHERE batch_id = $batch_id AND "window" = $window AND dimension = $dimension'
    )
    return sql, {"batch_id": batch_id, "window": window, "dimension": dimension}
