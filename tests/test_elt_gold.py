"""Unit test cho gold builder (app/elt/serving/gold.py).

Dựng SilverRow qua mapper từ fixtures có kiểm soát, rồi build_gold và kiểm 3 count +
median + unknown bucket + multi-category không nhân đôi + window 90d. k-anon KHÔNG ở gold.
"""
from __future__ import annotations

from datetime import date

from app.elt.serving.gold import WINDOW_90D, WINDOW_ALL_TIME, build_gold
from app.elt.serving.mapper import map_record
from tests.fixtures import mongo_like as fx

AS_OF = date(2026, 9, 1)
BATCH = "b1"
_IN_WINDOW = "2026-08-01"           # trong 90d
_OLD = "2026-05-01"                 # trước as_of-89 (2026-06-04)


def _silver(source, eid, level, cats, salary, posted=_IN_WINDOW):
    job = fx.job(source, eid, posted_at=posted)
    det = fx.detail(source, eid, level=level, categories=cats, salary=salary)
    return map_record(job, det, BATCH)


def _scenario():
    g1 = [fx.cat("5", "Backend", "g1~j5", "1", "5")]
    g1_g14 = g1 + [fx.cat("22", "Sales", "g14~j22", "14", "22")]
    g8 = [fx.cat("51", "Tài chính", "g8~j51", "8", "51")]
    return [
        _silver("topdev", "A", "Chuyên viên cấp cao", g1, fx.sal("30 - 50 triệu", 30, 50, "VND")),
        _silver("topdev", "B", "Chuyên viên", g1_g14, fx.sal("Tới 20 triệu", None, 20, "VND")),
        _silver("topdev", "C", "Chuyên viên", [], fx.NEGOTIABLE),
        _silver("vietnamworks", "D", "Trưởng phòng", g8, fx.sal("40 - 60 triệu", 40, 60, "VND")),
        _silver("vietnamworks", "E", "Tất cả cấp bậc", g8, fx.sal("10 - 10 triệu", 10, 10, "VND")),
    ]


def _index(rows, window=WINDOW_ALL_TIME):
    return {(r.dimension, r.dimension_value): r for r in rows if r.window == window}


def test_gold_source_counts_and_median():
    g = _index(build_gold(_scenario(), AS_OF, BATCH))
    td = g[("source", "topdev")]
    assert td.posting_count == 3 and td.salary_disclosed_count == 2 and td.salary_sample_count == 1
    assert td.median_salary_vnd_month == 40_000_000       # midpoint A
    vnw = g[("source", "vietnamworks")]
    assert vnw.posting_count == 2 and vnw.salary_sample_count == 2
    assert vnw.median_salary_vnd_month == 30_000_000      # median(50M, 10M)


def test_gold_seniority_buckets_and_unknown():
    g = _index(build_gold(_scenario(), AS_OF, BATCH))
    assert g[("seniority", "senior")].posting_count == 2        # A, D
    assert g[("seniority", "senior")].median_salary_vnd_month == 45_000_000  # median(40M,50M)
    mid = g[("seniority", "mid")]
    assert mid.posting_count == 2 and mid.salary_sample_count == 0            # B one-sided, C neg
    assert mid.median_salary_vnd_month is None
    assert g[("seniority", "unknown")].posting_count == 1      # E ('Tất cả cấp bậc'→None)


def test_gold_category_no_double_count_and_unknown_bucket():
    g = _index(build_gold(_scenario(), AS_OF, BATCH))
    # B ở cả g1~j5 lẫn g14~j22 nhưng posting đếm distinct job_id mỗi bucket
    assert g[("category", "topdev:g1~j5")].posting_count == 2   # A, B
    assert g[("category", "topdev:g14~j22")].posting_count == 1  # B
    assert g[("category", "topdev:unknown")].posting_count == 1  # C không category
    assert g[("category", "vietnamworks:g8~j51")].posting_count == 2  # D, E


def test_gold_invariant_posting_ge_disclosed_ge_sample():
    for r in build_gold(_scenario(), AS_OF, BATCH):
        assert r.posting_count >= r.salary_disclosed_count >= r.salary_sample_count


def test_gold_window_90d_filters_old_jobs():
    rows = _scenario() + [
        _silver("topdev", "OLD", "Chuyên viên", [], fx.sal("30 - 50 triệu", 30, 50, "VND"), posted=_OLD),
    ]
    gold = build_gold(rows, AS_OF, BATCH)
    all_td = _index(gold, WINDOW_ALL_TIME)[("source", "topdev")]
    win_td = _index(gold, WINDOW_90D)[("source", "topdev")]
    assert all_td.posting_count == 4      # gồm OLD
    assert win_td.posting_count == 3      # OLD bị loại khỏi 90d


def test_gold_keeps_real_median_below_k_anon():
    """Gold KHÔNG che median dù sample nhỏ (k-anon ở API)."""
    g = _index(build_gold(_scenario(), AS_OF, BATCH))
    td = g[("source", "topdev")]
    assert td.salary_sample_count == 1 and td.median_salary_vnd_month is not None
