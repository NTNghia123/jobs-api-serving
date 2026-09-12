"""Parse khoảng năm kinh nghiệm từ text nguồn → (min, max, status).

Nguồn:
  - TopDev `experiences_str`: 'Không yêu cầu kinh nghiệm', '1 - 3 năm', 'Trên 5 năm',
    'Dưới 1 năm', '5 năm'...
  - VNW label: số năm dạng chuỗi, vd '3', '0' (scraper trả String(yearsOfExperience);
    -1 = không xác định đã thành None ở scraper).

Quy ước (v1) — min là thứ API filter dùng (`experience_min_years <= experience_max`):
  - 'không yêu cầu' → (0, 0)
  - 'X - Y'         → (X, Y)
  - 'trên/từ/hơn/ít nhất X' / 'X+' → (X, None)
  - 'dưới/tới/tối đa X'            → (0, X)
  - 'X' / 'X năm' (một số)         → (X, X)
  - có text, không số, không phải 'không yêu cầu' → UNPARSED (None, None)
  - thiếu text → MISSING (None, None)
"""
from __future__ import annotations

import re

from app.elt.serving.silver import ExperienceParseStatus

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_NO_REQUIREMENT = re.compile(r"không\s*(?:yêu\s*cầu|cần|đòi\s*hỏi)|no\s+experience|not\s+required", re.I)
_LOWER_BOUND = re.compile(r"trên|từ|hơn|ít\s*nhất|tối\s*thiểu|\d+\s*\+|at\s+least|minimum|min\b", re.I)
_UPPER_BOUND = re.compile(r"dưới|tới|đến|tối\s*đa|ít\s*hơn|up\s+to|maximum|max\b|less\s+than", re.I)


def _num(token: str) -> float:
    return float(token.replace(",", "."))


def parse_experience(
    raw: object,
) -> tuple[float | None, float | None, ExperienceParseStatus]:
    if not isinstance(raw, str) or not raw.strip():
        return None, None, ExperienceParseStatus.MISSING

    text = raw.strip()

    if _NO_REQUIREMENT.search(text):
        return 0.0, 0.0, ExperienceParseStatus.PARSED

    numbers = [_num(m.group(0)) for m in _NUMBER.finditer(text)]
    if not numbers:
        # có chữ nhưng không có số (vd 'Không yêu cầu' đã bắt ở trên; còn lại là nhãn lạ)
        return None, None, ExperienceParseStatus.UNPARSED

    # khoảng tường minh: hai số → (min, max)
    if len(numbers) >= 2:
        lo, hi = numbers[0], numbers[1]
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi, ExperienceParseStatus.PARSED

    n = numbers[0]
    # một số + từ khoá biên
    if _LOWER_BOUND.search(text):
        return n, None, ExperienceParseStatus.PARSED
    if _UPPER_BOUND.search(text):
        return 0.0, n, ExperienceParseStatus.PARSED

    # một số trần trụi ('3', '5 năm') → điểm
    return n, n, ExperienceParseStatus.PARSED
