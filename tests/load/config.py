"""LOAD-TEST — cấu hình chạy: env, scenario/phase, spawn rate, SLA matrix, đường report.

KHÔNG import Locust ở đây để `dryrun.py` / test đơn vị có thể dùng lại. Đọc theo plan
docs/load-test-plan.md (mục Load profile + Ma trận nghiệm thu).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import NoReturn


def invalid_test(message: str) -> NoReturn:
    """Kết thúc với exit code 3 (INVALID_TEST) — KHÔNG dùng SystemExit(str) vì Python trả code 1."""
    print(f"[INVALID_TEST] {message}", file=sys.stderr)
    raise SystemExit(3)


class InvalidTestArgumentParser(argparse.ArgumentParser):
    """Chuẩn hoá lỗi CLI của công cụ load-test thành INVALID_TEST=3 (không phải argparse=2)."""

    def error(self, message: str) -> NoReturn:
        invalid_test(message)


# ── Endpoint canonical names (khớp tên dùng trong report) ─────────────────────
EP_HEALTH = "GET /health"
EP_METADATA = "GET /v1/metadata"
EP_SEARCH = "POST /v1/jobs/search"
EP_MARKET = "GET /v1/market/metrics"

# ── Ngưỡng SLA p95/p99 theo endpoint (ms). None = không áp dụng. ───────────────
SLA_MS: dict[str, dict[str, float | None]] = {
    EP_HEALTH: {"p95": 300, "p99": None},
    EP_METADATA: {"p95": 500, "p99": None},
    EP_SEARCH: {"p95": 2000, "p99": 5000},
    EP_MARKET: {"p95": 2000, "p99": 5000},
}

# Ngưỡng số mẫu tối thiểu để KẾT LUẬN một percentile (success/endpoint/phase).
MIN_SAMPLES_P95 = 100
MIN_SAMPLES_P99 = 1000

# Ngưỡng error nghiệm thu + abort.
MAX_UNEXPECTED_ERROR_RATE = 0.01     # < 1% cuối cùng
ABORT_ERROR_RATE_60S = 0.05          # > 5% trong 60s → abort
ABORT_P95_MS_60S = 10_000            # p95 > 10s trong 60s → abort
ROLLING_WINDOW_S = 60

# HTTP timeout (connect, read) giây — phân biệt read-timeout với 504 server.
HTTP_TIMEOUT = (5, 25)
MAX_REQUEST_DURATION_MS = 25_000

# Tỉ lệ pagination (đo trên HTTP request THỰC) + tolerance hợp lệ.
NEXT_PAGE_PROB = 0.20
NEXT_PAGE_TOLERANCE = (0.15, 0.25)
SEARCH_LIMIT = 20  # đủ nhỏ để chắc có next_page_token trên dataset thật


def cache_mode_error(scenario: str, cache_backend: str, bq_cache: str) -> str | None:
    """Trả yêu cầu cấu hình nếu cache mode không khớp ý nghĩa scenario nghiệm thu."""
    cache = cache_backend.lower()
    bqc = bq_cache.lower()
    if scenario == "bq_cold_search" and bqc != "false":
        return "JOBS_API_BQ_USE_QUERY_CACHE=false"
    if scenario == "market_coldmiss" and not (bqc == "false" and cache == "none"):
        return "JOBS_API_BQ_USE_QUERY_CACHE=false VÀ JOBS_API_CACHE_BACKEND=none"
    if scenario in {"realistic", "spike_recovery", "ops_baseline"} and not (
        bqc == "true" and cache != "none"
    ):
        return "JOBS_API_BQ_USE_QUERY_CACHE=true và JOBS_API_CACHE_BACKEND≠none"
    return None


# ── Chính sách nghiệm thu percentile theo pha ─────────────────────────────────
# p95/p99: "required" | "if_enough" | "report" | "na"
@dataclass(frozen=True)
class PhasePolicy:
    p95: str
    p99: str
    error_required: bool
    is_reference: bool = False   # pre-spike reference (phải đạt SLA để có baseline ổn định)
    is_recovery: bool = False    # so với reference_p95 × 1.2 VÀ SLA


PHASE_POLICY: dict[str, PhasePolicy] = {
    "warmup": PhasePolicy("na", "na", error_required=False),          # mẫu bị loại
    "ops_baseline": PhasePolicy("required", "na", True),
    "baseline": PhasePolicy("required", "report", True),
    "step1": PhasePolicy("required", "if_enough", True),
    "step2": PhasePolicy("required", "required", True),
    "step3": PhasePolicy("required", "required", True),
    "reference1": PhasePolicy("required", "na", True, is_reference=True),
    "reference2": PhasePolicy("required", "na", True, is_reference=True),
    "ramp": PhasePolicy("report", "report", True),
    "hold": PhasePolicy("report", "report", True),
    "down": PhasePolicy("report", "report", True),
    "recovery1": PhasePolicy("required", "na", True, is_recovery=True),
    "recovery2": PhasePolicy("required", "na", True, is_recovery=True),
    "market_cold": PhasePolicy("report", "na", True),
}

# Pha có coi 429 là abort NGAY (pha "năng lực").
CAPACITY_PHASES = {"step1", "step2", "step3", "reference1", "reference2", "ramp", "hold", "down",
                   "recovery1", "recovery2"}


# ── Định nghĩa 1 pha trong shape ──────────────────────────────────────────────
@dataclass(frozen=True)
class Phase:
    name: str
    users: int
    duration_s: int
    spawn_rate: float
    # gate=True: duration chỉ bắt đầu đếm KHI đã đủ user mục tiêu (pha đo steady: baseline/step/
    # reference/recovery/hold). gate=False: pha chuyển tiếp (ramp/down) — không đợi, duration = thời gian ramp.
    gate: bool = True


# ── Scenario = danh sách pha + trọng số endpoint ──────────────────────────────
@dataclass(frozen=True)
class Scenario:
    name: str
    phases: list[Phase]
    # trọng số chọn endpoint cho user "mix" (search/market). ops_baseline dùng riêng.
    weight_search: float = 0.65
    weight_market: float = 0.35
    ops_only: bool = False   # chỉ /health + /metadata


_WARMUP = Phase("warmup", 2, 120, 2)

SCENARIOS: dict[str, Scenario] = {
    "realistic": Scenario("realistic", [
        _WARMUP,
        Phase("baseline", 2, 180, 2),
        Phase("step1", 10, 300, 5),
        Phase("step2", 20, 300, 5),
        Phase("step3", 40, 300, 5),
    ]),
    "bq_cold_search": Scenario("bq_cold_search", [
        _WARMUP,
        Phase("baseline", 2, 180, 2),
        Phase("step1", 10, 300, 5),
        Phase("step2", 20, 300, 5),
    ]),
    "spike_recovery": Scenario("spike_recovery", [
        _WARMUP,
        Phase("reference1", 10, 60, 10),
        Phase("reference2", 10, 60, 10),
        Phase("ramp", 100, 10, 9, gate=False),
        Phase("hold", 100, 60, 9),
        Phase("down", 10, 10, 9, gate=False),
        Phase("recovery1", 10, 60, 10),
        Phase("recovery2", 10, 60, 10),
    ]),
    "market_coldmiss": Scenario("market_coldmiss", [
        _WARMUP,
        Phase("market_cold", 5, 180, 5),
    ], weight_search=0.0, weight_market=1.0),
    "ops_baseline": Scenario("ops_baseline", [
        _WARMUP,
        Phase("ops_baseline", 5, 180, 5),   # ≥100 request/endpoint với 2 endpoint
    ], ops_only=True),
    # Dùng để VALIDATE script trên DuckDB local (miễn phí) — không phải cấu hình nghiệm thu.
    "smoke": Scenario("smoke", [
        Phase("warmup", 2, 4, 2),
        Phase("baseline", 3, 6, 3),
        Phase("step1", 5, 6, 5),
    ]),
}


@dataclass
class RunConfig:
    base_url: str
    api_key: str
    auth_bearer: str
    run_id: str
    scenario: str
    load_seed: int
    report_prefix: str
    # metadata tái lập (điền dần khi chạy)
    extra: dict = field(default_factory=dict)

    @property
    def scenario_def(self) -> Scenario:
        return SCENARIOS[self.scenario]


def _require(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        invalid_test(f"thiếu biến môi trường bắt buộc: {name}")
    return v


def load_run_config() -> RunConfig:
    scenario = os.environ.get("LOAD_SCENARIO", "realistic").strip()
    if scenario not in SCENARIOS:
        invalid_test(f"LOAD_SCENARIO='{scenario}' không hợp lệ. Chọn: {', '.join(SCENARIOS)}")
    run_id = os.environ.get("RUN_ID", "").strip()
    if scenario != "smoke" and not run_id:
        invalid_test("RUN_ID bắt buộc và phải duy nhất cho scenario nghiệm thu")
    run_id = run_id or "local"
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", run_id):
        invalid_test("RUN_ID chỉ gồm a-z, 0-9, '_' hoặc '-', dài 1–63 ký tự "
                     "(để ánh xạ duy nhất sang BigQuery job label)")
    report_prefix = os.environ.get("REPORT_PREFIX", "").strip() or f"reports/{run_id}-{scenario}/run"
    base_url = _require("BASE_URL").rstrip("/")
    if not re.fullmatch(r"https?://[^\s/]+(?:/.*)?", base_url):
        invalid_test("BASE_URL phải là URL http(s) tuyệt đối")
    try:
        load_seed = int(os.environ.get("LOAD_SEED", "1234"))
    except ValueError:
        invalid_test("LOAD_SEED phải là số nguyên")
    return RunConfig(
        base_url=base_url,
        api_key=os.environ.get("API_KEY", "").strip(),  # trống hợp lệ khi DuckDB local seed key
        auth_bearer=os.environ.get("AUTH_BEARER", "").strip(),
        run_id=run_id,
        scenario=scenario,
        load_seed=load_seed,
        report_prefix=report_prefix,
    )


def theoretical_request_max(scenario: Scenario, pacing_interval: float = 1.0) -> int:
    """CẬN TRÊN số request (1 request/iteration) để đặt max_requests (safety cap).

    Với shape có gating, wall-time mỗi pha = thời gian ramp (≈ users/spawn_rate) + duration → phát
    NHIỀU hơn VU×duration. Để cap không bắn nhầm, tính cận trên: coi FULL users hoạt động suốt cả
    wall-time (gồm ramp). Đã gồm warmup."""
    total = 0.0
    for p in scenario.phases:
        ramp = (p.users / p.spawn_rate) if (p.gate and p.spawn_rate) else 0.0
        wall = p.duration_s + ramp
        total += p.users * wall / pacing_interval
    return int(total)
