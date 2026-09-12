"""Sanitized Mongo-like RAW fixtures cho ELT serving (Phase 2).

Mô phỏng HÌNH DẠNG THẬT của `jobs` × `job_details` (đã khảo sát scraper + dữ liệu),
KHÔNG chứa dữ liệu thật (company/id đều bịa). Phủ edge case mà mapper/quality-check
phải xử lý đúng: thiếu detail, negotiable, VND/USD, USD gắn-nhầm '$...tr', one-sided,
experience range, ngày invalid, thiếu title, multi-category + trùng key, lương rác.

Dùng builder để test dựng biến thể; các hằng *_JOB/_DETAIL là kịch bản đặt sẵn.
"""
from __future__ import annotations

from datetime import UTC, datetime


def job(
    source: str,
    external_id: str | None,
    *,
    title: str | None = "Kỹ sư phần mềm",
    posted_at: str | None = "2026-08-20",
    first_seen: datetime | None = datetime(2026, 8, 21, 2, 0, tzinfo=UTC),
    last_seen: datetime | None = datetime(2026, 8, 21, 2, 0, tzinfo=UTC),
    detail_url: str | None = "https://example.test/job",
    raw_list: dict | None = None,
) -> dict:
    """Dựng một document `jobs` (list record)."""
    return {
        "platformId": source,
        "externalId": external_id,
        "title": title,
        "postedAt": posted_at,
        "firstSeenAt": first_seen,
        "lastSeenAt": last_seen,
        "detailUrl": detail_url,
        "rawListData": raw_list,
    }


def detail(
    source: str,
    external_id: str,
    *,
    status: str = "completed",
    title: str | None = "Kỹ sư phần mềm",
    salary: dict | None = None,
    experience: str | None = "1 - 3 năm",
    deadline: str | None = "30/09/2026",
    categories: list[dict] | None = None,
    level: str | None = "Chuyên viên",
    address: str | None = "Hà Nội",
    source_url: str | None = "https://example.test/job/detail",
    company: str | None = "Công ty ABC",
) -> dict:
    """Dựng một document `job_details`. `company` đặt vào raw đúng theo nguồn."""
    if source == "topdev":
        raw = {"company_detail": {"display_name": company}} if company else {}
    else:
        raw = {"companyName": company} if company else {}
    return {
        "platformId": source,
        "externalId": external_id,
        "status": status,
        "title": title,
        "salary": salary,
        "experience": experience,
        "deadline": deadline,
        "categories": categories if categories is not None else [],
        "commonInfo": {"level": level},
        "companyInfo": {"address": None},
        "address": address,
        "sourceUrl": source_url,
        "raw": raw,
    }


# --- category helpers (shape JobCategory của scraper) ---
def cat(key: str, name: str, group: str, l1: str | None = None, l2: str | None = None) -> dict:
    return {"key": key, "name": name, "code": f"j{key}", "group": group,
            "level1Id": l1, "level2Id": l2, "level3Id": None}


# --- salary helpers (shape JobSalary của scraper) ---
def sal(raw: str, mn, mx, currency) -> dict:
    return {"raw": raw, "min": mn, "max": mx, "currency": currency, "unit": "month", "rangeCode": None}


NEGOTIABLE = {"raw": "Thương lượng", "min": None, "max": None,
              "currency": None, "unit": None, "rangeCode": None}


# --------------------------------------------------------------------------- #
# Kịch bản đặt sẵn: (job_doc, detail_doc) — detail_doc=None nghĩa là thiếu detail #
# --------------------------------------------------------------------------- #

# TopDev đầy đủ: VND triệu, multi-category + TRÙNG key, seniority 'Chuyên viên'→mid.
TOPDEV_FULL = (
    job("topdev", "td1001", title="Backend Engineer", posted_at="2026-08-20"),
    detail(
        "topdev", "td1001", title="Backend Engineer",
        salary=sal("10 - 58 triệu", 10, 58, "VND"),
        experience="2 - 4 năm", level="Chuyên viên",
        categories=[cat("16", "Kinh doanh", "g14~j16", "14", "16"),
                    cat("22", "Sales", "g14~j22", "14", "22"),
                    cat("22", "Sales", "g14~j22", "14", "22")],  # trùng → dedupe
        company="Công ty ABC",
    ),
)

# VNW đầy đủ: USD THẬT ('$' không 'tr'), seniority 'Trưởng phòng'→senior, exp 1 số.
VNW_FULL = (
    job("vietnamworks", "vw2001", title="Sales Manager", posted_at="2026-08-13T17:08:15+07:00"),
    detail(
        "vietnamworks", "vw2001", title="Sales Manager",
        salary=sal("$ 500-1,000 /tháng", 500, 1000, "USD"),
        experience="5", deadline="17/09/2026", level="Trưởng phòng",
        categories=[cat("149", "Bán hàng", "g26~j149", "26", "149")],
        company="Công ty XYZ",
    ),
)

# VNW USD gắn-nhầm '$...tr' → phải hiểu là triệu VND.
VNW_USD_MISLABEL = (
    job("vietnamworks", "vw2002"),
    detail("vietnamworks", "vw2002",
           salary=sal("$ 40tr-70tr /tháng", 40000000, 70000000, "USD")),
)

# Lương rác magnitude ('12,000-24,000 ₫' = 12–24 tỷ) → invalid.
VNW_SALARY_GARBAGE = (
    job("vietnamworks", "vw2003"),
    detail("vietnamworks", "vw2003",
           salary=sal("12,000-24,000 ₫/tháng", 12000, 24000, "VND")),
)

# Negotiable.
TOPDEV_NEGOTIABLE = (
    job("topdev", "td1002"),
    detail("topdev", "td1002", salary=NEGOTIABLE),
)

# One-sided 'Tới X triệu' → chỉ max.
TOPDEV_ONE_SIDED = (
    job("topdev", "td1003"),
    detail("topdev", "td1003", salary=sal("Tới 30 triệu", None, 30, "VND")),
)

# --- các case → QUARANTINE ---
MISSING_DETAIL = (job("topdev", "td9001"), None)
DETAIL_PENDING = (job("topdev", "td9002"), detail("topdev", "td9002", status="pending"))
MISSING_TITLE = (
    job("topdev", "td9003", title=None),
    detail("topdev", "td9003", title=None),
)
MISSING_ID = (job("topdev", None), detail("topdev", "x", status="completed"))
INVALID_DATE = (
    job("topdev", "td9004", posted_at="rác-không-parse-được", first_seen=None),
    detail("topdev", "td9004"),
)

# Gom kịch bản "được phục vụ" (→ SilverRow) cho test reconciliation.
# Lưu ý: VNW_SALARY_GARBAGE VẪN là SilverRow (chỉ salary_normalization_status=invalid),
# KHÔNG quarantine — lương hỏng không loại job khỏi corpus.
SERVED = [TOPDEV_FULL, VNW_FULL, VNW_USD_MISLABEL, TOPDEV_NEGOTIABLE, TOPDEV_ONE_SIDED,
          VNW_SALARY_GARBAGE]
QUARANTINED = [MISSING_DETAIL, DETAIL_PENDING, MISSING_TITLE, MISSING_ID, INVALID_DATE]
