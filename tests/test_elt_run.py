"""Test phần thuần của CLI ELT (transform, metadata, arg parsing). Không cần Mongo/BQ."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.elt.serving.run_serving_elt import (
    _build_parser,
    build_run_metric,
    make_batch_metadata,
    transform,
)
from tests.fixtures import mongo_like as fx

BATCH = "b1"
AS_OF = date(2026, 9, 1)


def _records():
    return [(job, detail, None) for job, detail in fx.SERVED + fx.QUARANTINED]


def test_transform_splits_silver_quarantine_and_builds_gold():
    out = transform(_records(), BATCH, AS_OF)
    assert len(out.silver) == len(fx.SERVED)
    assert len(out.quarantine) == len(fx.QUARANTINED)
    assert out.gold, "gold phải có dòng"
    assert all(r.batch_id == BATCH for r in out.silver)


def test_build_run_metric_payload():
    out = transform(_records(), BATCH, AS_OF)
    meta = make_batch_metadata(BATCH, datetime(2026, 9, 1, tzinfo=UTC), AS_OF,
                               {"topdev": 1, "vietnamworks": 1}, out)
    m = build_run_metric(meta, action="new_batch")
    assert m["event"] == "run_metric"                    # Dagster runner nhận diện
    assert m["silver_rows"] == len(out.silver)
    assert m["gold_rows"] == len(out.gold)
    assert m["quarantined_rows"] == len(out.quarantine)
    assert m["action"] == "new_batch"


def test_make_batch_metadata_reconciles_and_sets_versions():
    out = transform(_records(), BATCH, AS_OF)
    counts = {"topdev": 3 + len(fx.QUARANTINED), "vietnamworks": 3}
    meta = make_batch_metadata(BATCH, datetime(2026, 9, 7, tzinfo=UTC), AS_OF, counts, out,
                               crawl_batch_id="c1", dagster_run_id="d1")
    assert meta.source_jobs_total == sum(counts.values())
    assert meta.silver_rows + meta.quarantined_rows == meta.source_jobs_total  # reconciliation
    assert meta.mapping_version and meta.salary_fx_version
    assert meta.crawl_batch_id == "c1" and meta.dagster_run_id == "d1"


def test_parser_requires_environment():
    with pytest.raises(SystemExit):
        _build_parser().parse_args([])


def test_parser_rejects_unknown_environment():
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--environment", "dev"])


def test_parser_accepts_prod_and_flags():
    args = _build_parser().parse_args(
        ["--environment", "prod", "--dataset-suffix", "_test", "--dry-run"]
    )
    assert args.environment == "prod"
    assert args.dataset_suffix == "_test" and args.dry_run is True
