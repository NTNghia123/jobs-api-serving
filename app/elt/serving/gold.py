"""Build gold_market_metrics TỪ silver rows đã validate (thuần, không I/O).

Tổng hợp theo `dimension ∈ {source, seniority, category}` × `window ∈ {90d, all_time}`.
Khoá logic: `(batch_id, window, dimension, dimension_value)`. Ba count + median:

  - posting_count          = COUNT(DISTINCT job_id) trong nhóm/cửa sổ
  - salary_disclosed_count = distinct job_id có ≥1 cận VND (min HOẶC max) sau normalize
  - salary_sample_count    = distinct job_id có ĐỦ min & max (mẫu tính median)
  - median_salary_vnd_month= median của midpoint (min+max)/2 trên mẫu (gold giữ SỐ THẬT;
    k-anonymity che median khi sample < ngưỡng được áp ở TẦNG API, KHÔNG ở gold — ADR-020)

Bất biến theo thiết kế: posting ≥ disclosed ≥ sample (mẫu ⊆ disclosed ⊆ all).

Bucketing:
  - source    : job.source (mỗi job đúng một bucket)
  - seniority : job.seniority_normalized OR 'unknown'
  - category  : UNNEST(categories) → mỗi job vào MỌI category_key của nó (dedupe theo
    job_id trong bucket để multi-category KHÔNG nhân đôi posting); job không category
    → bucket '<source>:unknown'. Search KHÔNG filter được '<source>:unknown' (chỉ metrics).

window 90d neo `as_of_date`: effective_posted_date ∈ [as_of_date−89, as_of_date] (inclusive).
all_time = toàn corpus batch.
"""
from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from app.elt.serving.silver import SilverRow

WINDOW_90D = "90d"
WINDOW_ALL_TIME = "all_time"
_WINDOW_90D_DAYS = 89  # [as_of-89, as_of] = 90 ngày inclusive
_UNKNOWN = "unknown"


@dataclass(frozen=True)
class GoldMetricRow:
    batch_id: str
    window: str
    dimension: str
    dimension_value: str
    posting_count: int
    salary_disclosed_count: int
    salary_sample_count: int
    median_salary_vnd_month: float | None  # gold giữ số thật; API che khi sample < k

    def to_bq_row(self) -> dict[str, object | None]:
        return {
            "batch_id": self.batch_id,
            "window": self.window,
            "dimension": self.dimension,
            "dimension_value": self.dimension_value,
            "posting_count": self.posting_count,
            "salary_disclosed_count": self.salary_disclosed_count,
            "salary_sample_count": self.salary_sample_count,
            "median_salary_vnd_month": self.median_salary_vnd_month,
        }


# job_id → (min_vnd, max_vnd); dict đảm bảo DISTINCT job_id trong một bucket.
_Bucket = dict[str, tuple[int | None, int | None]]


def _aggregate(
    batch_id: str, window: str, dimension: str, buckets: dict[str, _Bucket],
) -> list[GoldMetricRow]:
    out: list[GoldMetricRow] = []
    for value, jobs in buckets.items():
        midpoints = [
            (mn + mx) / 2.0 for mn, mx in jobs.values() if mn is not None and mx is not None
        ]
        disclosed = sum(1 for mn, mx in jobs.values() if mn is not None or mx is not None)
        out.append(GoldMetricRow(
            batch_id=batch_id,
            window=window,
            dimension=dimension,
            dimension_value=value,
            posting_count=len(jobs),
            salary_disclosed_count=disclosed,
            salary_sample_count=len(midpoints),
            median_salary_vnd_month=statistics.median(midpoints) if midpoints else None,
        ))
    return out


def _salary_pair(r: SilverRow) -> tuple[int | None, int | None]:
    return r.salary_min_vnd_month, r.salary_max_vnd_month


def _build_window(batch_id: str, window: str, rows: Sequence[SilverRow]) -> list[GoldMetricRow]:
    by_source: dict[str, _Bucket] = {}
    by_seniority: dict[str, _Bucket] = {}
    by_category: dict[str, _Bucket] = {}

    for r in rows:
        pair = _salary_pair(r)
        by_source.setdefault(r.source, {})[r.job_id] = pair
        by_seniority.setdefault(r.seniority_normalized or _UNKNOWN, {})[r.job_id] = pair
        if r.categories:
            for c in r.categories:
                by_category.setdefault(c.category_key, {})[r.job_id] = pair
        else:
            by_category.setdefault(f"{r.source}:{_UNKNOWN}", {})[r.job_id] = pair

    return (
        _aggregate(batch_id, window, "source", by_source)
        + _aggregate(batch_id, window, "seniority", by_seniority)
        + _aggregate(batch_id, window, "category", by_category)
    )


def build_gold(
    silver_rows: Iterable[SilverRow],
    as_of_date: date,
    batch_id: str,
) -> list[GoldMetricRow]:
    """Dựng mọi dòng gold cho một batch (cả hai window)."""
    rows = list(silver_rows)
    start_90d = as_of_date - timedelta(days=_WINDOW_90D_DAYS)
    rows_90d = [r for r in rows if start_90d <= r.effective_posted_date <= as_of_date]

    return (
        _build_window(batch_id, WINDOW_ALL_TIME, rows)
        + _build_window(batch_id, WINDOW_90D, rows_90d)
    )
