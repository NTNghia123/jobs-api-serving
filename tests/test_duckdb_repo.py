"""Parity test DuckDB ↔ Fake — DuckDB CHẠY SQL thật (keyset/filter/EXISTS/limit+1) và phải
cho CÙNG kết quả FakeJobRepository trên cùng bộ dữ liệu (fixture SILVER mirror fake.SAMPLE).

Đây là lưới an toàn chạy-được cho ngữ nghĩa SQL Phase 3 (BQ không chạy được khi thiếu creds).
Publish-batch-flip nhiều batch verify ở BQ integration (RUN_BQ_INTEGRATION). Xem ADR-020, ADR-026.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.errors import UpstreamUnavailableError
from app.infrastructure.warehouse.duckdb_jobs import DuckDBJobRepository
from app.infrastructure.warehouse.duckdb_metrics import DuckDBMetricsRepository
from app.infrastructure.warehouse.fake_jobs import FakeJobRepository
from app.models.enums import SortOption
from app.models.jobs import SearchFilters, SearchRequest
from tests.fixtures.duckdb_fixture import BATCH_ID, DATA_AS_OF_AT, load_duckdb

FAKE = FakeJobRepository()   # SAMPLE mặc định = cùng 9 job với fixture SILVER


@pytest.fixture(scope="module")
def db_path(tmp_path_factory) -> str:
    p = str(tmp_path_factory.mktemp("duck") / "published.duckdb")
    load_duckdb(p)
    return p


@pytest.fixture
def repo(db_path) -> DuckDBJobRepository:
    return DuckDBJobRepository(db_path)


@pytest.fixture
def metrics(db_path) -> DuckDBMetricsRepository:
    return DuckDBMetricsRepository(db_path)


def _req(sort=SortOption.SALARY_MAX_DESC, limit=50, **filters) -> SearchRequest:
    return SearchRequest(
        filters=SearchFilters(posted_after=date(2020, 1, 1), **filters), sort=sort, limit=limit,
    )


def _ids(res) -> list[str]:
    return [i.job_id for i in res.items]


def _collect(repo, req) -> list[str]:
    """Duyệt hết các trang qua keyset cursor."""
    out: list[str] = []
    cur = None
    while True:
        res = repo.search(req, cur)
        out += _ids(res)
        if not res.next_cursor:
            break
        cur = res.next_cursor
    return out


# --- sort: thứ tự DuckDB (SQL) == fake (Python) ---
@pytest.mark.parametrize("sort", list(SortOption))
def test_sort_order_khop_fake(repo, sort):
    r = _req(sort=sort)
    assert _ids(repo.search(r, None)) == _ids(FAKE.search(r, None))


# --- keyset trong SQL: ghép mọi trang == full, không trùng/sót, đúng thứ tự fake ---
@pytest.mark.parametrize("sort", list(SortOption))
def test_paging_keyset_khop_fake(repo, sort):
    got = _collect(repo, _req(sort=sort, limit=2))
    expected = _ids(FAKE.search(_req(sort=sort), None))
    assert got == expected                      # đúng thứ tự, không sót
    assert len(got) == len(set(got))            # không trùng


# --- filter: DuckDB chọn đúng tập như fake ---
@pytest.mark.parametrize("filt", [
    {"source": "vietnamworks"},
    {"source": "topdev"},
    {"seniority": "senior"},
    {"seniority": "unknown"},                   # ↔ seniority_normalized IS NULL
    {"category": "topdev:g1~j5"},
    {"salary_min": 45_000_000},                 # LOẠI NULL salary
    {"experience_max": 3},                      # LOẠI NULL experience
    {"posted_before": date(2026, 8, 1)},
])
def test_filter_chon_dung_tap_nhu_fake(repo, filt):
    r = _req(**filt)
    assert sorted(_ids(repo.search(r, None))) == sorted(_ids(FAKE.search(r, None)))


def test_category_khong_nhan_dong_giu_ca_hai(repo):
    res = repo.search(_req(category="topdev:g1~j5"), None)
    ids = _ids(res)
    assert ids.count("topdev:1001") == 1        # multi-category không nhân dòng
    job = next(i for i in res.items if i.job_id == "topdev:1001")
    assert {c.category_key for c in job.categories} >= {"topdev:g1~j5", "topdev:g14~j22"}


def test_limit_plus_one_next_cursor(repo):
    res = repo.search(_req(limit=3), None)
    assert len(res.items) == 3 and res.next_cursor is not None
    assert _collect(repo, _req(limit=3)) == _ids(FAKE.search(_req(), None))   # gom đủ 9
    assert repo.search(_req(limit=50), None).next_cursor is None              # hết → None


def test_total_estimated_none_va_as_of(repo):
    res = repo.search(_req(), None)
    assert res.total_estimated is None          # mirror BQ (không COUNT(*))
    assert res.as_of == DATA_AS_OF_AT           # as_of từ batch (gắn UTC)


def test_job_id_composite(repo):
    for i in repo.search(_req(), None).items:
        assert ":" in i.job_id and i.job_id == f"{i.source.value}:{i.external_id}"


# --- batch scoping qua cursor (invariant token-batch) ---
def test_cursor_batch_id_gioi_han_query(repo):
    # cursor trỏ batch KHÔNG tồn tại → 0 dòng (batch_id lấy TỪ cursor được áp vào WHERE).
    bad = {"batch_id": "no-such-batch", "as_of": DATA_AS_OF_AT.isoformat(),
           "last_sort": 0, "last_job_id": ""}
    assert repo.search(_req(), bad).items == []


def test_as_of_snapshot_giua_cac_trang(repo):
    p1 = repo.search(_req(limit=2), None)
    p2 = repo.search(_req(limit=2), p1.next_cursor)
    assert p1.as_of == p2.as_of == DATA_AS_OF_AT     # snapshot, không đổi qua trang
    assert p1.next_cursor["batch_id"] == BATCH_ID


def test_chua_publish_thi_503(tmp_path):
    p = str(tmp_path / "empty.duckdb")
    load_duckdb(p, published=False)              # state trỏ NULL
    with pytest.raises(UpstreamUnavailableError):
        DuckDBJobRepository(p).as_of()
    with pytest.raises(UpstreamUnavailableError):
        DuckDBMetricsRepository(p).current_batch()


# --- metrics ---
def test_metrics_counts_monotonic(metrics):
    b = metrics.current_batch()
    assert b.batch_id == BATCH_ID and b.as_of == DATA_AS_OF_AT
    for dim in ("source", "seniority", "category"):
        rows = metrics.market_metrics(dim, "all_time", b.batch_id)
        assert rows
        for r in rows:
            assert r.salary_sample_count <= r.salary_disclosed_count <= r.posting_count


def test_metrics_unknown_category_bucket(metrics):
    b = metrics.current_batch()
    rows = {r.dimension_value: r for r in metrics.market_metrics("category", "all_time", b.batch_id)}
    assert "vietnamworks:unknown" in rows        # job 2005 thiếu category → bucket unknown
    assert rows["vietnamworks:unknown"].posting_count == 1


def test_metrics_90d_khong_lon_hon_all_time(metrics):
    b = metrics.current_batch()
    all_p = sum(r.posting_count for r in metrics.market_metrics("source", "all_time", b.batch_id))
    d90_p = sum(r.posting_count for r in metrics.market_metrics("source", "90d", b.batch_id))
    assert d90_p <= all_p                          # 90d là tập con corpus
