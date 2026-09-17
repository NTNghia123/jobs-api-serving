"""LOAD-TEST — BigQuery dry-run trên toàn corpus payload để size cost guard.

Dry-run trả total_bytes_PROCESSED (ước tính), KHÔNG phải bytes_billed (chỉ có sau query thật).
Đề xuất: maximum_bytes_billed = max(total_bytes_processed) × (1 + margin). Chạy trên service perf
với cùng ADC/dataset như khi load test (KHÔNG tốn tiền — dry-run không chạy query).

    JOBS_API_BQ_PROJECT=... JOBS_API_BQ_DATASET=... \
    python -m tests.load.dryrun [--margin 0.3]
"""
from __future__ import annotations

import math

from google.cloud import bigquery

from app.infrastructure.warehouse.bigquery_exec import to_bq_params
from app.infrastructure.warehouse.bigquery_read_sql import (
    ReadTarget,
    build_current_batch_meta_sql,
    build_metrics_sql,
    build_search_sql,
)
from app.models.enums import MetricDimension, MetricWindow, SortOption
from app.models.jobs import SearchFilters, SearchRequest
from app.settings import get_settings
from tests.load.config import InvalidTestArgumentParser, invalid_test
from tests.load.corpus import MARKET_CORPUS, SEARCH_CORPUS


def guard_bytes(max_bytes: int, margin: float) -> int:
    """Tính guard theo plan; từ chối input khiến cap thấp/quá rộng hoặc không có estimate."""
    if not 0.2 <= margin <= 0.3:
        invalid_test("--margin phải nằm trong [0.2, 0.3] theo kế hoạch kiểm thử")
    if max_bytes <= 0:
        invalid_test("dry-run không trả total_bytes_processed dương; kiểm dataset/location/SQL")
    return math.ceil(max_bytes * (1 + margin))


def _dry_run(client: bigquery.Client, target: ReadTarget, sql: str, params) -> int:
    cfg = bigquery.QueryJobConfig(
        dry_run=True, use_query_cache=False,
        query_parameters=to_bq_params(params),
    )
    job = client.query(sql, job_config=cfg)
    return int(job.total_bytes_processed or 0)


def main() -> None:
    ap = InvalidTestArgumentParser()
    ap.add_argument("--margin", type=float, default=0.3, help="biên an toàn cho guard (0.3 = +30%)")
    args = ap.parse_args()

    s = get_settings()
    if not (s.bq_project and s.bq_dataset):
        invalid_test("cần JOBS_API_BQ_PROJECT và JOBS_API_BQ_DATASET.")
    target = ReadTarget(project=s.bq_project, dataset=s.bq_dataset, location=s.bq_location)
    client = bigquery.Client(project=target.project, location=target.location)

    max_bytes = 0
    worst = ""
    # metadata/current-batch cũng chạy ở search/market; phải nằm dưới guard như mọi query khác.
    batch_meta = build_current_batch_meta_sql(target)
    b = _dry_run(client, target, batch_meta.sql, batch_meta.params)
    if b > max_bytes:
        max_bytes, worst = b, "batch_meta"
    # search corpus
    for item in SEARCH_CORPUS:
        req = SearchRequest(
            filters=SearchFilters(**item["filters"]),
            sort=SortOption(item["sort"]),
            limit=20,
        )
        sp = build_search_sql(target, req, batch_id="dryrun")
        b = _dry_run(client, target, sp.sql, sp.params)
        if b > max_bytes:
            max_bytes, worst = b, f"search {item}"
    # metrics corpus (+ batch_meta chạy ngầm mỗi request thật, byte nhỏ — bỏ qua ở sizing)
    for item in MARKET_CORPUS:
        sp = build_metrics_sql(target, MetricDimension(item["dimension"]).value,
                               MetricWindow(item["window"]).value, batch_id="dryrun")
        b = _dry_run(client, target, sp.sql, sp.params)
        if b > max_bytes:
            max_bytes, worst = b, f"metrics {item}"

    guard = guard_bytes(max_bytes, args.margin)
    gib = 1024 ** 3
    print(f"max total_bytes_processed = {max_bytes:,} B ({max_bytes/gib:.3f} GiB)  [{worst}]")
    print(f"đề xuất JOBS_API_BQ_MAXIMUM_BYTES_BILLED = {guard:,} B "
          f"({guard/gib:.3f} GiB)  (margin {args.margin:.0%})")


if __name__ == "__main__":
    main()
