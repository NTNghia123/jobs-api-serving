"""Unit test cưỡng chế query timeout (thread + interrupt) và bất biến timeout lồng nhau.

[FILE MỚI - TUẦN 7]  ·  Xem docs/adr/ADR-016
"""
from __future__ import annotations

import time

import duckdb
import pytest

from app.errors import QueryTimeoutError
from app.infrastructure.warehouse._duckdb_timeout import execute_with_timeout
from app.settings import Settings


def test_fast_query_tra_ve_desc_va_rows():
    con = duckdb.connect(":memory:")
    desc, rows = execute_with_timeout(con.cursor(), "SELECT 1 AS a, 2 AS b", {}, 5)
    assert [d[0] for d in desc] == ["a", "b"]
    assert rows == [(1, 2)]


def test_query_cham_bi_interrupt_va_nem_504():
    con = duckdb.connect(":memory:")
    # Truy vấn nặng (5 tỉ dòng) chắc chắn còn chạy khi hết hạn 0.3s → phải bị ngắt.
    t0 = time.time()
    with pytest.raises(QueryTimeoutError):
        execute_with_timeout(con.cursor(), "SELECT sum(i*i) FROM range(5000000000) t(i)", {}, 0.3)
    # Bị cắt sớm, không chạy tới cùng (nếu không interrupt sẽ mất nhiều giây).
    assert time.time() - t0 < 3.0


def test_query_timeout_error_la_504():
    assert QueryTimeoutError("x").status_code == 504
    assert QueryTimeoutError("x").code == "QUERY_TIMEOUT"


def test_validator_ep_query_nho_hon_request():
    ok = Settings(request_timeout_s=20, query_timeout_s=10)
    assert ok.query_timeout_s < ok.request_timeout_s
    with pytest.raises(ValueError):
        Settings(request_timeout_s=10, query_timeout_s=10)   # query >= request → không boot
    with pytest.raises(ValueError):
        Settings(request_timeout_s=5, query_timeout_s=8)
