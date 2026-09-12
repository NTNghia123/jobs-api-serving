"""Parse ngày đăng/hạn nộp — hai parse-status ĐỘC LẬP (ADR-019).

Định dạng nguồn (đã khảo sát scraper):
  - TopDev  posted = `published.date`, deadline = `expires.date` (thường 'YYYY-MM-DD').
  - VNW     posted = `approvedOn` ISO-8601 có offset (vd '2026-08-26T23:59:59+07:00'),
            deadline đã chuẩn hoá 'DD/MM/YYYY'.

Việt Nam luôn +07:00 (không DST) → dùng offset cố định, không cần `tzdata`.
`effective_posted_date` = DATE(posted_at) theo giờ VN; thiếu/lỗi → fallback
DATE(first_seen_at); cả hai hỏng → INVALID (mapper sẽ quarantine).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from app.elt.serving.silver import DeadlineDateParseStatus, PostedDateParseStatus

VN_TZ = timezone(timedelta(hours=7))  # Asia/Ho_Chi_Minh, cố định (không DST)

# Các định dạng không-ISO hay gặp (ISO thử trước bằng fromisoformat).
_FALLBACK_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d")


def _ensure_aware(dt: datetime) -> datetime:
    """Gắn UTC cho datetime naive (pymongo trả BSON Date dạng naive-UTC)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def parse_datetime_flexible(raw: object) -> datetime | None:
    """Chuỗi ngày/giờ bất kỳ → datetime tz-aware (giả định giờ VN nếu không có offset).

    Trả None nếu không phải chuỗi parse được.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None

    # 1) ISO-8601 (có/không offset, có/không phần giờ). fromisoformat chịu 'Z' từ 3.11+.
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=VN_TZ)
    except ValueError:
        pass

    # 2) các định dạng phi-ISO (date-only) → coi là 00:00 giờ VN.
    for fmt in _FALLBACK_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=VN_TZ)
        except ValueError:
            continue
    return None


def parse_posted_date(
    posted_raw: object,
    first_seen_at: datetime | None,
) -> tuple[datetime | None, date | None, PostedDateParseStatus]:
    """→ (posted_at, effective_posted_date, status).

    - parse OK → (posted_at tz-aware, DATE theo giờ VN, PARSED)
    - posted lỗi/thiếu, first_seen hợp lệ → (None, DATE(first_seen) giờ VN, FALLBACK_FIRST_SEEN)
    - cả hai hỏng → (None, None, INVALID)  → mapper quarantine
    """
    dt = parse_datetime_flexible(posted_raw)
    if dt is not None:
        return dt, dt.astimezone(VN_TZ).date(), PostedDateParseStatus.PARSED

    if first_seen_at is not None:
        eff = _ensure_aware(first_seen_at).astimezone(VN_TZ).date()
        return None, eff, PostedDateParseStatus.FALLBACK_FIRST_SEEN

    return None, None, PostedDateParseStatus.INVALID


def parse_deadline_date(
    deadline_raw: object,
) -> tuple[date | None, DeadlineDateParseStatus]:
    """→ (deadline_date, status). Thiếu → MISSING; có nhưng lỗi → INVALID (đều null, KHÔNG quarantine)."""
    if deadline_raw is None or (isinstance(deadline_raw, str) and not deadline_raw.strip()):
        return None, DeadlineDateParseStatus.MISSING
    dt = parse_datetime_flexible(deadline_raw)
    if dt is None:
        return None, DeadlineDateParseStatus.INVALID
    return dt.astimezone(VN_TZ).date(), DeadlineDateParseStatus.PARSED
