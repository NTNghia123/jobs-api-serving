"""Hợp đồng của /v1/market/metrics (Tuần 5).

Không PII. median_salary có thể null khi nhóm quá nhỏ (k-anonymity, áp ở tầng API).
Đơn vị lương KHÔNG xác định trong nguồn (số nguyên thô) — giữ nguyên, không bịa 'VND'.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MarketMetricRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_value: str = Field(description="Giá trị của chiều, vd 'senior' hoặc 'Denmark'.")
    median_salary: float | None = Field(
        default=None,
        description="Trung vị lương đại diện. NULL nếu nhóm < ngưỡng k-anonymity.",
    )
    posting_count: int = Field(description="Số tin trong nhóm.")


class MarketMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(description="Chiều đang tổng hợp: seniority | country.")
    items: list[MarketMetricRow]
    as_of: datetime = Field(description="Thời điểm nướng gold table gần nhất (độ tươi).")
    request_id: str
