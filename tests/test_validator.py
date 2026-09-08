"""Unit test cho QueryValidator — test từng chính sách ĐỘC LẬP với Pydantic.

[FILE MỚI]  Đích thật: tests/test_validator.py
Hướng dẫn:  ../W4-Huong-Dan-Thuc-Hien.md §5

GIẢI THÍCH: vì Pydantic đã chặn limit>100 / filter lạ ở biên HTTP, ta KHÔNG thể dựng
SearchRequest xấu để test. Vì vậy test gọi thẳng các method hạt nhỏ của validator với
giá trị thô — chứng minh chính sách hoạt động dù request tới từ đường nào.
"""
from __future__ import annotations

import pytest

from app.domain.validator import QueryValidator
from app.errors import (
    InvalidFilterError,
    InvalidRequestError,
    LimitExceededError,
    MissingRequiredFilterError,
)

v = QueryValidator()


# ---- trần số dòng ----
def test_limit_qua_lon_bi_tu_choi():
    with pytest.raises(LimitExceededError) as e:
        v.check_limit(1000)
    assert e.value.field == "limit"


def test_limit_hop_le_qua():
    v.check_limit(100)   # không ném lỗi


# ---- allowlist filter ----
def test_filter_la_bi_tu_choi():
    with pytest.raises(InvalidFilterError):
        v.check_filters(["city"])          # 'city' không nằm trong FILTER_NAMES


def test_filter_hop_le_qua():
    v.check_filters(["seniority", "country"])


# ---- required-filter (cơ chế rỗng cho data này) ----
def test_required_rong_thi_luon_qua():
    assert v.REQUIRED_FILTERS == frozenset()
    v.check_required([])                    # rỗng → không thiếu gì


def test_co_che_required_van_hoat_dong_khi_bat():
    """Chứng minh cơ chế required-filter chạy đúng NẾU bật (dù data này để rỗng)."""
    class WithRequired(QueryValidator):
        REQUIRED_FILTERS = frozenset({"country"})
    with pytest.raises(MissingRequiredFilterError):
        WithRequired().check_required([])   # thiếu 'country'
    WithRequired().check_required(["country"])  # đủ → qua


# ---- k-anonymity ----
def test_suppress_nhom_nho():
    assert v.suppress_if_small(3, 55000) is None       # < 5 tin → che
    assert v.suppress_if_small(10, 55000) == 55000     # đủ lớn → giữ


# ---- default-deny cột trả ra ----
def test_cot_pii_bi_chan():
    with pytest.raises(InvalidRequestError):
        v.assert_safe_columns(["job_id", "title", "email"])   # 'email' là PII/không allowlist


def test_cot_hop_le_qua():
    v.assert_safe_columns(["job_id", "title", "company_name", "country", "seniority",
                           "years_exp", "salary_min", "salary_max", "qualification", "url"])


# ---- dimension/metric cho /market/metrics (T5) ----
def test_dimension_metric_allowlist():
    v.validate_dimension("seniority")
    v.validate_metric("median_salary")
    with pytest.raises(InvalidRequestError):
        v.validate_dimension("email")
    with pytest.raises(InvalidRequestError):
        v.validate_metric("avg_password")
