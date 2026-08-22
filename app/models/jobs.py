"""Hợp đồng của /v1/jobs/search — căn chỉnh theo schema thật.

Nguồn: job_post JOIN company (fulfilen/job-portal).
Quyết định bám dữ liệu thật:
  - Bỏ posted_after: mọi tin cùng ngày 2017-10-10 nên "filter ngày" vô nghĩa.
  - seniority là giá trị SUY RA (không có sẵn), enum 3 mức.
  - country thay cho city: địa điểm chỉ ở company, là quốc gia.
  - Không có job_function / employment_type / work_mode / skills → không đưa vào.
  - Lương là số nguyên KHÔNG đơn vị tiền tệ → không đặt tên salary_*_vnd.
  - JobItem không chứa PII (email, password, dob, contactno) — ràng buộc thiết kế.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.catalog import DEFAULT_LIMIT, MAX_EXPERIENCE_YEARS, MAX_LIMIT
from app.models.enums import Seniority, SortOption


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seniority: Seniority | None = None
    experience_max: int | None = Field(default=None, ge=0, le=MAX_EXPERIENCE_YEARS)
    salary_min: int | None = Field(default=None, ge=0, le=1_000_000_000)
    country: str | None = Field(default=None, max_length=100)

    def active_names(self) -> list[str]:
        """Tên các filter đang dùng — cho log (KHÔNG log giá trị)."""
        return [k for k, v in self.model_dump().items() if v is not None]


class SearchRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "filters": {"seniority": "mid", "salary_min": 30000, "country": "Bulgaria"},
                "sort": "salary_max_desc",
                "limit": 20,
            }
        },
    )

    filters: SearchFilters = Field(default_factory=SearchFilters)
    sort: SortOption = SortOption.SALARY_MAX_DESC
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    page_token: str | None = Field(
        default=None,
        description="Chuỗi mờ lấy từ next_page_token của response trước. Không tự tạo.",
    )


class JobItem(BaseModel):
    """Một tin tuyển dụng. Mọi trường ở đây đều an toàn (không PII)."""

    model_config = ConfigDict(extra="forbid")

    job_id: int
    title: str                      # job_post.jobtitle
    company_name: str               # company.companyname
    country: str | None = None      # company.country (qua JOIN)
    seniority: Seniority            # suy ra từ years_exp
    years_exp: int                  # job_post.experience (đã CAST)
    salary_min: int | None = None   # job_post.minimumsalary (đã CAST)
    salary_max: int | None = None   # job_post.maximumsalary (đã CAST)
    qualification: str | None = None
    url: str


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JobItem]
    next_page_token: str | None = Field(
        default=None, description="null nghĩa là đã hết dữ liệu."
    )
    total_estimated: int | None = Field(
        default=None,
        description="ƯỚC LƯỢNG, không đảm bảo chính xác. Đừng dùng để phân trang.",
    )
    as_of: datetime = Field(description="Thời điểm chạy ELT gần nhất (độ tươi dữ liệu).")
    request_id: str
