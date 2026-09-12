"""Test state machine + SQL transaction publish (app/elt/serving/publish.py). Thuần, không BQ."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.elt.serving.publish import (
    BatchMetadata,
    PublishAction,
    build_bootstrap_state_sql,
    build_publish_parameters,
    build_publish_transaction_sql,
    decide_publish_action,
)
from app.elt.serving.targets import WriterConfig, resolve_target

TARGET = resolve_target("prod", WriterConfig(project="p", dataset_prod="jobs_prod"))
META = BatchMetadata(
    batch_id="b2", data_as_of_at=datetime(2026, 9, 7, tzinfo=UTC), as_of_date=date(2026, 9, 7),
    source_jobs_total=14477, topdev_source_jobs=3422, vietnamworks_source_jobs=11055,
    silver_rows=14477, gold_rows=370, quarantined_rows=0,
    mapping_version="seniority-2026-09-v1", salary_fx_version="fx-2026-09-v1",
)


# --- state machine ---
@pytest.mark.parametrize(
    ("in_catalog", "published", "rows", "expected"),
    [
        (True, "b2", False, PublishAction.NO_OP),            # có & là current
        (True, "b1", False, PublishAction.ALREADY_PUBLISHED),  # có & khác current → bất biến
        (False, "b1", True, PublishAction.RELOAD_PARTIAL),   # chưa có nhưng có rows dở dang
        (False, "b1", False, PublishAction.NEW_BATCH),       # batch mới tinh
        (False, None, False, PublishAction.NEW_BATCH),       # lần đầu (pointer NULL)
    ],
)
def test_decide_publish_action(in_catalog, published, rows, expected):
    assert decide_publish_action("b2", published, in_catalog, rows) == expected


# --- transaction SQL ---
def test_transaction_is_atomic_with_cas_and_assert():
    sql = build_publish_transaction_sql(TARGET)
    assert "BEGIN TRANSACTION" in sql and "COMMIT TRANSACTION" in sql
    assert "@@row_count != 1" in sql and "RAISE" in sql          # assert affected==1 → rollback
    assert "CURRENT_TIMESTAMP()" in sql                          # published_at sinh trong transaction


def test_cas_is_null_safe_for_bootstrap():
    sql = build_publish_transaction_sql(TARGET)
    assert "published_batch_id = @expected_prev" in sql
    assert "published_batch_id IS NULL AND @expected_prev IS NULL" in sql


def test_sql_uses_backticked_identifiers_and_params_not_values():
    sql = build_publish_transaction_sql(TARGET)
    assert "`p.jobs_prod.warehouse_state`" in sql
    assert "`p.jobs_prod.warehouse_batches`" in sql
    assert "@batch_id" in sql                     # giá trị là param
    assert "b2" not in sql                         # KHÔNG nội suy giá trị vào SQL


def test_bootstrap_seed_is_conditional():
    sql = build_bootstrap_state_sql(TARGET)
    assert "NOT EXISTS" in sql and "`p.jobs_prod.warehouse_state`" in sql


# --- params ---
def test_publish_parameters_cover_all_columns():
    params = build_publish_parameters(META, expected_prev="b1")
    assert params["expected_prev"] == "b1"
    assert params["batch_id"] == "b2"
    assert params["silver_rows"] == 14477 and params["gold_rows"] == 370
    # đủ mọi cột catalog (trừ published_at sinh trong SQL)
    for key in ("data_as_of_at", "as_of_date", "source_jobs_total", "quarantined_rows",
                "mapping_version", "salary_fx_version", "warehouse_name"):
        assert key in params


def test_publish_parameters_expected_prev_none_first_publish():
    params = build_publish_parameters(META, expected_prev=None)
    assert params["expected_prev"] is None
