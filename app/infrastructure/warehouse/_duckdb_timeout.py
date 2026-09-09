"""Cưỡng chế query timeout cho DuckDB bằng thread + interrupt (Tuần 7).

[FILE MỚI]  Xem docs/adr/ADR-016 (thay thế ADR-005).

DuckDB không có statement_timeout gắn sẵn. Cách thật để cắt một truy vấn treo:
chạy execute + fetch trong MỘT thread; nếu quá hạn thì gọi .interrupt() trên chính
cursor đó để NGẮT truy vấn đang chạy, rồi ném QueryTimeoutError (504).

Vì sao dùng cursor riêng (con.cursor()) cho mỗi request: interrupt() tác động lên
đúng connection đó, không đụng request khác chạy song song.
"""
from __future__ import annotations

import threading

from app.errors import QueryTimeoutError


def execute_with_timeout(cur, sql: str, params, timeout_s: int):
    """Chạy cur.execute(sql, params) + fetchall trong giới hạn timeout_s giây.

    Trả về (description, rows). Quá hạn -> interrupt cursor + ném QueryTimeoutError.
    """
    box: dict = {}

    def _work() -> None:
        try:
            cur.execute(sql, params)
            box["desc"] = cur.description
            box["rows"] = cur.fetchall()          # fetch TRONG thread để query nặng cũng bị cắt
        except BaseException as exc:              # gồm cả InterruptException khi bị ngắt
            box["err"] = exc

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout_s)

    if t.is_alive():
        cur.interrupt()          # yêu cầu DuckDB dừng truy vấn đang chạy
        t.join()                 # chờ thread thoát hẳn sau khi bị ngắt
        raise QueryTimeoutError("Truy vấn kho quá hạn")

    if "err" in box:
        raise box["err"]         # lỗi thật của truy vấn (không phải timeout) -> ném nguyên trạng
    return box["desc"], box["rows"]
