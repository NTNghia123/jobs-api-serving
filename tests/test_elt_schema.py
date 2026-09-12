"""Drift-test: schema BigQuery phải KHỚP to_bq_row() của dataclass.

Nếu ai thêm/bớt field ở SilverRow/GoldMetricRow/QuarantineRecord mà quên sửa schema.py
(hoặc ngược lại), test này đỏ — tránh load lên BQ bị lệch cột.
"""
from __future__ import annotations

from app.elt.serving import schema
from app.elt.serving.gold import GoldMetricRow
from app.elt.serving.mapper import map_record
from app.elt.serving.silver import SilverRow
from tests.fixtures import mongo_like as fx


def _silver_row() -> SilverRow:
    row = map_record(*fx.TOPDEV_FULL, "b1")
    assert isinstance(row, SilverRow)
    return row


def test_silver_schema_matches_to_bq_row():
    row = _silver_row()
    assert schema.field_names(schema.SILVER_FIELDS) == list(row.to_bq_row().keys())


def test_silver_category_subfields_match():
    row = _silver_row()
    assert row.categories, "fixture phải có category để test subfield"
    cat_field = next(f for f in schema.SILVER_FIELDS if f.name == "categories")
    assert [f.name for f in cat_field.fields] == list(row.categories[0].to_bq_row().keys())
    assert cat_field.mode == "REPEATED" and cat_field.type == "RECORD"


def test_gold_schema_matches_to_bq_row():
    g = GoldMetricRow("b1", "90d", "source", "topdev", 3, 2, 1, 40_000_000.0)
    assert schema.field_names(schema.GOLD_FIELDS) == list(g.to_bq_row().keys())


def test_quarantine_schema_matches_to_bq_row():
    rec = map_record(*fx.MISSING_DETAIL, "b1")
    assert schema.field_names(schema.QUARANTINE_FIELDS) == list(rec.to_bq_row().keys())


def test_required_key_fields_present():
    # những cột khoá/partition phải tồn tại đúng tên trong schema
    silver = schema.field_names(schema.SILVER_FIELDS)
    assert schema.SILVER_PARTITION_FIELD in silver
    assert all(c in silver for c in schema.SILVER_CLUSTER)
    batches = schema.field_names(schema.WAREHOUSE_BATCHES_FIELDS)
    assert {"batch_id", "data_as_of_at", "as_of_date", "published_at"} <= set(batches)
    state = schema.field_names(schema.WAREHOUSE_STATE_FIELDS)
    assert {"warehouse_name", "published_batch_id"} <= set(state)
