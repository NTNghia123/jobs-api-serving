"""Danh mục filter/metric/dimension/window — thứ /metadata công bố và validator thực thi.

Đọc từ CÙNG một chỗ để tài liệu và thực thi không lệch nhau. Căn theo dữ liệu thật
(Mongo → BigQuery, TopDev + VietnamWorks) — xem docs/adr/ADR-019, ADR-020.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import JobSource, MetricDimension, MetricWindow, Seniority, SortOption

# Giới hạn cứng của hợp đồng.
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_EXPERIENCE_YEARS = 50            # trần hợp lý cho số năm kinh nghiệm
MAX_SALARY_VND_MONTH = 10_000_000_000  # trần salary_min (VND/tháng) — chặn giá trị vô lý


def _values(enum_cls) -> list[str]:
    return [m.value for m in enum_cls]


@dataclass(frozen=True)
class FilterSpec:
    name: str
    type: str
    required: bool
    description: str
    allowed_values: list[str] | None = None
    minimum: int | None = None


@dataclass(frozen=True)
class MetricSpec:
    name: str
    description: str
    suppression_rule: str | None = None


FILTERS: tuple[FilterSpec, ...] = (
    FilterSpec(
        name="posted_after",
        type="date",
        required=True,
        description=(
            "BẮT BUỘC (ISO date). Chỉ lấy job có effective_posted_date >= giá trị này — "
            "đảm bảo partition prune trên BigQuery (kiểm soát chi phí)."
        ),
    ),
    FilterSpec(
        name="posted_before",
        type="date",
        required=False,
        description="Tùy chọn (ISO date). Chỉ lấy job có effective_posted_date <= giá trị này.",
    ),
    FilterSpec(
        name="source",
        type="string",
        required=False,
        description="Nền tảng nguồn.",
        allowed_values=_values(JobSource),
    ),
    FilterSpec(
        name="seniority",
        type="string",
        required=False,
        description="Cấp bậc chuẩn hoá (unknown = không xác định chắc chắn).",
        allowed_values=_values(Seniority),
    ),
    FilterSpec(
        name="category",
        type="string",
        required=False,
        description=(
            "category_key dạng '<source>:<group-path>', vd 'topdev:g14~j22'. "
            "Lấy từ /metadata hoặc từ categories trong kết quả search."
        ),
    ),
    FilterSpec(
        name="salary_min",
        type="integer",
        required=False,
        description=(
            "VND/tháng. Chỉ lấy job có salary_max_vnd_month >= giá trị này "
            "(job lương thoả thuận/không công khai bị loại)."
        ),
        minimum=0,
    ),
    FilterSpec(
        name="experience_max",
        type="integer",
        required=False,
        description="Chỉ lấy job yêu cầu <= số năm kinh nghiệm này (theo experience_min_years).",
        minimum=0,
    ),
)

METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name="median_salary_vnd_month",
        description=(
            "Trung vị của trung điểm lương (min+max)/2, quy về VND/tháng. "
            "NULL nếu salary_sample_count < ngưỡng k-anonymity."
        ),
        suppression_rule="null khi salary_sample_count < JOBS_API_METRICS_MIN_SAMPLE_SIZE",
    ),
    MetricSpec(name="posting_count", description="Số job (distinct job_id) trong nhóm/cửa sổ."),
    MetricSpec(
        name="salary_disclosed_count",
        description="Số job công khai ít nhất một cận lương (min HOẶC max) sau normalize.",
    ),
    MetricSpec(
        name="salary_sample_count",
        description="Số job có ĐỦ cả min và max hợp lệ (mẫu để tính median).",
    ),
)

DIMENSIONS: tuple[str, ...] = tuple(_values(MetricDimension))
WINDOWS: tuple[str, ...] = tuple(_values(MetricWindow))
SORT_OPTIONS: tuple[str, ...] = tuple(_values(SortOption))
FILTER_NAMES: frozenset[str] = frozenset(f.name for f in FILTERS)
