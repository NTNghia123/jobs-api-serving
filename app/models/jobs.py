"""Hợp đồng của /v1/jobs/search — căn theo dữ liệu THẬT (Mongo → BigQuery).

Xem docs/adr/ADR-019 (mapping), ADR-020 (serving), ADR-024 (salary).
Nguyên tắc: JobItem KHÔNG chứa raw payload / contact / session; text lớn
(description/benefit) và skills để [SAU] ở silver_job_details.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.catalog import (
    DEFAULT_LIMIT,
    MAX_EXPERIENCE_YEARS,
    MAX_LIMIT,
    MAX_SALARY_VND_MONTH,
)
from app.models.enums import JobSource, Seniority, SortOption


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # posted_after BẮT BUỘC (ADR-020) → đảm bảo partition prune, không quét cả kho.
    posted_after: date = Field(description="ISO date, bắt buộc. effective_posted_date >= giá trị.")
    posted_before: date | None = Field(default=None, description="ISO date. effective_posted_date <= giá trị.")
    source: JobSource | None = None
    seniority: Seniority | None = None
    category: str | None = Field(default=None, max_length=120, description="category_key '<source>:<group-path>'.")
    salary_min: int | None = Field(default=None, ge=0, le=MAX_SALARY_VND_MONTH, description="VND/tháng.")
    experience_max: int | None = Field(default=None, ge=0, le=MAX_EXPERIENCE_YEARS)

    @field_validator("posted_before")
    @classmethod
    def _range_not_inverted(cls, v, info):
        # posted_after khai TRƯỚC nên đã có trong info.data → chặn khoảng ngày đảo ngược
        # ngay ở biên (400 filters.posted_before) thay vì trả danh sách rỗng khó hiểu.
        after = (info.data or {}).get("posted_after")
        if v is not None and after is not None and v < after:
            raise ValueError("posted_before phải >= posted_after")
        return v

    def active_names(self) -> list[str]:
        """Tên các filter đang dùng — cho log (KHÔNG log giá trị)."""
        return [k for k, v in self.model_dump().items() if v is not None]


class SearchRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "filters": {
                    "posted_after": "2026-06-01",
                    "seniority": "senior",
                    "salary_min": 30000000,
                },
                "sort": "salary_max_desc",
                "limit": 20,
            }
        },
    )

    # filters BẮT BUỘC (posted_after bắt buộc) → không còn default_factory.
    filters: SearchFilters
    sort: SortOption = SortOption.SALARY_MAX_DESC
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    page_token: str | None = Field(
        default=None,
        description="Chuỗi mờ (gắn batch) lấy từ next_page_token của response trước. Không tự tạo/sửa.",
    )


class CategoryItem(BaseModel):
    """Một category của job (repeated). Key source-qualified theo group-path."""

    model_config = ConfigDict(extra="forbid")

    category_key: str          # '<source>:<group-path>', vd 'topdev:g14~j22'
    category_name: str
    category_path: str | None = None   # group-path, vd 'g14~j22'


class JobItem(BaseModel):
    """Một job. Không PII, không raw/text-lớn (serving-lean)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str                              # composite '<source>:<external_id>'
    source: JobSource
    external_id: str
    title: str
    company_name: str | None = None
    location_text: str | None = None
    seniority: Seniority                     # đã chuẩn hoá (có thể 'unknown')
    experience_min_years: float | None = None
    experience_max_years: float | None = None
    salary_min_vnd_month: int | None = None  # null khi thoả thuận / thiếu cận
    salary_max_vnd_month: int | None = None
    salary_currency: str | None = None       # tiền tệ gốc (VND/USD) — thông tin
    salary_period: str | None = None         # 'month'
    categories: list[CategoryItem] = Field(default_factory=list)
    posted_at: datetime | None = None
    effective_posted_date: date              # luôn có (COALESCE, ADR-019)
    deadline_date: date | None = None
    url: str                                 # source_url


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JobItem]
    next_page_token: str | None = Field(default=None, description="null nghĩa là đã hết dữ liệu.")
    total_estimated: int | None = Field(
        default=None,
        description="Thường null trên BigQuery (không COUNT(*) mỗi request). Đừng dùng để phân trang.",
    )
    as_of: datetime = Field(description="data cutoff của batch đang phục vụ (ADR-025).")
    request_id: str
