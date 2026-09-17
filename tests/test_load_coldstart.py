"""LOAD-TEST — unit test cold_start (KHÔNG import locust) + helper invalid_test.

Phủ: (1) invalid_test → exit code 3; (2) _wait_scale_to_zero chống stale zero + không đếm trùng
cùng một điểm metric (yêu cầu 2 điểm zero timestamp TĂNG DẦN).
"""
from __future__ import annotations

import json
import sys

import pytest

from tests.load import cold_start as CS
from tests.load import config as C


# ── finding 1: INVALID_TEST → exit code 3 (không phải 1) ──────────────────────
def test_invalid_test_exit_code_3():
    with pytest.raises(SystemExit) as e:
        C.invalid_test("thiếu gì đó")
    assert e.value.code == 3


# ── finding 2: dedup zero theo timestamp ──────────────────────────────────────
def _wait(monkeypatch, seq, not_before, timeout_s=0.4):
    """Chạy _wait_scale_to_zero với _instance_count trả lần lượt seq (hết thì lặp phần tử cuối)."""
    it = iter(seq)
    last = {"v": seq[-1]}

    def fake_count(service, region, project):
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]

    monkeypatch.setattr(CS, "_instance_count", fake_count)
    monkeypatch.setattr(CS.time, "sleep", lambda *a, **k: None)
    return CS._wait_scale_to_zero("svc", "r", "p", timeout_s, not_before, poll_s=0)


def test_two_increasing_fresh_zeros_confirm(monkeypatch):
    # hai điểm zero timestamp tăng (200 → 260), đều > not_before=100 → xác nhận cold
    assert _wait(monkeypatch, [(0, 200.0), (0, 260.0)], not_before=100.0) is True


def test_same_zero_point_not_counted_twice(monkeypatch):
    # cùng một điểm (0, 200) lặp lại (poll nhanh hơn chu kỳ metric) → KHÔNG đủ 2 xác nhận → False
    assert _wait(monkeypatch, [(0, 200.0)], not_before=100.0) is False


def test_stale_zero_rejected(monkeypatch):
    # zero cũ hơn mẫu trước (observed_at=50 < not_before=100) → không tính → False
    assert _wait(monkeypatch, [(0, 50.0)], not_before=100.0) is False


def test_positive_count_resets(monkeypatch):
    # có 1 zero tươi rồi lại thấy instance>0 → reset; sau đó chỉ 1 zero mới → chưa đủ 2 → False
    assert _wait(monkeypatch, [(0, 150.0), (2, 160.0), (0, 170.0)], not_before=100.0) is False


def test_unreadable_metric_skips(monkeypatch):
    # đọc None nhiều lần → không xác nhận được → bỏ mẫu (False)
    assert _wait(monkeypatch, [None, None, None], not_before=100.0) is False


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_serving_revision_requires_exactly_one_100pct_entry(monkeypatch):
    payload = {"status": {"url": "https://svc.example", "traffic": [
        {"revisionName": "rev-old", "percent": 0},
        {"revisionName": "rev-new", "percent": 100},
    ]}}
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Proc(json.dumps(payload)))
    assert CS._serving_revision("svc", "r", "p") == "rev-new"
    assert CS._serving_revision("svc", "r", "p", "https://wrong.example") is None


@pytest.mark.parametrize("payload", [
    {"status": {"traffic": [{"revisionName": "rev-a", "percent": 50},
                              {"revisionName": "rev-b", "percent": 50}]}},
    {"status": {"traffic": []}},
])
def test_serving_revision_rejects_split_or_missing_traffic(monkeypatch, payload):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Proc(json.dumps(payload)))
    assert CS._serving_revision("svc", "r", "p") is None


def test_serving_revision_gcloud_error(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Proc(returncode=1))
    assert CS._serving_revision("svc", "r", "p") is None


def test_cold_start_requires_service_for_revision_provenance(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cold_start", "--mode", "manual"])
    monkeypatch.setenv("BASE_URL", "https://example.test")
    monkeypatch.delenv("PERF_SERVICE", raising=False)
    with pytest.raises(SystemExit) as exc:
        CS.main()
    assert exc.value.code == 3


def test_cold_start_requires_project(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cold_start", "--service", "svc"])
    monkeypatch.setenv("BASE_URL", "https://example.test")
    monkeypatch.delenv("PERF_PROJECT", raising=False)
    with pytest.raises(SystemExit) as exc:
        CS.main()
    assert exc.value.code == 3


def test_manual_cold_start_is_rejected(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "cold_start", "--mode", "manual", "--service", "svc",
        "--project", "p", "--revision", "rev-old"])
    monkeypatch.setenv("BASE_URL", "https://example.test")
    monkeypatch.delenv("PERF_REVISION", raising=False)
    with pytest.raises(SystemExit) as exc:
        CS.main()
    assert exc.value.code == 3


def test_manual_cold_start_rejection_does_not_send_request(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "cold_start", "--mode", "manual", "--service", "svc",
        "--project", "p", "--revision", "rev-old", "--samples", "3"])
    monkeypatch.setenv("BASE_URL", "https://example.test")
    monkeypatch.setattr(CS.requests, "get", lambda *_a, **_k: pytest.fail("không được gửi request"))
    with pytest.raises(SystemExit) as exc:
        CS.main()
    assert exc.value.code == 3
