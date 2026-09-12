"""CLI ELT: MongoDB job_crawler → BigQuery (silver/gold) + publish nguyên tử.

Chạy (VM/Cloud Shell, ADC của sa-dagster-elt):
  export JOBS_MONGO_URI=...            JOBS_MONGO_DATABASE=job_crawler
  export JOBS_BQ_PROJECT=...           JOBS_BQ_DATASET_STAGING=jobs_staging
  python -m app.elt.serving.run_serving_elt --environment staging
  # thử không đụng dataset thật:  --dataset-suffix _test   ·  xem trước:  --dry-run

Exit code: 0 OK/NO_OP · 2 BATCH_ALREADY_PUBLISHED · 3 quality fail · 1 lỗi khác.
Dagster (Phase 5) bọc bằng run_serving_elt; sequencing ≠ data-deps.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime

from app.elt.serving.dates import VN_TZ
from app.elt.serving.extract_mongo import ExtractedRecord, MongoConfig, extract_from_uri
from app.elt.serving.gold import GoldMetricRow, build_gold
from app.elt.serving.mapper import map_record
from app.elt.serving.publish import BatchMetadata, PublishAction
from app.elt.serving.salary import SALARY_FX_VERSION
from app.elt.serving.seniority import SENIORITY_MAPPING_VERSION
from app.elt.serving.silver import QuarantineRecord, SilverRow
from app.elt.serving.targets import WriterConfig, WriterGuardError, resolve_target

log = logging.getLogger("serving_elt")


class TransformOutput:
    def __init__(self, silver: list[SilverRow], quarantine: list[QuarantineRecord],
                 gold: list[GoldMetricRow]):
        self.silver = silver
        self.quarantine = quarantine
        self.gold = gold


def transform(records: Sequence[ExtractedRecord], batch_id: str, as_of_date: date) -> TransformOutput:
    """Thuần: (job, detail, list_cat)* → silver + quarantine + gold. Test được không cần Mongo."""
    silver: list[SilverRow] = []
    quarantine: list[QuarantineRecord] = []
    for job, detail, list_cat in records:
        r = map_record(job, detail, batch_id, list_category=list_cat)
        (silver if isinstance(r, SilverRow) else quarantine).append(r)
    gold = build_gold(silver, as_of_date, batch_id)
    return TransformOutput(silver, quarantine, gold)


def make_batch_metadata(
    batch_id: str,
    data_as_of_at: datetime,
    as_of_date: date,
    counts: dict[str, int],
    out: TransformOutput,
    *,
    crawl_batch_id: str | None = None,
    dagster_run_id: str | None = None,
) -> BatchMetadata:
    """Thuần: dựng dòng warehouse_batches (số liệu + lineage + version)."""
    return BatchMetadata(
        batch_id=batch_id,
        data_as_of_at=data_as_of_at,
        as_of_date=as_of_date,
        source_jobs_total=sum(counts.values()),
        topdev_source_jobs=counts.get("topdev", 0),
        vietnamworks_source_jobs=counts.get("vietnamworks", 0),
        silver_rows=len(out.silver),
        gold_rows=len(out.gold),
        quarantined_rows=len(out.quarantine),
        crawl_batch_id=crawl_batch_id,
        dagster_run_id=dagster_run_id,
        mapping_version=SENIORITY_MAPPING_VERSION,
        salary_fx_version=SALARY_FX_VERSION,
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ELT Mongo → BigQuery (serving).")
    p.add_argument("--environment", required=True, choices=["staging", "prod"],
                   help="Môi trường đích (prod PHẢI truyền tường minh — writer guard).")
    p.add_argument("--batch-id", default=None, help="Mặc định sinh từ data_as_of_at.")
    p.add_argument("--crawl-batch-id", default=None, help="Lineage: batch crawl nguồn.")
    p.add_argument("--dagster-run-id", default=None, help="Lineage: run Dagster.")
    p.add_argument("--dataset-suffix", default="", help="Hậu tố dataset (vd _test) cho chạy thử.")
    p.add_argument("--dry-run", action="store_true", help="Map + quality check, KHÔNG ghi BQ.")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _build_parser().parse_args(argv)

    # --- cấu hình + writer guard (trước khi chạm dữ liệu) ---
    try:
        wcfg = WriterConfig.from_env()
        mcfg = MongoConfig.from_env()
        target = resolve_target(args.environment, wcfg, dataset_suffix=args.dataset_suffix)
    except (WriterGuardError, ValueError) as e:
        log.error("Cấu hình sai: %s", e)
        return 1
    log.info("Target: %s", target.describe())

    data_as_of_at = datetime.now(UTC)                       # cutoff: sau crawl, trước transform
    as_of_date = data_as_of_at.astimezone(VN_TZ).date()     # neo window 90d (giờ VN)
    batch_id = args.batch_id or f"batch-{data_as_of_at:%Y%m%dT%H%M%SZ}"
    log.info("batch_id=%s data_as_of_at=%s as_of_date=%s", batch_id, data_as_of_at.isoformat(), as_of_date)

    # --- extract + transform ---
    extracted = extract_from_uri(mcfg)
    out = transform(extracted.records, batch_id, as_of_date)
    meta = make_batch_metadata(batch_id, data_as_of_at, as_of_date, extracted.counts, out,
                               crawl_batch_id=args.crawl_batch_id, dagster_run_id=args.dagster_run_id)
    log.info("run_metric: source=%s silver=%d gold=%d quarantine=%d",
             extracted.counts, meta.silver_rows, meta.gold_rows, meta.quarantined_rows)

    # --- lazy import BQ executor (chỉ khi thật sự ghi) ---
    from app.elt.serving.bigquery_writer import (
        BatchAlreadyPublishedError,
        BigQueryWarehouseWriter,
        QualityCheckError,
        publish_batch,
    )

    if args.dry_run:
        from app.elt.serving.checks import run_quality_checks
        report = run_quality_checks(out.silver, out.quarantine, out.gold, batch_id, extracted.counts)
        print(report.summary())
        return 0 if report.ok else 3

    try:
        writer = BigQueryWarehouseWriter(target)
        action, report = publish_batch(writer, out.silver, out.gold, out.quarantine, meta, extracted.counts)
    except BatchAlreadyPublishedError as e:
        log.error("%s", e)
        return 2
    except QualityCheckError as e:
        log.error("%s", e)
        return 3

    log.info("DONE action=%s batch_id=%s (crawl=%s dagster=%s)",
             action.value, batch_id, args.crawl_batch_id, args.dagster_run_id)
    if action is PublishAction.NO_OP:
        log.info("NO_OP — batch đã publish & đang là current, không làm gì.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
