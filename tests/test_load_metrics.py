"""LOAD-TEST — unit test cho MetricsCollector.evaluate() (logic nghiệm thu khó test bằng live run).

Phủ: recovery pass/fail (so reference_p95×1.2 & SLA), 429 pha năng lực, error-rate >1%, p99 bắt buộc
thiếu mẫu → exit 3, ma trận theo pha.
"""
from __future__ import annotations

import json

from tests.load import config as C
from tests.load.metrics import (
    EXIT_INSUFFICIENT,
    EXIT_PASS,
    EXIT_SLA_FAIL,
    MetricsCollector,
    percentile,
)


def _cfg(scenario: str = "realistic"):
    return C.RunConfig(
        base_url="http://x", api_key="k", auth_bearer="", run_id="t",
        scenario=scenario, load_seed=1, report_prefix="unused",
    )


def _mc(expected=frozenset(), scenario: str = "realistic") -> MetricsCollector:
    # mặc định expected=∅ để cô lập từng nhánh; test ma trận kỳ vọng truyền set cụ thể.
    return MetricsCollector(_cfg(scenario), expected_keys=set(expected))


def _fill(mc: MetricsCollector, phase: str, ep: str, n: int, rt: float,
          ok: bool = True, err: str | None = None, page_kind=None):
    for _ in range(n):
        mc.record(ep, phase, rt, ok, err, page_kind)


def test_percentile_nearest_rank():
    assert percentile([], 95) is None
    assert percentile([1, 2, 3, 4, 5], 100) == 5
    assert percentile([10, 20, 30, 40], 50) == 20


def test_recovery_pass():
    mc = _mc()
    ep = C.EP_SEARCH
    _fill(mc, "reference1", ep, 120, 100)
    _fill(mc, "reference2", ep, 120, 100)   # reference_p95 ≈ 100ms
    _fill(mc, "recovery1", ep, 120, 110)    # ≤ 100×1.2=120 và ≤ 2000 → PASS
    _fill(mc, "recovery2", ep, 120, 110)
    rows, code, notes = mc.evaluate()
    assert code == EXIT_PASS, notes
    rec = [r for r in rows if r["phase"] == "recovery1"][0]
    assert "PASS(recovery)" in rec["sla_status"]


def test_recovery_fail_vs_reference():
    mc = _mc()
    ep = C.EP_SEARCH
    _fill(mc, "reference1", ep, 120, 100)
    _fill(mc, "reference2", ep, 120, 100)
    _fill(mc, "recovery1", ep, 120, 500)    # > 120ms (ref×1.2) → RECOVERY_FAIL dù ≤ SLA
    _fill(mc, "recovery2", ep, 120, 110)
    rows, code, _ = mc.evaluate()
    assert code == EXIT_SLA_FAIL
    rec = [r for r in rows if r["phase"] == "recovery1"][0]
    assert "RECOVERY_FAIL" in rec["sla_status"]


def test_429_in_capacity_phase_fails():
    mc = _mc()
    ep = C.EP_SEARCH
    _fill(mc, "step1", ep, 120, 50)                       # đủ mẫu p95, ≤ SLA
    _fill(mc, "step1", ep, 3, 0, ok=False, err="rate_limited")
    _rows, code, notes = mc.evaluate()
    assert code == EXIT_SLA_FAIL
    assert any("429" in n for n in notes)


def test_unexpected_error_rate_over_1pct_fails():
    mc = _mc()
    ep = C.EP_SEARCH
    _fill(mc, "step2", ep, 1000, 50)                      # đủ mẫu cả p95 lẫn p99
    _fill(mc, "step2", ep, 20, 0, ok=False, err="server_5xx")  # ~2% > 1%
    _rows, code, notes = mc.evaluate()
    assert code == EXIT_SLA_FAIL
    assert any("unexpected_error_rate" in n for n in notes)


def test_step2_p99_required_insufficient_exit3():
    mc = _mc()
    ep = C.EP_SEARCH
    _fill(mc, "step2", ep, 150, 50)   # đủ p95 (≥100) nhưng thiếu p99 (<1000)
    rows, code, _ = mc.evaluate()
    assert code == EXIT_INSUFFICIENT
    row = [r for r in rows if r["phase"] == "step2"][0]
    assert "INSUFFICIENT_SAMPLE(p99,mandatory)" in row["sla_status"]


def test_step_p95_over_sla_fails():
    mc = _mc()
    ep = C.EP_MARKET
    _fill(mc, "step2", ep, 1000, 3000)   # p95 3000ms > 2000ms SLA
    rows, code, _ = mc.evaluate()
    assert code == EXIT_SLA_FAIL
    row = [r for r in rows if r["phase"] == "step2"][0]
    assert "SLA_FAIL(p95" in row["sla_status"]


def test_warmup_samples_excluded():
    mc = _mc()
    _fill(mc, "warmup", C.EP_SEARCH, 50, 9999)
    rows, code, _ = mc.evaluate()
    assert code == EXIT_PASS
    assert all(r["phase"] != "warmup" for r in rows)


def test_market_ok_total_counts_all_phases_and_only_success(tmp_path):
    mc = _mc(scenario="market_coldmiss")
    mc.cfg.report_prefix = str(tmp_path / "run")
    _fill(mc, "warmup", C.EP_MARKET, 3, 50)
    _fill(mc, "_transition", C.EP_MARKET, 2, 50)
    _fill(mc, "market_cold", C.EP_MARKET, 4, 50)
    _fill(mc, "market_cold", C.EP_MARKET, 1, 50, ok=False, err="server_5xx")
    rows, code, notes = mc.evaluate()
    mc.write_reports(rows, code, notes)
    metadata = json.loads((tmp_path / "run-metadata.json").read_text(encoding="utf-8"))
    assert metadata["market_ok_total"] == 9


def test_metadata_records_measured_phase_windows_only(tmp_path):
    mc = _mc()
    mc.cfg.report_prefix = str(tmp_path / "run")
    _fill(mc, "warmup", C.EP_SEARCH, 1, 50)
    _fill(mc, "_transition", C.EP_SEARCH, 1, 50)
    _fill(mc, "step2", C.EP_SEARCH, 2, 50)
    rows, code, notes = mc.evaluate()
    mc.write_reports(rows, code, notes)
    windows = json.loads(
        (tmp_path / "run-metadata.json").read_text(encoding="utf-8"))["phase_windows"]
    assert set(windows) == {"step2"}
    assert windows["step2"]["start"] <= windows["step2"]["end"]


# ── finding 1: endpoint bắt buộc vắng mặt vẫn phải bị đánh giá ────────────────
def test_missing_mandatory_endpoint_fails():
    # kỳ vọng cả search & market ở step2; chỉ feed search → market vắng → INSUFFICIENT → exit 3.
    mc = _mc(expected={("step2", C.EP_SEARCH), ("step2", C.EP_MARKET)})
    _fill(mc, "step2", C.EP_SEARCH, 1000, 50)
    rows, code, _ = mc.evaluate()
    assert code == EXIT_INSUFFICIENT
    mkt = [r for r in rows if r["phase"] == "step2" and r["endpoint"] == C.EP_MARKET][0]
    assert mkt["success_count"] == 0 and "WORKLOAD_INVALID" in mkt["sla_status"]


def test_recovery_without_reference_fails():
    mc = _mc()  # không có reference1/2
    _fill(mc, "recovery1", C.EP_SEARCH, 120, 100)
    _fill(mc, "recovery2", C.EP_SEARCH, 120, 100)
    rows, code, _ = mc.evaluate()
    assert code == EXIT_SLA_FAIL
    rec = [r for r in rows if r["phase"] == "recovery1"][0]
    assert "RECOVERY_FAIL" in rec["sla_status"] and "reference" in rec["sla_status"]


# ── finding 2: pagination sai tỷ lệ → không kết luận (exit 3) ─────────────────
def test_workload_invalid_pagination_exit3():
    mc = _mc()
    _fill(mc, "step1", C.EP_SEARCH, 200, 50, page_kind="first")  # next-page = 0% → ngoài [15,25]%
    _rows, code, notes = mc.evaluate()
    assert code == EXIT_INSUFFICIENT
    assert any("WORKLOAD_INVALID" in n for n in notes)


def test_pagination_in_tolerance_passes():
    mc = _mc()
    _fill(mc, "step2", C.EP_SEARCH, 800, 50, page_kind="first")
    _fill(mc, "step2", C.EP_SEARCH, 200, 50, page_kind="next")   # 20% → hợp lệ
    _rows, code, _ = mc.evaluate()
    assert code == EXIT_PASS


# ── finding 3: 429 tính vào error rate ở MỌI pha, kể cả baseline ──────────────
def test_429_counts_in_baseline_error_rate():
    mc = _mc()
    _fill(mc, "baseline", C.EP_SEARCH, 200, 50)
    _fill(mc, "baseline", C.EP_SEARCH, 5, 0, ok=False, err="rate_limited")  # ~2.4% > 1%
    _rows, code, notes = mc.evaluate()
    assert code == EXIT_SLA_FAIL
    assert any("unexpected_error_rate" in n for n in notes)


def test_single_429_or_client_timeout_fails_even_below_one_percent():
    for err in ("rate_limited", "read_timeout", "connect_timeout", "client_over_25s",
                "server_5xx_over_25s"):
        mc = _mc(expected={("baseline", C.EP_SEARCH)})
        _fill(mc, "baseline", C.EP_SEARCH, 200, 100)
        _fill(mc, "baseline", C.EP_SEARCH, 1, 0, ok=False, err=err)
        _rows, code, notes = mc.evaluate()
        assert code == EXIT_SLA_FAIL, err
        assert any("zero-tolerance" in note for note in notes), err


# ── finding 4 (safety cap max_requests → SAFETY_ABORT exit 4) ─────────────────
def test_max_requests_triggers_abort():
    mc = _mc(scenario="smoke")   # trần nhỏ → dễ vượt
    over = int(mc._max_requests) + 5
    _fill(mc, "step1", C.EP_SEARCH, over, 50)
    assert mc.aborted
    _rows, code, notes = mc.evaluate()
    assert code == 4  # EXIT_SAFETY_ABORT
    assert any("max_requests" in n for n in notes)


# ── review-2 finding 3: report-only endpoint 0 request → WORKLOAD_INVALID ─────
def test_market_coldmiss_zero_request_invalid():
    # KHÔNG override expected → dẫn xuất từ scenario (market_cold × market).
    mc = MetricsCollector(_cfg("market_coldmiss"))
    rows, code, _ = mc.evaluate()
    assert code == EXIT_INSUFFICIENT
    row = [r for r in rows if r["endpoint"] == C.EP_MARKET][0]
    assert row["success_count"] == 0 and "WORKLOAD_INVALID" in row["sla_status"]


# ── review-2 finding 4: warmup KHÔNG kích hoạt rolling abort ──────────────────
def test_warmup_not_in_rolling_abort():
    mc = _mc()
    _fill(mc, "warmup", C.EP_SEARCH, 20, 50, ok=False, err="server_5xx")
    _fill(mc, "baseline", C.EP_SEARCH, 1, 50)
    assert mc.check_abort("baseline") is None   # warmup không vào rolling window
    assert not mc.aborted


def test_rolling_abort_does_not_mix_previous_phase():
    mc = _mc()
    _fill(mc, "baseline", C.EP_SEARCH, 20, 50, ok=False, err="server_5xx")
    _fill(mc, "step1", C.EP_SEARCH, 20, 50)
    assert mc.check_abort("step1") is None
    assert not mc.aborted


# ── review-3 finding 3: latency LỖI được giữ + xuất error_rows ────────────────
def test_error_latency_captured():
    mc = _mc()
    _fill(mc, "step2", C.EP_SEARCH, 5, 4800, ok=False, err="server_504")
    _fill(mc, "step2", C.EP_SEARCH, 3, 1200, ok=False, err="server_5xx")
    _fill(mc, "step2", C.EP_SEARCH, 10, 50)  # thành công → KHÔNG vào error_rows
    erows = mc.error_rows()
    e504 = [r for r in erows if r["error_class"] == "server_504"][0]
    assert e504["count"] == 5 and e504["p95"] == 4800 and e504["max"] == 4800
    assert {r["error_class"] for r in erows} == {"server_504", "server_5xx"}
    # warmup/_transition không xuất hiện trong error_rows
    _fill(mc, "warmup", C.EP_SEARCH, 3, 999, ok=False, err="server_5xx")
    assert all(r["phase"] != "warmup" for r in mc.error_rows())


# ── review-2 finding 2 (metrics side): pha "_transition" bị loại hoàn toàn ─────
def test_transition_phase_excluded():
    mc = _mc()
    _fill(mc, "_transition", C.EP_SEARCH, 50, 9999, ok=False, err="server_5xx")
    rows, code, _ = mc.evaluate()
    assert code == EXIT_PASS
    assert all(r["phase"] != "_transition" for r in rows)
    assert mc.check_abort("_transition") is None
