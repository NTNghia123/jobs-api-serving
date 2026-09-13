"""Unit test builder SQL DuckDB (structural). Parity NGỮ NGHĨA chạy-được ở test_duckdb_repo.

Mirror test_bigquery_sql nhưng dialect DuckDB ($param, list_contains, "window" quote).
"""
from __future__ import annotations

from datetime import date

import pytest

from app.infrastructure.warehouse.duckdb_read_sql import (
    build_current_batch_meta_sql,
    build_metrics_sql,
    build_search_sql,
)
from app.models.enums import JobSource, Seniority, SortOption
from app.models.jobs import SearchFilters, SearchRequest


def _req(**over) -> SearchRequest:
    filters = over.pop("filters", {"posted_after": date(2026, 1, 1)})
    return SearchRequest(filters=SearchFilters(**filters), **over)


def test_khung_co_ban_dung_dollar_param():
    sql, p = build_search_sql(_req(), batch_id="b1")
    assert "SELECT *" not in sql
    assert "FROM silver_jobs" in sql
    assert "batch_id = $batch_id" in sql
    assert "effective_posted_date >= $posted_after" in sql
    assert "LIMIT $limit_plus_one" in sql
    assert "ORDER BY COALESCE(salary_max_vnd_month, 0) DESC, job_id ASC" in sql
    assert p["batch_id"] == "b1"
    assert p["posted_after"] == date(2026, 1, 1)
    assert p["limit_plus_one"] == _req().limit + 1
    assert "@" not in sql            # KHÔNG dùng @param (đó là BQ)


def test_category_dung_list_contains_khong_unnest():
    sql, p = build_search_sql(
        _req(filters={"posted_after": date(2026, 1, 1), "category": "topdev:g1~j5"}), batch_id="b")
    assert "list_contains(list_transform(categories, c -> c.category_key), $category)" in sql
    assert p["category"] == "topdev:g1~j5"


def test_salary_min_loai_null_seniority_coalesce():
    sql, _ = build_search_sql(
        _req(filters={"posted_after": date(2026, 1, 1), "salary_min": 45_000_000,
                      "seniority": Seniority.UNKNOWN, "source": JobSource.TOPDEV}), batch_id="b")
    assert "salary_max_vnd_month >= $salary_min" in sql             # LOẠI NULL
    assert "COALESCE(salary_max_vnd_month, 0) >=" not in sql
    assert "COALESCE(seniority_normalized, 'unknown') = $seniority" in sql
    assert "source = $source" in sql


@pytest.mark.parametrize(
    "sort, expr, direction, cmp",
    [
        (SortOption.SALARY_MAX_DESC, "COALESCE(salary_max_vnd_month, 0)", "DESC", "<"),
        (SortOption.SALARY_MIN_ASC,  "COALESCE(salary_min_vnd_month, 0)", "ASC", ">"),
        (SortOption.EXPERIENCE_ASC,  "COALESCE(experience_min_years, 0.0)", "ASC", ">"),
        (SortOption.POSTED_DESC,     "effective_posted_date", "DESC", "<"),
    ],
)
def test_sort_va_keyset(sort, expr, direction, cmp):
    sql, p = build_search_sql(_req(sort=sort), batch_id="b",
                              last_sort=5 if sort != SortOption.POSTED_DESC else "2026-02-01",
                              last_job_id="topdev:9")
    assert f"ORDER BY {expr} {direction}, job_id ASC" in sql
    assert f"({expr} {cmp} $last_sort OR ({expr} = $last_sort AND job_id > $last_job_id))" in sql
    assert p["last_job_id"] == "topdev:9"


def test_keyset_date_iso_coerce():
    _, p = build_search_sql(_req(sort=SortOption.POSTED_DESC), batch_id="b",
                            last_sort="2026-02-01", last_job_id="topdev:9")
    assert p["last_sort"] == date(2026, 2, 1)


def test_khong_cursor_thi_khong_keyset():
    sql, p = build_search_sql(_req(), batch_id="b")
    assert "$last_sort" not in sql and "last_sort" not in p


def test_current_batch_va_metrics_sql():
    sql, p = build_current_batch_meta_sql()
    assert "FROM warehouse_state s" in sql and "JOIN warehouse_batches b" in sql
    assert p["warehouse_name"] == "serving"

    sql2, p2 = build_metrics_sql("source", "90d", "b1")
    assert 'WHERE batch_id = $batch_id AND "window" = $window AND dimension = $dimension' in sql2
    assert (p2["batch_id"], p2["window"], p2["dimension"]) == ("b1", "90d", "source")
