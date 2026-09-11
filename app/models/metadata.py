"""Model cho /metadata — bản tự mô tả của API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FilterInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    required: bool
    description: str
    allowed_values: list[str] | None = None
    minimum: int | None = None


class MetricInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    suppression_rule: str | None = None


class LimitsInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_limit: int
    max_limit: int
    max_experience_years: int


class MetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    as_of: datetime = Field(description="data cutoff của batch đang phục vụ.")
    filters: list[FilterInfo]
    metrics: list[MetricInfo]
    dimensions: list[str] = Field(description="Chiều hợp lệ của /market/metrics.")
    windows: list[str] = Field(description="Cửa sổ hợp lệ của /market/metrics.")
    metrics_min_sample_size: int = Field(description="Ngưỡng k-anonymity cho median lương.")
    sort_options: list[str]
    limits: LimitsInfo
    request_id: str
