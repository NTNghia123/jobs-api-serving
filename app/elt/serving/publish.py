"""Publish nguyên tử: state machine + SQL transaction (INSERT catalog + CAS pointer).

Invariant 2 (plan): batch có mặt trong `warehouse_batches` = ĐÃ PUBLISH, BẤT BIẾN.
Retry là state machine theo điều đó, KHÔNG phải theo rows silver/gold:
  - có & == current pointer        → NO_OP (đã publish & đang phục vụ)
  - có & != current                → ALREADY_PUBLISHED (bất biến; chỉ rollback/promote
                                      tường minh mới đổi pointer — KHÔNG sửa gì ở đây)
  - chưa có & silver/gold có rows   → RELOAD_PARTIAL (lần chạy trước dở dang → xoá batch
                                      đó rồi load lại)
  - chưa có & không rows            → NEW_BATCH

Publish = `warehouse_batches` INSERT + `warehouse_state` CAS trong CÙNG transaction:
  CAS `WHERE published_batch_id = @expected_prev` (NULL-safe cho lần đầu); assert đúng 1 dòng
  bị sửa TRƯỚC COMMIT (≠1 → RAISE → transaction rollback). `published_at` sinh trong transaction.
  Abort do concurrent mutation → caller ĐỌC LẠI state, áp lại state machine (không retry mù).

Thuần, không I/O. Loader (2.6d) cấp client + typed params.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from app.elt.serving.schema import (
    TABLE_BATCHES,
    TABLE_STATE,
    WAREHOUSE_STATE_NAME,
)
from app.elt.serving.targets import Target


class PublishAction(str, Enum):
    NO_OP = "no_op"
    ALREADY_PUBLISHED = "already_published"  # → CLI exit non-zero có kiểm soát
    RELOAD_PARTIAL = "reload_partial"
    NEW_BATCH = "new_batch"


def decide_publish_action(
    batch_id: str,
    published_batch_id: str | None,
    batch_in_catalog: bool,
    data_rows_exist: bool,
) -> PublishAction:
    """State machine idempotency (xem docstring module). Thuần, tất định."""
    if batch_in_catalog:
        return PublishAction.NO_OP if batch_id == published_batch_id else PublishAction.ALREADY_PUBLISHED
    return PublishAction.RELOAD_PARTIAL if data_rows_exist else PublishAction.NEW_BATCH


@dataclass(frozen=True)
class BatchMetadata:
    """Giá trị 1 dòng `warehouse_batches` (trừ published_at — sinh trong transaction)."""

    batch_id: str
    data_as_of_at: datetime
    as_of_date: date
    source_jobs_total: int
    topdev_source_jobs: int
    vietnamworks_source_jobs: int
    silver_rows: int
    gold_rows: int
    quarantined_rows: int
    crawl_batch_id: str | None = None
    dagster_run_id: str | None = None
    mapping_version: str | None = None
    salary_fx_version: str | None = None


# thứ tự cột INSERT warehouse_batches (published_at = CURRENT_TIMESTAMP() ở giữa)
_BATCH_COLUMNS = (
    "batch_id", "crawl_batch_id", "dagster_run_id", "data_as_of_at", "as_of_date",
    "published_at", "source_jobs_total", "topdev_source_jobs", "vietnamworks_source_jobs",
    "silver_rows", "gold_rows", "quarantined_rows", "mapping_version", "salary_fx_version",
)


def build_bootstrap_state_sql(target: Target) -> str:
    """Seed 1 dòng pointer `published_batch_id=NULL` nếu chưa có (idempotent). CAS cần dòng này."""
    state = target.table_id(TABLE_STATE)
    return (
        f"INSERT INTO {state} (warehouse_name, published_batch_id, updated_at)\n"
        f"SELECT @warehouse_name, NULL, CURRENT_TIMESTAMP()\n"
        f"WHERE NOT EXISTS (SELECT 1 FROM {state} WHERE warehouse_name = @warehouse_name);"
    )


def build_publish_transaction_sql(target: Target) -> str:
    """SQL multi-statement: CAS pointer + assert + INSERT catalog, trong 1 transaction.

    Identifier bảng: fully-qualified + backtick (an toàn, từ allowlist). Mọi GIÁ TRỊ là @param
    (không nội suy giá trị vào SQL). CAS NULL-safe cho expected_prev lần đầu (bootstrap NULL).
    """
    state = target.table_id(TABLE_STATE)
    batches = target.table_id(TABLE_BATCHES)
    cols = ", ".join(_BATCH_COLUMNS)
    values = (
        "@batch_id, @crawl_batch_id, @dagster_run_id, @data_as_of_at, @as_of_date, "
        "CURRENT_TIMESTAMP(), @source_jobs_total, @topdev_source_jobs, @vietnamworks_source_jobs, "
        "@silver_rows, @gold_rows, @quarantined_rows, @mapping_version, @salary_fx_version"
    )
    return f"""
BEGIN TRANSACTION;

-- CAS: chỉ flip pointer nếu NÓ VẪN đúng giá trị đã đọc (chống publish đồng thời).
UPDATE {state}
SET published_batch_id = @batch_id, updated_at = CURRENT_TIMESTAMP()
WHERE warehouse_name = @warehouse_name
  AND ((published_batch_id = @expected_prev)
       OR (published_batch_id IS NULL AND @expected_prev IS NULL));

-- assert đúng 1 dòng: sai → RAISE → transaction ROLLBACK (không commit gì).
IF @@row_count != 1 THEN
  RAISE USING MESSAGE =
    'BATCH_PUBLISH_CAS_FAILED: warehouse_state đổi do publish đồng thời, hoặc thiếu dòng bootstrap';
END IF;

-- catalog bất biến; published_at sinh TRONG transaction.
INSERT INTO {batches} ({cols})
VALUES ({values});

COMMIT TRANSACTION;
""".strip()


def build_publish_parameters(
    meta: BatchMetadata,
    expected_prev: str | None,
    warehouse_name: str = WAREHOUSE_STATE_NAME,
) -> dict[str, object | None]:
    """name → value cho query params (loader map sang typed ScalarQueryParameter)."""
    return {
        "warehouse_name": warehouse_name,
        "expected_prev": expected_prev,
        "batch_id": meta.batch_id,
        "crawl_batch_id": meta.crawl_batch_id,
        "dagster_run_id": meta.dagster_run_id,
        "data_as_of_at": meta.data_as_of_at,
        "as_of_date": meta.as_of_date,
        "source_jobs_total": meta.source_jobs_total,
        "topdev_source_jobs": meta.topdev_source_jobs,
        "vietnamworks_source_jobs": meta.vietnamworks_source_jobs,
        "silver_rows": meta.silver_rows,
        "gold_rows": meta.gold_rows,
        "quarantined_rows": meta.quarantined_rows,
        "mapping_version": meta.mapping_version,
        "salary_fx_version": meta.salary_fx_version,
    }
