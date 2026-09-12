"""Test quality checks (app/elt/serving/checks.py). Thuần, không BQ."""
from __future__ import annotations

from collections import Counter
from datetime import date

from app.elt.serving.checks import run_quality_checks
from app.elt.serving.gold import GoldMetricRow, build_gold
from app.elt.serving.mapper import map_record
from app.elt.serving.silver import QuarantineRecord, SilverRow
from tests.fixtures import mongo_like as fx

BATCH = "b1"
AS_OF = date(2026, 9, 1)


def _build():
    results = [map_record(j, d, BATCH) for j, d in fx.SERVED + fx.QUARANTINED]
    silver = [r for r in results if isinstance(r, SilverRow)]
    quar = [r for r in results if isinstance(r, QuarantineRecord)]
    gold = build_gold(silver, AS_OF, BATCH)
    extract = Counter(j["platformId"] for j, _ in fx.SERVED + fx.QUARANTINED)
    return silver, quar, gold, dict(extract)


def test_all_checks_pass_on_good_batch():
    silver, quar, gold, extract = _build()
    report = run_quality_checks(silver, quar, gold, BATCH, extract)
    assert report.ok, report.summary()


def test_reconciliation_fails_on_wrong_extract_count():
    silver, quar, gold, extract = _build()
    extract["topdev"] += 1   # giả vờ Mongo có thêm 1 job không thành silver/quar
    report = run_quality_checks(silver, quar, gold, BATCH, extract)
    assert not report.ok
    assert any("reconciliation" in f.name for f in report.failures)


def test_distinct_fails_on_duplicate_job_id():
    silver, quar, gold, extract = _build()
    dup = silver + [silver[0]]
    report = run_quality_checks(dup, quar, gold, BATCH, dict(Counter(r.source for r in dup) + Counter(r.source for r in quar)))
    assert any("distinct" in f.name for f in report.failures)


def test_batch_id_mismatch_detected():
    silver, quar, gold, extract = _build()
    report = run_quality_checks(silver, quar, gold, "OTHER_BATCH", extract)
    assert any("batch_id" in f.name for f in report.failures)


def test_gold_count_ordering_violation_detected():
    silver, quar, gold, extract = _build()
    bad = list(gold) + [GoldMetricRow(BATCH, "90d", "source", "x", 1, 2, 0, None)]  # disclosed>posting
    report = run_quality_checks(silver, quar, bad, BATCH, extract)
    assert any("posting>=disclosed" in f.name for f in report.failures)


def test_gold_uniqueness_violation_detected():
    silver, quar, gold, extract = _build()
    dup_key = list(gold) + [gold[0]]
    report = run_quality_checks(silver, quar, dup_key, BATCH, extract)
    assert any("unique" in f.name for f in report.failures)


def test_gold_median_consistency_violation_detected():
    silver, quar, gold, extract = _build()
    # sample=0 nhưng median khác None → vi phạm
    bad = list(gold) + [GoldMetricRow(BATCH, "90d", "source", "y", 5, 0, 0, 10_000_000.0)]
    report = run_quality_checks(silver, quar, bad, BATCH, extract)
    assert any("median" in f.name for f in report.failures)
