"""LOAD-TEST — máy trạng thái pha (THUẦN, KHÔNG import locust).

Tách khỏi shapes.py để unit-test được mà không kéo `import locust` (locust gọi gevent.monkey.patch_all()
lúc import → làm bẩn stdlib và phá các test dùng FastAPI TestClient). shapes.ScenarioShape chỉ là vỏ mỏng
bọc PhasePlanner + cập nhật PHASE.
"""
from __future__ import annotations

from tests.load import config as C


class PhasePlanner:
    """Quyết định (phase_name, users, spawn_rate) theo run-time + số user hiện tại.

    - gate=True: đồng hồ pha CHỈ chạy khi cur == target (cả scale-up lẫn scale-down); trong lúc chờ
      trả nhãn "_transition" (mẫu bị loại) và tiếp tục drive user về target.
    - gate=False (ramp/down): pha chuyển tiếp, đo ngay, duration = thời gian ramp.
    """

    def __init__(self, phases: list[C.Phase]):
        self._phases = phases
        self._idx = 0
        self._full_since: float | None = None
        self._max_wall_s = sum(p.duration_s for p in phases) + 120
        self._timed_out = False

    @property
    def idx(self) -> int:
        return self._idx

    @property
    def full_since(self) -> float | None:
        return self._full_since

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    def step(self, now: float, cur: int) -> tuple[str, int, float] | None:
        if self._idx >= len(self._phases):
            return None
        # Safety độc lập request: nếu user không spawn được thì MetricsCollector không có request để
        # gọi check_abort(). Shape vẫn phải tự dừng, tránh treo/chạy vô hạn và tuyệt đối không PASS giả.
        if now > self._max_wall_s:
            self._timed_out = True
            return None
        ph = self._phases[self._idx]
        if ph.gate and cur != ph.users:
            self._full_since = None
            return ("_transition", ph.users, ph.spawn_rate)
        if self._full_since is None:
            self._full_since = now
        if now - self._full_since >= ph.duration_s:
            self._idx += 1
            self._full_since = None
            return self.step(now, cur)   # đánh giá ngay pha kế
        return (ph.name, ph.users, ph.spawn_rate)
