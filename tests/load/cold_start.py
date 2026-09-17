"""LOAD-TEST — đo cold start Cloud Run (script RIÊNG, không trộn warm latency).

Quy trình mỗi mẫu:
  1) Chờ instance count về 0 (timeout tối đa) — cần quyền đọc Cloud Monitoring.
  2) Gửi 1 request /health.
  3) Ghi latency.
Lặp 3–5 lần → median/max, hoặc INSUFFICIENT_SAMPLE nếu không thu đủ mẫu trong thời gian cho phép.

    BASE_URL=https://<perf-svc>.run.app AUTH_BEARER=$(gcloud auth print-identity-token) \
    python -m tests.load.cold_start --service <perf-svc> --project <perf-project> \
        --samples 5 --wait-zero-timeout 900

Lưu ý: đặt min-instances=0 KHÔNG giết instance đang chạy ngay; nếu quá timeout mà chưa về 0 thì bỏ
mẫu đó (không chờ vô hạn). Deploy revision mới không thay thế được gate này: Cloud Run khởi động một
instance để health-check deployment, nên "request đầu của revision mới" chưa chắc là cold start.
"""
from __future__ import annotations

import json
import os
import statistics
import time

import requests

from tests.load import config as C
from tests.load.config import invalid_test
from tests.load.response_validation import valid_success_body


def _instance_count(service: str, region: str, project: str) -> tuple[int, float] | None:
    """Tổng container instance count mới nhất qua **Monitoring API** (`projects.timeSeries.list`).

    KHÔNG dùng `gcloud monitoring time-series list` (không có trong nhóm lệnh stable). Trả:
      - (count, observed_at): tổng điểm mới nhất (state active+idle) + thời điểm quan sát (epoch s)
        — chỉ khi CÓ series và CÓ point. observed_at để loại 'stale zero' cũ hơn mẫu trước.
      - None: không đọc được / không có series / không point → KHÔNG được coi là 0 (metric lấy mẫu
        60–120s, rỗng có thể do trễ hoặc thiếu quyền, không chắc là đã scale-to-zero).
    Lọc theo cả service_name LẪN location (region) để không cộng nhầm service cùng tên khác vùng."""
    if not project:
        return None
    try:
        from google.cloud import monitoring_v3
    except Exception:
        print("  (thiếu google-cloud-monitoring → không gate được scale-to-zero; "
              "cài dependency rồi chạy lại)")
        return None
    try:
        client = monitoring_v3.MetricServiceClient()
        now = time.time()
        interval = monitoring_v3.TimeInterval({
            "end_time": {"seconds": int(now)},
            "start_time": {"seconds": int(now - 180)},  # cửa sổ 3 phút cho độ trễ 60–120s
        })
        flt = (
            'metric.type="run.googleapis.com/container/instance_count" '
            f'AND resource.labels.service_name="{service}" '
            f'AND resource.labels.location="{region}"'
        )
        series = list(client.list_time_series(request={
            "name": f"projects/{project}",
            "filter": flt,
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        }))
        if not series:
            return None  # KHÔNG có series → không xác nhận được (không coi là 0)
        total = 0
        observed_at: float | None = None
        for s in series:
            if s.points:  # points[0] là điểm mới nhất
                total += int(s.points[0].value.int64_value or s.points[0].value.double_value or 0)
                try:  # dùng MIN timestamp các series tham gia → chỉ 'tươi' khi MỌI series đều tươi
                    ts = s.points[0].interval.end_time.timestamp()
                    observed_at = ts if observed_at is None else min(observed_at, ts)
                except Exception:
                    pass
        if observed_at is None:
            return None  # có series nhưng KHÔNG point (hoặc không đọc được ts) → không xác nhận được
        return (total, observed_at)
    except Exception as e:  # noqa: BLE001
        print(f"  không đọc được Monitoring API: {e}")
        return None


def _serving_revision(service: str, region: str, project: str,
                      expected_url: str = "") -> str | None:
    """Revision duy nhất nhận 100% traffic; nếu có expected_url thì URL service cũng phải khớp."""
    if not service:
        return None
    import subprocess
    try:
        out = subprocess.run(
            ["gcloud", "run", "services", "describe", service,
             f"--project={project}", f"--region={region}",
             "--format=json"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return None
        status = (json.loads(out.stdout).get("status") or {})
        actual_url = str(status.get("url") or "").rstrip("/")
        if expected_url and actual_url != expected_url.rstrip("/"):
            return None
        traffic = status.get("traffic") or []
        serving = []
        for item in traffic:
            revision = item.get("revisionName") or item.get("revision")
            if revision and int(item.get("percent") or 0) == 100:
                serving.append(revision)
        return serving[0] if len(serving) == 1 else None
    except Exception:
        return None


def _identity_token() -> str | None:
    """Lấy token mới cho từng mẫu dài; None nếu gcloud lỗi/rỗng."""
    import subprocess
    try:
        out = subprocess.run(
            ["gcloud", "auth", "print-identity-token"],
            capture_output=True, text=True, timeout=30,
        )
        token = out.stdout.strip()
        return token if out.returncode == 0 and token else None
    except Exception:
        return None


def _wait_scale_to_zero(service, region, project, timeout_s: int, not_before: float,
                        poll_s: int = 15) -> bool:
    """Xác nhận scale-to-zero, chống 2 loại nhiễu:
      - stale zero: chỉ nhận zero có observed_at > not_before (thời điểm mẫu trước hoàn thành);
      - đếm trùng: poll 15s nhưng metric lấy mẫu 60s → cùng một điểm (observed_at lặp) KHÔNG được
        tính là 2 xác nhận. Yêu cầu 2 điểm zero có timestamp TĂNG DẦN, khác nhau."""
    deadline = time.monotonic() + timeout_s
    unread = 0
    fresh_zeros = 0
    last_zero_ts = not_before  # điểm zero được tính phải mới hơn mốc này (ban đầu = not_before)
    while time.monotonic() < deadline:
        res = _instance_count(service, region, project)
        if res is None:
            unread += 1
            fresh_zeros = 0
            if unread >= 3:  # đọc lỗi/không có series nhiều lần → không xác nhận được → bỏ mẫu
                print("  không xác nhận được scale-to-zero qua metric → bỏ mẫu "
                      "(kiểm quyền Monitoring/độ trễ metric rồi chạy lại)")
                return False
        else:
            unread = 0
            count, observed_at = res
            if count == 0 and observed_at > last_zero_ts:  # zero TƯƠI và là ĐIỂM MỚI (ts tăng)
                fresh_zeros += 1
                last_zero_ts = observed_at
                if fresh_zeros >= 2:
                    return True
            elif count != 0:
                fresh_zeros = 0   # có instance → reset (zero cũ/trùng thì GIỮ nguyên đếm)
        time.sleep(poll_s)
    return False


def main() -> None:
    ap = C.InvalidTestArgumentParser()
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--wait-zero-timeout", type=int, default=900)
    ap.add_argument("--service", default=os.environ.get("PERF_SERVICE", ""))
    ap.add_argument("--region", default=os.environ.get("PERF_REGION", "asia-southeast1"))
    ap.add_argument("--project", default=os.environ.get("PERF_PROJECT", ""))
    ap.add_argument("--revision", default=os.environ.get("PERF_REVISION", ""),
                    help="revision khai báo lúc bắt đầu (chỉ lưu audit; không thay thế xác minh traffic)")
    ap.add_argument("--mode", choices=["metric", "manual"], default="metric",
                    help="metric = gate scale-to-zero qua Monitoring. 'manual' bị từ chối vì "
                         "revision mới đã được Cloud Run khởi động để health-check, không chứng minh cold.")
    ap.add_argument("--out", default="", help="lưu kết quả cold-start (JSON) làm artifact audit")
    ap.add_argument("--refresh-auth", action="store_true",
                    help="lấy identity token mới bằng gcloud trước từng mẫu (nên dùng khi chờ lâu)")
    args = ap.parse_args()
    if not 3 <= args.samples <= 5:
        invalid_test("--samples phải trong [3,5] theo kế hoạch cold-start")
    if args.wait_zero_timeout <= 0:
        invalid_test("--wait-zero-timeout phải > 0")
    if not args.service:
        invalid_test("--service bắt buộc để xác minh revision nhận 100% traffic cho từng mẫu")
    if not args.project or not args.region:
        invalid_test("--project và --region bắt buộc để đọc đúng service/Monitoring")
    if args.mode == "manual":
        invalid_test("--mode manual không chứng minh cold start: Cloud Run khởi động instance để "
                     "health-check khi deploy. Dùng --mode metric và gate bằng 2 điểm zero tươi.")
    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    base = os.environ.get("BASE_URL", "").rstrip("/")
    if not base:
        invalid_test("thiếu BASE_URL")
    headers = {}
    if os.environ.get("AUTH_BEARER"):
        headers["Authorization"] = f"Bearer {os.environ['AUTH_BEARER']}"

    samples: list[dict] = []   # mỗi mẫu ghi revision phục vụ ngay trước request
    provenance_unreadable = False
    auth_unreadable = False
    invalid_response = False
    not_before = time.time()   # chỉ chấp nhận zero quan sát SAU mốc này (ban đầu = lúc bắt đầu)
    for i in range(1, args.samples + 1):
        cold = _wait_scale_to_zero(args.service, args.region, args.project,
                                   args.wait_zero_timeout, not_before)
        if not cold:
            print(f"[mẫu {i}] không xác nhận scale-to-zero (zero tươi) trong "
                  f"{args.wait_zero_timeout}s → bỏ")
            continue
        # Xác minh revision nhận 100% traffic NGAY trước request.
        rev = _serving_revision(args.service, args.region, args.project, base)
        if rev is None:
            provenance_unreadable = True
            print(f"[mẫu {i}] không xác minh được revision nhận 100% traffic → bỏ mẫu")
            continue
        if args.refresh_auth:
            refreshed = _identity_token()
            if not refreshed:
                auth_unreadable = True
                print(f"[mẫu {i}] không lấy được identity token mới → bỏ mẫu")
                continue
            headers["Authorization"] = f"Bearer {refreshed}"
        headers["X-Request-ID"] = f"coldstart:{i}:{int(time.time())}"
        t0 = time.perf_counter()
        try:
            r = requests.get(f"{base}/health", headers=headers, timeout=(5, 60))
            rt = (time.perf_counter() - t0) * 1000
            print(f"[mẫu {i}] status={r.status_code} cold_start={rt:.0f}ms revision={rev}")
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                body = None
            if r.status_code == 200 and valid_success_body(C.EP_HEALTH, body):
                samples.append({"latency_ms": round(rt, 1), "revision": rev,
                                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            elif r.status_code in (401, 403) or r.status_code == 200:
                invalid_response = True
        except Exception as e:  # noqa: BLE001
            print(f"[mẫu {i}] lỗi: {e}")
        not_before = time.time()   # mẫu tiếp theo chỉ nhận zero MỚI sau khi mẫu này hoàn thành

    latencies = [s["latency_ms"] for s in samples]
    enough = len(latencies) >= 3
    # PROVENANCE: mọi mẫu phải xác minh được revision nhận 100% traffic; nếu không, kết quả
    # không truy vết được đã đo revision nào → KHÔNG coi là OK.
    revs = {s["revision"] for s in samples}
    if provenance_unreadable:
        status = "NO_REVISION_PROVENANCE"
    elif auth_unreadable:
        status = "AUTH_UNREADABLE"
    elif invalid_response:
        status = "INVALID_RESPONSE"
    elif enough:
        status = "OK"
    else:
        status = "INSUFFICIENT_SAMPLE"
    result = {
        "project": args.project, "service": args.service, "region": args.region,
        "revision_at_start": args.revision, "base_url": base,
        "started_utc": started_utc, "ended_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "samples_requested": args.samples,
        "samples_collected": len(samples),
        "samples": samples,   # per-sample: latency_ms + revision + at
        "revisions": sorted(r for r in revs if r),
        "median_ms": round(statistics.median(latencies), 1) if latencies else None,
        "max_ms": round(max(latencies), 1) if latencies else None,
        "status": status,
    }
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"→ {args.out}")
    if provenance_unreadable:
        print("NO_REVISION_PROVENANCE: có mẫu không xác minh được revision "
              "nhận 100% traffic")
        raise SystemExit(5)
    if auth_unreadable:
        print("AUTH_UNREADABLE: không lấy được identity token mới")
        raise SystemExit(5)
    if invalid_response:
        print("INVALID_RESPONSE: /health bị từ chối xác thực hoặc response 200 sai contract")
        raise SystemExit(3)
    if not enough:
        print(f"INSUFFICIENT_SAMPLE: chỉ thu {len(latencies)} mẫu cold start (<3)")
        raise SystemExit(3)
    print(f"cold_start median={result['median_ms']:.0f}ms  max={result['max_ms']:.0f}ms  (n={len(latencies)})")


if __name__ == "__main__":
    main()
