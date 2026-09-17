"""LOAD-TEST — unit test máy trạng thái pha (PhasePlanner, KHÔNG import locust → không gevent).

Kiểm: (1) scale-DOWN cũng phải đợi user về đúng target trước khi vào recovery; (2) trong lúc đợi trả
nhãn "_transition" (mẫu bị loại), chỉ vào pha đo khi cur==target; (3) sang pha kế khi hết duration.
"""
from __future__ import annotations

from tests.load import config as C
from tests.load.planner import PhasePlanner


def _planner(scenario: str) -> PhasePlanner:
    return PhasePlanner(C.SCENARIOS[scenario].phases)


def _names(scenario: str) -> list[str]:
    return [p.name for p in C.SCENARIOS[scenario].phases]


def test_scale_down_waits_for_target():
    p = _planner("spike_recovery")
    p._idx = _names("spike_recovery").index("recovery1")
    # user vẫn 50 (chưa giảm về target 10) → "_transition", đồng hồ chưa chạy
    name, users, _ = p.step(100.0, 50)
    assert name == "_transition" and users == 10
    assert p.full_since is None
    # user về đúng target → mới vào recovery1 và bắt đầu đếm
    name, _, _ = p.step(101.0, 10)
    assert name == "recovery1" and p.full_since is not None


def test_scale_up_waits_for_target():
    p = _planner("spike_recovery")
    p._idx = _names("spike_recovery").index("reference1")
    assert p.step(5.0, 2)[0] == "_transition"   # chưa ramp lên 10
    assert p.step(6.0, 10)[0] == "reference1"


def test_transition_advances_when_duration_elapsed():
    p = _planner("spike_recovery")
    r1 = _names("spike_recovery").index("reference1")
    p._idx = r1
    p.step(0.0, 10)                                   # vào reference1, full_since=0
    dur = C.SCENARIOS["spike_recovery"].phases[r1].duration_s
    assert p.step(dur + 1, 10)[0] == "reference2"     # quá duration → pha kế


def test_gate_false_phase_measured_immediately():
    # ramp (gate=False): đo ngay, không cần cur==target
    p = _planner("spike_recovery")
    p._idx = _names("spike_recovery").index("ramp")
    name, users, _ = p.step(0.0, 10)   # cur=10 nhưng target=100, gate=False → vào ngay
    assert name == "ramp" and users == 100


def test_planner_stops_after_last_phase():
    p = _planner("smoke")
    total = sum(ph.duration_s for ph in C.SCENARIOS["smoke"].phases)
    p._idx = len(C.SCENARIOS["smoke"].phases)
    assert p.step(total + 999, 0) is None
    assert p.timed_out is False


def test_planner_safety_timeout_even_when_no_user_can_spawn():
    p = _planner("smoke")
    max_wall = sum(ph.duration_s for ph in C.SCENARIOS["smoke"].phases) + 120
    assert p.step(max_wall + 0.1, 0) is None
    assert p.timed_out is True
