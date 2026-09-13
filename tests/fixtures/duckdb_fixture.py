"""Fixture + loader DuckDB cho parity test (Phase 4).

Nạp SilverRow (+ gold dựng từ build_gold) vào một file DuckDB → mở read_only bằng
DuckDBJobRepository/DuckDBMetricsRepository. SILVER MIRROR đúng 9 job của FakeJobRepository
(fake_jobs.SAMPLE) để so trực tiếp duckdb ↔ fake. Chỉ tạo cột read-path cần (subset silver).

Loader là test/dev tooling (DuckDB chưa có ELT bơm dữ liệu — BQ→DuckDB export để [SAU]).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb

from app.elt.serving.gold import GoldMetricRow, build_gold
from app.elt.serving.salary import SalaryNormalizationStatus
from app.elt.serving.silver import (
    DeadlineDateParseStatus,
    ExperienceParseStatus,
    PostedDateParseStatus,
    SilverCategory,
    SilverRow,
)

BATCH_ID = "duckdb-batch-2026-09-01"
DATA_AS_OF_AT = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)   # khớp fake _AS_OF
AS_OF_DATE = date(2026, 9, 1)


def _cat(key: str, name: str) -> SilverCategory:
    return SilverCategory(category_key=key, category_name=name, category_path=key.split(":", 1)[1])


def _silver(source, ext, title, company, loc, sen, emin, emax,
            smin, smax, cur, cats, posted, deadline) -> SilverRow:
    has_salary = smin is not None or smax is not None
    return SilverRow(
        job_id=f"{source}:{ext}", source=source, external_id=ext,
        source_url=f"https://{source}.example/job/{ext}", detail_status="completed",
        title=title, company_name=company, location_text=loc,
        seniority_raw=None, seniority_normalized=sen, seniority_mapping_version="seniority-2026-09-v1",
        experience_raw=None, experience_min_years=emin, experience_max_years=emax,
        experience_parse_status=ExperienceParseStatus.PARSED,
        salary_raw=None, salary_min_original=None, salary_max_original=None,
        salary_currency=cur, salary_period="month" if has_salary else None,
        salary_min_vnd_month=smin, salary_max_vnd_month=smax,
        fx_rate_to_vnd=None, salary_fx_version=None,
        salary_normalization_status=(
            SalaryNormalizationStatus.PARSED if has_salary else SalaryNormalizationStatus.NEGOTIABLE
        ),
        posted_at=datetime(posted.year, posted.month, posted.day, tzinfo=UTC) if posted else None,
        effective_posted_date=posted, deadline_date=deadline,
        posted_date_parse_status=PostedDateParseStatus.PARSED,
        deadline_date_parse_status=(
            DeadlineDateParseStatus.PARSED if deadline else DeadlineDateParseStatus.MISSING
        ),
        first_seen_at=datetime(2026, 8, 21, 2, 0, tzinfo=UTC), last_seen_at=None,
        batch_id=BATCH_ID, categories=cats,
    )


_BE = [_cat("topdev:g1~j5", "Backend")]
_SALES = [_cat("topdev:g14~j22", "Sales")]
_HR = [_cat("vietnamworks:g5~j40", "Nhân sự")]
_FIN = [_cat("vietnamworks:g8~j51", "Tài chính")]

# MIRROR fake_jobs.SAMPLE (cùng 9 job) → so trực tiếp duckdb ↔ fake.
SILVER: list[SilverRow] = [
    _silver("topdev", "1001", "Backend Engineer", "Acme", "Hà Nội", "senior", 4, 6,
            30_000_000, 50_000_000, "VND", _BE + _SALES, date(2026, 8, 20), date(2026, 10, 1)),
    _silver("topdev", "1002", "Sales Executive", "Beta", "TP.HCM", "junior", 0, 1,
            12_000_000, 18_000_000, "VND", _SALES, date(2026, 8, 10), date(2026, 9, 30)),
    _silver("topdev", "1003", "Senior Backend (USD)", "Gamma", "Đà Nẵng", "senior", 5, 8,
            51_000_000, 76_500_000, "USD", _BE, date(2026, 7, 15), None),
    _silver("topdev", "1004", "Backend Intern", "Delta", "Hà Nội", "junior", 0, 0,
            None, None, None, _BE, date(2026, 9, 1), date(2026, 11, 1)),   # negotiable
    _silver("vietnamworks", "2001", "HR Manager", "Epsilon", "TP.HCM", "mid", 2, 4,
            25_000_000, 35_000_000, "VND", _HR, date(2026, 6, 5), date(2026, 9, 15)),
    _silver("vietnamworks", "2002", "Finance Analyst", "Zeta", "Hà Nội", "mid", 3, 5,
            28_000_000, 40_000_000, "VND", _FIN, date(2026, 5, 20), date(2026, 9, 20)),
    _silver("vietnamworks", "2003", "HR Assistant", "Eta", "Cần Thơ", "junior", 1, 2,
            15_000_000, 22_000_000, "VND", _HR, date(2026, 8, 25), None),
    _silver("vietnamworks", "2004", "Finance Lead", "Theta", "TP.HCM", "senior", 6, 9,
            45_000_000, 70_000_000, "VND", _FIN, date(2026, 7, 30), date(2026, 10, 10)),
    _silver("vietnamworks", "2005", "Chưa rõ cấp bậc", "Iota", "Hà Nội", None, None, None,
            20_000_000, 30_000_000, "VND", [], date(2026, 8, 1), None),   # seniority null→unknown
]

_CREATE = """
CREATE TABLE silver_jobs(
  job_id VARCHAR, source VARCHAR, external_id VARCHAR, source_url VARCHAR,
  title VARCHAR, company_name VARCHAR, location_text VARCHAR,
  seniority_normalized VARCHAR, experience_min_years DOUBLE, experience_max_years DOUBLE,
  salary_min_vnd_month BIGINT, salary_max_vnd_month BIGINT, salary_currency VARCHAR,
  salary_period VARCHAR, posted_at TIMESTAMP, effective_posted_date DATE, deadline_date DATE,
  batch_id VARCHAR,
  categories STRUCT(category_key VARCHAR, category_name VARCHAR, category_path VARCHAR)[]
);
CREATE TABLE gold_market_metrics(
  batch_id VARCHAR, "window" VARCHAR, dimension VARCHAR, dimension_value VARCHAR,
  posting_count BIGINT, salary_disclosed_count BIGINT, salary_sample_count BIGINT,
  median_salary_vnd_month DOUBLE
);
CREATE TABLE warehouse_batches(batch_id VARCHAR, data_as_of_at TIMESTAMP, as_of_date DATE);
CREATE TABLE warehouse_state(warehouse_name VARCHAR, published_batch_id VARCHAR);
"""


def _naive(dt: datetime | None) -> datetime | None:
    # cột TIMESTAMP (naive) — bỏ tzinfo khi insert (repo gắn lại UTC lúc đọc). Tránh pytz.
    return dt.replace(tzinfo=None) if dt is not None else None


def _silver_tuple(r: SilverRow) -> tuple:
    return (
        r.job_id, r.source, r.external_id, r.source_url, r.title, r.company_name, r.location_text,
        r.seniority_normalized, r.experience_min_years, r.experience_max_years,
        r.salary_min_vnd_month, r.salary_max_vnd_month, r.salary_currency, r.salary_period,
        _naive(r.posted_at), r.effective_posted_date, r.deadline_date, r.batch_id,
        [{"category_key": c.category_key, "category_name": c.category_name,
          "category_path": c.category_path} for c in r.categories],
    )


def _gold_tuple(g: GoldMetricRow) -> tuple:
    return (g.batch_id, g.window, g.dimension, g.dimension_value,
            g.posting_count, g.salary_disclosed_count, g.salary_sample_count,
            g.median_salary_vnd_month)


def load_duckdb(
    path: str,
    silver: list[SilverRow] | None = None,
    *,
    published: bool = True,
) -> None:
    """Tạo file DuckDB + nạp silver/gold + pointer batch. published=False → state trỏ NULL
    (mô phỏng chưa publish → repo raise 503)."""
    silver = SILVER if silver is None else silver
    gold = build_gold(silver, AS_OF_DATE, BATCH_ID)
    con = duckdb.connect(path)
    try:
        con.execute(_CREATE)
        con.executemany(
            "INSERT INTO silver_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_silver_tuple(r) for r in silver],
        )
        con.executemany(
            "INSERT INTO gold_market_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [_gold_tuple(g) for g in gold],
        )
        con.execute("INSERT INTO warehouse_batches VALUES (?, ?, ?)",
                    [BATCH_ID, _naive(DATA_AS_OF_AT), AS_OF_DATE])
        con.execute("INSERT INTO warehouse_state VALUES (?, ?)",
                    ["serving", BATCH_ID if published else None])
    finally:
        con.close()
