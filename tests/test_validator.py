"""Unit test QueryValidator — test từng chính sách ĐỘC LẬP với Pydantic."""
from __future__ import annotations

import pytest

from app.domain.validator import QueryValidator
from app.errors import (
    InvalidFilterError,
    InvalidRequestError,
    LimitExceededError,
    MissingRequiredFilterError,
)
from app.models.jobs import JobItem

v = QueryValidator()


# ---- trần số dòng ----
def test_limit_qua_lon_bi_tu_choi():
    with pytest.raises(LimitExceededError) as e:
        v.check_limit(1000)
    assert e.value.field == "limit"


def test_limit_hop_le_qua():
    v.check_limit(100)


# ---- allowlist filter ----
def test_filter_la_bi_tu_choi():
    with pytest.raises(InvalidFilterError):
        v.check_filters(["city"])


def test_filter_hop_le_qua():
    v.check_filters(["posted_after", "seniority", "source", "category"])


# ---- required-filter: posted_after bắt buộc ----
def test_posted_after_la_required():
    assert v.REQUIRED_FILTERS == frozenset({"posted_after"})
    v.check_required(["posted_after", "seniority"])   # đủ → qua
    with pytest.raises(MissingRequiredFilterError):
        v.check_required(["seniority"])               # thiếu posted_after


# ---- k-anonymity theo salary_sample_count ----
def test_suppress_theo_sample_count():
    assert v.suppress_if_small(3, 55_000_000) is None        # < 5 mẫu → che
    assert v.suppress_if_small(10, 55_000_000) == 55_000_000  # đủ → giữ
    assert v.suppress_if_small(6, 1, min_size=8) is None      # ngưỡng từ settings


# ---- default-deny cột trả ra ----
def test_cot_pii_bi_chan():
    with pytest.raises(InvalidRequestError):
        v.assert_safe_columns(["job_id", "title", "email"])


def test_cot_hop_le_qua():
    v.assert_safe_columns(list(JobItem.model_fields))   # đúng schema JobItem → qua


# ---- dimension/metric cho /market/metrics ----
def test_dimension_metric_allowlist():
    for d in ("source", "seniority", "category"):
        v.validate_dimension(d)
    v.validate_metric("median_salary_vnd_month")
    v.validate_metric("salary_sample_count")
    with pytest.raises(InvalidRequestError):
        v.validate_dimension("email")
    with pytest.raises(InvalidRequestError):
        v.validate_metric("avg_password")
