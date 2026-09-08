"""Mở DuckDB UI để xem dữ liệu trong analytics.duckdb trên trình duyệt.

Chạy:   python xem_duckdb_ui.py
Dừng:   Ctrl+C
Xem ở:  http://localhost:4213
"""
import duckdb
import time

con = duckdb.connect("analytics.duckdb")
con.sql("INSTALL ui; LOAD ui; CALL start_ui();")
print("Đã sẵn sàng: http://localhost:4213  (Ctrl+C để dừng)")

try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    print("\nĐang đóng...")
