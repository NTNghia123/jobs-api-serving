"""Writer guard: phân giải môi trường → dataset BigQuery (allowlist), chống ghi nhầm.

Nguyên tắc (ADR-022 §cost/safety, plan §Phase 2 writer guard):
  - environment BẮT BUỘC truyền tường minh, CHỈ nhận {staging, prod} — không default
    trúng prod. CLI prod phải `--environment prod`.
  - dataset SUY TỪ environment qua map cố định — KHÔNG nhận dataset tuỳ ý từ ngoài.
  - log project/dataset trước khi ghi (loader gọi `Target.describe()`).
  - dataset_suffix cho integration test (vd '_test') — không đụng dataset thật.

Thuần, không I/O. `WriterConfig.from_env()` đọc biến môi trường cho CLI; test dựng trực tiếp.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

ENVIRONMENTS = ("staging", "prod")


class WriterGuardError(ValueError):
    """Môi trường không hợp lệ / cấu hình thiếu → chặn trước khi chạm BigQuery."""


@dataclass(frozen=True)
class WriterConfig:
    """Cấu hình writer — project + tên dataset mỗi môi trường + location + trần byte."""

    project: str
    dataset_staging: str = "jobs_staging"
    dataset_prod: str = "jobs_prod"
    location: str = "asia-southeast1"
    maximum_bytes_billed: int = 2_000_000_000

    @classmethod
    def from_env(cls) -> WriterConfig:
        project = os.environ.get("JOBS_BQ_PROJECT", "").strip()
        if not project:
            raise WriterGuardError("Thiếu JOBS_BQ_PROJECT (project BigQuery đích).")
        return cls(
            project=project,
            dataset_staging=os.environ.get("JOBS_BQ_DATASET_STAGING", "jobs_staging"),
            dataset_prod=os.environ.get("JOBS_BQ_DATASET_PROD", "jobs_prod"),
            location=os.environ.get("JOBS_BQ_LOCATION", "asia-southeast1"),
            maximum_bytes_billed=int(os.environ.get("JOBS_BQ_MAX_BYTES_BILLED", "2000000000")),
        )

    def _dataset_for(self, environment: str) -> str:
        return {"staging": self.dataset_staging, "prod": self.dataset_prod}[environment]


@dataclass(frozen=True)
class Target:
    """Đích đã phân giải cho một lần chạy ELT. Bất biến."""

    environment: str
    project: str
    dataset: str
    location: str
    maximum_bytes_billed: int

    def table_ref(self, table: str) -> str:
        """'project.dataset.table' — cho google-cloud-bigquery client."""
        return f"{self.project}.{self.dataset}.{table}"

    def table_id(self, table: str) -> str:
        """'`project.dataset.table`' — fully-qualified + backtick, cho SQL."""
        return f"`{self.project}.{self.dataset}.{table}`"

    def describe(self) -> str:
        return f"env={self.environment} project={self.project} dataset={self.dataset} location={self.location}"


def resolve_target(
    environment: str,
    config: WriterConfig,
    *,
    dataset_suffix: str = "",
) -> Target:
    """environment → Target. Raise nếu environment ngoài allowlist (không bao giờ đoán prod)."""
    if environment not in ENVIRONMENTS:
        raise WriterGuardError(
            f"environment={environment!r} không hợp lệ — chỉ {ENVIRONMENTS} "
            "(prod phải truyền tường minh)."
        )
    dataset = config._dataset_for(environment) + dataset_suffix
    return Target(
        environment=environment,
        project=config.project,
        dataset=dataset,
        location=config.location,
        maximum_bytes_billed=config.maximum_bytes_billed,
    )
