"""Unit test phần THUẦN của BigQuery jobs adapter (map Row→JobItem, cursor, params).

KHÔNG dựng bigquery.Client (cần ADC/mạng) — search()/as_of() I/O để integration Phase 4
(RUN_BQ_INTEGRATION=1). Ở đây chỉ kiểm mapping + dựng cursor + map param typed.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.infrastructure.warehouse.bigquery_exec import to_bq_params
from app.infrastructure.warehouse.bigquery_read_sql import QueryParam
from app.infrastructure.warehouse.read_mapping import build_next_cursor, to_job_item
from app.models.enums import SortOption


def _row(**over) -> dict:
    base = {
        "job_id": "topdev:1001", "source": "topdev", "external_id": "1001",
        "title": "Backend Engineer", "company_name": "Acme", "location_text": "Hà Nội",
        "seniority": "senior", "experience_min_years": 4.0, "experience_max_years": 6.0,
        "salary_min_vnd_month": 30_000_000, "salary_max_vnd_month": 50_000_000,
        "salary_currency": "VND", "salary_period": "month",
        "posted_at": datetime(2026, 8, 20, tzinfo=UTC), "effective_posted_date": date(2026, 8, 20),
        "deadline_date": date(2026, 10, 1), "url": "https://topdev.example/job/1001",
        "categories": [
            {"category_key": "topdev:g1~j5", "category_name": "Backend", "category_path": "g1~j5"},
            {"category_key": "topdev:g14~j22", "category_name": "Sales", "category_path": "g14~j22"},
        ],
    }
    base.update(over)
    return base


def test_to_job_item_day_du():
    j = to_job_item(_row())
    assert j.job_id == "topdev:1001"
    assert j.source.value == "topdev"
    assert j.seniority.value == "senior"
    assert j.url == "https://topdev.example/job/1001"      # alias source_url
    assert {c.category_key for c in j.categories} == {"topdev:g1~j5", "topdev:g14~j22"}


def test_to_job_item_null_va_khong_category():
    j = to_job_item(_row(company_name=None, salary_min_vnd_month=None, salary_max_vnd_month=None,
                         salary_currency=None, salary_period=None, seniority="unknown",
                         experience_min_years=None, experience_max_years=None,
                         deadline_date=None, categories=[]))
    assert j.company_name is None
    assert j.salary_min_vnd_month is None
    assert j.seniority.value == "unknown"
    assert j.categories == []
    assert j.deadline_date is None


def test_to_job_item_bo_qua_cot_ngoai_allowlist():
    """Map TƯỜNG MINH: cột lạ trong row (nếu có) không lọt vào JobItem. Bảo đảm PII thật
    nằm ở SELECT allowlist cố định (test_bigquery_sql: không 'SELECT *')."""
    j = to_job_item(_row(contact_email="x@y.z"))
    assert not hasattr(j, "contact_email")
    assert "contact_email" not in j.model_dump()


_AS_OF = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "sort, expected_last_sort",
    [
        (SortOption.SALARY_MAX_DESC, 50_000_000),
        (SortOption.SALARY_MIN_ASC, 30_000_000),
        (SortOption.EXPERIENCE_ASC, 4.0),
        (SortOption.POSTED_DESC, date(2026, 8, 20)),
    ],
)
def test_build_next_cursor(sort, expected_last_sort):
    c = build_next_cursor(sort, "batch-1", _AS_OF, _row())
    assert c["batch_id"] == "batch-1"
    assert c["as_of"] == _AS_OF.isoformat()      # snapshot ISO
    assert c["last_job_id"] == "topdev:1001"
    assert c["last_sort"] == expected_last_sort


def test_build_next_cursor_coalesce_null():
    c = build_next_cursor(SortOption.SALARY_MAX_DESC, "b", _AS_OF,
                          _row(salary_max_vnd_month=None))
    assert c["last_sort"] == 0                    # NULL→0 khớp ORDER BY COALESCE


def test_to_bq_params_map_typed():
    params = [
        QueryParam("batch_id", "STRING", "b1"),
        QueryParam("salary_min", "INT64", 45_000_000),
        QueryParam("experience_max", "FLOAT64", 3.0),
        QueryParam("posted_after", "DATE", date(2026, 1, 1)),
    ]
    out = to_bq_params(params)
    assert [(p.name, p.type_, p.value) for p in out] == [
        ("batch_id", "STRING", "b1"),
        ("salary_min", "INT64", 45_000_000),
        ("experience_max", "FLOAT64", 3.0),
        ("posted_after", "DATE", date(2026, 1, 1)),
    ]
