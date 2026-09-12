"""Unit test builder SQL THUẦN cho BigQuery read adapter (app/.../bigquery_read_sql.py).

Không cần google-cloud-bigquery/ADC/mạng — chỉ kiểm chuỗi SQL + params. Ngữ nghĩa
filter/sort/keyset phải khớp FakeJobRepository (chạy thật lên BQ để Phase 4).
Xem docs/adr/ADR-020.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.infrastructure.warehouse.bigquery_read_sql import (
    QueryParam,
    ReadSqlError,
    ReadTarget,
    build_current_batch_meta_sql,
    build_metrics_sql,
    build_search_sql,
    cursor_sort_value,
)
from app.models.enums import JobSource, Seniority, SortOption
from app.models.jobs import SearchFilters, SearchRequest

TARGET = ReadTarget(project="my-proj", dataset="jobs_prod")


def _req(**overrides) -> SearchRequest:
    filters = overrides.pop("filters", {"posted_after": date(2026, 1, 1)})
    body = {"filters": SearchFilters(**filters), **overrides}
    return SearchRequest(**body)


def _pmap(params: list[QueryParam]) -> dict[str, QueryParam]:
    return {p.name: p for p in params}


# --- ReadTarget: identifier an toàn ---
def test_table_id_fully_qualified_backtick():
    assert TARGET.table_id("silver_jobs") == "`my-proj.jobs_prod.silver_jobs`"


@pytest.mark.parametrize("bad", ["a.b", "a b", "drop`table", "a;b", ""])
def test_identifier_la_bi_tu_choi(bad):
    with pytest.raises(ReadSqlError):
        ReadTarget(project=bad, dataset="ok")
    with pytest.raises(ReadSqlError):
        ReadTarget(project="ok", dataset=bad)


# --- search: khung cơ bản ---
def test_search_khung_co_ban():
    sp = build_search_sql(TARGET, _req(), batch_id="b1")
    sql = sp.sql
    assert "SELECT *" not in sql                      # projection tường minh
    assert "`my-proj.jobs_prod.silver_jobs`" in sql
    assert "batch_id = @batch_id" in sql              # batch scoping
    assert "effective_posted_date >= @posted_after" in sql   # posted_after bắt buộc
    assert "LIMIT @limit_plus_one" in sql
    assert "ORDER BY COALESCE(salary_max_vnd_month, 0) DESC, job_id ASC" in sql  # sort mặc định
    p = _pmap(sp.params)
    assert p["batch_id"].value == "b1"
    assert p["posted_after"] == QueryParam("posted_after", "DATE", date(2026, 1, 1))
    assert p["limit_plus_one"].value == _req().limit + 1  # limit+1


def test_search_khong_co_cursor_thi_khong_keyset():
    sp = build_search_sql(TARGET, _req(), batch_id="b1")
    assert "@last_sort" not in sp.sql
    assert "last_sort" not in _pmap(sp.params)


# --- search: từng filter → clause + param đúng (allowlist) ---
def test_filter_posted_before():
    sp = build_search_sql(TARGET, _req(filters={"posted_after": date(2026, 1, 1),
                                                "posted_before": date(2026, 3, 1)}), batch_id="b")
    assert "effective_posted_date <= @posted_before" in sp.sql
    assert _pmap(sp.params)["posted_before"].value == date(2026, 3, 1)


def test_filter_source_va_seniority():
    sp = build_search_sql(TARGET, _req(filters={"posted_after": date(2026, 1, 1),
                                                "source": JobSource.VIETNAMWORKS,
                                                "seniority": Seniority.SENIOR}), batch_id="b")
    assert "source = @source" in sp.sql
    # seniority COALESCE → 'unknown' (khớp map JobItem.seniority)
    assert "COALESCE(seniority_normalized, 'unknown') = @seniority" in sp.sql
    p = _pmap(sp.params)
    assert p["source"].value == "vietnamworks"      # enum → .value
    assert p["seniority"].value == "senior"


def test_filter_seniority_unknown_map_sang_coalesce():
    sp = build_search_sql(TARGET, _req(filters={"posted_after": date(2026, 1, 1),
                                                "seniority": Seniority.UNKNOWN}), batch_id="b")
    assert "COALESCE(seniority_normalized, 'unknown') = @seniority" in sp.sql
    assert _pmap(sp.params)["seniority"].value == "unknown"


def test_filter_category_dung_exists_khong_explode():
    sp = build_search_sql(TARGET, _req(filters={"posted_after": date(2026, 1, 1),
                                                "category": "topdev:g1~j5"}), batch_id="b")
    assert "EXISTS (SELECT 1 FROM UNNEST(categories) c WHERE c.category_key = @category)" in sp.sql
    assert _pmap(sp.params)["category"].value == "topdev:g1~j5"


def test_filter_salary_min_loai_null():
    """salary_min lọc theo salary_max, LOẠI NULL (khớp fake is not None) — KHÔNG coalesce."""
    sp = build_search_sql(TARGET, _req(filters={"posted_after": date(2026, 1, 1),
                                                "salary_min": 45_000_000}), batch_id="b")
    assert "salary_max_vnd_month >= @salary_min" in sp.sql
    assert "COALESCE(salary_max_vnd_month, 0) >= @salary_min" not in sp.sql
    assert _pmap(sp.params)["salary_min"] == QueryParam("salary_min", "INT64", 45_000_000)


def test_filter_experience_max_loai_null():
    sp = build_search_sql(TARGET, _req(filters={"posted_after": date(2026, 1, 1),
                                                "experience_max": 3}), batch_id="b")
    assert "experience_min_years <= @experience_max" in sp.sql
    assert _pmap(sp.params)["experience_max"] == QueryParam("experience_max", "FLOAT64", 3.0)


# --- sort → ORDER BY + kiểu keyset ---
@pytest.mark.parametrize(
    "sort, expr, direction, cmp, ptype",
    [
        (SortOption.SALARY_MAX_DESC, "COALESCE(salary_max_vnd_month, 0)", "DESC", "<", "INT64"),
        (SortOption.SALARY_MIN_ASC,  "COALESCE(salary_min_vnd_month, 0)", "ASC", ">", "INT64"),
        (SortOption.EXPERIENCE_ASC,  "COALESCE(experience_min_years, 0.0)", "ASC", ">", "FLOAT64"),
        (SortOption.POSTED_DESC,     "effective_posted_date", "DESC", "<", "DATE"),
    ],
)
def test_sort_order_by_va_keyset(sort, expr, direction, cmp, ptype):
    sp = build_search_sql(TARGET, _req(sort=sort), batch_id="b",
                          last_sort=5 if ptype != "DATE" else "2026-02-01", last_job_id="topdev:9")
    assert f"ORDER BY {expr} {direction}, job_id ASC" in sp.sql
    # keyset: hướng DESC dùng '<', ASC dùng '>'; luôn có nhánh job_id > phá hoà
    assert f"({expr} {cmp} @last_sort OR ({expr} = @last_sort AND job_id > @last_job_id))" in sp.sql
    assert _pmap(sp.params)["last_sort"].bq_type == ptype
    assert _pmap(sp.params)["last_job_id"].value == "topdev:9"


def test_keyset_date_iso_string_duoc_coerce_ve_date():
    sp = build_search_sql(TARGET, _req(sort=SortOption.POSTED_DESC), batch_id="b",
                          last_sort="2026-02-01", last_job_id="topdev:9")
    assert _pmap(sp.params)["last_sort"].value == date(2026, 2, 1)   # str ISO → date


# --- cursor_sort_value: khớp COALESCE của _SORT ---
@pytest.mark.parametrize(
    "sort, row, expected",
    [
        (SortOption.SALARY_MAX_DESC, {"salary_max_vnd_month": None}, 0),
        (SortOption.SALARY_MAX_DESC, {"salary_max_vnd_month": 50}, 50),
        (SortOption.SALARY_MIN_ASC,  {"salary_min_vnd_month": None}, 0),
        (SortOption.EXPERIENCE_ASC,  {"experience_min_years": None}, 0.0),
        (SortOption.EXPERIENCE_ASC,  {"experience_min_years": 4.0}, 4.0),
        (SortOption.POSTED_DESC,     {"effective_posted_date": date(2026, 2, 1)}, date(2026, 2, 1)),
    ],
)
def test_cursor_sort_value(sort, row, expected):
    assert cursor_sort_value(sort, row) == expected


# --- metadata + metrics ---
def test_current_batch_meta_sql():
    sp = build_current_batch_meta_sql(TARGET)
    assert "`my-proj.jobs_prod.warehouse_state`" in sp.sql
    assert "`my-proj.jobs_prod.warehouse_batches`" in sp.sql
    assert "b.batch_id = s.published_batch_id" in sp.sql
    assert _pmap(sp.params)["warehouse_name"].value == "serving"


def test_metrics_sql():
    sp = build_metrics_sql(TARGET, "source", "90d", "b1")
    assert "`my-proj.jobs_prod.gold_market_metrics`" in sp.sql
    assert "WHERE batch_id = @batch_id AND window = @window AND dimension = @dimension" in sp.sql
    assert "median_salary_vnd_month" in sp.sql   # gold giữ số thật; API mới che
    p = _pmap(sp.params)
    assert (p["batch_id"].value, p["window"].value, p["dimension"].value) == ("b1", "90d", "source")
