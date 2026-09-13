"""Adapter: RateLimiter token bucket trên Redis — chia sẻ giữa NHIỀU instance (Tuần 7).

[FILE MỚI - TUẦN 7]  ·  Trả nợ ADR-013  ·  Quyết định: docs/adr/ADR-017

Cùng hợp đồng port RateLimiter (allow(client_id) -> bool) và cùng THUẬT TOÁN token bucket
với InMemoryRateLimiter, nhưng trạng thái sống trong Redis nên mọi instance dùng CHUNG một xô.

Vì sao cần Lua: token bucket là chuỗi ĐỌC-TÍNH-GHI. Nếu làm bằng nhiều lệnh Redis rời rạc,
hai request song song có thể cùng đọc rồi cùng ghi -> vượt hạn mức (race). Script Lua chạy
NGUYÊN TỬ trên server Redis nên đọc-tính-ghi là một bước không thể chen ngang.

Vì sao lấy đồng hồ bằng redis TIME: mọi instance phải cùng một nguồn thời gian; dùng đồng hồ
cục bộ của từng máy sẽ lệch nhau và tính token sai.
"""
from __future__ import annotations

import logging
import math

from app.domain.ports.rate_limiter import RateLimiter

logger = logging.getLogger("ratelimit.redis")

# Timeout CÓ CHẶN cho socket: rate limiter chạy TRƯỚC mọi /v1; network blackhole mà không có timeout
# sẽ treo request lâu hơn 25s Cloud Run. fail-open (allow) chỉ có tác dụng khi lỗi trả về NHANH.
_REDIS_TIMEOUT_S = 0.5

# KEYS[1] = khoá xô của client. ARGV = rate(token/giây), capacity, ttl(giây).
# Trả 1 nếu cho phép (đã trừ 1 token), 0 nếu hết token.

# HMGET, HMSET như method, EXPIRE là đặt TTL cho key, xoá nếu quá hạn, tạo lại khi có request mới
# __init__ đã register_script, muốn nạp args chỉ cần truyền args như function bình thường
_LUA_TOKEN_BUCKET = """
local rate = tonumber(ARGV[1])
local cap  = tonumber(ARGV[2])
local ttl  = tonumber(ARGV[3])

local t    = redis.call('TIME')
local now  = tonumber(t[1]) + tonumber(t[2]) / 1000000

local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')

local tokens = tonumber(data[1])
local last   = tonumber(data[2])
if tokens == nil then
  tokens = cap
  last = now
end
tokens = math.min(cap, tokens + (now - last) * rate)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], ttl)
return allowed
"""


class RedisRateLimiter(RateLimiter):
    def __init__(self, url: str, rate_per_sec: float, capacity: float, *, key_prefix: str = "rl:"):
        # Import TRỄ: chỉ nạp redis khi thực sự dùng backend này (dev in-memory không cần Redis).
        import redis

        self._r = redis.Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=_REDIS_TIMEOUT_S, socket_timeout=_REDIS_TIMEOUT_S,
        )
        self._rate = rate_per_sec
        self._cap = capacity
        self._prefix = key_prefix
        # TTL để khoá của client rảnh tự dọn: đủ dài để hồi từ 0 lên đầy xô, + biên an toàn.
        self._ttl = max(60, math.ceil(capacity / rate_per_sec) + 2) if rate_per_sec > 0 else 60
        self._script = self._r.register_script(_LUA_TOKEN_BUCKET)   # EVALSHA, tự nạp lại nếu cache miss

    # hàm chạy Lua script
    def allow(self, client_id: str) -> bool:
        # FAIL-OPEN (invariant plan: "rate-limiter fail-open+log"): rate-limit là lớp BẢO VỆ tốc độ,
        # KHÔNG phải nguồn sự thật. Redis chết → CHO QUA (return True) để API vẫn sống, log WARN để
        # còn thấy. Đánh đổi: trong lúc Redis down, giới hạn tốc độ tạm bị bỏ (chấp nhận cho demo).
        try:
            allowed = self._script(keys=[self._prefix + client_id], args=[self._rate, self._cap, self._ttl])
            return bool(allowed)
        except Exception as exc:  # noqa: BLE001 — cố ý nuốt MỌI lỗi để fail-open
            logger.warning("rate-limiter lỗi (fail-open → cho qua): %s", exc)
            return True
