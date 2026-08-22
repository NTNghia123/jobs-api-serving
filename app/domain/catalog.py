"""Danh mục filter/metric — thứ mà /metadata công bố và tầng validate thực thi.

Đọc từ CÙNG một chỗ để tài liệu và thực thi không lệch nhau.
Đã căn chỉnh theo schema thật: chỉ những filter/metric mà job_post + company hỗ trợ.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import Seniority, SortOption

# Giới hạn cứng của hợp đồng.
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_EXPERIENCE_YEARS = 50  # trần hợp lý; dữ liệu thật chỉ 1..5


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
        name="seniority",
        type="string",
        required=False,
        description="Cấp bậc suy ra từ số năm kinh nghiệm (0–1 junior, 2–3 mid, 4–5 senior).",
        allowed_values=_values(Seniority),
    ),
    FilterSpec(
        name="experience_max",
        type="integer",
        required=False,
        description="Chỉ lấy tin yêu cầu <= số năm kinh nghiệm này (từ job_post.experience).",
        minimum=0,
    ),
    FilterSpec(
        name="salary_min",
        type="integer",
        required=False,
        description=(
            "Chỉ lấy tin có maximumsalary >= giá trị này. "
            "Đơn vị KHÔNG xác định trong nguồn — là số nguyên thô, không phải VND."
        ),
        minimum=0,
    ),
    FilterSpec(
        name="country",
        type="string",
        required=False,
        description=(
            "Quốc gia của công ty đăng tin (lấy qua JOIN job_post × company). "
            "Địa điểm chỉ có ở cấp công ty, không có ở cấp tin."
        ),
    ),
)

METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name="median_salary",
        description=(
            "Trung vị của lương đại diện (trung điểm min–max) theo cấp bậc. "
            "Đơn vị không xác định (số thô)."
        ),
        suppression_rule="trả null khi posting_count < 5",
    ),
    MetricSpec(
        name="posting_count",
        description="Số tin tuyển dụng trong nhóm (đếm theo id_jobpost).",
    ),
)

SORT_OPTIONS: tuple[str, ...] = tuple(_values(SortOption))
FILTER_NAMES: frozenset[str] = frozenset(f.name for f in FILTERS)
