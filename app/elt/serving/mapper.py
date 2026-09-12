"""Lắp ráp (job × detail) Mongo → SilverRow | QuarantineRecord.

Một bản ghi nguồn = một job (`jobs`) đã LEFT JOIN detail (`job_details`) theo
(platformId, externalId). Mapper là NƠI DUY NHẤT quyết định "phục vụ hay quarantine":

  quarantine khi  (thứ tự kiểm tra):
    1. thiếu externalId            → MISSING_ID
    2. detail không `completed`    → MISSING_DETAIL   (lỗi / chưa crawl xong)
    3. title trống cả detail & list→ MISSING_TITLE
    4. posted_at & first_seen đều lỗi → INVALID_POSTED_DATE

Còn lại → SilverRow (ráp 5 transform thuần + salary + composite job_id).
Reconciliation (plan #9): `source_jobs = silver + quarantine` theo source; silver
distinct job_id == count. `reconcile()` tính báo cáo; enforce ở cụm quality-check.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.elt.serving.categories import build_categories
from app.elt.serving.company import extract_company_name
from app.elt.serving.dates import parse_deadline_date, parse_posted_date
from app.elt.serving.experience import parse_experience
from app.elt.serving.salary import normalize_salary
from app.elt.serving.seniority import normalize_seniority
from app.elt.serving.silver import (
    PostedDateParseStatus,
    QuarantineReason,
    QuarantineRecord,
    QuarantineStage,
    SilverRow,
)

MapResult = SilverRow | QuarantineRecord
_COMPLETED = "completed"


def _clean(v: object) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _path(d: object, *keys: str) -> object:
    cur = d
    for k in keys:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(k)
    return cur


def _quarantine(
    batch_id: str, source: str, external_id: str | None,
    reason: QuarantineReason, detail: str | None = None,
) -> QuarantineRecord:
    return QuarantineRecord(
        batch_id=batch_id, source=source, external_id=external_id,
        stage=QuarantineStage.MAP, reason_code=reason, reason_detail_sanitized=detail,
    )


def map_record(
    job_doc: Mapping[str, object],
    detail_doc: Mapping[str, object] | None,
    batch_id: str,
    list_category: Mapping[str, object] | None = None,
) -> MapResult:
    """Map một cặp (job, detail). `detail_doc=None` = không có job_details."""
    source = str(job_doc.get("platformId") or "").strip() or "unknown"
    external_id = _clean(job_doc.get("externalId"))

    # 1) thiếu id → không dựng được job_id composite.
    if external_id is None:
        return _quarantine(batch_id, source, None, QuarantineReason.MISSING_ID)

    # 2) detail phải tồn tại & completed (lưới an toàn cho job lỗi / chưa crawl xong).
    status = _clean((detail_doc or {}).get("status"))
    if detail_doc is None or status != _COMPLETED:
        return _quarantine(
            batch_id, source, external_id, QuarantineReason.MISSING_DETAIL,
            detail=f"status={status!r}",
        )

    # 3) title: detail trước, fallback list; trống cả hai → quarantine.
    title = _clean(detail_doc.get("title")) or _clean(job_doc.get("title"))
    if title is None:
        return _quarantine(batch_id, source, external_id, QuarantineReason.MISSING_TITLE)

    # 4) ngày đăng: posted_at || first_seen_at; cả hai hỏng → quarantine.
    first_seen = job_doc.get("firstSeenAt")
    first_seen = first_seen if isinstance(first_seen, datetime) else None
    posted_at, effective_date, posted_status = parse_posted_date(
        job_doc.get("postedAt"), first_seen,
    )
    if posted_status == PostedDateParseStatus.INVALID or effective_date is None:
        return _quarantine(
            batch_id, source, external_id, QuarantineReason.INVALID_POSTED_DATE,
            detail=f"posted={job_doc.get('postedAt')!r} first_seen={first_seen!r}",
        )

    # --- qua hết cổng → dựng SilverRow ---
    deadline_date, deadline_status = parse_deadline_date(detail_doc.get("deadline"))
    salary = normalize_salary(_as_mapping(detail_doc.get("salary")))
    exp_min, exp_max, exp_status = parse_experience(detail_doc.get("experience"))
    seniority_raw = _clean(_path(detail_doc, "commonInfo", "level"))
    seniority_norm, seniority_ver = normalize_seniority(seniority_raw)
    company = extract_company_name(
        source, _as_mapping(detail_doc.get("raw")), _as_mapping(job_doc.get("rawListData")),
    )
    categories = build_categories(
        source, _as_sequence(detail_doc.get("categories")), list_category,
    )
    location = _clean(detail_doc.get("address")) or _clean(_path(detail_doc, "companyInfo", "address"))
    source_url = _clean(detail_doc.get("sourceUrl")) or _clean(job_doc.get("detailUrl"))
    last_seen = job_doc.get("lastSeenAt")
    last_seen = last_seen if isinstance(last_seen, datetime) else None

    return SilverRow(
        job_id=f"{source}:{external_id}",
        source=source,
        external_id=external_id,
        source_url=source_url,
        detail_status=status,
        title=title,
        company_name=company,
        location_text=location,
        seniority_raw=seniority_raw,
        seniority_normalized=seniority_norm,
        seniority_mapping_version=seniority_ver,
        experience_raw=_clean(detail_doc.get("experience")),
        experience_min_years=exp_min,
        experience_max_years=exp_max,
        experience_parse_status=exp_status,
        **SilverRow.salary_fields(salary),
        posted_at=posted_at,
        effective_posted_date=effective_date,
        deadline_date=deadline_date,
        posted_date_parse_status=posted_status,
        deadline_date_parse_status=deadline_status,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        batch_id=batch_id,
        categories=categories,
    )


def _as_mapping(v: object) -> Mapping[str, object] | None:
    return v if isinstance(v, Mapping) else None


def _as_sequence(v: object) -> Sequence[Mapping[str, object]] | None:
    # str cũng là Sequence — loại rõ ràng; chỉ nhận list/tuple category dict.
    return v if isinstance(v, (list, tuple)) else None


# --------------------------------------------------------------------------- #
# Reconciliation (plan #9)                                                     #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ReconciliationReport:
    """Đối soát theo source: source_jobs == silver + quarantine; silver distinct == count."""

    source: str
    silver_rows: int
    quarantined_rows: int
    distinct_job_ids: int

    @property
    def source_jobs(self) -> int:
        return self.silver_rows + self.quarantined_rows

    @property
    def is_consistent(self) -> bool:
        # silver phải 1 job = 1 dòng (distinct job_id == số dòng silver).
        return self.distinct_job_ids == self.silver_rows


def reconcile(results: Sequence[MapResult]) -> dict[str, ReconciliationReport]:
    """Gộp kết quả map theo source → báo cáo đối soát (chưa raise; enforce ở quality-check)."""
    silver: dict[str, list[SilverRow]] = {}
    quarantined: dict[str, int] = {}
    for r in results:
        if isinstance(r, SilverRow):
            silver.setdefault(r.source, []).append(r)
        else:
            quarantined[r.source] = quarantined.get(r.source, 0) + 1

    sources = set(silver) | set(quarantined)
    out: dict[str, ReconciliationReport] = {}
    for src in sources:
        rows = silver.get(src, [])
        out[src] = ReconciliationReport(
            source=src,
            silver_rows=len(rows),
            quarantined_rows=quarantined.get(src, 0),
            distinct_job_ids=len({r.job_id for r in rows}),
        )
    return out
