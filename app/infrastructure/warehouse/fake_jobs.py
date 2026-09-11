"""Adapter giả lập (test double) cho JobRepository — dữ liệu mẫu, không cần BigQuery/DuckDB.

Dùng cho test và backend `fake`. Mẫu mô phỏng hình dạng THẬT sau ELT (Mongo → silver):
composite job_id, hai nguồn, salary đã quy VND/tháng (có negotiable→null), categories
repeated (key source-qualified), ngày đăng thật (để test posted_after + posted_desc),
seniority chuẩn hoá (gồm 'unknown'). Ngữ nghĩa filter/sort ở đây phải KHỚP adapter BigQuery
(bộ contract test chạy chung) — xem docs/adr/ADR-019, ADR-020.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.ports.job_repository import JobRepository, SearchResult
from app.models.enums import JobSource, Seniority, SortOption
from app.models.jobs import CategoryItem, JobItem, SearchRequest

# data cutoff của "batch" giả (as_of). Prod lấy động từ warehouse_batches (ADR-025).
_AS_OF = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)


def _cat(key: str, name: str) -> CategoryItem:
    return CategoryItem(category_key=key, category_name=name, category_path=key.split(":", 1)[1])


def _job(source, ext, title, company, loc, sen, exp_min, exp_max,
         smin, smax, cur, cats, posted, deadline):
    return JobItem(
        job_id=f"{source.value}:{ext}",
        source=source,
        external_id=ext,
        title=title,
        company_name=company,
        location_text=loc,
        seniority=sen,
        experience_min_years=exp_min,
        experience_max_years=exp_max,
        salary_min_vnd_month=smin,
        salary_max_vnd_month=smax,
        salary_currency=cur,
        salary_period="month" if smin is not None or smax is not None else None,
        categories=cats,
        posted_at=datetime(posted.year, posted.month, posted.day, tzinfo=UTC) if posted else None,
        effective_posted_date=posted,
        deadline_date=deadline,
        url=f"https://{source.value}.example/job/{ext}",
    )


_TD = JobSource.TOPDEV
_VW = JobSource.VIETNAMWORKS
_SALES = [_cat("topdev:g14~j22", "Sales")]
_BE = [_cat("topdev:g1~j5", "Backend")]
_HR = [_cat("vietnamworks:g5~j40", "Nhân sự")]
_FIN = [_cat("vietnamworks:g8~j51", "Tài chính")]

SAMPLE: list[JobItem] = [
    # job MULTI-CATEGORY (Backend + Sales) — để test category filter không nhân dòng.
    _job(_TD, "1001", "Backend Engineer", "Acme", "Hà Nội", Seniority.SENIOR, 4, 6,
         30_000_000, 50_000_000, "VND", _BE + _SALES, date(2026, 8, 20), date(2026, 10, 1)),
    _job(_TD, "1002", "Sales Executive", "Beta", "TP.HCM", Seniority.JUNIOR, 0, 1,
         12_000_000, 18_000_000, "VND", _SALES, date(2026, 8, 10), date(2026, 9, 30)),
    _job(_TD, "1003", "Senior Backend (USD)", "Gamma", "Đà Nẵng", Seniority.SENIOR, 5, 8,
         51_000_000, 76_500_000, "USD", _BE, date(2026, 7, 15), None),
    _job(_TD, "1004", "Backend Intern", "Delta", "Hà Nội", Seniority.JUNIOR, 0, 0,
         None, None, None, _BE, date(2026, 9, 1), date(2026, 11, 1)),   # negotiable
    _job(_VW, "2001", "HR Manager", "Epsilon", "TP.HCM", Seniority.MID, 2, 4,
         25_000_000, 35_000_000, "VND", _HR, date(2026, 6, 5), date(2026, 9, 15)),
    _job(_VW, "2002", "Finance Analyst", "Zeta", "Hà Nội", Seniority.MID, 3, 5,
         28_000_000, 40_000_000, "VND", _FIN, date(2026, 5, 20), date(2026, 9, 20)),
    _job(_VW, "2003", "HR Assistant", "Eta", "Cần Thơ", Seniority.JUNIOR, 1, 2,
         15_000_000, 22_000_000, "VND", _HR, date(2026, 8, 25), None),
    _job(_VW, "2004", "Finance Lead", "Theta", "TP.HCM", Seniority.SENIOR, 6, 9,
         45_000_000, 70_000_000, "VND", _FIN, date(2026, 7, 30), date(2026, 10, 10)),
    _job(_VW, "2005", "Chưa rõ cấp bậc", "Iota", "Hà Nội", Seniority.UNKNOWN, None, None,
         20_000_000, 30_000_000, "VND", [], date(2026, 8, 1), None),   # unknown seniority, no category
]


class FakeJobRepository(JobRepository):
    def __init__(self, rows: list[JobItem] | None = None):
        self._rows = rows if rows is not None else SAMPLE

    def as_of(self) -> datetime:
        return _AS_OF

    def _sort_key(self, req: SearchRequest):
        s = req.sort
        if s == SortOption.SALARY_MIN_ASC:
            return lambda r: [r.salary_min_vnd_month or 0, r.job_id]
        if s == SortOption.EXPERIENCE_ASC:
            return lambda r: [r.experience_min_years or 0, r.job_id]
        if s == SortOption.POSTED_DESC:
            return lambda r: [-r.effective_posted_date.toordinal(), r.job_id]
        return lambda r: [-(r.salary_max_vnd_month or 0), r.job_id]   # SALARY_MAX_DESC

    def search(self, req: SearchRequest, cursor: dict | None) -> SearchResult:
        f = req.filters
        rows = list(self._rows)

        rows = [r for r in rows if r.effective_posted_date >= f.posted_after]   # posted_after BẮT BUỘC
        if f.posted_before is not None:
            rows = [r for r in rows if r.effective_posted_date <= f.posted_before]
        if f.source is not None:
            rows = [r for r in rows if r.source == f.source]
        if f.seniority is not None:
            rows = [r for r in rows if r.seniority == f.seniority]
        if f.category is not None:
            rows = [r for r in rows if any(c.category_key == f.category for c in r.categories)]
        if f.salary_min is not None:
            rows = [r for r in rows if r.salary_max_vnd_month is not None and r.salary_max_vnd_month >= f.salary_min]
        if f.experience_max is not None:
            rows = [r for r in rows if r.experience_min_years is not None and r.experience_min_years <= f.experience_max]

        total = len(rows)
        key = self._sort_key(req)
        rows.sort(key=key)
        if cursor:
            last = cursor.get("k")
            rows = [r for r in rows if key(r) > last]   # keyset: chỉ lấy sau con trỏ

        page = rows[: req.limit]
        next_cursor = {"k": key(page[-1])} if len(rows) > req.limit else None
        return SearchResult(items=page, next_cursor=next_cursor, total_estimated=total, as_of=_AS_OF)
