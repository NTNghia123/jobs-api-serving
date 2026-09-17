"""LOAD-TEST — LoadTestShape chọn theo LOAD_SCENARIO.

CHỈ MỘT concrete shape được định nghĩa (Locust cấm nhiều LoadTestShape cùng lúc). Vỏ mỏng bọc
PhasePlanner (thuần, tests/load/planner.py) + cập nhật PHASE cho collector/handler.
"""
from __future__ import annotations

from locust import LoadTestShape

from tests.load import config as C
from tests.load.metrics import PHASE
from tests.load.planner import PhasePlanner


class ScenarioShape(LoadTestShape):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._planner = PhasePlanner(C.load_run_config().scenario_def.phases)

    def tick(self):
        res = self._planner.step(self.get_run_time(), self.get_current_user_count())
        if res is None:
            PHASE.set("_safety_timeout" if self._planner.timed_out else None)
            return None
        phase_name, users, spawn_rate = res
        PHASE.set(phase_name)
        return (users, spawn_rate)
