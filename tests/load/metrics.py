"""LOAD-TEST — thu thập & nghiệm thu chỉ số.

Tự quản (Locust không làm sẵn):
- Histogram SUCCESS-ONLY theo (phase, endpoint) → p50/p95/p99 nghiệm thu.
- Rolling window 60s cho AUTO-ABORT (error rate / p95); khác với cửa sổ recovery (cố định theo pha).
- Phân loại lỗi: unexpected (4xx ngoài dự kiến, 5xx/504, sai schema, read-timeout, conn-fail) / 429 / timeout.
- Reference_p95 (gộp reference1+2) → recovery1/2 so `≤ ref×1.2 VÀ ≤ SLA`, từng endpoint.
- Ma trận nghiệm thu theo pha (config.PHASE_POLICY) + ngưỡng số mẫu → sla_status + exit code.

Exit code: 0 PASS · 2 SLA_FAIL · 3 INVALID_TEST/INSUFFICIENT(bắt buộc) · 4 SAFETY_ABORT.
"""
from __future__ import annotations

import csv
import json
import math
import os
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from tests.load import config as C

EXIT_PASS = 0
EXIT_SLA_FAIL = 2
EXIT_INSUFFICIENT = 3
EXIT_SAFETY_ABORT = 4


def percentile(sorted_vals: list[float], q: float) -> float | None:
    """Nearest-rank percentile trên list ĐÃ sort. q trong [0,100]."""
    if not sorted_vals:
        return None
    k = math.ceil(q / 100 * len(sorted_vals))
    return sorted_vals[min(max(k, 1), len(sorted_vals)) - 1]


# ── Trạng thái pha hiện tại (shape cập nhật, handler đọc) ──────────────────────
class _PhaseState:
    def __init__(self) -> None:
        self._phase: str | None = None
        self._lock = threading.Lock()

    def set(self, phase: str | None) -> None:
        with self._lock:
            self._phase = phase

    def get(self) -> str | None:
        with self._lock:
            return self._phase


PHASE = _PhaseState()


class MetricsCollector:
    def __init__(self, run_cfg: C.RunConfig, expected_keys: set | None = None):
        self.cfg = run_cfg
        # None → dẫn xuất từ scenario (thực-run). set() → override (unit test cô lập từng nhánh).
        self._expected_override = expected_keys
        self._lock = threading.Lock()
        # success response-time (ms) theo (phase, endpoint)
        self._success: dict[tuple[str, str], list[float]] = defaultdict(list)
        # latency LỖI (ms) theo (phase, endpoint, error_class) — cho chẩn đoán (504/timeout/5xx mất bao lâu)
        self._error_rt: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        # đếm lỗi/thành công theo (phase, endpoint, class)
        self._counts: dict[tuple[str, str, str], int] = defaultdict(int)
        # pagination: đếm request thực theo loại
        self._page_first = 0
        self._page_next = 0
        # tổng market-ok mọi pha (gồm warmup) → 'expected' log cho finalize completeness
        self._market_ok_all = 0
        # wall-clock request window theo pha đo; finalizer dùng để lấy instance_count đúng Step 2/3.
        # Monotonic clock vẫn dùng riêng cho rolling window/safety timeout.
        self._phase_wall: dict[str, list[float]] = {}
        # rolling window cho abort: (ts, phase, is_unexpected, rt_ms_or_None).
        # Giữ phase để tải/lỗi của pha trước không pha loãng hoặc kích nhầm pha sau.
        self._roll: deque[tuple[float, str, bool, float | None]] = deque()
        self._abort_reason: str | None = None
        self._invalid_reason: str | None = None
        self._last_abort_check = 0.0
        # safety cap: max_requests = trần lý thuyết × 1.1; max_time = planned + 120s.
        self._req_count = 0
        self._start = time.monotonic()
        try:
            scn = run_cfg.scenario_def
            self._max_requests = int(C.theoretical_request_max(scn) * 1.1)
            self._max_time = sum(p.duration_s for p in scn.phases) + 120
        except Exception:
            self._max_requests = 0   # 0 = không cap (unit test dùng scenario tối giản)
            self._max_time = 0

    def trigger_invalid(self, reason: str) -> None:
        with self._lock:
            self._invalid_reason = self._invalid_reason or reason

    @property
    def invalid(self) -> str | None:
        return self._invalid_reason

    # ── ghi nhận 1 kết quả request ────────────────────────────────────────────
    def record(self, endpoint: str, phase: str | None, rt_ms: float,
               ok: bool, err_class: str | None, page_kind: str | None) -> None:
        phase = phase or "unknown"
        now = time.monotonic()
        wall_now = time.time()
        # warmup và các pha chuyển tiếp ("_transition") bị loại khỏi HISTOGRAM lẫn ROLLING WINDOW
        # (lỗi warmup không được kích hoạt abort của pha đo kế tiếp).
        measured = phase not in ("warmup", "_transition")
        with self._lock:
            # ĐẾM MỌI market-ok (kể cả warmup/_transition) — mỗi response 200 sinh đúng 1 log market_metrics.
            # Dùng làm 'expected' cho finalize completeness (apples-to-apples với log, không bị warmup làm lệch).
            if endpoint == C.EP_MARKET and ok:
                self._market_ok_all += 1
            if measured:
                phase_wall = self._phase_wall.setdefault(phase, [wall_now, wall_now])
                phase_wall[1] = wall_now
                if ok:
                    self._success[(phase, endpoint)].append(rt_ms)
                else:
                    self._error_rt[(phase, endpoint, err_class or "error")].append(rt_ms)
                self._counts[(phase, endpoint, "ok" if ok else (err_class or "error"))] += 1
                if page_kind == "first":
                    self._page_first += 1
                elif page_kind == "next":
                    self._page_next += 1
                is_unexpected = (not ok) and err_class not in (None, "ok")
                self._roll.append((now, phase, is_unexpected, rt_ms if ok else None))
                # cắt deque > 2× cửa sổ để bounded
                cutoff = now - 2 * C.ROLLING_WINDOW_S
                while self._roll and self._roll[0][0] < cutoff:
                    self._roll.popleft()
            # safety cap theo SỐ REQUEST THỰC (không phải iteration).
            self._req_count += 1
            if self._max_requests and self._req_count > self._max_requests:
                self._abort_reason = self._abort_reason or (
                    f"vượt max_requests={self._max_requests} (thực={self._req_count})")

    # ── kiểm tra abort (gọi tối đa ~1 lần/giây) ───────────────────────────────
    def check_abort(self, phase: str | None) -> str | None:
        now = time.monotonic()
        with self._lock:
            if self._abort_reason:
                return self._abort_reason
            if self._max_time and now - self._start > self._max_time:
                self._abort_reason = f"vượt max_time={self._max_time}s (planned + 120s)"
                return self._abort_reason
            if now - self._last_abort_check < 1.0:
                return None
            self._last_abort_check = now
            window = [r for r in self._roll
                      if r[0] >= now - C.ROLLING_WINDOW_S and r[1] == phase]
            if len(window) < 20:
                return None  # chưa đủ mẫu để kết luận abort
            total = len(window)
            unexpected = sum(1 for _, _, u, _ in window if u)
            rate = unexpected / total
            succ_rt = sorted(rt for _, _, _, rt in window if rt is not None)
            p95 = percentile(succ_rt, 95) or 0.0
            reason = None
            if rate > C.ABORT_ERROR_RATE_60S:
                reason = f"unexpected_error_rate={rate:.1%} > {C.ABORT_ERROR_RATE_60S:.0%}/60s"
            elif p95 > C.ABORT_P95_MS_60S:
                reason = f"p95={p95:.0f}ms > {C.ABORT_P95_MS_60S}ms/60s"
            if reason:
                self._abort_reason = reason
            return reason

    def trigger_abort(self, reason: str) -> None:
        with self._lock:
            self._abort_reason = self._abort_reason or reason

    @property
    def aborted(self) -> bool:
        return self._abort_reason is not None

    # ── nghiệm thu cuối ───────────────────────────────────────────────────────
    def _reference_p95(self) -> dict[str, float]:
        """p95 gộp reference1 + reference2, theo endpoint."""
        merged: dict[str, list[float]] = defaultdict(list)
        for (phase, ep), vals in self._success.items():
            if phase in ("reference1", "reference2"):
                merged[ep].extend(vals)
        return {ep: percentile(sorted(v), 95) for ep, v in merged.items() if v}

    def _phase_error_rate(self, phase: str) -> tuple[int, int]:
        """(unexpected, total) cho 1 pha, cộng mọi endpoint.
        429 LÀ unexpected error ở MỌI pha (chỉ cơ chế 'abort ngay' mới giới hạn ở capacity phase)."""
        total = unexpected = 0
        for (p, _ep, cls), n in self._counts.items():
            if p != phase:
                continue
            total += n
            if cls != "ok":
                unexpected += n
        return unexpected, total

    def _expected_keys(self) -> set[tuple[str, str]]:
        """(phase, endpoint) BẮT BUỘC phải xuất hiện, dựng từ scenario. Thiếu → succ=0 →
        INSUFFICIENT/MISSING → không được PASS ngầm."""
        if self._expected_override is not None:
            return set(self._expected_override)
        try:
            scn = self.cfg.scenario_def
        except Exception:
            return set()
        if scn.ops_only:
            eps = [C.EP_HEALTH, C.EP_METADATA]
        else:
            eps = []
            if scn.weight_search > 0:
                eps.append(C.EP_SEARCH)
            if scn.weight_market > 0:
                eps.append(C.EP_MARKET)
        return {(p.name, ep) for p in scn.phases if p.name != "warmup" for ep in eps}

    def evaluate(self) -> tuple[list[dict], int, list[str]]:
        rows: list[dict] = []
        notes: list[str] = []
        exit_code = EXIT_PASS
        ref_p95 = self._reference_p95()

        # gom (phase, endpoint) xuất hiện + BẮT BUỘC theo scenario (thiếu vẫn phải bị đánh giá)
        expected = self._expected_keys()
        keys = {(p, ep) for (p, ep) in self._success}
        keys |= {(p, ep) for (p, ep, _) in self._counts}
        keys |= expected

        for (phase, ep) in sorted(keys):
            if phase == "warmup":
                continue
            pol = C.PHASE_POLICY.get(phase)
            if pol is None:
                continue
            vals = sorted(self._success.get((phase, ep), []))
            succ = len(vals)
            fail = sum(n for (p, e, c), n in self._counts.items()
                       if p == phase and e == ep and c not in ("ok",))
            p50 = percentile(vals, 50)
            p95 = percentile(vals, 95)
            p99 = percentile(vals, 99)
            sla = C.SLA_MS.get(ep, {})
            statuses: list[str] = []

            # ZERO REQUEST cho endpoint BẮT BUỘC → workload invalid (kể cả report-only) → exit 3.
            if (phase, ep) in expected and (succ + fail) == 0:
                rows.append({
                    "run_id": self.cfg.run_id, "phase": phase, "endpoint": ep,
                    "success_count": 0, "failure_count": 0,
                    "p50": None, "p95": None, "p99": None,
                    "sla_status": "WORKLOAD_INVALID(zero request)",
                })
                exit_code = max(exit_code, EXIT_INSUFFICIENT)
                continue

            # p95
            if pol.p95 == "required":
                if succ < C.MIN_SAMPLES_P95:
                    statuses.append("INSUFFICIENT_SAMPLE(p95,mandatory)")
                    exit_code = max(exit_code, EXIT_INSUFFICIENT)
                elif pol.is_recovery:
                    thr = sla.get("p95")
                    ref = ref_p95.get(ep)
                    if ref is None:   # thiếu reference → không có baseline ổn định → KHÔNG PASS
                        statuses.append("RECOVERY_FAIL(thiếu pre-spike reference)")
                        exit_code = max(exit_code, EXIT_SLA_FAIL)
                        rows.append({
                            "run_id": self.cfg.run_id, "phase": phase, "endpoint": ep,
                            "success_count": succ, "failure_count": fail,
                            "p50": round(p50, 1) if p50 else None,
                            "p95": round(p95, 1) if p95 else None,
                            "p99": round(p99, 1) if p99 else None,
                            "sla_status": statuses[-1],
                        })
                        continue
                    ok_sla = thr is None or (p95 is not None and p95 <= thr)
                    ok_ref = p95 is not None and p95 <= ref * 1.2
                    if ok_sla and ok_ref:
                        statuses.append("PASS(recovery)")
                    else:
                        statuses.append(f"RECOVERY_FAIL(p95={p95:.0f},ref={ref},sla={thr})")
                        exit_code = max(exit_code, EXIT_SLA_FAIL)
                else:
                    thr = sla.get("p95")
                    if thr is not None and p95 is not None and p95 > thr:
                        statuses.append(f"SLA_FAIL(p95={p95:.0f}>{thr})")
                        exit_code = max(exit_code, EXIT_SLA_FAIL)
                    else:
                        statuses.append("PASS(p95)")
            elif pol.p95 == "report":
                statuses.append("REPORT_ONLY(p95)")

            # p99
            if pol.p99 == "required":
                if succ < C.MIN_SAMPLES_P99:
                    statuses.append("INSUFFICIENT_SAMPLE(p99,mandatory)")
                    exit_code = max(exit_code, EXIT_INSUFFICIENT)
                else:
                    thr = sla.get("p99")
                    if thr is not None and p99 is not None and p99 > thr:
                        statuses.append(f"SLA_FAIL(p99={p99:.0f}>{thr})")
                        exit_code = max(exit_code, EXIT_SLA_FAIL)
                    else:
                        statuses.append("PASS(p99)")
            elif pol.p99 == "if_enough":
                if succ >= C.MIN_SAMPLES_P99:
                    thr = sla.get("p99")
                    if thr is not None and p99 is not None and p99 > thr:
                        statuses.append(f"SLA_FAIL(p99={p99:.0f}>{thr})")
                        exit_code = max(exit_code, EXIT_SLA_FAIL)
                    else:
                        statuses.append("PASS(p99)")
                else:
                    statuses.append("NOT_EVALUATED(p99)")
            elif pol.p99 == "report":
                statuses.append("REPORT_ONLY(p99)")

            rows.append({
                "run_id": self.cfg.run_id, "phase": phase, "endpoint": ep,
                "success_count": succ, "failure_count": fail,
                "p50": round(p50, 1) if p50 else None,
                "p95": round(p95, 1) if p95 else None,
                "p99": round(p99, 1) if p99 else None,
                "sla_status": " | ".join(statuses) or "REPORT_ONLY",
            })

        # error rate theo pha (error_required)
        phases_seen = {p for (p, _e) in keys if p != "warmup"}
        for phase in sorted(phases_seen):
            pol = C.PHASE_POLICY.get(phase)
            if not pol or not pol.error_required:
                continue
            unexpected, total = self._phase_error_rate(phase)
            if total and unexpected / total >= C.MAX_UNEXPECTED_ERROR_RATE:
                notes.append(f"[{phase}] unexpected_error_rate={unexpected/total:.2%} ≥ 1% → SLA_FAIL")
                exit_code = max(exit_code, EXIT_SLA_FAIL)
            # Zero-tolerance theo plan: 429, client timeout và request >25s phải bằng 0 ở MỌI pha đo.
            zero_tolerance = {"rate_limited", "read_timeout", "connect_timeout"}
            violations: dict[str, int] = {}
            for (p, _e, cls), n in self._counts.items():
                if p == phase and (cls in zero_tolerance or cls.endswith("_over_25s")) and n > 0:
                    violations[cls] = violations.get(cls, 0) + n
            if violations:
                notes.append(
                    f"[{phase}] zero-tolerance 429/timeout/>25s bị vi phạm: {violations} → SLA_FAIL")
                exit_code = max(exit_code, EXIT_SLA_FAIL)

        # tolerance pagination — ngoài khoảng → KHÔNG kết luận hiệu năng được (exit 3)
        tot_search = self._page_first + self._page_next
        if tot_search:
            frac = self._page_next / tot_search
            lo, hi = C.NEXT_PAGE_TOLERANCE
            if not (lo <= frac <= hi):
                notes.append(
                    f"WORKLOAD_INVALID: next-page fraction={frac:.1%} ngoài [{lo:.0%},{hi:.0%}] → không kết luận")
                exit_code = max(exit_code, EXIT_INSUFFICIENT)

        if self._invalid_reason:
            exit_code = max(exit_code, EXIT_INSUFFICIENT)
            notes.append(f"INVALID_TEST: {self._invalid_reason}")
        if self.aborted:  # SAFETY_ABORT có ưu tiên cao nhất (tuyệt đối)
            exit_code = EXIT_SAFETY_ABORT
            notes.append(f"SAFETY_ABORT: {self._abort_reason}")
        return rows, exit_code, notes

    def error_rows(self) -> list[dict]:
        """Latency LỖI theo (phase, endpoint, error_class) — chẩn đoán 504/timeout/5xx mất bao lâu."""
        out = []
        for (phase, ep, cls), vals in sorted(self._error_rt.items()):
            if phase in ("warmup", "_transition"):
                continue
            v = sorted(vals)
            out.append({
                "run_id": self.cfg.run_id, "phase": phase, "endpoint": ep, "error_class": cls,
                "count": len(v),
                "p50": round(percentile(v, 50), 1) if v else None,
                "p95": round(percentile(v, 95), 1) if v else None,
                "max": round(max(v), 1) if v else None,
            })
        return out

    # ── xuất report ───────────────────────────────────────────────────────────
    def write_reports(self, rows: list[dict], exit_code: int, notes: list[str]) -> None:
        prefix = self.cfg.report_prefix
        os.makedirs(os.path.dirname(prefix) or ".", exist_ok=True)
        with open(f"{prefix}-success-metrics.json", "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "exit_code": exit_code, "notes": notes}, f,
                      ensure_ascii=False, indent=2)
        with open(f"{prefix}-success-metrics.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "run_id", "phase", "endpoint", "success_count", "failure_count",
                "p50", "p95", "p99", "sla_status"])
            w.writeheader()
            w.writerows(rows)
        meta = {
            "run_id": self.cfg.run_id,
            "scenario": self.cfg.scenario,
            "load_seed": self.cfg.load_seed,
            "next_page": {"first": self._page_first, "next": self._page_next},
            "market_ok_total": self._market_ok_all,   # expected log market cho finalize completeness
            "phase_windows": {
                phase: {
                    "start": datetime.fromtimestamp(bounds[0], UTC).isoformat(),
                    "end": datetime.fromtimestamp(bounds[1], UTC).isoformat(),
                }
                for phase, bounds in sorted(self._phase_wall.items())
            },
            "exit_code": exit_code,
            "notes": notes,
            **self.cfg.extra,
        }
        with open(f"{prefix}-metadata.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        # error latency (chẩn đoán) — tách khỏi success-only percentile nghiệm thu
        errs = self.error_rows()
        with open(f"{prefix}-error-metrics.json", "w", encoding="utf-8") as f:
            json.dump({"rows": errs}, f, ensure_ascii=False, indent=2)
        with open(f"{prefix}-error-metrics.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "run_id", "phase", "endpoint", "error_class", "count", "p50", "p95", "max"])
            w.writeheader()
            w.writerows(errs)
