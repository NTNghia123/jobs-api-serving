"""LOAD-TEST — corpus payload CỐ ĐỊNH (tái lập được).

Vì greenlet chạy khác thứ tự nên một global seed không tái tạo hoàn toàn. Thay vào đó: một corpus
cố định + mỗi user một PRNG riêng (seed dẫn xuất từ LOAD_SEED). Hash corpus lưu trong metadata report
→ realistic và bq_cold dùng CÙNG phân phối truy vấn.
"""
from __future__ import annotations

import hashlib
import json
import random

# Giá trị hợp lệ theo app/models/enums.py.
_SENIORITY = ["junior", "mid", "senior"]
_SOURCE = ["topdev", "vietnamworks"]
_SORT = ["salary_max_desc", "salary_min_asc", "experience_asc", "posted_desc"]
# posted_after BẮT BUỘC (ADR-020) — chọn vài mốc để prune partition khác nhau.
_POSTED_AFTER = ["2026-01-01", "2026-03-01", "2026-06-01"]
_SALARY_MIN = [None, 15_000_000, 30_000_000]

# Tham số /market/metrics.
_DIMENSION = ["source", "seniority", "category"]
_WINDOW = ["90d", "all_time"]


def _build_search_corpus() -> list[dict]:
    out: list[dict] = []
    for posted in _POSTED_AFTER:
        for sen in _SENIORITY:
            for sort in _SORT:
                for smin in _SALARY_MIN:
                    filters = {"posted_after": posted, "seniority": sen}
                    if smin is not None:
                        filters["salary_min"] = smin
                    out.append({"filters": filters, "sort": sort})
    return out


def _build_market_corpus() -> list[dict]:
    return [{"dimension": d, "window": w} for d in _DIMENSION for w in _WINDOW]


SEARCH_CORPUS: list[dict] = _build_search_corpus()
MARKET_CORPUS: list[dict] = _build_market_corpus()


def corpus_hash() -> str:
    """SHA-256 (12 hex đầu) của corpus đã chuẩn hoá → lưu metadata để đối chiếu giữa các run."""
    blob = json.dumps(
        {"search": SEARCH_CORPUS, "market": MARKET_CORPUS},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


class UserCorpus:
    """PRNG riêng mỗi user (seed = load_seed ⊕ user_index) → chọn payload tất định, độc lập greenlet."""

    def __init__(self, load_seed: int, user_index: int):
        self._rng = random.Random(load_seed * 1_000_003 + user_index)

    def search_payload(self, limit: int) -> dict:
        base = self._rng.choice(SEARCH_CORPUS)
        return {**base, "filters": dict(base["filters"]), "limit": limit}

    def market_params(self) -> dict:
        return dict(self._rng.choice(MARKET_CORPUS))

    def rand(self) -> float:
        return self._rng.random()
