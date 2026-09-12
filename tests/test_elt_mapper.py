"""Unit test cho mapper + reconciliation của ELT serving.

Dùng Mongo-like fixtures (tests/fixtures/mongo_like.py). Không chạm Mongo/BigQuery.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.elt.serving.mapper import map_record, reconcile
from app.elt.serving.silver import QuarantineRecord, SilverRow
from tests.fixtures import mongo_like as fx

BATCH = "batch-test"


def _map(pair):
    job, detail = pair
    return map_record(job, detail, BATCH)


# --------------------------------------------------------------------------- #
# Happy path                                                                   #
# --------------------------------------------------------------------------- #
def test_topdev_full_maps_to_silver():
    row = _map(fx.TOPDEV_FULL)
    assert isinstance(row, SilverRow)
    assert row.job_id == "topdev:td1001"
    assert row.source == "topdev" and row.external_id == "td1001"
    assert row.company_name == "Công ty ABC"
    assert row.seniority_normalized == "mid"           # 'Chuyên viên'
    assert (row.salary_min_vnd_month, row.salary_max_vnd_month) == (10_000_000, 58_000_000)
    assert row.experience_min_years == 2.0 and row.experience_max_years == 4.0
    assert row.effective_posted_date == date(2026, 8, 20)
    # multi-category + trùng key → dedupe còn 2 dòng, 1 job vẫn 1 row
    assert [c.category_key for c in row.categories] == ["topdev:g14~j16", "topdev:g14~j22"]
    assert row.batch_id == BATCH


def test_vnw_full_genuine_usd():
    row = _map(fx.VNW_FULL)
    assert isinstance(row, SilverRow)
    assert row.seniority_normalized == "senior"        # 'Trưởng phòng'
    assert row.salary_currency == "USD"
    assert (row.salary_min_vnd_month, row.salary_max_vnd_month) == (12_750_000, 25_500_000)


def test_vnw_usd_mislabel_becomes_vnd_millions():
    row = _map(fx.VNW_USD_MISLABEL)
    assert isinstance(row, SilverRow)
    assert row.salary_currency == "VND"  # '$ 40tr-70tr' hiểu là triệu VND
    assert (row.salary_min_vnd_month, row.salary_max_vnd_month) == (40_000_000, 70_000_000)


def test_salary_garbage_is_silver_but_invalid():
    """Lương bất khả thi KHÔNG loại job — chỉ đánh dấu invalid."""
    row = _map(fx.VNW_SALARY_GARBAGE)
    assert isinstance(row, SilverRow)
    assert row.salary_normalization_status.value == "invalid"
    assert row.salary_min_vnd_month is None and row.salary_max_vnd_month is None


def test_negotiable_and_one_sided():
    neg = _map(fx.TOPDEV_NEGOTIABLE)
    assert isinstance(neg, SilverRow) and neg.salary_normalization_status.value == "negotiable"
    one = _map(fx.TOPDEV_ONE_SIDED)
    assert one.salary_min_vnd_month is None and one.salary_max_vnd_month == 30_000_000


# --------------------------------------------------------------------------- #
# Quarantine                                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("pair", "reason"),
    [
        (fx.MISSING_DETAIL, "missing_detail"),
        (fx.DETAIL_PENDING, "missing_detail"),
        (fx.MISSING_TITLE, "missing_title"),
        (fx.MISSING_ID, "missing_external_id"),
        (fx.INVALID_DATE, "invalid_posted_date"),
    ],
)
def test_quarantine_reasons(pair, reason):
    rec = _map(pair)
    assert isinstance(rec, QuarantineRecord)
    assert rec.reason_code.value == reason
    assert rec.batch_id == BATCH


def test_quarantine_order_detail_before_title():
    """Thiếu cả detail lẫn title → báo missing_detail trước (cổng 2 trước cổng 3)."""
    job = fx.job("topdev", "tdX", title=None)
    rec = map_record(job, None, BATCH)
    assert isinstance(rec, QuarantineRecord)
    assert rec.reason_code.value == "missing_detail"


# --------------------------------------------------------------------------- #
# Reconciliation (plan #9)                                                     #
# --------------------------------------------------------------------------- #
def test_reconcile_source_equals_silver_plus_quarantine():
    results = [_map(p) for p in fx.SERVED + fx.QUARANTINED]
    reports = reconcile(results)

    total_silver = sum(r.silver_rows for r in reports.values())
    total_quar = sum(r.quarantined_rows for r in reports.values())
    assert total_silver == len(fx.SERVED)
    assert total_quar == len(fx.QUARANTINED)

    for rep in reports.values():
        assert rep.source_jobs == rep.silver_rows + rep.quarantined_rows
        assert rep.is_consistent                       # distinct job_id == silver rows


def test_reconcile_distinct_job_id_detects_duplicates():
    dup = [_map(fx.TOPDEV_FULL), _map(fx.TOPDEV_FULL)]  # cùng job_id 2 lần
    rep = reconcile(dup)["topdev"]
    assert rep.silver_rows == 2 and rep.distinct_job_ids == 1
    assert not rep.is_consistent                       # bắt được trùng
