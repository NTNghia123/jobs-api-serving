"""Hợp đồng của /v1/market/metrics — căn theo dữ liệu thật.

Xem docs/adr/ADR-020. Không PII. median_salary_vnd_month null khi
salary_sample_count < ngưỡng k-anonymity. Đơn vị: VND/tháng.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class MarketMetricRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_value: str = Field(description="Giá trị chiều, vd 'senior', 'topdev', 'topdev:g14~j22', 'unknown'.")
    display_name: str | None = Field(default=None, description="Nhãn hiển thị (vd 'Unknown').")
    posting_count: int = Field(description="Số job (distinct job_id) trong nhóm/cửa sổ.")
    salary_disclosed_count: int = Field(description="Số job công khai ít nhất một cận lương.")
    salary_sample_count: int = Field(description="Số job có đủ min&max (mẫu tính median).")
    median_salary_vnd_month: float | None = Field(
        default=None,
        description="Trung vị trung điểm lương (VND/tháng). NULL nếu salary_sample_count < ngưỡng.",
    )


class MarketMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(description="Chiều: source | seniority | category.")
    window: str = Field(description="Cửa sổ: 90d | all_time.")
    window_start: date | None = Field(default=None, description="Với 90d: as_of_date - 89 ngày.")
    window_end: date | None = Field(default=None, description="Với 90d: as_of_date.")
    items: list[MarketMetricRow]
    as_of: datetime = Field(description="data cutoff của batch (ADR-025).")
    request_id: str
