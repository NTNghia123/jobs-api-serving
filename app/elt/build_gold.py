"""Job 'nướng' gold table Tuần 5: tổng hợp silver_jobs → gold_market_metrics.

Chạy:  python -m app.elt.build_gold      (LUÔN chạy SAU build_silver)
Xem:   docs/adr/ADR-010 (định nghĩa "lương đại diện")

Ý tưởng: ĐẨY VIỆC NẶNG RA KHỎI ĐƯỜNG PHỤC VỤ. Thay vì tổng hợp mỗi request, tính sẵn
median lương + số tin theo từng chiều rồi ghi vào một bảng nhỏ. Endpoint /market/metrics
chỉ đọc kết quả đã nướng → cực nhanh.

Bảng gold dạng LONG (một bảng cho MỌI chiều) để endpoint chỉ cần WHERE dimension = ?:
    gold_market_metrics(dimension, dimension_value, median_salary, posting_count, as_of)
"""
from __future__ import annotations

import duckdb

from app.settings import get_settings

# GIẢI THÍCH 'lương đại diện' = trung điểm (min+max)/2 của mỗi tin (ADR-010), rồi lấy
# MEDIAN của các trung điểm theo nhóm (median chịu ngoại lệ tốt hơn mean).
#
# GIẢI THÍCH bẫy timezone: now() của DuckDB trả TIMESTAMP có timezone → khi đọc sang
# Python đòi thư viện pytz. Tránh bằng CAST(now() AS TIMESTAMP) (naive); repository sẽ
# gắn UTC lúc đọc. Nhờ vậy không phải thêm dependency.
GOLD_SQL = """
CREATE OR REPLACE TABLE gold_market_metrics AS
WITH rep AS (
    SELECT seniority, country,
           (salary_min + salary_max) / 2.0 AS rep_salary      -- lương đại diện mỗi tin
    FROM silver_jobs
    WHERE salary_min IS NOT NULL AND salary_max IS NOT NULL
),
agg AS (
    -- chiều 1: theo cấp bậc
    SELECT 'seniority' AS dimension,
           CAST(seniority AS VARCHAR) AS dimension_value,
           median(rep_salary)         AS median_salary,
           COUNT(*)                   AS posting_count
    FROM rep GROUP BY seniority
    UNION ALL
    -- chiều 2: theo quốc gia công ty
    SELECT 'country' AS dimension,
           CAST(country AS VARCHAR) AS dimension_value,
           median(rep_salary)       AS median_salary,
           COUNT(*)                 AS posting_count
    FROM rep GROUP BY country
)
SELECT dimension, dimension_value, median_salary, posting_count,
       CAST(now() AS TIMESTAMP) AS as_of        -- độ tươi = lúc nướng gold (naive; repo gắn UTC)
FROM agg
"""


def build_gold(path: str | None = None) -> int:
    """Nướng gold table từ silver_jobs. Trả về số dòng gold. Idempotent (CREATE OR REPLACE).

    'path' cho phép truyền file DuckDB khác khi test; mặc định lấy từ settings.
    """
    path = path or get_settings().duckdb_path
    con = duckdb.connect(path)          # mở GHI (silver_jobs phải đã tồn tại)
    try:
        con.execute(GOLD_SQL)
        n = con.execute("SELECT COUNT(*) FROM gold_market_metrics").fetchone()[0]
    finally:
        con.close()
    return int(n)


if __name__ == "__main__":
    rows = build_gold()
    print(f"gold_market_metrics: {rows} dòng")
    # LƯU Ý: k-anonymity KHÔNG áp ở đây — gold giữ SỐ THẬT (kể cả nhóm nhỏ). Việc che
    # nhóm < MIN_GROUP_SIZE nằm ở tầng API (app/api/market.py, Tuần 5 bước 3).
