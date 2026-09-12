"""Chuẩn hoá cấp bậc: commonInfo.level (raw) → junior|mid|senior|None.

Căn theo phân bố THẬT trong Mongo (TopDev job_levels_str, VNW jobLevelVI):
  junior : thực tập / mới tốt nghiệp / sinh viên
  mid    : nhân viên / chuyên viên              (IC chuẩn — 2 bucket lớn nhất)
  senior : cấp cao / trưởng (phòng|nhóm) / phó (phòng|giám đốc) / giám đốc / quản lý
  None   : 'Tất cả cấp bậc', nhãn lạ, hoặc combo lẫn nhiều bậc (null nếu KHÔNG chắc, ADR-019)

KHÔNG suy cấp bậc từ title. `seniority_normalized=None` → bucket 'unknown' ở metrics.
Versioned: đổi bảng map là đổi version để batch cũ vẫn truy vết được.
"""
from __future__ import annotations

from app.models.enums import Seniority

SENIORITY_MAPPING_VERSION = "seniority-2026-09-v1"

# Thứ tự QUAN TRỌNG: xét junior rồi senior rồi mid, vì 'chuyên viên cấp cao'
# chứa cả 'cấp cao' (senior) lẫn 'chuyên viên' (mid) — senior phải thắng.
_JUNIOR_KW = ("thực tập", "mới tốt nghiệp", "sinh viên", "intern", "fresher", "fresh grad")
_SENIOR_KW = (
    "cấp cao", "trưởng phòng", "trưởng nhóm", "trưởng bộ phận", "trưởng",
    "phó phòng", "phó giám đốc", "giám đốc", "quản lý", "cấp cao hơn",
    "manager", "director", "lead", "head", "senior", "chief",
)
_MID_KW = ("nhân viên", "chuyên viên", "staff", "kỹ sư", "experienced")


def _token_bucket(token: str) -> Seniority | None:
    t = token.strip().lower()
    if not t:
        return None
    if any(kw in t for kw in _JUNIOR_KW):
        return Seniority.JUNIOR
    if any(kw in t for kw in _SENIOR_KW):
        return Seniority.SENIOR
    if any(kw in t for kw in _MID_KW):
        return Seniority.MID
    return None


def normalize_seniority(raw: object) -> tuple[str | None, str | None]:
    """→ (seniority_normalized, seniority_mapping_version).

    raw rỗng → (None, None). Combo nhiều bậc (vd 'Nhân viên, Chuyên viên cấp cao')
    → (None, version) vì không chắc chắn. Cùng một bậc ở mọi token → bậc đó.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, None

    # tách combo 'A, B' hoặc 'A / B' → tập các bucket map được
    tokens = [p for chunk in raw.split(",") for p in chunk.split("/")]
    buckets = {b for b in (_token_bucket(t) for t in tokens) if b is not None}

    normalized = buckets.pop().value if len(buckets) == 1 else None
    return normalized, SENIORITY_MAPPING_VERSION
