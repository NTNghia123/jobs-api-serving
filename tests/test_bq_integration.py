"""Integration test BigQuery THẬT — ghi/đọc roundtrip trên dataset `_test` (skip mặc định).

Bật bằng: RUN_BQ_INTEGRATION=1 + JOBS_BQ_PROJECT (+ JOBS_BQ_DATASET_STAGING, JOBS_BQ_LOCATION)
+ ADC (gcloud auth application-default login hoặc SA). Dataset đích = <staging>_test (writer guard
dataset_suffix). Publish batch SILVER (mirror fake) qua ELT, rồi đọc lại qua adapter serving.

Kiểm phần I/O mà fake/duckdb KHÔNG phủ được: SQL chạy thật trên BQ (keyset/EXISTS/COALESCE
khớp fake), metadata theo batch, maximum_bytes_billed (cost guard). Xem docs/adr/ADR-020, ADR-026.
Teardown xoá bảng đã tạo (giữ dataset _test).
"""
from __future__ import annotations

import os
from datetime import date

import pytest

from app.infrastructure.warehouse.fake_jobs import FakeJobRepository
from app.models.enums import SortOption
from app.models.jobs import SearchFilters, SearchRequest
from tests.fixtures.duckdb_fixture import AS_OF_DATE, BATCH_ID, DATA_AS_OF_AT, SILVER

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BQ_INTEGRATION") != "1",
    reason="Đặt RUN_BQ_INTEGRATION=1 + JOBS_BQ_* + ADC để chạy (ghi/đọc BQ thật, dataset _test).",
)

FAKE = FakeJobRepository()   # SAMPLE = cùng 9 job với SILVER


def _req(sort=SortOption.SALARY_MAX_DESC, limit=50, **filters) -> SearchRequest:
    return SearchRequest(
        filters=SearchFilters(posted_after=date(2020, 1, 1), **filters), sort=sort, limit=limit,
    )


def _ids(res) -> list[str]:
    return [i.job_id for i in res.items]


@pytest.fixture(scope="module")
def read_target():
    """Publish batch SILVER vào <staging>_test qua ELT, yield ReadTarget để đọc. Teardown xoá bảng."""
    from google.cloud import bigquery

    from app.elt.serving.bigquery_writer import BigQueryWarehouseWriter, publish_batch
    from app.elt.serving.gold import build_gold
    from app.elt.serving.publish import BatchMetadata
    from app.elt.serving.salary import SALARY_FX_VERSION
    from app.elt.serving.schema import (
        TABLE_BATCHES,
        TABLE_GOLD,
        TABLE_GOLD_CANDIDATE,
        TABLE_QUARANTINE,
        TABLE_SILVER,
        TABLE_SILVER_CANDIDATE,
        TABLE_STATE,
    )
    from app.elt.serving.seniority import SENIORITY_MAPPING_VERSION
    from app.elt.serving.targets import WriterConfig, resolve_target
    from app.infrastructure.warehouse.bigquery_read_sql import ReadTarget

    target = resolve_target("staging", WriterConfig.from_env(), dataset_suffix="_test")
    writer = BigQueryWarehouseWriter(target)

    ds = bigquery.Dataset(f"{target.project}.{target.dataset}")
    ds.location = target.location
    writer.client.create_dataset(ds, exists_ok=True)   # dataset _test có thể chưa tồn tại

    gold = build_gold(SILVER, AS_OF_DATE, BATCH_ID)
    counts = {"topdev": sum(r.source == "topdev" for r in SILVER),
              "vietnamworks": sum(r.source == "vietnamworks" for r in SILVER)}
    meta = BatchMetadata(
        batch_id=BATCH_ID, data_as_of_at=DATA_AS_OF_AT, as_of_date=AS_OF_DATE,
        source_jobs_total=len(SILVER), topdev_source_jobs=counts["topdev"],
        vietnamworks_source_jobs=counts["vietnamworks"], silver_rows=len(SILVER),
        gold_rows=len(gold), quarantined_rows=0,
        mapping_version=SENIORITY_MAPPING_VERSION, salary_fx_version=SALARY_FX_VERSION,
    )
    publish_batch(writer, SILVER, gold, [], meta, counts)

    yield ReadTarget(project=target.project, dataset=target.dataset,
                     location=target.location, maximum_bytes_billed=target.maximum_bytes_billed)

    for t in (TABLE_SILVER, TABLE_SILVER_CANDIDATE, TABLE_GOLD, TABLE_GOLD_CANDIDATE,
              TABLE_STATE, TABLE_BATCHES, TABLE_QUARANTINE):
        writer.client.delete_table(target.table_ref(t), not_found_ok=True)


@pytest.fixture
def jobs_repo(read_target):
    from app.infrastructure.warehouse.bigquery_jobs import BigQueryJobRepository
    return BigQueryJobRepository(read_target)


@pytest.mark.parametrize("sort", list(SortOption))
def test_search_sort_khop_fake(jobs_repo, sort):
    r = _req(sort=sort)
    assert _ids(jobs_repo.search(r, None)) == _ids(FAKE.search(r, None))   # SQL BQ thật == fake


def test_paging_keyset_khop_fake(jobs_repo):
    out, cur = [], None
    while True:
        res = jobs_repo.search(_req(limit=2), cur)
        out += _ids(res)
        if not res.next_cursor:
            break
        cur = res.next_cursor
    assert out == _ids(FAKE.search(_req(), None))
    assert len(out) == len(set(out))


def test_category_exists_khong_nhan_dong(jobs_repo):
    res = jobs_repo.search(_req(category="topdev:g1~j5"), None)
    ids = _ids(res)
    assert ids.count("topdev:1001") == 1
    job = next(i for i in res.items if i.job_id == "topdev:1001")
    assert {c.category_key for c in job.categories} >= {"topdev:g1~j5", "topdev:g14~j22"}


def test_as_of_va_total_estimated(jobs_repo):
    res = jobs_repo.search(_req(), None)
    assert res.as_of == DATA_AS_OF_AT       # metadata đọc theo batch
    assert res.total_estimated is None


def test_metrics_roundtrip(read_target):
    from app.infrastructure.warehouse.bigquery_metrics import BigQueryMetricsRepository
    repo = BigQueryMetricsRepository(read_target)
    b = repo.current_batch()
    assert b.batch_id == BATCH_ID and b.as_of == DATA_AS_OF_AT
    rows = repo.market_metrics("category", "all_time", b.batch_id)
    assert {r.dimension_value for r in rows} >= {"topdev:g1~j5", "vietnamworks:unknown"}
    for r in rows:
        assert r.salary_sample_count <= r.salary_disclosed_count <= r.posting_count


def test_cost_guard_maximum_bytes_billed(read_target):
    """maximum_bytes_billed=1 → BQ TỪ CHỐI job (cost guard) — điều fake/duckdb không kiểm được."""
    from app.infrastructure.warehouse.bigquery_jobs import BigQueryJobRepository
    from app.infrastructure.warehouse.bigquery_read_sql import ReadTarget
    tiny = ReadTarget(project=read_target.project, dataset=read_target.dataset,
                      location=read_target.location, maximum_bytes_billed=1)
    with pytest.raises(Exception):   # google.api_core exceeded maximum_bytes_billed  # noqa: B017
        BigQueryJobRepository(tiny).search(_req(), None)
