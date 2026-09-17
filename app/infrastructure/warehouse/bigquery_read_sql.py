"""Builder SQL THUẦN cho BigQuery read adapter — KHÔNG import google-cloud-bigquery.

Tách phần dựng SQL + params (test được, không cần client/ADC/mạng) khỏi phần I/O
(bigquery_jobs.py / bigquery_metrics.py) — cùng mẫu với ELT (publish.py THUẦN vs
bigquery_writer.py I/O). Phase 3 unit-test builder ở đây; chạy thật lên BQ để Phase 4
(RUN_BQ_INTEGRATION=1 + dataset _test). Xem docs/adr/ADR-020.

Hai nguyên tắc AN TOÀN (như DuckDB adapter):
  1. IDENTIFIER TỪ ALLOWLIST: tên bảng (schema.py) + cột/toán tử/ORDER BY lấy từ HẰNG
     dưới đây, KHÔNG từ user; project/dataset validate charset (defense-in-depth) rồi
     backtick + fully-qualified. Tham số @param KHÔNG thay được identifier.
  2. VALUE TỪ THAM SỐ @name: mọi giá trị filter/keyset đi qua ScalarQueryParameter
     (loader map từ QueryParam), KHÔNG BAO GIỜ nối chuỗi vào SQL.

Ngữ nghĩa filter/sort tái tạo CHÍNH XÁC FakeJobRepository (contract test chạy chung):
  - SORT/KEYSET: NULL salary/experience COALESCE → 0 (khớp `x or 0` của fake).
  - FILTER salary_min/experience_max: LOẠI NULL (khớp `is not None` của fake).
  - seniority: COALESCE(seniority_normalized,'unknown') (JobItem.seniority bắt buộc, có 'unknown').

Đọc theo BATCH (invariant 1/3/4): mọi query silver/gold buộc `batch_id = @batch_id`
(batch current từ warehouse_state, hoặc batch trong token cho trang 2+).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from app.elt.serving.schema import (
    TABLE_BATCHES,
    TABLE_GOLD,
    TABLE_SILVER,
    TABLE_STATE,
    WAREHOUSE_STATE_NAME,
)
from app.models.enums import SortOption
from app.models.jobs import SearchRequest

# project/dataset an toàn để đặt trong backtick (không cho ký tự lạ lọt vào identifier).
_IDENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ReadSqlError(ValueError):
    """Cấu hình đọc không hợp lệ (identifier lạ) — chặn trước khi chạm BigQuery."""


@dataclass(frozen=True)
class ReadTarget:
    """Đích ĐỌC đã phân giải cho tầng API (một môi trường = một dataset). Bất biến, thuần."""

    project: str
    dataset: str
    location: str = "asia-southeast1"
    maximum_bytes_billed: int = 2_000_000_000
    # ★ LOAD-TEST — BigQuery result cache. false → cold-query run (service perf). Mặc định true (prod).
    use_query_cache: bool = True

    def __post_init__(self) -> None:
        for part in (self.project, self.dataset):
            if not _IDENT_RE.match(part or ""):
                raise ReadSqlError(f"identifier BigQuery không hợp lệ: {part!r}")

    def table_id(self, table: str) -> str:
        """'`project.dataset.table`' — fully-qualified + backtick, cho SQL."""
        return f"`{self.project}.{self.dataset}.{table}`"


@dataclass(frozen=True)
class QueryParam:
    """Một tham số @name (thuần). Loader map sang bigquery.ScalarQueryParameter."""

    name: str
    bq_type: str      # STRING | INT64 | FLOAT64 | DATE | TIMESTAMP
    value: object | None


@dataclass(frozen=True)
class SqlAndParams:
    sql: str
    params: list[QueryParam]


# --- ALLOWLIST sort: SortOption → (expr SQL, hướng, kiểu param keyset) ---
@dataclass(frozen=True)
class _SortSpec:
    expr: str
    direction: str    # ASC | DESC
    bq_type: str      # kiểu của last_sort trong keyset


# COALESCE khớp fake (`x or 0`); effective_posted_date REQUIRED nên không cần COALESCE.
_SORT: dict[SortOption, _SortSpec] = {
    SortOption.SALARY_MAX_DESC: _SortSpec("COALESCE(salary_max_vnd_month, 0)", "DESC", "INT64"),
    SortOption.SALARY_MIN_ASC:  _SortSpec("COALESCE(salary_min_vnd_month, 0)", "ASC", "INT64"),
    SortOption.EXPERIENCE_ASC:  _SortSpec("COALESCE(experience_min_years, 0.0)", "ASC", "FLOAT64"),
    SortOption.POSTED_DESC:     _SortSpec("effective_posted_date", "DESC", "DATE"),
}

# SELECT tường minh — column projection (KHÔNG 'SELECT *', KHÔNG cột PII). Trên BigQuery
# byte quét = tiền, nên chỉ lấy đúng cột dựng JobItem. categories chỉ lấy 3 subfield API cần.
# COALESCE seniority + alias source_url→url để map thẳng row → JobItem(**row).
_SELECT = """\
  job_id, source, external_id, title, company_name, location_text,
  COALESCE(seniority_normalized, 'unknown') AS seniority,
  experience_min_years, experience_max_years,
  salary_min_vnd_month, salary_max_vnd_month, salary_currency, salary_period,
  posted_at, effective_posted_date, deadline_date,
  source_url AS url,
  ARRAY(SELECT AS STRUCT c.category_key, c.category_name, c.category_path
        FROM UNNEST(categories) c) AS categories"""


def build_search_sql(
    target: ReadTarget,
    req: SearchRequest,
    *,
    batch_id: str,
    last_sort: object | None = None,
    last_job_id: str | None = None,
) -> SqlAndParams:
    """SQL /v1/jobs/search: filter allowlist + keyset trong SQL + LIMIT limit+1, gắn batch.

    Trang 2+ truyền last_sort/last_job_id (từ cursor) → thêm mệnh đề keyset. total KHÔNG
    tính (adapter trả total_estimated=None) — không COUNT(*) mỗi request.
    """
    f = req.filters
    spec = _SORT[req.sort]

    # batch scoping + posted_after BẮT BUỘC (cũng là partition filter cho require_partition_filter).
    where: list[str] = ["batch_id = @batch_id", "effective_posted_date >= @posted_after"]
    params: list[QueryParam] = [
        QueryParam("batch_id", "STRING", batch_id),
        QueryParam("posted_after", "DATE", f.posted_after),
    ]

    if f.posted_before is not None:
        where.append("effective_posted_date <= @posted_before")
        params.append(QueryParam("posted_before", "DATE", f.posted_before))
    if f.source is not None:
        where.append("source = @source")
        params.append(QueryParam("source", "STRING", f.source.value))
    if f.seniority is not None:
        # JobItem.seniority bắt buộc (có 'unknown'); silver seniority_normalized nullable.
        where.append("COALESCE(seniority_normalized, 'unknown') = @seniority")
        params.append(QueryParam("seniority", "STRING", f.seniority.value))
    if f.category is not None:
        # EXISTS: job nhiều category KHÔNG bị nhân dòng (không explode) — ADR-020.
        where.append("EXISTS (SELECT 1 FROM UNNEST(categories) c WHERE c.category_key = @category)")
        params.append(QueryParam("category", "STRING", f.category))
    if f.salary_min is not None:
        # LOẠI NULL (khớp fake `salary_max is not None and >=`). KHÔNG coalesce ở FILTER.
        where.append("salary_max_vnd_month >= @salary_min")
        params.append(QueryParam("salary_min", "INT64", f.salary_min))
    if f.experience_max is not None:
        where.append("experience_min_years <= @experience_max")
        params.append(QueryParam("experience_max", "FLOAT64", float(f.experience_max)))

    # KEYSET (chỉ trang 2+): lấy các dòng SAU con trỏ theo đúng hướng sort, job_id phá hoà.
    if last_job_id is not None:
        lv = last_sort
        if spec.bq_type == "DATE" and isinstance(lv, str):
            lv = date.fromisoformat(lv)   # cursor lưu date dạng ISO (JSON) → khôi phục date
        cmp = "<" if spec.direction == "DESC" else ">"
        where.append(
            f"({spec.expr} {cmp} @last_sort "
            f"OR ({spec.expr} = @last_sort AND job_id > @last_job_id))"
        )
        params.append(QueryParam("last_sort", spec.bq_type, lv))
        params.append(QueryParam("last_job_id", "STRING", last_job_id))

    params.append(QueryParam("limit_plus_one", "INT64", req.limit + 1))

    sql = (
        f"SELECT\n{_SELECT}\n"
        f"FROM {target.table_id(TABLE_SILVER)}\n"
        f"WHERE {' AND '.join(where)}\n"
        f"ORDER BY {spec.expr} {spec.direction}, job_id ASC\n"
        f"LIMIT @limit_plus_one"
    )
    return SqlAndParams(sql, params)


def cursor_sort_value(sort: SortOption, row: Mapping[str, object]) -> object:
    """Giá trị sort (đã COALESCE như fake) của một row → nhét vào cursor cho keyset trang sau.

    Khớp _SORT[...].expr: salary/experience NULL→0; posted trả date (JSON hoá thành ISO).
    """
    if sort == SortOption.SALARY_MAX_DESC:
        return row["salary_max_vnd_month"] or 0
    if sort == SortOption.SALARY_MIN_ASC:
        return row["salary_min_vnd_month"] or 0
    if sort == SortOption.EXPERIENCE_ASC:
        return float(row["experience_min_years"] or 0.0)
    return row["effective_posted_date"]   # POSTED_DESC → date


def build_current_batch_meta_sql(target: ReadTarget) -> SqlAndParams:
    """Batch ĐANG publish + metadata của nó (as_of) — JOIN pointer(state) × catalog(batches).

    0 dòng ⇔ pointer NULL (chưa publish batch nào) → I/O raise 503. Đọc metadata theo
    batch_id đang phục vụ, KHÔNG suy từ state hiện thời cho trang cũ (invariant 1).
    """
    sql = (
        "SELECT b.batch_id, b.data_as_of_at, b.as_of_date\n"
        f"FROM {target.table_id(TABLE_STATE)} s\n"
        f"JOIN {target.table_id(TABLE_BATCHES)} b ON b.batch_id = s.published_batch_id\n"
        "WHERE s.warehouse_name = @warehouse_name\n"
        "LIMIT 1"
    )
    return SqlAndParams(sql, [QueryParam("warehouse_name", "STRING", WAREHOUSE_STATE_NAME)])


def build_metrics_sql(
    target: ReadTarget, dimension: str, window: str, batch_id: str,
) -> SqlAndParams:
    """SQL /v1/market/metrics: đọc gold đã tổng hợp, gắn batch + window + dimension.

    dimension/window đã validate ở API (allowlist). Gold giữ SỐ THẬT; k-anonymity che
    median áp ở tầng API (không ở SQL) — ADR-020.
    """
    sql = (
        "SELECT dimension_value, posting_count, salary_disclosed_count,\n"
        "       salary_sample_count, median_salary_vnd_month\n"
        f"FROM {target.table_id(TABLE_GOLD)}\n"
        "WHERE batch_id = @batch_id AND `window` = @window AND dimension = @dimension"
    )
    return SqlAndParams(sql, [
        QueryParam("batch_id", "STRING", batch_id),
        QueryParam("window", "STRING", window),
        QueryParam("dimension", "STRING", dimension),
    ])
