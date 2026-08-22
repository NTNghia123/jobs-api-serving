"""Giá trị được phép cho các filter — nguồn sự thật duy nhất.

Đã căn chỉnh theo SCHEMA THẬT (fulfilen/job-portal). Database chỉ có:
  - experience (số năm '1'..'5')  → ta SUY RA seniority
  - minimumsalary/maximumsalary (số nguyên)
  - địa điểm ở bảng company (country/state/city)
Không có: job_function, employment_type, work_mode, skills, thành phố ở tin.
Vì vậy enum ở đây chỉ giữ những gì dữ liệu thật hỗ trợ.
"""
from enum import Enum


class Seniority(str, Enum):
    """KHÔNG có trong DB — suy ra ở bước transform từ số năm kinh nghiệm:
    0–1 năm → junior · 2–3 → mid · 4–5 → senior."""
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"


class SortOption(str, Enum):
    """Không sort theo ngày: mọi tin có cùng createdat (2017-10-10) nên vô nghĩa."""
    SALARY_MAX_DESC = "salary_max_desc"
    SALARY_MIN_ASC = "salary_min_asc"
    EXPERIENCE_ASC = "experience_asc"
