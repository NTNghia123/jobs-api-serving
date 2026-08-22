"""Repository giả lập — dữ liệu mẫu MÔ PHỎNG hình dạng thật của job_post × company.

Ở Tuần 3, cái này được thay bằng đường ELT MySQL → DuckDB. Dữ liệu mẫu ở đây
cố ý giống DB thật: công ty nước ngoài, lương là số nguyên (không đơn vị tiền tệ),
số năm kinh nghiệm 1..5, seniority SUY RA từ số năm. Tiêu đề để giống dữ liệu
seed (cụt, kiểu Lorem) để nhắc rằng đây chưa phải dữ liệu sạch.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.enums import Seniority, SortOption
from app.models.jobs import JobItem, SearchRequest
from app.warehouse.base import JobRepository, SearchResult

# Độ tươi = thời điểm ELT chạy (giả lập).
_AS_OF = datetime(2025, 8, 17, 0, 0, tzinfo=timezone.utc)


def _seniority_from_years(y: int) -> Seniority:
    """Quy tắc chuẩn hoá — CHỐT MỘT LẦN ở đây (Tuần 3 chuyển vào transform)."""
    if y <= 1:
        return Seniority.JUNIOR
    if y <= 3:
        return Seniority.MID
    return Seniority.SENIOR


def _job(job_id, title, company, country, years, smin, smax, qual):
    return JobItem(
        job_id=job_id,
        title=title,
        company_name=company,
        country=country,
        seniority=_seniority_from_years(years),
        years_exp=years,
        salary_min=smin,
        salary_max=smax,
        qualification=qual,
        url=f"/view-job-post.php?id={job_id}",
    )


SAMPLE: list[JobItem] = [
    _job(1, "elit, a feugiat tellus lorem", "Vestibulum Ltd", "Bulgaria", 3, 43334, 76458, "aliquam eros"),
    _job(2, "orci. Donec nibh.", "Proin Limited", "Andorra", 5, 26784, 70971, "tristique pharetra."),
    _job(3, "mauris erat eget ipsum.", "Curabitur Inc", "Austria", 1, 38506, 68594, "et ultrices"),
    _job(4, "dolor quam, elementum at,", "Integer Co", "Belgium", 5, 22759, 77659, "Suspendisse ac"),
    _job(5, "justo. Proin non", "Aliquam LLC", "Croatia", 1, 39160, 78868, "elit, pharetra"),
    _job(6, "Nunc sollicitudin commodo", "Nunc Group", "Denmark", 4, 39918, 78808, "nibh enim,"),
    _job(7, "Aliquam nec enim. Nunc ut", "Nunc Group", "Denmark", 1, 28080, 62841, "non, vestibulum"),
    _job(8, "sodales nisi magna sed dui.", "Sodales SA", "Estonia", 1, 35591, 76965, "leo. Cras"),
    _job(9, "felis, adipiscing fringilla", "Felis Ltd", "Finland", 2, 33114, 60830, "gravida mauris"),
    _job(10, "nulla. Integer urna.", "Integer Co", "Belgium", 5, 29334, 63711, "In ornare"),
]


class FakeJobRepository(JobRepository):
    def __init__(self, rows: list[JobItem] | None = None):
        self._rows = rows if rows is not None else SAMPLE

    def as_of(self) -> datetime:
        return _AS_OF

    def search(self, req: SearchRequest, cursor: dict | None) -> SearchResult:
        f = req.filters
        rows = list(self._rows)
        if f.seniority:
            rows = [r for r in rows if r.seniority == f.seniority]
        if f.experience_max is not None:
            rows = [r for r in rows if r.years_exp <= f.experience_max]
        if f.salary_min is not None:
            rows = [r for r in rows if r.salary_max is not None and r.salary_max >= f.salary_min]
        if f.country:
            rows = [r for r in rows if (r.country or "").lower() == f.country.lower()]

        total = len(rows)

        # Khoá sắp xếp LUÔN kèm job_id để thứ tự tất định (bắt buộc cho keyset).
        if req.sort == SortOption.SALARY_MIN_ASC:
            key = lambda r: [r.salary_min or 0, r.job_id]           # noqa: E731
        elif req.sort == SortOption.EXPERIENCE_ASC:
            key = lambda r: [r.years_exp, r.job_id]                 # noqa: E731
        else:  # SALARY_MAX_DESC
            key = lambda r: [-(r.salary_max or 0), r.job_id]        # noqa: E731

        rows.sort(key=key)
        if cursor:
            last = cursor.get("k")
            rows = [r for r in rows if key(r) > last]

        page = rows[: req.limit]
        next_cursor = {"k": key(page[-1])} if len(rows) > req.limit else None
        return SearchResult(items=page, next_cursor=next_cursor, total_estimated=total, as_of=_AS_OF)
