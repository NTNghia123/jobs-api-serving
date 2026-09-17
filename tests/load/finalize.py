"""LOAD-TEST — verdict cuối của một scenario, sau khi Locust chạy xong.

Locust exit 0 chỉ là "client-side SLA sơ bộ". Suite PASS cho 1 scenario ⇔ Locust exit 0 VÀ finalize
exit 0. finalize kiểm (fail-CLOSED):
  - Có jobs trong cửa sổ (n_total > 0) — không bằng chứng ⇒ FAIL.
  - Tổng total_bytes_billed ≤ ngân sách.
  - BQ cache-hit < 5% cho bq_cold_search & market_coldmiss (tính TRÊN job thành công).
  - market_coldmiss: app-cache-hit = 0% — mặc định đếm Cloud Logging của đúng revision/run.
  - URL, image digest, cache, concurrency, min/max instances của revision thật KHỚP metadata.
  - Revision đang phục vụ nhận 100% traffic (bắt buộc).
  - Gộp exit code Locust vào artifact, nên kết quả scenario không thể PASS khi client-side FAIL.

Chạy NGAY sau MỖI scenario (trước lần deploy kế). --budget-only: chỉ cộng bytes trong 1 cửa sổ (dùng cho
kiểm ngân sách CẢ SUITE từ START đầu → END cuối, không gắn scenario/revision).

Exit: 0 PASS · 2 BUDGET_EXCEEDED · 3 INVALID (zero-jobs/cache/revision/app-cache/thiếu input) · 5 KHÔNG ĐỌC ĐƯỢC.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from app.infrastructure.warehouse.bigquery_exec import load_test_run_label, to_bq_params
from app.infrastructure.warehouse.bigquery_read_sql import ReadTarget, build_current_batch_meta_sql
from tests.load import config as C

EXIT_PASS = 0
EXIT_BUDGET = 2
EXIT_INVALID = 3
EXIT_UNREADABLE = 5

COLD_BQ_SCENARIOS = {"bq_cold_search", "market_coldmiss"}
INSTANCE_EVIDENCE_SCENARIOS = {"realistic", "bq_cold_search"}
INSTANCE_EVIDENCE_PHASES = {
    "realistic": ("step2", "step3"),
    "bq_cold_search": ("step2",),
}
BQ_CACHE_MAX = 0.05
VALID_SCENARIOS = sorted(set(C.SCENARIOS) - {"smoke"})


# ── helpers THUẦN (unit-test được, không I/O) ────────────────────────────────
def row_counts(row) -> tuple[int, int, int, int, int, int]:
    """Map row → (workload_total, workload_success, all_bytes, workload_hits, active, billable)."""
    return (int(row["n_total"]), int(row["n_success"]), int(row["bytes"]), int(row["hits"]),
            int(row["n_active"]), int(row["n_billable"]))


def traffic_ok(traffic: list[dict], expected: str) -> bool:
    """True nếu CÓ entry revisionName==expected VÀ percent==100 (so từng trường, không tìm chuỗi)."""
    for t in traffic or []:
        rev = t.get("revisionName") or t.get("revision")
        if rev == expected and int(t.get("percent") or 0) == 100:
            return True
    return False


def suite_verdict(scenario: str, n_total: int, n_success: int, total_bytes_billed: int,
                  cache_hits: int, budget_bytes: int, revision_ok: bool,
                  app_cache_ok: bool | None = None,
                  app_cache_evidence: dict | None = None) -> tuple[dict, int]:
    checks: list[dict] = []
    exit_code = EXIT_PASS

    checks.append({"check": "has_jobs", "n_total": n_total, "pass": n_total > 0})
    if n_total == 0:
        exit_code = max(exit_code, EXIT_INVALID)

    over = total_bytes_billed > budget_bytes
    checks.append({"check": "budget", "total_bytes_billed": total_bytes_billed,
                   "budget_bytes": budget_bytes, "pass": not over})
    if over:
        exit_code = max(exit_code, EXIT_BUDGET)

    cache_ratio = (cache_hits / n_success) if n_success else None
    if scenario in COLD_BQ_SCENARIOS:
        ok = cache_ratio is not None and cache_ratio < BQ_CACHE_MAX
        checks.append({"check": "bq_cache_hit", "ratio": cache_ratio, "threshold": BQ_CACHE_MAX,
                       "n_success": n_success, "pass": ok})
        if not ok:
            exit_code = max(exit_code, EXIT_INVALID)
    else:
        checks.append({"check": "bq_cache_hit", "ratio": cache_ratio, "n_success": n_success,
                       "pass": True, "note": "report-only (không phải cold)"})

    # app-cache: market_coldmiss cần app-cache-hit = 0 (BQ không thấy → BẰNG CHỨNG từ Cloud Logging,
    # đếm log jsonPayload.cache="hit"; xem app_cache_evidence). Manual chỉ được ghi nhận FAIL.
    if scenario == "market_coldmiss":
        ok = app_cache_ok is True
        checks.append({"check": "app_cache_miss", "pass": ok, "evidence": app_cache_evidence})
        if not ok:
            exit_code = max(exit_code, EXIT_INVALID)

    checks.append({"check": "revision_matches_100pct", "pass": bool(revision_ok)})
    if not revision_ok:
        exit_code = max(exit_code, EXIT_INVALID)

    return {"scenario": scenario, "n_total": n_total, "n_success": n_success,
            "total_bytes_billed": total_bytes_billed,
            "cache_ratio": cache_ratio, "app_cache_ok": app_cache_ok,
            "checks": checks, "exit_code": exit_code}, exit_code


# ── I/O adapters ─────────────────────────────────────────────────────────────
def _query_jobs(project: str, location: str, start: str, end: str,
                run_id: str, *, prefix: bool = False, audit_end: str | None = None,
                maximum_bytes_billed: int | None = None):
    """Usage workload + audit của đúng run; cache ratio chỉ tính workload.

    Ở mode per-scenario, chính query INFORMATION_SCHEMA này cũng được gắn ``load_test_report`` và
    cộng bytes thực sau khi hoàn tất. Mode prefix (budget cuối suite) là truy vấn báo cáo ngoài cửa sổ
    đo nên không tự cộng chính nó.
    """
    try:
        from google.cloud import bigquery
    except Exception:
        print("  thiếu google-cloud-bigquery → không đọc được jobs")
        return None
    try:
        client = bigquery.Client(project=project, location=location)
        sql = f"""
        WITH scoped AS (
          SELECT state, error_result, total_bytes_billed, cache_hit,
                 EXISTS (
                   SELECT 1 FROM UNNEST(labels) AS label
                   WHERE label.key = 'load_test_run'
                     AND label.value IN UNNEST(@run_labels)
                 ) AS is_workload
          FROM `region-{location}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
          WHERE job_type = 'QUERY' AND (
            (creation_time BETWEEN @start AND @end AND EXISTS (
                SELECT 1 FROM UNNEST(labels) AS label
                WHERE label.key = 'load_test_run'
                  AND label.value IN UNNEST(@run_labels)
              )) OR
            (creation_time BETWEEN @start AND @audit_end AND EXISTS (
                SELECT 1 FROM UNNEST(labels) AS label
                WHERE label.key = 'load_test_audit'
                  AND label.value IN UNNEST(@run_labels)
              )) OR
            (creation_time BETWEEN @start AND @audit_end
              AND job_id != @current_report_job_id AND EXISTS (
                SELECT 1 FROM UNNEST(labels) AS label
                WHERE label.key = 'load_test_report'
                  AND label.value IN UNNEST(@run_labels)
              ))
          )
        )
        SELECT COUNTIF(is_workload) AS n_total,
               COUNTIF(is_workload AND state = 'DONE' AND error_result IS NULL) AS n_success,
               IFNULL(SUM(IF(state = 'DONE', total_bytes_billed, 0)), 0) AS bytes,
               COUNTIF(is_workload AND state = 'DONE' AND cache_hit
                       AND error_result IS NULL) AS hits,
               COUNTIF(state != 'DONE') AS n_active,
               COUNT(*) AS n_billable
        FROM scoped
        """
        run_labels = [
            load_test_run_label(f"lt_{run_id}-{scenario}:scope")
            for scenario in VALID_SCENARIOS
        ] if prefix else [load_test_run_label(f"lt_{run_id}:scope")]
        if any(not label for label in run_labels) or len(set(run_labels)) != len(run_labels):
            print("  run/suite id không tạo được tập BigQuery label duy nhất")
            return None
        report_job_id = "" if prefix else f"load_report_{uuid.uuid4().hex}"
        params = [
            bigquery.ScalarQueryParameter("start", "TIMESTAMP", start),
            bigquery.ScalarQueryParameter("end", "TIMESTAMP", end),
            bigquery.ScalarQueryParameter("audit_end", "TIMESTAMP", audit_end or end),
            bigquery.ScalarQueryParameter(
                "current_report_job_id", "STRING", report_job_id),
            bigquery.ArrayQueryParameter("run_labels", "STRING", run_labels),
        ]
        cfg_kwargs = {"query_parameters": params, "use_query_cache": False}
        if maximum_bytes_billed is not None:
            cfg_kwargs["maximum_bytes_billed"] = maximum_bytes_billed
        if not prefix:
            cfg_kwargs["labels"] = {"load_test_report": run_labels[0]}
        cfg = bigquery.QueryJobConfig(**cfg_kwargs)
        query_kwargs = {"job_config": cfg}
        if report_job_id:
            query_kwargs["job_id"] = report_job_id
        job = client.query(sql, **query_kwargs)
        counts = list(row_counts(next(iter(job.result()))))
        if not prefix:
            counts[2] += int(job.total_bytes_billed or 0)
            counts[5] += 1
        return tuple(counts)
    except Exception as e:  # noqa: BLE001
        print(f"  không đọc được INFORMATION_SCHEMA: {e}")
        return None


def _read_published_batch(project: str, dataset: str, location: str,
                          maximum_bytes_billed: int, run_id: str) -> dict | None:
    """Đọc pointer batch THẬT của snapshot; None nếu không thể chứng minh.

    Query audit mang nhãn ``load_test_audit=<run>`` riêng: được cộng vào ngân sách nhưng không làm nhiễu
    cache ratio/job count workload. Guard byte vẫn áp dụng để audit không vượt trần đã duyệt.
    """
    try:
        from google.cloud import bigquery
    except Exception:
        return None
    try:
        target = ReadTarget(
            project=project, dataset=dataset, location=location,
            maximum_bytes_billed=maximum_bytes_billed,
        )
        sp = build_current_batch_meta_sql(target)
        audit_label = load_test_run_label(f"lt_{run_id}:scope")
        if not audit_label:
            return None
        cfg = bigquery.QueryJobConfig(
            query_parameters=to_bq_params(sp.params),
            maximum_bytes_billed=maximum_bytes_billed,
            use_query_cache=False,
            labels={"load_test_audit": audit_label},
        )
        rows = list(bigquery.Client(project=project, location=location).query(
            sp.sql, job_config=cfg).result(timeout=60))
        if len(rows) != 1:
            print(f"  snapshot trả {len(rows)} published batch (cần đúng 1)")
            return None
        as_of = rows[0]["data_as_of_at"]
        if not isinstance(as_of, datetime):
            as_of = _parse_utc(str(as_of))
        elif as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)
        return {
            "batch_id": str(rows[0]["batch_id"]),
            "as_of": as_of.astimezone(UTC).isoformat(),
        }
    except Exception as e:  # noqa: BLE001
        print(f"  không đọc được published batch của snapshot: {e}")
        return None


def snapshot_mismatches(expected: dict, actual: dict) -> list[dict]:
    """So provenance dữ liệu khai trong metadata với pointer batch thật."""
    mismatches = []
    if str(expected.get("batch_id") or "") != str(actual.get("batch_id") or ""):
        mismatches.append({"field": "batch_id", "expected": expected.get("batch_id"),
                           "actual": actual.get("batch_id")})
    try:
        expected_as_of = _parse_utc(str(expected.get("as_of") or ""))
        actual_as_of = _parse_utc(str(actual.get("as_of") or ""))
        if expected_as_of != actual_as_of:
            mismatches.append({"field": "as_of", "expected": expected.get("as_of"),
                               "actual": actual.get("as_of")})
    except (TypeError, ValueError):
        mismatches.append({"field": "as_of", "expected": expected.get("as_of"),
                           "actual": actual.get("as_of")})
    return mismatches


def _verify_revision(project: str, region: str, service: str, expected: str) -> bool | None:
    """True nếu `expected` nhận 100% traffic. None nếu không đọc được (gcloud lỗi / JSON hỏng)."""
    try:
        out = subprocess.run(
            ["gcloud", "run", "services", "describe", service,
             f"--project={project}", f"--region={region}", "--format=json"],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            print(f"  gcloud describe lỗi: {out.stderr.strip()[:200]}")
            return None
        data = json.loads(out.stdout)
        return traffic_ok((data.get("status") or {}).get("traffic") or [], expected)
    except Exception as e:  # noqa: BLE001
        print(f"  không kiểm được revision: {e}")
        return None


def _read_deployment(project: str, region: str, service: str,
                     revision: str) -> dict | None:
    """Đọc cấu hình Cloud Run THẬT của service/revision dùng cho nghiệm thu."""
    try:
        service_out = subprocess.run(
            ["gcloud", "run", "services", "describe", service,
             f"--project={project}", f"--region={region}", "--format=json"],
            capture_output=True, text=True, timeout=60,
        )
        revision_out = subprocess.run(
            ["gcloud", "run", "revisions", "describe", revision,
             f"--project={project}", f"--region={region}", "--format=json"],
            capture_output=True, text=True, timeout=60,
        )
        if service_out.returncode != 0 or revision_out.returncode != 0:
            print("  không đọc được service/revision Cloud Run")
            return None
        service_data = json.loads(service_out.stdout)
        revision_data = json.loads(revision_out.stdout)
        spec = revision_data.get("spec") or {}
        containers = spec.get("containers") or []
        container = containers[0] if containers else {}
        env = {
            item.get("name"): item.get("value")
            for item in (container.get("env") or [])
            if item.get("name")
        }
        annotations = (revision_data.get("metadata") or {}).get("annotations") or {}
        service_annotations = (service_data.get("metadata") or {}).get("annotations") or {}
        limits = (container.get("resources") or {}).get("limits") or {}
        return {
            "traffic_100pct": traffic_ok(
                (service_data.get("status") or {}).get("traffic") or [], revision),
            "base_url": (service_data.get("status") or {}).get("url"),
            "image_digest": (revision_data.get("status") or {}).get("imageDigest"),
            "cache_backend": env.get("JOBS_API_CACHE_BACKEND"),
            "bq_use_query_cache": env.get("JOBS_API_BQ_USE_QUERY_CACHE"),
            "bq_maximum_bytes_billed": env.get("JOBS_API_BQ_MAXIMUM_BYTES_BILLED"),
            "bq_project": env.get("JOBS_API_BQ_PROJECT"),
            "bq_dataset": env.get("JOBS_API_BQ_DATASET"),
            "bq_location": env.get("JOBS_API_BQ_LOCATION"),
            "query_timeout_s": env.get("JOBS_API_QUERY_TIMEOUT_S"),
            "cache_ttl_seconds": env.get("JOBS_API_CACHE_TTL_SECONDS"),
            "rate_limit_per_minute": env.get("JOBS_API_RATE_LIMIT_PER_MINUTE"),
            "rate_limit_burst": env.get("JOBS_API_RATE_LIMIT_BURST"),
            "run_concurrency": spec.get("containerConcurrency"),
            "cpu_limit": limits.get("cpu"),
            "memory_limit": limits.get("memory"),
            "min_instances": annotations.get("autoscaling.knative.dev/minScale", "0"),
            "max_instances": annotations.get("autoscaling.knative.dev/maxScale"),
            "service_min_instances": service_annotations.get("run.googleapis.com/minScale", "0"),
            "service_max_instances": service_annotations.get(
                "run.googleapis.com/maxScale", "default"),
            "scaling_mode": service_annotations.get(
                "run.googleapis.com/scalingMode", "automatic"),
            "manual_instance_count": service_annotations.get(
                "run.googleapis.com/manualInstanceCount"),
        }
    except Exception as e:  # noqa: BLE001
        print(f"  không đọc được cấu hình Cloud Run: {e}")
        return None


def deployment_mismatches(expected: dict, actual: dict) -> list[dict]:
    """So cấu hình khai trong metadata với revision thật; trả danh sách sai khác."""
    fields = [
        "base_url", "image_digest", "cache_backend", "bq_use_query_cache",
        "bq_maximum_bytes_billed",
        "bq_project", "bq_dataset", "bq_location",
        "query_timeout_s", "cache_ttl_seconds", "rate_limit_per_minute", "rate_limit_burst",
        "run_concurrency", "cpu_limit", "memory_limit", "min_instances", "max_instances",
        "service_min_instances", "service_max_instances", "scaling_mode",
    ]
    mismatches = []
    for field in fields:
        want = expected.get(field)
        got = actual.get(field)
        want_norm = str(want).strip().lower() if want is not None else ""
        got_norm = str(got).strip().lower() if got is not None else ""
        if want_norm != got_norm:
            mismatches.append({"field": field, "expected": want, "actual": got})
    return mismatches


def _count_app_cache(project: str, service: str, start: str, end: str,
                     revision: str, run_id: str) -> tuple[int, int, int] | None:
    """(hits, misses, other) log /market/metrics của ĐÚNG revision trong cửa sổ — BẰNG CHỨNG app-cache.

    Chỉ 'hit'/'miss' là giá trị HỢP LỆ; bất kỳ giá trị lạ nào → `other` (KHÔNG gộp vào miss — coi như bất
    thường, caller fail-closed). Scope revision_name + request_id prefix để không lẫn revision/run khác.
    None nếu gcloud lỗi.
    total = hits+misses+other; total=0 ⇒ chưa có bằng chứng (log chưa propagate)."""
    request_prefix_re = "^lt_" + re.escape(run_id) + ":"
    flt = (
        'resource.type="cloud_run_revision" '
        f"AND resource.labels.service_name={json.dumps(service)} "
        f"AND resource.labels.revision_name={json.dumps(revision)} "
        'AND jsonPayload.message="market_metrics" '
        f"AND jsonPayload.request_id=~{json.dumps(request_prefix_re)} "
        f'AND timestamp>="{start}" AND timestamp<="{end}"'
    )
    try:
        out = subprocess.run(
            ["gcloud", "logging", "read", flt, f"--project={project}",
             "--format=value(jsonPayload.cache)", "--limit=5000"],
            capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            print(f"  gcloud logging read lỗi: {out.stderr.strip()[:200]}")
            return None
        vals = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        hits = sum(1 for v in vals if v == "hit")
        misses = sum(1 for v in vals if v == "miss")
        other = len(vals) - hits - misses      # giá trị lạ → KHÔNG tính là miss
        return hits, misses, other
    except Exception as e:  # noqa: BLE001
        print(f"  không đọc được Cloud Logging: {e}")
        return None


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp phải có timezone")
    return parsed.astimezone(UTC)


def _instance_evidence(project: str, service: str, region: str, revision: str,
                       start: str, end: str) -> dict | None:
    """Peak instance count trong đúng revision/window; None nếu Monitoring không đọc được."""
    try:
        from google.cloud import monitoring_v3
    except Exception:
        return None
    try:
        start_dt, end_dt = _parse_utc(start), _parse_utc(end)
        if end_dt <= start_dt:
            return None
        interval = monitoring_v3.TimeInterval({
            "start_time": {"seconds": int(start_dt.timestamp())},
            "end_time": {"seconds": int(end_dt.timestamp())},
        })
        flt = (
            'metric.type="run.googleapis.com/container/instance_count" '
            f"AND resource.labels.service_name={json.dumps(service)} "
            f"AND resource.labels.location={json.dumps(region)} "
            f"AND resource.labels.revision_name={json.dumps(revision)}"
        )
        client = monitoring_v3.MetricServiceClient()
        series = client.list_time_series(request={
            "name": f"projects/{project}", "filter": flt, "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        })
        totals: dict[int, float] = defaultdict(float)
        for item in series:
            for point in item.points:
                ts = int(point.interval.end_time.timestamp())
                value = point.value.int64_value or point.value.double_value or 0
                totals[ts] += float(value)
        if not totals:
            return None
        return {
            "source": "cloud_monitoring",
            "metric": "run.googleapis.com/container/instance_count",
            "revision": revision,
            "sample_points": len(totals),
            "peak_instances": max(totals.values()),
            "first_point_utc": datetime.fromtimestamp(min(totals), UTC).isoformat(),
            "last_point_utc": datetime.fromtimestamp(max(totals), UTC).isoformat(),
            "scale_out_observed": max(totals.values()) > 1,
            "verdict": "report_only",
        }
    except Exception as e:  # noqa: BLE001
        print(f"  không đọc được instance evidence: {e}")
        return None


def _expected_market_logs(meta: dict) -> int | None:
    """Số log market_metrics KỲ VỌNG = market_ok_total (MỌI pha, gồm warmup) do load test ghi trong
    metadata. Apples-to-apples với log query (cũng gồm warmup) → warm-up KHÔNG làm phồng completeness.
    None nếu metadata thiếu field (bản cũ / không có --metadata)."""
    v = meta.get("market_ok_total")
    return int(v) if isinstance(v, int) and v > 0 else None


def _fail(msg: str, code: int):
    print(f"[SUITE] {msg}")
    raise SystemExit(code)


def _write(out: str, result: dict, exit_code: int) -> None:
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[SUITE] exit_code={exit_code}  → {out}")


def main(argv: list[str] | None = None) -> None:
    ap = C.InvalidTestArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--location", default="asia-southeast1", help="BigQuery dataset LOCATION (không phải Cloud Run region)")
    ap.add_argument("--budget-gib", type=float, required=True)
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--out", default="suite-result.json")
    # per-scenario mode
    ap.add_argument("--service", default="")
    ap.add_argument("--region", default="asia-southeast1", help="Cloud Run region (cho gcloud)")
    ap.add_argument("--scenario", default="")
    ap.add_argument("--expect-revision", default="")
    ap.add_argument("--metadata", default="", help="run-metadata.json → tự lấy started/ended/scenario/revision")
    ap.add_argument("--locust-process-exit", type=int, default=None,
                    help="exit code thật của process Locust; bắt buộc và phải khớp metadata")
    ap.add_argument("--app-cache-check", choices=["auto", "pass", "fail"], default="auto",
                    help="market_coldmiss: 'auto' = ĐẾM log app-cache hit qua Cloud Logging (bằng chứng); "
                         "'fail' = ghi nhận lỗi thủ công có bằng chứng; 'pass' bị từ chối")
    ap.add_argument("--app-cache-evidence", default="",
                    help="BẮT BUỘC khi --app-cache-check pass|fail: link/đường dẫn tới bằng chứng đã lưu")
    ap.add_argument("--evidence-wait-seconds", type=int, default=120,
                    help="market auto: chờ Cloud Logging ingest đủ log (mặc định 120s; 0 = không chờ)")
    # suite-wide budget mode
    ap.add_argument("--budget-only", action="store_true",
                    help="chỉ cộng bytes trong [start,end] để kiểm NGÂN SÁCH SUITE (bỏ scenario/revision/cache)")
    ap.add_argument("--suite-id", default="",
                    help="BẮT BUỘC với --budget-only; dựng đúng tập run-label của 5 scenario")
    args = ap.parse_args(argv)
    if not math.isfinite(args.budget_gib) or args.budget_gib <= 0:
        _fail("--budget-gib phải là số hữu hạn > 0", EXIT_INVALID)
    budget_bytes = int(args.budget_gib * (1024 ** 3))

    # ── chế độ ngân sách cả suite ─────────────────────────────────────────────
    if args.budget_only:
        if not (args.start and args.end and args.suite_id):
            _fail("--budget-only cần --start, --end và --suite-id", EXIT_INVALID)
        jobs = _query_jobs(args.project, args.location, args.start, args.end,
                           args.suite_id, prefix=True,
                           maximum_bytes_billed=budget_bytes)
        if jobs is None:
            _fail("không đọc được BigQuery jobs", EXIT_UNREADABLE)
        _workload_total, _n_success, total_bytes, _hits, n_active, n_billable = jobs
        if n_active:
            _fail(f"còn {n_active} BigQuery job chưa DONE → bytes chưa chốt; chạy lại finalize",
                  EXIT_UNREADABLE)
        over = total_bytes > budget_bytes
        if n_billable == 0:         # KHÔNG có job → không bằng chứng (cửa sổ/location sai?) → INVALID
            code, passed = EXIT_INVALID, False
        elif over:
            code, passed = EXIT_BUDGET, False
        else:
            code, passed = EXIT_PASS, True
        _write(args.out, {"mode": "budget_only", "window": {"start": args.start, "end": args.end},
                          "suite_id": args.suite_id,
                          "project": args.project, "location": args.location,
                          "n_total": n_billable, "total_bytes_billed": total_bytes,
                          "budget_bytes": budget_bytes, "pass": passed, "exit_code": code}, code)
        raise SystemExit(code)

    # ── chế độ per-scenario ───────────────────────────────────────────────────
    if not args.metadata:
        _fail("finalize per-scenario bắt buộc --metadata để gộp Locust verdict và provenance",
              EXIT_INVALID)
    try:
        with open(args.metadata, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:  # noqa: BLE001
        _fail(f"không đọc được metadata {args.metadata}: {e}", EXIT_UNREADABLE)
    if not isinstance(meta, dict):
        _fail("metadata phải là JSON object", EXIT_INVALID)

    for name, override, recorded in [
        ("start", args.start, meta.get("started_utc", "")),
        ("end", args.end, meta.get("ended_utc", "")),
        ("scenario", args.scenario, meta.get("scenario", "")),
        ("expect-revision", args.expect_revision, meta.get("cloud_run_revision", "")),
    ]:
        if override and override != recorded:
            _fail(f"--{name}={override!r} không khớp metadata {recorded!r}", EXIT_INVALID)

    start = args.start or meta.get("started_utc", "")
    end = args.end or meta.get("ended_utc", "")
    scenario = args.scenario or meta.get("scenario", "")
    expect_rev = args.expect_revision or meta.get("cloud_run_revision", "")
    run_id = meta.get("run_id", "")
    locust_exit = meta.get("exit_code")

    missing = [n for n, v in [("start", start), ("end", end), ("scenario", scenario),
                              ("expect-revision", expect_rev), ("service", args.service),
                              ("run-id", run_id)] if not v]
    if missing:
        _fail(f"thiếu input bắt buộc {missing} (truyền trực tiếp hoặc qua --metadata)", EXIT_INVALID)
    if scenario not in VALID_SCENARIOS:
        _fail(f"scenario '{scenario}' không hợp lệ (chọn: {VALID_SCENARIOS})", EXIT_INVALID)
    if type(locust_exit) is not int or locust_exit not in (0, 2, 3, 4):
        _fail("metadata thiếu exit_code Locust hợp lệ (0|2|3|4)", EXIT_INVALID)
    if args.locust_process_exit is None:
        _fail("finalize per-scenario bắt buộc --locust-process-exit", EXIT_INVALID)
    if args.locust_process_exit not in (0, 2, 3, 4):
        _fail(f"process Locust trả exit code bất thường {args.locust_process_exit}", EXIT_INVALID)
    if args.locust_process_exit != locust_exit:
        _fail(f"exit code process Locust ({args.locust_process_exit}) không khớp metadata "
              f"({locust_exit})", EXIT_INVALID)
    infra_fields = ["base_url", "image_digest", "batch_id", "as_of",
                    "cache_backend", "bq_use_query_cache",
                    "bq_maximum_bytes_billed",
                    "region", "service", "bq_project", "bq_dataset", "bq_location",
                    "query_timeout_s", "cache_ttl_seconds",
                    "rate_limit_per_minute", "rate_limit_burst",
                    "run_concurrency", "cpu_limit", "memory_limit",
                    "min_instances", "max_instances",
                    "service_min_instances", "service_max_instances", "scaling_mode"]
    missing_infra = [field for field in infra_fields if meta.get(field) in (None, "")]
    if missing_infra:
        _fail(f"metadata thiếu provenance hạ tầng {missing_infra}", EXIT_INVALID)
    missing_test_provenance = [
        field for field in ("corpus_hash", "locust_version")
        if meta.get(field) in (None, "")
    ]
    if type(meta.get("load_seed")) is not int:
        missing_test_provenance.append("load_seed(int)")
    if missing_test_provenance:
        _fail(f"metadata thiếu provenance workload {missing_test_provenance}", EXIT_INVALID)
    target_mismatches = [
        name for name, actual, recorded in [
            ("service", args.service, meta["service"]),
            ("region", args.region, meta["region"]),
            ("bq_project", args.project, meta["bq_project"]),
            ("bq_location", args.location, meta["bq_location"]),
        ] if actual != recorded
    ]
    if target_mismatches:
        _fail(f"tham số finalize không khớp metadata run: {target_mismatches}", EXIT_INVALID)

    # app-cache cho market_coldmiss: mặc định auto → BẰNG CHỨNG từ Cloud Logging (đếm hit).
    app_cache_ok = None
    app_cache_evidence = None
    if scenario == "market_coldmiss":
        if args.app_cache_check == "auto":
            if not run_id:
                _fail("market_coldmiss auto cần --metadata có 'run_id' để cô lập log đúng run",
                      EXIT_INVALID)
            # CHỐNG partial-logs PASS giả: log phải ĐỦ so với market_ok_total của run (gồm warmup).
            # BẮT BUỘC biết expected (qua --metadata có market_ok_total) — nếu không, không xác nhận log đủ.
            expected = _expected_market_logs(meta)
            if expected is None:
                _fail("market_coldmiss auto cần --metadata có 'market_ok_total' (kiểm log đã đủ chưa) "
                      "— chỉ auto mới có thể tạo PASS", EXIT_INVALID)
            deadline = time.monotonic() + max(0, args.evidence_wait_seconds)
            while True:
                counts = _count_app_cache(
                    args.project, args.service, start, end, expect_rev, run_id)
                if counts is None:
                    _fail("không đọc được log app-cache (Cloud Logging) → không kết luận được "
                          "(manual chỉ có thể ghi nhận FAIL, không thể tạo PASS)",
                          EXIT_UNREADABLE)
                hits_app, misses_app, other_app = counts
                total_app = hits_app + misses_app + other_app
                if total_app >= expected or time.monotonic() >= deadline:
                    break
                print(f"  Cloud Logging mới có {total_app}/{expected} log; chờ ingest...")
                time.sleep(min(10, max(0, deadline - time.monotonic())))
            if total_app == 0:   # KHÔNG có log market_metrics cho revision → chưa có bằng chứng
                _fail("0 log market_metrics cho revision trong cửa sổ (chưa propagate?/sai revision) "
                      "→ không kết luận được app-cache", EXIT_UNREADABLE)
            if total_app != expected:
                _fail(f"số log market không khớp run ({total_app}/{expected}) "
                      "→ có thể chưa propagate hoặc instrumentation bị lặp; chờ rồi chạy lại finalize",
                      EXIT_UNREADABLE)
            if other_app > 0:    # giá trị cache lạ → bất thường instrumentation, fail-closed
                _fail(f"{other_app} log market có giá trị cache lạ (≠hit/miss) → bất thường, không kết luận",
                      EXIT_INVALID)
            app_cache_ok = hits_app == 0
            app_cache_evidence = {"source": "cloud_logging", "revision": expect_rev,
                                  "run_id": run_id,
                                  "cache_hit_count": hits_app, "cache_miss_count": misses_app,
                                  "other_count": other_app, "total_market_logs": total_app,
                                  "expected_market_requests": expected}
        else:
            # Khẳng định thủ công chỉ dùng để ghi nhận FAIL; không được tạo formal PASS.
            if not args.app_cache_evidence:
                _fail("--app-cache-check pass|fail cần --app-cache-evidence (link/đường dẫn bằng chứng log)",
                      EXIT_INVALID)
            if args.app_cache_check == "pass":
                _fail("manual app-cache 'pass' không được dùng cho verdict tự động; "
                      "hãy dùng --app-cache-check auto", EXIT_INVALID)
            app_cache_ok = args.app_cache_check == "pass"
            app_cache_evidence = {"source": "manual", "asserted": args.app_cache_check,
                                  "evidence_ref": args.app_cache_evidence}

    # Đọc snapshot TRƯỚC usage query để cả audit trước-run và audit sau-run đều được tính vào ngân sách.
    snapshot = _read_published_batch(
        args.project, meta["bq_dataset"], args.location,
        int(meta["bq_maximum_bytes_billed"]), run_id,
    )
    if snapshot is None:
        _fail("không đọc được published batch của snapshot → không xác minh được provenance dữ liệu",
              EXIT_UNREADABLE)
    data_mismatches = snapshot_mismatches(meta, snapshot)

    audit_end = datetime.now(UTC).isoformat()
    jobs = _query_jobs(
        args.project, args.location, start, end, run_id, audit_end=audit_end,
        maximum_bytes_billed=int(meta["bq_maximum_bytes_billed"]),
    )
    if jobs is None:
        _fail("không đọc được BigQuery jobs", EXIT_UNREADABLE)
    n_total, n_success, total_bytes, hits, n_active, n_billable = jobs
    if n_active:
        _fail(f"còn {n_active} BigQuery job chưa DONE → bytes/cache chưa chốt; chạy lại finalize",
              EXIT_UNREADABLE)

    deployment = _read_deployment(args.project, args.region, args.service, expect_rev)
    if deployment is None:
        _fail("không đọc được revision/traffic/config Cloud Run → không kết luận được", EXIT_UNREADABLE)
    revision_ok = bool(deployment["traffic_100pct"])
    mismatches = deployment_mismatches(meta, deployment)

    instance_evidence = None
    if scenario in INSTANCE_EVIDENCE_SCENARIOS:
        phase_windows = meta.get("phase_windows")
        required_phases = INSTANCE_EVIDENCE_PHASES[scenario]
        if not isinstance(phase_windows, dict) or any(
                not isinstance(phase_windows.get(phase), dict)
                or not phase_windows[phase].get("start")
                or not phase_windows[phase].get("end")
                for phase in required_phases):
            _fail(f"metadata thiếu phase_windows cho {list(required_phases)} → "
                  "không thể gắn instance_count đúng pha tải", EXIT_INVALID)
        phase_evidence = {}
        for phase in required_phases:
            window = phase_windows[phase]
            evidence = _instance_evidence(
                args.project, args.service, args.region, expect_rev,
                window["start"], window["end"])
            if evidence is None:
                _fail(f"không đọc được instance_count cho pha {phase} → "
                      "thiếu bằng chứng scale-out", EXIT_UNREADABLE)
            phase_evidence[phase] = evidence
        instance_evidence = {
            "source": "cloud_monitoring",
            "verdict": "report_only",
            "phases": phase_evidence,
            "sample_points": sum(item["sample_points"] for item in phase_evidence.values()),
            "peak_instances": max(item["peak_instances"] for item in phase_evidence.values()),
            "scale_out_observed": any(
                item["scale_out_observed"] for item in phase_evidence.values()),
        }

    result, server_exit = suite_verdict(scenario, n_total, n_success, total_bytes, hits,
                                        budget_bytes, revision_ok, app_cache_ok, app_cache_evidence)
    config_ok = not mismatches
    result["checks"].append({"check": "deployment_config_matches", "pass": config_ok,
                             "mismatches": mismatches})
    if not config_ok:
        server_exit = max(server_exit, EXIT_INVALID)
    snapshot_ok = not data_mismatches
    result["checks"].append({"check": "dataset_snapshot_matches", "pass": snapshot_ok,
                             "mismatches": data_mismatches})
    if not snapshot_ok:
        server_exit = max(server_exit, EXIT_INVALID)
    result["checks"].append({"check": "locust_client_verdict", "pass": locust_exit == 0,
                             "exit_code": locust_exit})
    if instance_evidence is not None:
        result["checks"].append({"check": "instance_evidence", "pass": True,
                                 "report_only": True,
                                 "peak_instances": instance_evidence["peak_instances"]})
    exit_code = max(server_exit, locust_exit)
    result["server_exit_code"] = server_exit
    result["locust_exit_code"] = locust_exit
    result["locust_process_exit_code"] = args.locust_process_exit
    result["exit_code"] = exit_code
    result["pass"] = exit_code == EXIT_PASS
    result["window"] = {"start": start, "end": end}
    result["run_id"] = run_id
    result["billable_job_count"] = n_billable
    result["expect_revision"] = expect_rev
    result["instance_evidence"] = instance_evidence
    result["target"] = {"project": args.project, "service": args.service,
                        "region": args.region, "bq_location": args.location,
                        "bq_dataset": meta["bq_dataset"]}
    result["deployment"] = deployment
    result["dataset_snapshot"] = snapshot
    result["test_provenance"] = {
        "image_digest": meta["image_digest"],
        "corpus_hash": meta["corpus_hash"],
        "load_seed": meta["load_seed"],
        "locust_version": meta["locust_version"],
    }
    _write(args.out, result, exit_code)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
