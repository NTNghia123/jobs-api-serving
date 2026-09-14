"""Publish nguyên tử: state machine + SQL transaction (INSERT catalog + CAS pointer).

Invariant 2 (plan): batch có mặt trong `warehouse_batches` = ĐÃ PUBLISH, BẤT BIẾN.
Retry là state machine theo điều đó, KHÔNG phải theo rows silver/gold:
  - có & == current pointer        → NO_OP (đã publish & đang phục vụ)
  - có & != current                → ALREADY_PUBLISHED (bất biến; chỉ rollback/promote
                                      tường minh mới đổi pointer — KHÔNG sửa gì ở đây)
  - chưa có & silver/gold có rows   → RELOAD_PARTIAL (lần chạy trước dở dang → xoá batch
                                      đó rồi load lại)
  - chưa có & không rows            → NEW_BATCH

Publish = DELETE+INSERT dữ liệu (silver/gold/quarantine từ candidate) + `warehouse_batches` INSERT
+ `warehouse_state` CAS, TẤT CẢ trong CÙNG một transaction (sửa 2026-09-13 — trước đây append đứng
NGOÀI transaction, nên hai run cùng batch_id có thể để lại rows trùng của run thua CAS):
  DELETE theo batch_id (idempotent: dọn partial/dup của chính batch — gộp luôn RELOAD_PARTIAL) →
  INSERT từ candidate → CAS `WHERE published_batch_id = @expected_prev` (NULL-safe lần đầu) →
  assert đúng 1 dòng NGAY SAU UPDATE (≠1 → RAISE → rollback TẤT CẢ, kể cả INSERT dữ liệu) →
  INSERT catalog. `published_at` sinh trong transaction. Run thua CAS hoặc bị abort do concurrent
  mutation → rollback sạch, KHÔNG để lại rows trùng. Caller đọc lại state, áp lại state machine.

Thuần, không I/O. Loader cấp client + typed params + tên candidate table (per-batch).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from app.elt.serving.schema import (
    TABLE_BATCHES,
    TABLE_GOLD,
    TABLE_QUARANTINE,
    TABLE_SILVER,
    TABLE_STATE,
    WAREHOUSE_STATE_NAME,
)
from app.elt.serving.targets import Target

# floor partition để DELETE silver thoả require_partition_filter. Dùng MIN DATE của BigQuery
# (0001-01-01) → phủ MỌI effective_posted_date, kể cả ngày < 2000 (bad data parse được) → DELETE
# không bỏ sót row nào (idempotency reload). Khớp bigquery_writer._PARTITION_FLOOR.
_PARTITION_FLOOR = "0001-01-01"


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
        f"FROM (SELECT 1)\n"
        f"WHERE NOT EXISTS (SELECT 1 FROM {state} WHERE warehouse_name = @warehouse_name);"
    )


def build_publish_transaction_sql(
    target: Target,
    silver_candidate: str,
    gold_candidate: str,
    quarantine_candidate: str,
) -> str:
    """SQL multi-statement: DELETE+INSERT dữ liệu + CAS pointer + assert + INSERT catalog, trong
    1 transaction → ghi dữ liệu và chốt sổ CÙNG SỐNG-CHẾT (run thua CAS rollback cả INSERT dữ liệu).

    Identifier bảng: fully-qualified + backtick (published từ allowlist; candidate = tên đã sanitize
    [A-Za-z0-9_] do writer sinh). Mọi GIÁ TRỊ là @param (không nội suy). CAS NULL-safe lần đầu.
    """
    state = target.table_id(TABLE_STATE)
    batches = target.table_id(TABLE_BATCHES)
    silver, gold, quar = (target.table_id(t) for t in (TABLE_SILVER, TABLE_GOLD, TABLE_QUARANTINE))
    sil_c, gold_c, quar_c = (
        target.table_id(c) for c in (silver_candidate, gold_candidate, quarantine_candidate)
    )
    cols = ", ".join(_BATCH_COLUMNS)
    values = (
        "@batch_id, @crawl_batch_id, @dagster_run_id, @data_as_of_at, @as_of_date, "
        "CURRENT_TIMESTAMP(), @source_jobs_total, @topdev_source_jobs, @vietnamworks_source_jobs, "
        "@silver_rows, @gold_rows, @quarantined_rows, @mapping_version, @salary_fx_version"
    )
    bootstrap = build_bootstrap_state_sql(target)   # INSERT singleton WHERE NOT EXISTS (idempotent)
    return f"""
BEGIN TRANSACTION;

-- Seed singleton pointer NẾU CHƯA CÓ, TRONG transaction: hai first-publish đồng thời trên dataset
-- rỗng sẽ serialize (một run abort do concurrent mutation warehouse_state) thay vì cùng chèn 2 dòng
-- ngoài txn (2 dòng ⇒ CAS sau này @@row_count=2 ⇒ hỏng vĩnh viễn). Xem ADR-025.
{bootstrap}

-- Dọn rows CỦA CHÍNH batch này (idempotent + gộp RELOAD_PARTIAL); chỉ batch_id → không đụng batch khác.
-- silver require_partition_filter → thêm floor effective_posted_date.
DELETE FROM {silver} WHERE effective_posted_date >= DATE '{_PARTITION_FLOOR}' AND batch_id = @batch_id;
DELETE FROM {gold} WHERE batch_id = @batch_id;
DELETE FROM {quar} WHERE batch_id = @batch_id;

-- Ghi dữ liệu TỪ candidate — nằm TRONG transaction: run thua CAS sẽ rollback cả các INSERT này.
INSERT INTO {silver} SELECT * FROM {sil_c};
INSERT INTO {gold} SELECT * FROM {gold_c};
INSERT INTO {quar} SELECT * FROM {quar_c};

-- CAS: chỉ flip pointer nếu NÓ VẪN đúng giá trị đã đọc (chống publish đồng thời).
UPDATE {state}
SET published_batch_id = @batch_id, updated_at = CURRENT_TIMESTAMP()
WHERE warehouse_name = @warehouse_name
  AND ((published_batch_id = @expected_prev)
       OR (published_batch_id IS NULL AND @expected_prev IS NULL));

-- assert NGAY SAU UPDATE (@@row_count của UPDATE): sai → RAISE → ROLLBACK toàn bộ transaction.
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
