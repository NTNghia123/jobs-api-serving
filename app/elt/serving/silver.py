"""Hợp đồng bảng silver_jobs + quarantine — đúng ma trận ADR-019.

SilverRow là "một job = một dòng" (categories là repeated STRUCT, KHÔNG explode —
giữ keyset pagination + invariant distinct job_id). Đây là nhà của các parse-status
enum dùng chung cho cả pipeline; `dates.py`/`experience.py` import từ đây.

KHÔNG đưa vào silver serving: raw payload, contact, session, text lớn
(description/benefit/skills) — để bảng `silver_job_details` [SAU] (ADR-019).

`to_bq_row()` trả dict JSON-serializable khớp tên cột BigQuery (DATE='YYYY-MM-DD',
TIMESTAMP=ISO-8601) để load bằng `load_table_from_json` ở cụm loader (2.6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum

from app.elt.serving.salary import NormalizedSalary, SalaryNormalizationStatus


class PostedDateParseStatus(str, Enum):
    """Hai trạng thái ngày ĐỘC LẬP (ADR-019) — KHÔNG gộp với deadline."""

    PARSED = "parsed"                      # posted_at parse OK → effective = DATE(posted_at)
    FALLBACK_FIRST_SEEN = "fallback_first_seen"  # posted thiếu/lỗi, dùng DATE(first_seen_at)
    INVALID = "invalid"                    # cả hai thiếu/lỗi → QUARANTINE (không vào silver)


class DeadlineDateParseStatus(str, Enum):
    PARSED = "parsed"      # deadline parse OK
    MISSING = "missing"    # không có deadline → deadline_date = null
    INVALID = "invalid"    # có nhưng parse lỗi → deadline_date = null (KHÔNG quarantine)


class ExperienceParseStatus(str, Enum):
    PARSED = "parsed"        # bóc được ít nhất một cận năm
    MISSING = "missing"      # không có text kinh nghiệm
    UNPARSED = "unparsed"    # có text nhưng không bóc được số năm


class QuarantineStage(str, Enum):
    """Chặng sinh ra bản ghi quarantine (để lọc khi điều tra)."""

    MAP = "map"  # lỗi khi map job×detail → silver (thiếu id/title, ngày invalid)


class QuarantineReason(str, Enum):
    """Lý do bị loại khỏi silver (mã ổn định để đếm/điều tra; detail đã sanitize)."""

    MISSING_ID = "missing_external_id"        # không dựng được job_id composite
    MISSING_TITLE = "missing_title"           # title trống ở cả detail lẫn list
    INVALID_POSTED_DATE = "invalid_posted_date"  # posted_at lẫn first_seen_at đều invalid
    MISSING_DETAIL = "missing_detail"         # không có job_details completed (lỗi / chưa crawl xong) → chưa phục vụ


@dataclass(frozen=True)
class SilverCategory:
    """Một category (repeated). `category_key = '<source>:<group-path>'` (ADR-019)."""

    category_key: str
    category_name: str
    category_code: str | None = None
    category_path: str | None = None      # group-path, vd 'g14~j22'
    level1_id: str | None = None
    level2_id: str | None = None
    level3_id: str | None = None

    def to_bq_row(self) -> dict[str, object | None]:
        return {
            "category_key": self.category_key,
            "category_name": self.category_name,
            "category_code": self.category_code,
            "category_path": self.category_path,
            "level1_id": self.level1_id,
            "level2_id": self.level2_id,
            "level3_id": self.level3_id,
        }


@dataclass(frozen=True)
class SilverRow:
    """Một dòng silver_jobs. Thứ tự field theo ma trận ADR-019."""

    # --- định danh & nguồn ---
    job_id: str                 # composite '<source>:<external_id>'
    source: str                 # 'topdev' | 'vietnamworks'
    external_id: str
    source_url: str | None
    detail_status: str          # trạng thái detail ('completed'|'pending'|...)

    # --- mô tả cốt lõi ---
    title: str
    company_name: str | None
    location_text: str | None

    # --- cấp bậc ---
    seniority_raw: str | None
    seniority_normalized: str | None       # null → bucket 'unknown' ở metrics
    seniority_mapping_version: str | None

    # --- kinh nghiệm ---
    experience_raw: str | None
    experience_min_years: float | None
    experience_max_years: float | None
    experience_parse_status: ExperienceParseStatus

    # --- lương (nhóm từ NormalizedSalary) ---
    salary_raw: str | None
    salary_min_original: float | None
    salary_max_original: float | None
    salary_currency: str | None
    salary_period: str | None
    salary_min_vnd_month: int | None
    salary_max_vnd_month: int | None
    fx_rate_to_vnd: float | None
    salary_fx_version: str | None
    salary_normalization_status: SalaryNormalizationStatus

    # --- ngày ---
    posted_at: datetime | None
    effective_posted_date: date            # luôn có (COALESCE; cả hai invalid → quarantine)
    deadline_date: date | None
    posted_date_parse_status: PostedDateParseStatus
    deadline_date_parse_status: DeadlineDateParseStatus

    # --- metadata nguồn & batch ---
    first_seen_at: datetime
    last_seen_at: datetime | None
    batch_id: str

    # --- categories (repeated; thiếu → []) ---
    categories: list[SilverCategory] = field(default_factory=list)

    @classmethod
    def salary_fields(cls, s: NormalizedSalary) -> dict[str, object | None]:
        """Trải NormalizedSalary thành kwargs nhóm salary_* cho constructor (mapper dùng)."""
        return {
            "salary_raw": s.salary_raw,
            "salary_min_original": s.salary_min_original,
            "salary_max_original": s.salary_max_original,
            "salary_currency": s.salary_currency,
            "salary_period": s.salary_period,
            "salary_min_vnd_month": s.salary_min_vnd_month,
            "salary_max_vnd_month": s.salary_max_vnd_month,
            "fx_rate_to_vnd": s.fx_rate_to_vnd,
            "salary_fx_version": s.salary_fx_version,
            "salary_normalization_status": s.salary_normalization_status,
        }

    def to_bq_row(self) -> dict[str, object | None]:
        """Dict khớp cột BigQuery (ngày→ISO, enum→.value, categories→list[dict])."""
        return {
            "job_id": self.job_id,
            "source": self.source,
            "external_id": self.external_id,
            "source_url": self.source_url,
            "detail_status": self.detail_status,
            "title": self.title,
            "company_name": self.company_name,
            "location_text": self.location_text,
            "seniority_raw": self.seniority_raw,
            "seniority_normalized": self.seniority_normalized,
            "seniority_mapping_version": self.seniority_mapping_version,
            "experience_raw": self.experience_raw,
            "experience_min_years": self.experience_min_years,
            "experience_max_years": self.experience_max_years,
            "experience_parse_status": self.experience_parse_status.value,
            "salary_raw": self.salary_raw,
            "salary_min_original": self.salary_min_original,
            "salary_max_original": self.salary_max_original,
            "salary_currency": self.salary_currency,
            "salary_period": self.salary_period,
            "salary_min_vnd_month": self.salary_min_vnd_month,
            "salary_max_vnd_month": self.salary_max_vnd_month,
            "fx_rate_to_vnd": self.fx_rate_to_vnd,
            "salary_fx_version": self.salary_fx_version,
            "salary_normalization_status": self.salary_normalization_status.value,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "effective_posted_date": self.effective_posted_date.isoformat(),
            "deadline_date": self.deadline_date.isoformat() if self.deadline_date else None,
            "posted_date_parse_status": self.posted_date_parse_status.value,
            "deadline_date_parse_status": self.deadline_date_parse_status.value,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "batch_id": self.batch_id,
            "categories": [c.to_bq_row() for c in self.categories],
        }


@dataclass(frozen=True)
class QuarantineRecord:
    """Một job bị loại khỏi silver. Chỉ mã + detail đã sanitize (KHÔNG raw/PII)."""

    batch_id: str
    source: str
    external_id: str | None
    stage: QuarantineStage
    reason_code: QuarantineReason
    reason_detail_sanitized: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_bq_row(self) -> dict[str, object | None]:
        return {
            "batch_id": self.batch_id,
            "source": self.source,
            "external_id": self.external_id,
            "stage": self.stage.value,
            "reason_code": self.reason_code.value,
            "reason_detail_sanitized": self.reason_detail_sanitized,
            "created_at": self.created_at.isoformat(),
        }
