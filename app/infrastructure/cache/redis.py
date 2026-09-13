"""Adapter cache Redis (hiện thực CacheBackend) — chia sẻ giữa NHIỀU instance.

Trước refactor: một phần của app/domain/cache.py. Cần một Redis server đang chạy;
bật bằng JOBS_API_CACHE_BACKEND=redis. Xem docs/adr/ADR-009.
"""
from __future__ import annotations

import logging

from app.domain.ports.cache import CacheBackend

logger = logging.getLogger("cache.redis")

# Timeout CÓ CHẶN cho socket: fail-open chỉ cứu khi Redis TRẢ LỖI; một network blackhole (SYN không
# hồi) sẽ TREO tới timeout của OS (hàng chục giây) > 25s Cloud Run. Đặt nhỏ để hỏng-thì-nhanh.
_REDIS_TIMEOUT_S = 0.5


class RedisCache(CacheBackend):
    def __init__(self, url: str, ttl_seconds: int = 300):
        # Import TRỄ: chỉ nạp thư viện redis khi thực sự dùng backend này, để dev chạy
        # in-memory không cần cài/chạy Redis.
        import redis

        self._r = redis.Redis.from_url(
            url, decode_responses=True,          # get() trả str, không phải bytes
            socket_connect_timeout=_REDIS_TIMEOUT_S, socket_timeout=_REDIS_TIMEOUT_S,
        )
        self._ttl = ttl_seconds

    # FAIL-OPEN (invariant plan: "Cache fail-open"): cache chỉ để TĂNG TỐC, không phải nguồn sự
    # thật. Redis chết (mạng VPC chớp, restart) → KHÔNG được làm sập request. get lỗi → coi như
    # miss (handler sẽ tính lại từ BigQuery); set lỗi → bỏ qua. Luôn log WARN để còn nhìn thấy.
    def get(self, key: str) -> str | None:
        try:
            return self._r.get(key)
        except Exception as exc:  # noqa: BLE001 — cố ý nuốt MỌI lỗi cache để fail-open
            logger.warning("cache get lỗi (fail-open → coi như miss): %s", exc)
            return None

    def set(self, key: str, value: str) -> None:
        try:
            self._r.setex(key, self._ttl, value)   # SET kèm hạn sống (giây) trong một lệnh
        except Exception as exc:  # noqa: BLE001 — cố ý nuốt MỌI lỗi cache để fail-open
            logger.warning("cache set lỗi (fail-open → bỏ qua ghi cache): %s", exc)
