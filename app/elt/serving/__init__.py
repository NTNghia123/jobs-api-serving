"""ELT phục vụ dữ liệu THẬT: MongoDB `job_crawler` → silver/gold BigQuery.

Đây là pipeline MỚI của migration (Phase 2), KHÁC hẳn pipeline demo cũ
`app/elt/build_silver.py` / `build_gold.py` (MySQL → DuckDB, giữ dưới profile
`legacy-duckdb`). CLI điểm vào: `python -m app.elt.serving.run_serving_elt`
(Dagster Phase 5 bọc ngoài bằng `run_serving_elt`).

Tầng thuần (pure, không I/O) — test được không cần Mongo/BigQuery:
  - `silver.py`      hợp đồng SilverRow/Quarantine (ma trận ADR-019)
  - `salary.py`      chuẩn hoá lương VND/USD/tháng (ADR-024)
  - `dates.py`       parse posted/deadline theo nguồn, hai parse-status độc lập
  - `experience.py`  parse khoảng năm kinh nghiệm
  - `categories.py`  dựng repeated STRUCT, category_key source-qualified
  - `seniority.py`   map level → junior/mid/senior|null (versioned)
  - `mapper.py`      lắp ráp (job × detail) → SilverRow | Quarantine

Tầng I/O (Phase 2.6–2.7): extract Mongo, load BigQuery, publish transaction, CLI.
"""
