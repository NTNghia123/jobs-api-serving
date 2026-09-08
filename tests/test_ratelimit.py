"""Unit test token bucket — dùng đồng hồ GIẢ để kiểm soát thời gian.

[FILE MỚI - TUẦN 6]  ·  Hướng dẫn §6
"""
from __future__ import annotations

from app.infrastructure.ratelimit.memory import InMemoryRateLimiter


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_cho_phep_toi_suc_chua_roi_chan():
    clock = FakeClock()
    # 1 token/giây, chứa tối đa 3 → 3 request đầu qua, cái thứ 4 bị chặn.
    rl = InMemoryRateLimiter(rate_per_sec=1.0, capacity=3, now=clock)
    assert [rl.allow("c1") for _ in range(4)] == [True, True, True, False]


def test_token_hoi_lai_theo_thoi_gian():
    clock = FakeClock()
    rl = InMemoryRateLimiter(rate_per_sec=1.0, capacity=1, now=clock)
    assert rl.allow("c1") is True     # tốn token duy nhất
    assert rl.allow("c1") is False    # hết token
    clock.t = 1.0                     # 1 giây trôi qua → hồi 1 token
    assert rl.allow("c1") is True


def test_moi_client_mot_xo_rieng():
    clock = FakeClock()
    rl = InMemoryRateLimiter(rate_per_sec=1.0, capacity=1, now=clock)
    assert rl.allow("c1") is True
    assert rl.allow("c2") is True     # client khác không bị ảnh hưởng
    assert rl.allow("c1") is False
