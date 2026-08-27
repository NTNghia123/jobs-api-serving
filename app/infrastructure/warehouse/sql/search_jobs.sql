-- Template tìm tin tuyển dụng.  Vị trí: app/infrastructure/warehouse/sql/search_jobs.sql
-- Được đọc bởi app/infrastructure/warehouse/duckdb_jobs.py
--
-- GIẢI THÍCH cấu trúc:
--   * Danh sách cột và tên bảng là CỐ ĐỊNH ở đây (không đến từ user) — đây là
--     'allowlist identifier'. Tham số SQL KHÔNG thay được tên cột/bảng, nên phần
--     này bắt buộc phải cứng trong code, không nhận từ request.
--   * {where} và {order} do duckdb_repo.py chèn vào, nhưng cũng chỉ ráp TỪ ALLOWLIST
--     (_FILTER_TO_SQL, _SORT_TO_SQL). Giá trị lọc đi qua tham số $tên, không nối chuỗi.
--   * SELECT liệt kê đúng cột cần, KHÔNG 'SELECT *' — 'column projection'. Ở DuckDB
--     tiết kiệm thời gian/bộ nhớ; ngày lên BigQuery thành tiền thật (tính theo byte).
--   * KHÔNG có cột PII nào ở đây — silver_jobs vốn đã không chứa PII (chặn từ ELT).
SELECT
    job_id,
    title,
    company_name,
    country,
    seniority,
    years_exp,
    salary_min,
    salary_max,
    qualification,
    url
FROM silver_jobs
{where}
{order}
