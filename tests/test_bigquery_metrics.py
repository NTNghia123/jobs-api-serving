"""Unit test phần THUẦN của BigQuery metrics adapter (map Row→MetricRow).

KHÔNG dựng bigquery.Client — current_batch()/market_metrics() I/O để integration Phase 4.
"""
from __future__ import annotations

from app.infrastructure.warehouse.read_mapping import to_metric_row


def test_to_metric_row_giu_so_that():
    """Gold giữ SỐ THẬT (kể cả sample nhỏ) — k-anon che median ở handler, KHÔNG ở adapter."""
    row = {
        "dimension_value": "vietnamworks:unknown",
        "posting_count": 7,
        "salary_disclosed_count": 5,
        "salary_sample_count": 3,          # < k nhưng adapter KHÔNG che
        "median_salary_vnd_month": 26_000_000.0,
    }
    m = to_metric_row(row)
    assert m.dimension_value == "vietnamworks:unknown"
    assert (m.posting_count, m.salary_disclosed_count, m.salary_sample_count) == (7, 5, 3)
    assert m.median_salary_vnd_month == 26_000_000.0   # adapter trả nguyên số thật


def test_to_metric_row_median_null():
    m = to_metric_row({
        "dimension_value": "topdev",
        "posting_count": 10, "salary_disclosed_count": 0, "salary_sample_count": 0,
        "median_salary_vnd_month": None,
    })
    assert m.median_salary_vnd_month is None
