"""Chuẩn hoá lương: JobSalary (Mongo) → cận VND/tháng cho silver.

Quy tắc ĐÃ KHOÁ (ADR-019 §salary, ADR-024 planned) — implement Ở ĐÂY, một chỗ.

**Nguồn đơn vị = chuỗi raw hiển thị, KHÔNG phải field số.** Lý do (đối chiếu dữ liệu
thật): VNW trả `salaryMin/Max` lúc là số VND tuyệt đối, lúc là số triệu — MÂU THUẪN,
và `salaryCurrency` có khi gắn nhầm ('$ 40tr-70tr' gắn USD). Nhưng `prettySalary`
LUÔN hiển thị con số theo **triệu VND** (trừ USD thật: có '$'/'USD' và KHÔNG có 'tr').
Nên parse con số từ raw rồi suy đơn vị từ chính raw là đáng tin nhất.

  - có 'tr'/'triệu' (hoặc không có dấu USD) → con số là **triệu VND** → ×1e6.
  - có '$'/'USD' và KHÔNG 'tr'/'triệu'      → **USD thật**        → ×25.500.
  - negotiable (scraper bỏ trống cả min & max) → cả hai cận null, status `negotiable`.
  - 'Từ X' → chỉ min; 'Tới/Đến X' → chỉ max; 'X - Y' → [min,max]; một số → điểm.
  - min > max / vượt TRẦN sanity (10 tỷ VND/tháng) → cả hai null, status `invalid`
    (bắt rác kiểu '12,000-24,000 ₫' = 12–24 tỷ). KHÔNG đặt sàn: map-to-triệu đã cứu
    các số bé (vd '8-15 ₫' → 8–15 triệu).

Giữ lại bản gốc (`salary_*_original` = con số hiển thị theo đơn vị đã suy, `raw`,
`currency`, `period`) để truy vết, kể cả khi `invalid`.

Hàm thuần, không I/O. Dùng chung TopDev & VietnamWorks.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from app.domain.catalog import MAX_SALARY_VND_MONTH

# --- Hệ số quy đổi (VERSIONED) — đổi tỉ giá là đổi version để batch cũ vẫn truy vết được.
USD_TO_VND = 25_500
VND_MILLION = 1_000_000
SALARY_FX_VERSION = "fx-2026-09-v1"

# Trần sanity: cận > ngưỡng này là bất khả thi (rác nguồn) → invalid. Tái dùng trần
# của catalog (cũng là trần của filter salary_min) để contract nhất quán.
SALARY_SANITY_MAX_VND = MAX_SALARY_VND_MONTH  # 10 tỷ VND/tháng

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# 'tr' / 'triệu' đánh dấu con số theo triệu VND (kể cả khi raw có '$').
_MILLION_MARK = re.compile(r"tri[eệ]u|\btr\b|\dtr", re.I)
_USD_MARK = re.compile(r"\$|\busd\b", re.I)
_FROM_MARK = re.compile(r"\btừ\b|\bfrom\b", re.I)
_UPTO_MARK = re.compile(r"\btới\b|\bđến\b|up\s*to", re.I)


class SalaryNormalizationStatus(str, Enum):
    """Trạng thái chuẩn hoá lương (silver field `salary_normalization_status`, NOT NULL)."""

    PARSED = "parsed"          # có ít nhất một cận VND hợp lệ
    NEGOTIABLE = "negotiable"  # thoả thuận / không công khai / thiếu hẳn salary
    INVALID = "invalid"        # ngoài phạm vi / bất khả thi → không dùng cận nào


@dataclass(frozen=True)
class NormalizedSalary:
    """Kết quả chuẩn hoá — khớp nhóm cột salary_* của SilverRow."""

    salary_raw: str | None
    salary_min_original: float | None   # con số hiển thị theo đơn vị đã suy (triệu VND, hoặc USD)
    salary_max_original: float | None
    salary_currency: str | None
    salary_period: str | None
    salary_min_vnd_month: int | None
    salary_max_vnd_month: int | None
    fx_rate_to_vnd: float | None
    salary_fx_version: str | None
    salary_normalization_status: SalaryNormalizationStatus


def _negotiable(raw: str | None) -> NormalizedSalary:
    return NormalizedSalary(
        salary_raw=raw, salary_min_original=None, salary_max_original=None,
        salary_currency=None, salary_period=None,
        salary_min_vnd_month=None, salary_max_vnd_month=None,
        fx_rate_to_vnd=None, salary_fx_version=None,
        salary_normalization_status=SalaryNormalizationStatus.NEGOTIABLE,
    )


def _invalid(raw, min_o, max_o, currency) -> NormalizedSalary:
    # Giữ bản gốc đã parse; chỉ cận VND null (không dùng ngầm giá trị bất khả thi).
    return NormalizedSalary(
        salary_raw=raw, salary_min_original=min_o, salary_max_original=max_o,
        salary_currency=currency, salary_period="month" if currency else None,
        salary_min_vnd_month=None, salary_max_vnd_month=None,
        fx_rate_to_vnd=None, salary_fx_version=None,
        salary_normalization_status=SalaryNormalizationStatus.INVALID,
    )


def _parse_amount(token: str) -> float:
    """'40'→40, '1,000'→1000, '10,000'→10000, '12,5'→12.5 (heuristic locale-independent)."""
    last = max(token.rfind(","), token.rfind("."))
    if last == -1:
        return float(token)
    trailing = token[last + 1:]
    if len(trailing) == 3:  # nhóm phần nghìn → bỏ hết dấu
        return float(token.replace(",", "").replace(".", ""))
    integer = token[:last].replace(",", "").replace(".", "")
    return float(f"{integer}.{trailing}")


def normalize_salary(salary: Mapping[str, object] | None) -> NormalizedSalary:
    """JobSalary của Mongo {raw, min, max, currency, unit} | None → cận VND/tháng."""
    # --- 0) không có salary object → coi như không công khai (negotiable).
    if not salary:
        return _negotiable(None)

    raw_val = salary.get("raw")
    raw = raw_val.strip() if isinstance(raw_val, str) and raw_val.strip() else None

    # --- 1) negotiable: scraper bỏ trống cả min & max khi thoả thuận (tín hiệu đáng tin).
    if salary.get("min") is None and salary.get("max") is None:
        return _negotiable(raw)

    # --- 2) con số + đơn vị suy TỪ RAW (field số của VNW không nhất quán thang đo).
    if raw is None:
        return _invalid(None, None, None, None)
    nums = [_parse_amount(m.group(0)) for m in _NUMBER.finditer(raw)]
    if not nums:
        return _negotiable(raw)

    is_usd = bool(_USD_MARK.search(raw)) and not _MILLION_MARK.search(raw)
    currency = "USD" if is_usd else "VND"

    # biên nào có mặt: theo từ khoá 'Từ'/'Tới', mặc định dải hai số / điểm.
    if _FROM_MARK.search(raw) and not _UPTO_MARK.search(raw):
        min_o, max_o = nums[0], None
    elif _UPTO_MARK.search(raw) and not _FROM_MARK.search(raw):
        min_o, max_o = None, nums[-1]
    elif len(nums) >= 2:
        min_o, max_o = nums[0], nums[1]
    else:
        min_o = max_o = nums[0]

    # --- 3) dải đảo ngược → invalid.
    if min_o is not None and max_o is not None and min_o > max_o:
        return _invalid(raw, min_o, max_o, currency)

    # --- 4) quy đổi VND/tháng; trần sanity bắt rác magnitude.
    factor = USD_TO_VND if is_usd else VND_MILLION
    fx = USD_TO_VND if is_usd else None
    min_vnd = round(min_o * factor) if min_o is not None else None
    max_vnd = round(max_o * factor) if max_o is not None else None
    if (min_vnd is not None and min_vnd > SALARY_SANITY_MAX_VND) or (
        max_vnd is not None and max_vnd > SALARY_SANITY_MAX_VND
    ):
        return _invalid(raw, min_o, max_o, currency)

    return NormalizedSalary(
        salary_raw=raw,
        salary_min_original=min_o,
        salary_max_original=max_o,
        salary_currency=currency,
        salary_period="month",
        salary_min_vnd_month=min_vnd,
        salary_max_vnd_month=max_vnd,
        fx_rate_to_vnd=fx,
        salary_fx_version=SALARY_FX_VERSION,
        salary_normalization_status=SalaryNormalizationStatus.PARSED,
    )
