"""Extract từ MongoDB `job_crawler`: jobs LEFT JOIN job_details (ADR-019).

- CHỈ 2 nguồn trong phạm vi: platformId ∈ {topdev, vietnamworks} (itviec/topcv... ngoài phạm vi).
- LEFT JOIN theo (platformId, externalId) — giữ cả job chưa có detail (mapper sẽ quarantine).
- Đếm Mongo THỰC TẾ per-source cùng lần extract (mẫu số reconciliation — KHÔNG dùng count crawl).
- Fallback category: resolve job_categories theo (platformId, categoryExternalId), mapper chỉ
  dùng khi detail.categories rỗng.

Cấu hình qua env (tách khỏi Settings API): JOBS_MONGO_URI, JOBS_MONGO_DATABASE.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from pymongo import MongoClient

SOURCES = ("topdev", "vietnamworks")

# (job_doc, detail_doc|None, list_category|None)
ExtractedRecord = tuple[dict, dict | None, dict | None]


@dataclass(frozen=True)
class MongoConfig:
    uri: str
    database: str = "job_crawler"

    @classmethod
    def from_env(cls) -> MongoConfig:
        uri = os.environ.get("JOBS_MONGO_URI", "").strip()
        if not uri:
            raise ValueError("Thiếu JOBS_MONGO_URI (chuỗi kết nối MongoDB nguồn).")
        return cls(uri=uri, database=os.environ.get("JOBS_MONGO_DATABASE", "job_crawler"))


@dataclass
class ExtractResult:
    records: list[ExtractedRecord] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)  # per-source (mẫu số reconciliation)


def extract(db, sources: tuple[str, ...] = SOURCES) -> ExtractResult:
    """Đọc jobs (2 nguồn) + join detail + fallback category. Trả records + counts per-source."""
    src_filter = {"platformId": {"$in": list(sources)}}

    detail_index: dict[tuple[str, str], dict] = {
        (d["platformId"], d["externalId"]): d
        for d in db["job_details"].find(src_filter)
    }
    category_index: dict[tuple[str, str], dict] = {
        (c["platformId"], c["externalId"]): c
        for c in db["job_categories"].find(src_filter)
    }

    result = ExtractResult(counts=dict.fromkeys(sources, 0))
    for job in db["jobs"].find(src_filter):
        pid, eid = job.get("platformId"), job.get("externalId")
        detail = detail_index.get((pid, eid))
        list_cat = category_index.get((pid, job.get("categoryExternalId")))
        result.records.append((job, detail, list_cat))
        result.counts[pid] = result.counts.get(pid, 0) + 1
    return result


def extract_from_uri(config: MongoConfig, sources: tuple[str, ...] = SOURCES) -> ExtractResult:
    """Mở kết nối từ config rồi extract (đóng client sau)."""
    client = MongoClient(config.uri, serverSelectionTimeoutMS=10000)
    try:
        return extract(client[config.database], sources)
    finally:
        client.close()
