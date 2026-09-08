"""Job ELT Tuần 3: bóc job_post × company từ MySQL, làm sạch, ghi silver_jobs vào DuckDB.

[FILE MỚI]  Đích thật: app/elt/build_silver.py
Chạy:       python -m app.elt.build_silver
Hướng dẫn:  ../W3-Huong-Dan-Thuc-Hien.md §2   ·   Quyết định: docs/adr/ADR-004

Ba tính chất bắt buộc của job này:
  1. CHỈ bóc cột an toàn — PII (email/password/contactno/dob/address) ở lại MySQL. (phòng thủ lớp 1)
  2. Idempotent — chạy lại bao nhiêu lần cũng ra cùng số dòng (dùng CREATE OR REPLACE).
  3. Chỉ đọc nguồn — kết nối bằng user MySQL 'reader' (GRANT SELECT), không sửa được nguồn.
"""
from __future__ import annotations

import duckdb
import pandas as pd
from sqlalchemy import create_engine, text

from app.settings import get_settings

# ---------------------------------------------------------------------------
# EXTRACT — câu SELECT chạy TRÊN MYSQL.
#
# GIẢI THÍCH — vì sao JOIN:
#   Địa điểm chỉ tồn tại ở bảng company (country/state/city), KHÔNG có ở job_post.
#   Muốn có 'country' cho mỗi tin phải JOIN job_post × company.
#
# GIẢI THÍCH — vì sao liệt kê cột thay vì SELECT *:
#   Đây là allowlist ở TẦNG DỮ LIỆU — lớp phòng thủ PII số một. Cột nhạy cảm
#   (c.email, c.contactno, users.*) không nằm trong danh sách nên KHÔNG BAO GIỜ
#   rời khỏi MySQL. Allowlist ở tầng API (Tuần 4) chỉ là lớp phòng thủ thứ hai.
#
# GIẢI THÍCH — vì sao CAST(... AS UNSIGNED):
#   minimumsalary/maximumsalary khai varchar nhưng dữ liệu là số nguyên sạch;
#   experience là chuỗi '1'..'5'. CAST ngay trên MySQL để bảng silver hết kiểu chuỗi.
#   LƯU Ý: 'UNSIGNED' là cú pháp của MySQL — chỉ dùng ở ĐÂY, đừng mang sang DuckDB.
# ---------------------------------------------------------------------------
EXTRACT_SQL = """
    SELECT
        j.id_jobpost                       AS job_id,
        j.jobtitle                         AS title,
        CAST(j.minimumsalary AS UNSIGNED)  AS salary_min,
        CAST(j.maximumsalary AS UNSIGNED)  AS salary_max,
        CAST(j.experience    AS UNSIGNED)  AS years_exp,
        j.qualification                    AS qualification,
        c.companyname                      AS company_name,
        c.country                          AS country
    FROM job_post j
    JOIN company c ON c.id_company = j.id_company
"""
# CHÚ Ý: KHÔNG có j.description ở đây — JobItem không có trường đó, và bỏ bớt cột
# không cần là thói quen 'column projection' (Tuần 5). Nếu sau này cần mô tả để làm
# embedding (stretch goal), thêm lại có chủ đích.


def _seniority(years: int) -> str:
    """TRANSFORM — suy cấp bậc từ số năm kinh nghiệm (DB không có sẵn seniority).

    GIẢI THÍCH: quy tắc này là 'semantic layer' — chốt MỘT LẦN, MỘT CHỖ, ở bước
    transform, không rải trong API. Khớp đúng _seniority_from_years cũ trong
    app/infrastructure/warehouse/fake_jobs.py để hai backend cho cùng kết quả:
        0–1 năm → junior · 2–3 → mid · 4–5 → senior
    """
    if years <= 1:
        return "junior"
    if years <= 3:
        return "mid"
    return "senior"


def build_silver() -> int:
    """Chạy trọn Extract → Transform → Load. Trả về số dòng của silver_jobs."""
    settings = get_settings()

    # --- 1) EXTRACT: đọc từ MySQL bằng user CHỈ-ĐỌC ---
    # GIẢI THÍCH: settings.mysql_url trỏ tới user 'reader' (GRANT SELECT). API KHÔNG
    # bao giờ đọc chuỗi này — chỉ script ELT dùng. Đây là ranh giới thứ nhất.
    engine = create_engine(settings.mysql_url)
    with engine.connect() as conn:
        df = pd.read_sql(text(EXTRACT_SQL), conn)

    # --- 2) TRANSFORM: chuẩn hoá trong pandas (dữ liệu nhỏ nên rẻ) ---
    df["seniority"] = df["years_exp"].apply(_seniority)
    # url dựng lại giống fake.py để JobItem có đủ trường (JobItem yêu cầu 'url').
    df["url"] = df["job_id"].apply(lambda i: f"/view-job-post.php?id={i}")

    # --- 3) LOAD: ghi vào DuckDB, IDEMPOTENT ---
    # GIẢI THÍCH: CREATE OR REPLACE = truncate-then-load. Chạy lại → xoá sạch rồi
    # dựng lại → luôn cùng số dòng. Nếu dùng INSERT INTO, chạy 2 lần sẽ nhân đôi.
    con = duckdb.connect(settings.duckdb_path)      # mở ở chế độ GHI
    try:
        con.register("df_silver", df)               # cho DuckDB 'thấy' DataFrame
        con.execute("CREATE OR REPLACE TABLE silver_jobs AS SELECT * FROM df_silver")
        n = con.execute("SELECT COUNT(*) FROM silver_jobs").fetchone()[0]
    finally:
        con.close()                                 # luôn đóng để nhả khoá file
    return int(n)


if __name__ == "__main__":
    rows = build_silver()
    print(f"silver_jobs: {rows} dòng")
    # KIỂM CHỨNG idempotent: chạy lệnh này 2 lần, số dòng phải BẰNG NHAU (kỳ vọng 76).
