"""Unit test cho tầng transform thuần của ELT serving (app/elt/serving/*).

Căn theo ADR-019 (mapping) + ADR-024 (salary). Không chạm Mongo/BigQuery.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.elt.serving.categories import build_categories
from app.elt.serving.company import extract_company_name
from app.elt.serving.dates import parse_deadline_date, parse_posted_date
from app.elt.serving.experience import parse_experience
from app.elt.serving.salary import normalize_salary
from app.elt.serving.seniority import SENIORITY_MAPPING_VERSION, normalize_seniority
from app.elt.serving.silver import (
    DeadlineDateParseStatus,
    ExperienceParseStatus,
    PostedDateParseStatus,
)
from tests.fixtures.mongo_like import cat, sal


# --------------------------------------------------------------------------- #
# SALARY (ADR-024) — đơn vị suy từ raw; trần sanity 10 tỷ                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("salary", "status", "min_vnd", "max_vnd", "currency"),
    [
        (sal("28tr-32tr ₫/tháng", 28_000_000, 32_000_000, "VND"), "parsed", 28_000_000, 32_000_000, "VND"),
        (sal("$ 40tr-70tr /tháng", 40_000_000, 70_000_000, "USD"), "parsed", 40_000_000, 70_000_000, "VND"),  # mislabel → VND
        (sal("$ 500-1,000 /tháng", 500, 1000, "USD"), "parsed", 12_750_000, 25_500_000, "USD"),  # USD thật
        (sal("8-15 ₫/tháng", 8, 15, "VND"), "parsed", 8_000_000, 15_000_000, "VND"),  # số bé = triệu
        (sal("Tới 30 triệu", None, 30, "VND"), "parsed", None, 30_000_000, "VND"),  # one-sided max
        (sal("Từ 20 triệu", 20, None, "VND"), "parsed", 20_000_000, None, "VND"),  # one-sided min
        (sal("12,000-24,000 ₫/tháng", 12000, 24000, "VND"), "invalid", None, None, "VND"),  # >trần
        (sal("50 - 10 triệu", 50, 10, "VND"), "invalid", None, None, "VND"),  # min>max
        (None, "negotiable", None, None, None),
    ],
)
def test_normalize_salary(salary, status, min_vnd, max_vnd, currency):
    r = normalize_salary(salary)
    assert r.salary_normalization_status.value == status
    assert r.salary_min_vnd_month == min_vnd
    assert r.salary_max_vnd_month == max_vnd
    if status == "parsed":
        assert r.salary_currency == currency
        assert r.salary_period == "month"


def test_salary_negotiable_when_both_none():
    assert normalize_salary(
        {"raw": "Thương lượng", "min": None, "max": None, "currency": None, "unit": None}
    ).salary_normalization_status.value == "negotiable"


def test_salary_usd_sets_fx_version():
    r = normalize_salary(sal("$ 500-1,000 /tháng", 500, 1000, "USD"))
    assert r.fx_rate_to_vnd == 25_500
    assert r.salary_fx_version is not None


def test_salary_invalid_keeps_originals():
    r = normalize_salary(sal("12,000-24,000 ₫/tháng", 12000, 24000, "VND"))
    assert r.salary_raw is not None and r.salary_min_original is not None  # truy vết được


# --------------------------------------------------------------------------- #
# DATES — hai parse-status độc lập                                             #
# --------------------------------------------------------------------------- #
_FS = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)


def test_posted_iso_with_offset():
    _, eff, status = parse_posted_date("2026-08-26T23:59:59+07:00", _FS)
    assert eff == date(2026, 8, 26) and status == PostedDateParseStatus.PARSED


def test_posted_date_only():
    _, eff, status = parse_posted_date("2026-08-20", _FS)
    assert eff == date(2026, 8, 20) and status == PostedDateParseStatus.PARSED


def test_posted_fallback_first_seen():
    posted, eff, status = parse_posted_date("không-parse", _FS)
    assert posted is None and status == PostedDateParseStatus.FALLBACK_FIRST_SEEN
    assert eff == _FS.date()  # 2026-09-07 theo giờ VN


def test_posted_invalid_both_missing():
    _, eff, status = parse_posted_date(None, None)
    assert eff is None and status == PostedDateParseStatus.INVALID


@pytest.mark.parametrize(
    ("raw", "expected", "status"),
    [
        ("30/09/2026", date(2026, 9, 30), DeadlineDateParseStatus.PARSED),
        (None, None, DeadlineDateParseStatus.MISSING),
        ("rác", None, DeadlineDateParseStatus.INVALID),
    ],
)
def test_parse_deadline(raw, expected, status):
    assert parse_deadline_date(raw) == (expected, status)


# --------------------------------------------------------------------------- #
# EXPERIENCE                                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "lo", "hi", "status"),
    [
        ("Không yêu cầu kinh nghiệm", 0.0, 0.0, ExperienceParseStatus.PARSED),
        ("1 - 3 năm", 1.0, 3.0, ExperienceParseStatus.PARSED),
        ("Trên 5 năm", 5.0, None, ExperienceParseStatus.PARSED),
        ("Dưới 1 năm", 0.0, 1.0, ExperienceParseStatus.PARSED),
        ("5", 5.0, 5.0, ExperienceParseStatus.PARSED),
        (None, None, None, ExperienceParseStatus.MISSING),
        ("Nhân viên cao cấp", None, None, ExperienceParseStatus.UNPARSED),
    ],
)
def test_parse_experience(raw, lo, hi, status):
    assert parse_experience(raw) == (lo, hi, status)


# --------------------------------------------------------------------------- #
# CATEGORIES — repeated, dedupe, fallback                                      #
# --------------------------------------------------------------------------- #
def test_categories_key_source_qualified_and_dedupe():
    cats = [cat("22", "Sales", "g14~j22", "14", "22"), cat("22", "Sales", "g14~j22", "14", "22")]
    out = build_categories("topdev", cats)
    assert len(out) == 1
    assert out[0].category_key == "topdev:g14~j22"
    assert out[0].category_path == "g14~j22"
    assert out[0].level1_id == "14"


def test_categories_fallback_to_list_category():
    out = build_categories("vietnamworks", None, {"name": "IT", "group": "g5~j40", "code": "j40"})
    assert [c.category_key for c in out] == ["vietnamworks:g5~j40"]


def test_categories_empty_when_no_group_or_code():
    assert build_categories("topdev", [{"name": "X"}]) == []


# --------------------------------------------------------------------------- #
# SENIORITY — versioned, null khi không chắc                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Chuyên viên", "mid"),
        ("Nhân viên", "mid"),
        ("Chuyên viên cấp cao", "senior"),
        ("Trưởng phòng", "senior"),
        ("Giám Đốc và Cấp Cao Hơn", "senior"),
        ("Thực tập, Mới tốt nghiệp", "junior"),
        ("Thực tập sinh/Sinh viên", "junior"),
        ("Tất cả cấp bậc", None),
        ("Chuyên viên cấp cao, Chuyên viên", None),  # combo lẫn bậc
        (None, None),
    ],
)
def test_normalize_seniority(raw, expected):
    normalized, version = normalize_seniority(raw)
    assert normalized == expected
    if raw:
        assert version == SENIORITY_MAPPING_VERSION


# --------------------------------------------------------------------------- #
# COMPANY — lấy từ raw theo nguồn; thiếu → None                                #
# --------------------------------------------------------------------------- #
def test_company_topdev_prefers_company_detail():
    assert extract_company_name("topdev", {"company_detail": {"display_name": "ABC"}}) == "ABC"
    assert extract_company_name("topdev", {"company": {"display_name": "XYZ"}}) == "XYZ"


def test_company_vietnamworks():
    assert extract_company_name("vietnamworks", {"companyName": "Takeuni"}) == "Takeuni"


def test_company_missing_returns_none():
    assert extract_company_name("topdev", {}) is None
