"""BQ executor — I/O glue: ensure tables, load candidate, APPEND, publish transaction.

Ráp các mảnh thuần (schema, targets, publish, checks, gold) thành luồng ghi thật lên
BigQuery. Đây là tầng DUY NHẤT chạm google-cloud-bigquery trong ELT serving.

Luồng `publish_batch` (plan §Phase 2):
  ensure_tables → bootstrap_state → đọc state/catalog → decide_publish_action
  → NO_OP/ALREADY_PUBLISHED thì dừng; RELOAD_PARTIAL thì xoá batch cũ
  → quality gate (in-memory) → load candidate (WRITE_TRUNCATE) → APPEND published
  → publish transaction (CAS + INSERT catalog).

Append silver/gold NGOÀI transaction (OK — plan); chỉ catalog+pointer trong transaction.
Không unit-test (cần BQ) → integration `RUN_BQ_INTEGRATION=1` + dataset `_test` ở Phase 4.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from google.cloud import bigquery

from app.elt.serving.checks import QualityReport, run_quality_checks
from app.elt.serving.gold import GoldMetricRow
from app.elt.serving.publish import (
    BatchMetadata,
    PublishAction,
    build_bootstrap_state_sql,
    build_publish_parameters,
    build_publish_transaction_sql,
    decide_publish_action,
)
from app.elt.serving.schema import (
    GOLD_CLUSTER,
    GOLD_FIELDS,
    QUARANTINE_FIELDS,
    SILVER_CLUSTER,
    SILVER_FIELDS,
    SILVER_PARTITION_FIELD,
    SILVER_REQUIRE_PARTITION_FILTER,
    TABLE_BATCHES,
    TABLE_GOLD,
    TABLE_GOLD_CANDIDATE,
    TABLE_QUARANTINE,
    TABLE_SILVER,
    TABLE_SILVER_CANDIDATE,
    TABLE_STATE,
    WAREHOUSE_BATCHES_FIELDS,
    WAREHOUSE_STATE_FIELDS,
    WAREHOUSE_STATE_NAME,
    Field,
)
from app.elt.serving.silver import QuarantineRecord, SilverRow
from app.elt.serving.targets import Target

log = logging.getLogger(__name__)

# floor partition để thoả require_partition_filter khi DELETE/COUNT theo batch_id.
_PARTITION_FLOOR = "2000-01-01"

# BQ type chuẩn (legacy names mà SchemaField nhận chắc chắn).
_TYPE_MAP = {"INT64": "INTEGER", "FLOAT64": "FLOAT"}
# type cho scalar query param của publish transaction.
_PUBLISH_PARAM_TYPES = {
    "warehouse_name": "STRING", "expected_prev": "STRING", "batch_id": "STRING",
    "crawl_batch_id": "STRING", "dagster_run_id": "STRING", "data_as_of_at": "TIMESTAMP",
    "as_of_date": "DATE", "source_jobs_total": "INT64", "topdev_source_jobs": "INT64",
    "vietnamworks_source_jobs": "INT64", "silver_rows": "INT64", "gold_rows": "INT64",
    "quarantined_rows": "INT64", "mapping_version": "STRING", "salary_fx_version": "STRING",
}


class BatchAlreadyPublishedError(RuntimeError):
    """Batch đã có trong catalog nhưng khác current pointer — bất biến, không sửa."""


class QualityCheckError(RuntimeError):
    def __init__(self, report: QualityReport):
        super().__init__("Quality checks FAIL — không publish:\n" + report.summary())
        self.report = report


def _schema_field(f: Field) -> bigquery.SchemaField:
    bq_type = _TYPE_MAP.get(f.type, f.type)
    return bigquery.SchemaField(
        f.name, bq_type, mode=f.mode,
        fields=tuple(_schema_field(x) for x in f.fields),
    )


def to_bq_schema(fields: tuple[Field, ...]) -> list[bigquery.SchemaField]:
    return [_schema_field(f) for f in fields]


class BigQueryWarehouseWriter:
    """Ghi warehouse cho một Target (một môi trường đã phân giải)."""

    def __init__(self, target: Target, client: bigquery.Client | None = None):
        self.target = target
        self.client = client or bigquery.Client(project=target.project, location=target.location)

    # ---- helpers ----
    def _query(self, sql: str, params: list | None = None) -> bigquery.table.RowIterator:
        cfg = bigquery.QueryJobConfig(
            maximum_bytes_billed=self.target.maximum_bytes_billed,
            query_parameters=params or [],
        )
        return self.client.query(sql, job_config=cfg).result()

    def _scalar_params(self, values: dict[str, object | None]) -> list[bigquery.ScalarQueryParameter]:
        return [
            bigquery.ScalarQueryParameter(name, _PUBLISH_PARAM_TYPES[name], value)
            for name, value in values.items()
        ]

    # ---- DDL ----
    def ensure_tables(self) -> None:
        """Tạo mọi bảng nếu chưa có (idempotent qua exists_ok). Partition/cluster cho published."""
        t = self.target

        silver = bigquery.Table(t.table_ref(TABLE_SILVER), schema=to_bq_schema(SILVER_FIELDS))
        silver.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY, field=SILVER_PARTITION_FIELD,
        )
        silver.require_partition_filter = SILVER_REQUIRE_PARTITION_FILTER
        silver.clustering_fields = list(SILVER_CLUSTER)

        gold = bigquery.Table(t.table_ref(TABLE_GOLD), schema=to_bq_schema(GOLD_FIELDS))
        gold.clustering_fields = list(GOLD_CLUSTER)

        plain = [
            bigquery.Table(t.table_ref(TABLE_SILVER_CANDIDATE), schema=to_bq_schema(SILVER_FIELDS)),
            bigquery.Table(t.table_ref(TABLE_GOLD_CANDIDATE), schema=to_bq_schema(GOLD_FIELDS)),
            bigquery.Table(t.table_ref(TABLE_STATE), schema=to_bq_schema(WAREHOUSE_STATE_FIELDS)),
            bigquery.Table(t.table_ref(TABLE_BATCHES), schema=to_bq_schema(WAREHOUSE_BATCHES_FIELDS)),
            bigquery.Table(t.table_ref(TABLE_QUARANTINE), schema=to_bq_schema(QUARANTINE_FIELDS)),
        ]
        for table in [silver, gold, *plain]:
            self.client.create_table(table, exists_ok=True)
        log.info("ensure_tables OK: %s", t.describe())

    def bootstrap_state(self) -> None:
        self._query(
            build_bootstrap_state_sql(self.target),
            [bigquery.ScalarQueryParameter("warehouse_name", "STRING", WAREHOUSE_STATE_NAME)],
        )

    # ---- state reads (cho state machine) ----
    def read_published_batch_id(self) -> str | None:
        sql = (
            f"SELECT published_batch_id FROM {self.target.table_id(TABLE_STATE)} "
            "WHERE warehouse_name = @n LIMIT 1"
        )
        rows = list(self._query(sql, [bigquery.ScalarQueryParameter("n", "STRING", WAREHOUSE_STATE_NAME)]))
        return rows[0]["published_batch_id"] if rows else None

    def batch_in_catalog(self, batch_id: str) -> bool:
        sql = f"SELECT 1 FROM {self.target.table_id(TABLE_BATCHES)} WHERE batch_id = @b LIMIT 1"
        rows = list(self._query(sql, [bigquery.ScalarQueryParameter("b", "STRING", batch_id)]))
        return bool(rows)

    def data_rows_exist(self, batch_id: str) -> bool:
        # published silver yêu cầu partition filter → thêm floor effective_posted_date.
        sql = (
            f"SELECT 1 FROM {self.target.table_id(TABLE_SILVER)} "
            f"WHERE effective_posted_date >= DATE '{_PARTITION_FLOOR}' AND batch_id = @b LIMIT 1"
        )
        rows = list(self._query(sql, [bigquery.ScalarQueryParameter("b", "STRING", batch_id)]))
        return bool(rows)

    # ---- mutations ----
    def delete_batch_rows(self, batch_id: str) -> None:
        b = bigquery.ScalarQueryParameter("b", "STRING", batch_id)
        self._query(
            f"DELETE FROM {self.target.table_id(TABLE_SILVER)} "
            f"WHERE effective_posted_date >= DATE '{_PARTITION_FLOOR}' AND batch_id = @b", [b],
        )
        self._query(f"DELETE FROM {self.target.table_id(TABLE_GOLD)} WHERE batch_id = @b", [b])
        self._query(f"DELETE FROM {self.target.table_id(TABLE_QUARANTINE)} WHERE batch_id = @b", [b])

    def load_candidate(self, table: str, fields: tuple[Field, ...], rows: list[dict]) -> None:
        cfg = bigquery.LoadJobConfig(
            schema=to_bq_schema(fields),
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        self.client.load_table_from_json(
            rows, self.target.table_ref(table), job_config=cfg,
        ).result()
        log.info("load candidate %s: %d rows (WRITE_TRUNCATE)", table, len(rows))

    def append_candidate_to_published(self, candidate: str, published: str) -> None:
        self._query(
            f"INSERT INTO {self.target.table_id(published)} "
            f"SELECT * FROM {self.target.table_id(candidate)}"
        )

    def append_quarantine(self, rows: list[dict]) -> None:
        if not rows:
            return
        cfg = bigquery.LoadJobConfig(
            schema=to_bq_schema(QUARANTINE_FIELDS),
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        self.client.load_table_from_json(
            rows, self.target.table_ref(TABLE_QUARANTINE), job_config=cfg,
        ).result()

    def run_publish_transaction(self, meta: BatchMetadata, expected_prev: str | None) -> None:
        values = build_publish_parameters(meta, expected_prev)
        self._query(build_publish_transaction_sql(self.target), self._scalar_params(values))
        log.info("published batch_id=%s (prev=%s)", meta.batch_id, expected_prev)


def publish_batch(
    writer: BigQueryWarehouseWriter,
    silver: Sequence[SilverRow],
    gold: Sequence[GoldMetricRow],
    quarantine: Sequence[QuarantineRecord],
    meta: BatchMetadata,
    extract_counts: dict[str, int],
) -> tuple[PublishAction, QualityReport | None]:
    """Ráp toàn bộ luồng publish nguyên tử. Trả (action, report)."""
    writer.ensure_tables()
    writer.bootstrap_state()

    published = writer.read_published_batch_id()
    action = decide_publish_action(
        meta.batch_id, published,
        batch_in_catalog=writer.batch_in_catalog(meta.batch_id),
        data_rows_exist=writer.data_rows_exist(meta.batch_id),
    )
    log.info("publish action=%s (batch=%s, current=%s)", action.value, meta.batch_id, published)

    if action is PublishAction.NO_OP:
        return action, None
    if action is PublishAction.ALREADY_PUBLISHED:
        raise BatchAlreadyPublishedError(
            f"batch_id={meta.batch_id} đã publish (current={published}) — bất biến, không sửa."
        )

    # cổng chất lượng TRƯỚC khi chạm bảng published (fail → pointer giữ nguyên).
    report = run_quality_checks(silver, quarantine, gold, meta.batch_id, extract_counts)
    if not report.ok:
        raise QualityCheckError(report)

    if action is PublishAction.RELOAD_PARTIAL:
        writer.delete_batch_rows(meta.batch_id)

    writer.load_candidate(TABLE_SILVER_CANDIDATE, SILVER_FIELDS, [r.to_bq_row() for r in silver])
    writer.load_candidate(TABLE_GOLD_CANDIDATE, GOLD_FIELDS, [g.to_bq_row() for g in gold])
    writer.append_candidate_to_published(TABLE_SILVER_CANDIDATE, TABLE_SILVER)
    writer.append_candidate_to_published(TABLE_GOLD_CANDIDATE, TABLE_GOLD)
    writer.append_quarantine([q.to_bq_row() for q in quarantine])

    writer.run_publish_transaction(meta, expected_prev=published)
    return action, report
