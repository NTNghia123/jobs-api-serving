"""Giá trị được phép cho filter/dimension — nguồn sự thật duy nhất.

Căn theo dữ liệu THẬT (Mongo `job_crawler`, TopDev + VietnamWorks) — xem
docs/adr/ADR-019. Scraper đã normalize sẵn salary/categories; seniority suy ra ở
transform (null → bucket 'unknown'); experience là khoảng min–max năm.
"""
from enum import Enum


class JobSource(str, Enum):
    """Nền tảng nguồn — cũng là tiền tố của composite job_id (`<source>:<external_id>`)."""
    TOPDEV = "topdev"
    VIETNAMWORKS = "vietnamworks"


class Seniority(str, Enum):
    """Suy ra ở transform từ level/kinh nghiệm nguồn. `unknown` = không map chắc chắn
    (giữ nguyên, không đoán từ title) — vẫn là một bucket hợp lệ ở metrics/search."""
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    UNKNOWN = "unknown"


class SortOption(str, Enum):
    """Dữ liệu thật CÓ ngày đăng thật → cho phép sort theo ngày (khác demo cũ đồng nhất ngày).
    Mọi sort đều kèm job_id để thứ tự TẤT ĐỊNH (keyset — adapter lo)."""
    SALARY_MAX_DESC = "salary_max_desc"
    SALARY_MIN_ASC = "salary_min_asc"
    EXPERIENCE_ASC = "experience_asc"
    POSTED_DESC = "posted_desc"


class MetricDimension(str, Enum):
    """Chiều tổng hợp của /v1/market/metrics. `province` để [SAU] (cần location parser)."""
    SOURCE = "source"
    SENIORITY = "seniority"
    CATEGORY = "category"


class MetricWindow(str, Enum):
    """Cửa sổ thời gian của metrics. Neo theo as_of_date của batch (xem ADR-020).
    Arbitrary date range để [SAU] (median-of-medians không chính xác)."""
    D90 = "90d"
    ALL_TIME = "all_time"
