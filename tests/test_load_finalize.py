"""LOAD-TEST — unit test finalize: suite_verdict thuần + helper traffic_ok/row_counts + adapter (mock)."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest
from google.cloud import bigquery

from tests.load import finalize as F
from tests.load.finalize import (
    EXIT_BUDGET,
    EXIT_INVALID,
    EXIT_PASS,
    EXIT_UNREADABLE,
    row_counts,
    suite_verdict,
    traffic_ok,
)

GIB = 1024 ** 3


def _v(scenario, n_total, n_success, bytes_billed, hits, budget_gib=5, revision_ok=True,
       app_cache_ok=None):
    return suite_verdict(scenario, n_total, n_success, bytes_billed, hits,
                         int(budget_gib * GIB), revision_ok, app_cache_ok)[1]


def test_realistic_pass_within_budget():
    assert _v("realistic", 10000, 10000, 2 * GIB, 8000) == EXIT_PASS   # cache report-only


def test_zero_jobs_invalid_even_realistic():
    # KHÔNG có job trong cửa sổ → không bằng chứng → INVALID (không PASS ngầm)
    assert _v("realistic", 0, 0, 0, 0) == EXIT_INVALID


def test_budget_exceeded_fails():
    assert _v("realistic", 100, 100, 6 * GIB, 0) == EXIT_BUDGET


def test_bq_cold_high_cache_hit_invalid():
    assert _v("bq_cold_search", 1000, 1000, 1 * GIB, 400) == EXIT_INVALID   # 40%


def test_bq_cold_low_cache_hit_pass():
    assert _v("bq_cold_search", 1000, 1000, 1 * GIB, 10) == EXIT_PASS       # 1% < 5%


def test_cache_ratio_only_on_successful_jobs():
    # 1000 job DONE nhưng 900 lỗi; 100 thành công, 4 cache-hit → 4% < 5% (KHÔNG lấy 4/1000)
    assert _v("bq_cold_search", 1000, 100, 1 * GIB, 4) == EXIT_PASS


def test_market_coldmiss_cache_hit_invalid():
    assert _v("market_coldmiss", 500, 500, 1 * GIB, 50) == EXIT_INVALID     # 10%


def test_revision_mismatch_invalid():
    assert _v("realistic", 100, 100, 1 * GIB, 0, revision_ok=False) == EXIT_INVALID


def test_valid_scenarios_excludes_smoke():
    from tests.load.finalize import VALID_SCENARIOS
    assert "smoke" not in VALID_SCENARIOS and "realistic" in VALID_SCENARIOS


# ── market_coldmiss: bằng chứng app-cache nằm TRONG công thức PASS ────────────
def test_market_coldmiss_app_cache_missing_invalid():
    # BQ cache ok (0 hit) nhưng KHÔNG có bằng chứng app-cache (None) → INVALID
    assert _v("market_coldmiss", 500, 500, 1 * GIB, 0, app_cache_ok=None) == EXIT_INVALID


def test_market_coldmiss_app_cache_fail_invalid():
    assert _v("market_coldmiss", 500, 500, 1 * GIB, 0, app_cache_ok=False) == EXIT_INVALID


def test_market_coldmiss_all_pass():
    assert _v("market_coldmiss", 500, 500, 1 * GIB, 0, app_cache_ok=True) == EXIT_PASS


# ── helper traffic_ok (parse từng entry, không tìm chuỗi) ─────────────────────
def test_traffic_ok_true_only_for_100pct_matching_revision():
    traffic = [{"revisionName": "rev-A", "percent": 0}, {"revisionName": "rev-B", "percent": 100}]
    assert traffic_ok(traffic, "rev-B") is True
    assert traffic_ok(traffic, "rev-A") is False        # rev-A ở 0%
    assert traffic_ok(traffic, "rev-C") is False        # không có


def test_traffic_ok_no_false_positive_from_split_entries():
    # rev kỳ vọng ở 30%, một rev khác ở 100% → KHÔNG được PASS
    traffic = [{"revisionName": "rev-A", "percent": 30}, {"revisionName": "rev-X", "percent": 100}]
    assert traffic_ok(traffic, "rev-A") is False


def test_row_counts_mapping():
    assert row_counts({"n_total": "10", "n_success": "8", "bytes": "2048", "hits": "3",
                       "n_active": "1", "n_billable": "12"}) == (10, 8, 2048, 3, 1, 12)


def test_snapshot_mismatches_compares_batch_and_normalized_as_of():
    expected = {"batch_id": "b1", "as_of": "2026-09-01T00:00:00Z"}
    same = {"batch_id": "b1", "as_of": "2026-09-01T00:00:00+00:00"}
    assert F.snapshot_mismatches(expected, same) == []
    fields = {item["field"] for item in F.snapshot_mismatches(
        expected, {"batch_id": "b2", "as_of": "2026-09-02T00:00:00Z"})}
    assert fields == {"batch_id", "as_of"}


def test_read_published_batch_uses_guard_and_returns_pointer(monkeypatch):
    captured = {}

    class _Query:
        def result(self, timeout):
            captured["timeout"] = timeout
            return [{"batch_id": "b1", "data_as_of_at": "2026-09-01T00:00:00Z"}]

    class _Client:
        def __init__(self, **_):
            pass

        def query(self, sql, job_config, **kwargs):
            captured["sql"] = sql
            captured["job_id"] = kwargs.get("job_id")
            captured["guard"] = job_config.maximum_bytes_billed
            captured["cache"] = job_config.use_query_cache
            captured["labels"] = job_config.labels
            return _Query()

    monkeypatch.setattr(bigquery, "Client", _Client)
    assert F._read_published_batch("p", "snapshot", "asia-southeast1", 1234, "run-1") == {
        "batch_id": "b1", "as_of": "2026-09-01T00:00:00+00:00",
    }
    assert captured["guard"] == 1234 and captured["cache"] is False
    assert captured["labels"] == {"load_test_audit": "run-1"}
    assert "warehouse_state" in captured["sql"] and captured["timeout"] == 60


# ── _verify_revision với subprocess mock ──────────────────────────────────────
def _fake_proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_verify_revision_100pct(monkeypatch):
    payload = json.dumps({"status": {"traffic": [{"revisionName": "rev-B", "percent": 100}]}})
    monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: _fake_proc(stdout=payload))
    assert F._verify_revision("p", "r", "svc", "rev-B") is True


def test_verify_revision_gcloud_error_returns_none(monkeypatch):
    monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: _fake_proc(returncode=1, stderr="boom"))
    assert F._verify_revision("p", "r", "svc", "rev-B") is None


def test_verify_revision_bad_json_returns_none(monkeypatch):
    monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: _fake_proc(stdout="{not json"))
    assert F._verify_revision("p", "r", "svc", "rev-B") is None


def test_deployment_mismatches_detects_real_cloud_run_config():
    expected = _base_meta()
    actual = {
        "base_url": "https://svc.example",
        "image_digest": "img@sha256:abc", "cache_backend": "none",
        "bq_use_query_cache": "false", "run_concurrency": 80,
        "bq_maximum_bytes_billed": "2000000000",
        "bq_project": "p", "bq_dataset": "snapshot", "bq_location": "asia-southeast1",
        "query_timeout_s": "10", "cache_ttl_seconds": "300",
        "rate_limit_per_minute": "100000", "rate_limit_burst": "100000",
        "cpu_limit": "2000m", "memory_limit": "512Mi",
        "min_instances": "0", "max_instances": "3",
        "service_min_instances": "0", "service_max_instances": "default",
        "scaling_mode": "automatic", "manual_instance_count": None,
    }
    fields = {item["field"] for item in F.deployment_mismatches(expected, actual)}
    assert fields == {"cache_backend", "bq_use_query_cache", "run_concurrency",
                      "cpu_limit", "min_instances"}


def test_deployment_mismatches_rejects_manual_scaling():
    expected = _base_meta()
    actual = {field: expected.get(field) for field in [
        "base_url", "image_digest", "cache_backend", "bq_use_query_cache",
        "bq_maximum_bytes_billed", "bq_project", "bq_dataset", "bq_location",
        "query_timeout_s", "cache_ttl_seconds", "rate_limit_per_minute", "rate_limit_burst",
        "run_concurrency", "cpu_limit", "memory_limit", "min_instances", "max_instances",
        "service_min_instances", "service_max_instances", "scaling_mode",
    ]}
    actual["scaling_mode"] = "manual"
    assert [item["field"] for item in F.deployment_mismatches(expected, actual)] == [
        "scaling_mode"]


def test_read_deployment_parses_revision_and_service(monkeypatch):
    service = {
        "metadata": {"annotations": {
            "run.googleapis.com/minScale": "0",
        }},
        "status": {"url": "https://svc.example", "traffic": [
            {"revisionName": "rev-B", "percent": 100}]},
    }
    revision = {
        "metadata": {"annotations": {
            "autoscaling.knative.dev/minScale": "1",
            "autoscaling.knative.dev/maxScale": "3",
        }},
        "spec": {"containerConcurrency": 40, "containers": [{
            "resources": {"limits": {"cpu": "1000m", "memory": "512Mi"}}, "env": [
            {"name": "JOBS_API_CACHE_BACKEND", "value": "memory"},
            {"name": "JOBS_API_BQ_USE_QUERY_CACHE", "value": "true"},
            {"name": "JOBS_API_BQ_MAXIMUM_BYTES_BILLED", "value": "2000000000"},
                {"name": "JOBS_API_BQ_PROJECT", "value": "p"},
                {"name": "JOBS_API_BQ_DATASET", "value": "snapshot"},
                {"name": "JOBS_API_BQ_LOCATION", "value": "asia-southeast1"},
                {"name": "JOBS_API_QUERY_TIMEOUT_S", "value": "10"},
                {"name": "JOBS_API_CACHE_TTL_SECONDS", "value": "300"},
                {"name": "JOBS_API_RATE_LIMIT_PER_MINUTE", "value": "100000"},
                {"name": "JOBS_API_RATE_LIMIT_BURST", "value": "100000"},
        ]}]},
        "status": {"imageDigest": "img@sha256:abc"},
    }
    replies = iter([_fake_proc(stdout=json.dumps(service)), _fake_proc(stdout=json.dumps(revision))])
    monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: next(replies))
    actual = F._read_deployment("p", "r", "svc", "rev-B")
    assert actual == {
        "traffic_100pct": True, "base_url": "https://svc.example",
        "image_digest": "img@sha256:abc", "cache_backend": "memory",
        "bq_use_query_cache": "true", "run_concurrency": 40,
        "bq_maximum_bytes_billed": "2000000000",
        "bq_project": "p", "bq_dataset": "snapshot", "bq_location": "asia-southeast1",
        "query_timeout_s": "10", "cache_ttl_seconds": "300",
        "rate_limit_per_minute": "100000", "rate_limit_burst": "100000",
        "cpu_limit": "1000m", "memory_limit": "512Mi",
        "min_instances": "1", "max_instances": "3",
        "service_min_instances": "0", "service_max_instances": "default",
        "scaling_mode": "automatic", "manual_instance_count": None,
    }


@pytest.mark.parametrize("prefix", [False, True])
def test_query_jobs_is_scoped_to_load_run_label(monkeypatch, prefix):
    captured = {}

    class _Query:
        total_bytes_billed = 7

        def result(self):
            return iter([{"n_total": 2, "n_success": 2, "bytes": 10, "hits": 0,
                          "n_active": 0, "n_billable": 3}])

    class _Client:
        def __init__(self, **_):
            pass

        def query(self, sql, job_config, **kwargs):
            captured["sql"] = sql
            captured["job_id"] = kwargs.get("job_id")
            captured["labels"] = job_config.labels
            captured["maximum_bytes_billed"] = job_config.maximum_bytes_billed
            captured["use_query_cache"] = job_config.use_query_cache
            captured["params"] = {
                p.name: p.values if hasattr(p, "values") else p.value
                for p in job_config.query_parameters
            }
            return _Query()

    monkeypatch.setattr(bigquery, "Client", _Client)
    assert F._query_jobs("p", "asia-southeast1", "2026-09-01T00:00:00Z",
                         "2026-09-01T00:01:00Z", "suite-01", prefix=prefix,
                         maximum_bytes_billed=999) == (
        (2, 2, 10, 0, 0, 3) if prefix else (2, 2, 17, 0, 0, 4))
    assert "UNNEST(labels)" in captured["sql"]
    assert all(label in captured["sql"] for label in (
        "load_test_run", "load_test_audit", "load_test_report"))
    assert captured["maximum_bytes_billed"] == 999
    assert captured["use_query_cache"] is False
    if prefix:
        assert captured["labels"] == {}
        assert captured["job_id"] is None
        assert set(captured["params"]["run_labels"]) == {
            f"suite-01-{scenario}" for scenario in F.VALID_SCENARIOS
        }
    else:
        assert captured["labels"] == {"load_test_report": "suite-01"}
        assert captured["job_id"].startswith("load_report_")
        assert captured["params"]["current_report_job_id"] == captured["job_id"]
        assert captured["params"]["run_labels"] == ["suite-01"]


def test_instance_evidence_sums_series_by_timestamp(monkeypatch):
    from google.cloud import monitoring_v3

    def point(ts, *, integer=0, decimal=0.0):
        return SimpleNamespace(
            interval=SimpleNamespace(end_time=SimpleNamespace(timestamp=lambda: ts)),
            value=SimpleNamespace(int64_value=integer, double_value=decimal),
        )

    class _Client:
        def list_time_series(self, request):
            assert 'resource.labels.revision_name="rev-B"' in request["filter"]
            return [
                SimpleNamespace(points=[point(100, integer=1), point(200, integer=2)]),
                SimpleNamespace(points=[point(100, integer=1), point(200, integer=1)]),
            ]

    monkeypatch.setattr(monitoring_v3, "MetricServiceClient", _Client)
    evidence = F._instance_evidence(
        "p", "svc", "asia-southeast1", "rev-B",
        "2026-09-01T00:00:00Z", "2026-09-01T00:05:00Z")
    assert evidence["sample_points"] == 2
    assert evidence["peak_instances"] == 3.0
    assert evidence["scale_out_observed"] is True


def test_instance_evidence_empty_or_invalid_window_is_unreadable(monkeypatch):
    from google.cloud import monitoring_v3

    class _Client:
        def list_time_series(self, request):
            return []

    monkeypatch.setattr(monitoring_v3, "MetricServiceClient", _Client)
    assert F._instance_evidence(
        "p", "svc", "r", "rev", "2026-09-01T00:00:00Z",
        "2026-09-01T00:05:00Z") is None
    assert F._instance_evidence(
        "p", "svc", "r", "rev", "2026-09-01T00:05:00Z",
        "2026-09-01T00:00:00Z") is None


# ── main() CLI-level: fail-closed + ghi suite-result.json ─────────────────────
def _base_meta(scenario="realistic", *, locust_exit=0):
    return {
        "started_utc": "S", "ended_utc": "E", "scenario": scenario,
        "cloud_run_revision": "rev-B", "run_id": "run-1", "exit_code": locust_exit,
        "base_url": "https://svc.example",
        "image_digest": "img@sha256:abc", "batch_id": "batch-20260901",
        "corpus_hash": "abc123", "load_seed": 1234, "locust_version": "2.32.4",
        "as_of": "2026-09-01T00:00:00Z", "cache_backend": "memory",
        "bq_use_query_cache": "true", "run_concurrency": "40",
        "bq_maximum_bytes_billed": "2000000000",
        "region": "asia-southeast1", "service": "svc",
        "bq_project": "p", "bq_dataset": "snapshot", "bq_location": "asia-southeast1",
        "query_timeout_s": "10", "cache_ttl_seconds": "300",
        "rate_limit_per_minute": "100000", "rate_limit_burst": "100000",
        "cpu_limit": "1000m", "memory_limit": "512Mi",
        "min_instances": "1", "max_instances": "3",
        "service_min_instances": "0", "service_max_instances": "default",
        "scaling_mode": "automatic",
        "phase_windows": {
            "step2": {"start": "2026-09-01T00:05:00Z", "end": "2026-09-01T00:10:00Z"},
            "step3": {"start": "2026-09-01T00:10:00Z", "end": "2026-09-01T00:15:00Z"},
        },
    }


def _run_main(monkeypatch, argv, jobs=(100, 100, GIB, 0), revision=True,
              instance_evidence=True, snapshot=None):
    argv = list(argv)
    if ("--budget-only" not in argv and "--locust-process-exit" not in argv
            and "--metadata" in argv):
        metadata_path = argv[argv.index("--metadata") + 1]
        with open(metadata_path, encoding="utf-8") as f:
            metadata_exit = json.load(f)["exit_code"]
        argv.extend(["--locust-process-exit", str(metadata_exit)])
    if len(jobs) == 4:
        jobs = (*jobs, 0, jobs[0])
    elif len(jobs) == 5:
        jobs = (*jobs, jobs[0])
    monkeypatch.setattr(F, "_query_jobs", lambda *a, **k: jobs)
    monkeypatch.setattr(F, "_read_deployment", lambda *a, **k: {
        "traffic_100pct": revision, "base_url": "https://svc.example",
        "image_digest": "img@sha256:abc", "cache_backend": "memory",
        "bq_use_query_cache": "true", "run_concurrency": 40,
        "bq_maximum_bytes_billed": "2000000000",
        "bq_project": "p", "bq_dataset": "snapshot", "bq_location": "asia-southeast1",
        "query_timeout_s": "10", "cache_ttl_seconds": "300",
        "rate_limit_per_minute": "100000", "rate_limit_burst": "100000",
        "cpu_limit": "1000m", "memory_limit": "512Mi",
        "min_instances": "1", "max_instances": "3",
        "service_min_instances": "0", "service_max_instances": "default",
        "scaling_mode": "automatic", "manual_instance_count": None,
    } if revision is not None else None)
    monkeypatch.setattr(F, "deployment_mismatches", lambda *a, **k: [])
    if snapshot is None:
        snapshot = {"batch_id": "batch-20260901", "as_of": "2026-09-01T00:00:00Z"}
    monkeypatch.setattr(F, "_read_published_batch", lambda *a, **k: snapshot)
    evidence = {
        "source": "cloud_monitoring", "metric": "instance_count", "revision": "rev-B",
        "sample_points": 3, "peak_instances": 2.0, "scale_out_observed": True,
        "verdict": "report_only",
    }
    monkeypatch.setattr(F, "_instance_evidence", lambda *a, **k: evidence
                        if instance_evidence else None)
    with pytest.raises(SystemExit) as e:
        F.main(argv)
    return e.value.code


def test_main_writes_result_and_passes(monkeypatch, tmp_path):
    out = tmp_path / "suite.json"
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(_base_meta()), encoding="utf-8")
    code = _run_main(monkeypatch, [
        "--project", "p", "--location", "asia-southeast1", "--service", "svc",
        "--budget-gib", "5", "--metadata", str(meta), "--out", str(out)])
    assert code == EXIT_PASS
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["scenario"] == "realistic" and data["exit_code"] == 0
    assert data["locust_exit_code"] == 0 and data["target"]["service"] == "svc"
    assert data["target"]["bq_dataset"] == "snapshot"
    assert data["test_provenance"]["corpus_hash"] == "abc123"
    assert set(data["instance_evidence"]["phases"]) == {"step2", "step3"}


def test_main_realistic_requires_phase_windows(monkeypatch, tmp_path):
    meta_data = _base_meta()
    meta_data.pop("phase_windows")
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(meta_data), encoding="utf-8")
    code = _run_main(monkeypatch, [
        "--project", "p", "--service", "svc", "--budget-gib", "5",
        "--metadata", str(meta), "--out", str(tmp_path / "s.json")])
    assert code == EXIT_INVALID


def test_main_realistic_unreadable_phase_instance_evidence_exit5(monkeypatch, tmp_path):
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(_base_meta()), encoding="utf-8")
    code = _run_main(monkeypatch, [
        "--project", "p", "--service", "svc", "--budget-gib", "5",
        "--metadata", str(meta), "--out", str(tmp_path / "s.json")],
        instance_evidence=False)
    assert code == EXIT_UNREADABLE


def test_main_unreadable_revision_exit5(monkeypatch, tmp_path):
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(_base_meta()), encoding="utf-8")
    code = _run_main(monkeypatch, [
        "--project", "p", "--service", "svc", "--budget-gib", "5",
        "--metadata", str(meta), "--out", str(tmp_path / "s.json")], revision=None)
    assert code == EXIT_UNREADABLE


def test_main_rejects_snapshot_batch_or_as_of_mismatch(monkeypatch, tmp_path):
    out = tmp_path / "suite.json"
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(_base_meta()), encoding="utf-8")
    code = _run_main(monkeypatch, [
        "--project", "p", "--service", "svc", "--budget-gib", "5",
        "--metadata", str(meta), "--out", str(out)],
        snapshot={"batch_id": "another-batch", "as_of": "2026-09-02T00:00:00Z"})
    assert code == EXIT_INVALID
    check = next(item for item in json.loads(out.read_text(encoding="utf-8"))["checks"]
                 if item["check"] == "dataset_snapshot_matches")
    assert check["pass"] is False
    assert {item["field"] for item in check["mismatches"]} == {"batch_id", "as_of"}


def test_main_unreadable_snapshot_exit5(monkeypatch, tmp_path):
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(_base_meta()), encoding="utf-8")
    monkeypatch.setattr(F, "_query_jobs", lambda *a, **k: (100, 100, GIB, 0, 0))
    monkeypatch.setattr(F, "_read_deployment", lambda *a, **k: {"traffic_100pct": True})
    monkeypatch.setattr(F, "deployment_mismatches", lambda *a, **k: [])
    monkeypatch.setattr(F, "_read_published_batch", lambda *a, **k: None)
    with pytest.raises(SystemExit) as exc:
        F.main(["--project", "p", "--service", "svc", "--budget-gib", "5",
                "--metadata", str(meta), "--locust-process-exit", "0",
                "--out", str(tmp_path / "s.json")])
    assert exc.value.code == EXIT_UNREADABLE


def test_main_rejects_active_bigquery_jobs_as_unsettled_cost(monkeypatch, tmp_path):
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(_base_meta()), encoding="utf-8")
    code = _run_main(monkeypatch, [
        "--project", "p", "--service", "svc", "--budget-gib", "5",
        "--metadata", str(meta), "--out", str(tmp_path / "s.json")],
        jobs=(100, 99, GIB, 0, 1))
    assert code == EXIT_UNREADABLE


def test_main_rejects_finalize_target_different_from_run_metadata(monkeypatch, tmp_path):
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(_base_meta()), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        F.main(["--project", "another-project", "--service", "svc", "--budget-gib", "5",
                "--metadata", str(meta), "--locust-process-exit", "0",
                "--out", str(tmp_path / "s.json")])
    assert exc.value.code == EXIT_INVALID


def test_main_combines_locust_failure_into_scenario_result(monkeypatch, tmp_path):
    out = tmp_path / "suite.json"
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(_base_meta(locust_exit=2)), encoding="utf-8")
    code = _run_main(monkeypatch, [
        "--project", "p", "--service", "svc", "--budget-gib", "5",
        "--metadata", str(meta), "--out", str(out)])
    assert code == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["locust_exit_code"] == 2 and data["server_exit_code"] == 0
    assert data["locust_process_exit_code"] == 2


def test_main_rejects_locust_process_exit_mismatch(monkeypatch, tmp_path):
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(_base_meta(locust_exit=0)), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        F.main(["--project", "p", "--service", "svc", "--budget-gib", "5",
                "--metadata", str(meta), "--locust-process-exit", "1"])
    assert exc.value.code == EXIT_INVALID


def test_main_bad_metadata_exit5(monkeypatch, tmp_path):
    bad = tmp_path / "meta.json"
    bad.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        F.main(["--project", "p", "--service", "svc", "--budget-gib", "5",
                "--metadata", str(bad), "--out", str(tmp_path / "s.json")])
    assert e.value.code == EXIT_UNREADABLE


def test_main_rejects_non_object_or_conflicting_metadata(tmp_path):
    meta = tmp_path / "meta.json"
    meta.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        F.main(["--project", "p", "--service", "svc", "--budget-gib", "5",
                "--metadata", str(meta)])
    assert exc.value.code == EXIT_INVALID

    meta.write_text(json.dumps(_base_meta()), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        F.main(["--project", "p", "--service", "svc", "--budget-gib", "5",
                "--metadata", str(meta), "--scenario", "ops_baseline"])
    assert exc.value.code == EXIT_INVALID


def test_main_budget_only_mode(monkeypatch, tmp_path):
    out = tmp_path / "suite.json"
    # tổng bytes 6 GiB > budget 5 → BUDGET_EXCEEDED, KHÔNG cần scenario/revision
    code = _run_main(monkeypatch, [
        "--project", "p", "--budget-gib", "5", "--service", "x",
        "--start", "S", "--end", "E", "--suite-id", "suite-1",
        "--budget-only", "--out", str(out)],
        jobs=(9000, 9000, 6 * GIB, 0))
    assert code == EXIT_BUDGET
    assert json.loads(out.read_text(encoding="utf-8"))["mode"] == "budget_only"


# ── app-cache BẰNG CHỨNG từ Cloud Logging (scope revision, (hits,misses,other)) ──
def test_count_app_cache_parses_hits_misses(monkeypatch):
    monkeypatch.setattr(F.subprocess, "run",
                        lambda *a, **k: _fake_proc(stdout="miss\nhit\nmiss\nhit\nmiss\n"))
    assert F._count_app_cache("p", "svc", "S", "E", "rev-B", "run-1") == (2, 3, 0)


def test_count_app_cache_invalid_value_is_other_not_miss(monkeypatch):
    # "HIT"/"true" là giá trị lạ → other, KHÔNG được tính vào miss
    monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: _fake_proc(stdout="miss\nHIT\ntrue\n"))
    assert F._count_app_cache("p", "svc", "S", "E", "rev-B", "run-1") == (0, 1, 2)


def test_count_app_cache_no_logs_zero(monkeypatch):
    monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: _fake_proc(stdout="\n  \n"))
    assert F._count_app_cache("p", "svc", "S", "E", "rev-B", "run-1") == (0, 0, 0)


def test_count_app_cache_error_none(monkeypatch):
    monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: _fake_proc(returncode=1, stderr="denied"))
    assert F._count_app_cache("p", "svc", "S", "E", "rev-B", "run-1") is None


def test_count_app_cache_scopes_filter_to_run_id(monkeypatch):
    captured = {}

    def fake_run(cmd, **_kwargs):
        captured["filter"] = cmd[3]
        return _fake_proc(stdout="miss\n")

    monkeypatch.setattr(F.subprocess, "run", fake_run)
    assert F._count_app_cache("p", "svc", "S", "E", "rev-B", "run.1") == (0, 1, 0)
    expected = json.dumps("^lt_" + F.re.escape("run.1") + ":")
    assert f"jsonPayload.request_id=~{expected}" in captured["filter"]


def _market_argv(tmp_path, market_success=300, extra=()):
    """Ghi metadata (có market_ok_total) rồi trả argv dùng --metadata (auto cần expected)."""
    meta = _base_meta("market_coldmiss")
    meta.update({"cache_backend": "none", "bq_use_query_cache": "false",
                 "market_ok_total": market_success})
    (tmp_path / "run-metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return ["--project", "p", "--service", "svc", "--budget-gib", "5",
            "--metadata", str(tmp_path / "run-metadata.json"),
            "--evidence-wait-seconds", "0", "--out", str(tmp_path / "s.json"), *extra]


def test_main_market_coldmiss_auto_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(F, "_count_app_cache", lambda *a, **k: (0, 300, 0))   # 0 hit, 300 miss → pass
    code = _run_main(monkeypatch, _market_argv(tmp_path, market_success=300), jobs=(500, 500, GIB, 0))
    assert code == EXIT_PASS
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert data["app_cache_ok"] is True


def test_main_market_coldmiss_auto_hits_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(F, "_count_app_cache", lambda *a, **k: (7, 293, 0))   # có hit → INVALID
    code = _run_main(monkeypatch, _market_argv(tmp_path, market_success=300), jobs=(500, 500, GIB, 0))
    assert code == EXIT_INVALID
    ev = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert any(c.get("check") == "app_cache_miss" and c["evidence"]["cache_hit_count"] == 7
               for c in ev["checks"])


def test_main_market_coldmiss_other_value_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(F, "_count_app_cache", lambda *a, **k: (0, 97, 3))   # đủ 100 log, 3 giá trị lạ
    code = _run_main(monkeypatch, _market_argv(tmp_path, market_success=100), jobs=(500, 500, GIB, 0))
    assert code == EXIT_INVALID


def test_main_market_coldmiss_no_logs_exit5(monkeypatch, tmp_path):
    monkeypatch.setattr(F, "_count_app_cache", lambda *a, **k: (0, 0, 0))   # total=0 → chưa bằng chứng → 5
    code = _run_main(monkeypatch, _market_argv(tmp_path), jobs=(500, 500, GIB, 0))
    assert code == EXIT_UNREADABLE


def test_main_market_coldmiss_auto_without_metadata_invalid(monkeypatch, tmp_path):
    # auto mà KHÔNG có --metadata → không biết expected → không thể chống partial-logs → INVALID
    monkeypatch.setattr(F, "_count_app_cache", lambda *a, **k: (0, 300, 0))
    code = _run_main(monkeypatch, [
        "--project", "p", "--service", "svc", "--budget-gib", "5", "--start", "S", "--end", "E",
        "--scenario", "market_coldmiss", "--expect-revision", "rev-B", "--out", str(tmp_path / "s.json")],
        jobs=(500, 500, GIB, 0))
    assert code == EXIT_INVALID


def test_main_market_coldmiss_partial_logs_exit5(monkeypatch, tmp_path):
    # expected = 480; log chỉ 100 → chưa đủ → 5
    monkeypatch.setattr(F, "_count_app_cache", lambda *a, **k: (0, 100, 0))
    code = _run_main(monkeypatch, _market_argv(tmp_path, market_success=480), jobs=(500, 500, GIB, 0))
    assert code == EXIT_UNREADABLE


def test_main_market_coldmiss_one_log_missing_exit5(monkeypatch, tmp_path):
    monkeypatch.setattr(F, "_count_app_cache", lambda *a, **k: (0, 299, 0))
    code = _run_main(monkeypatch, _market_argv(tmp_path, market_success=300), jobs=(500, 500, GIB, 0))
    assert code == EXIT_UNREADABLE


def test_main_market_coldmiss_extra_log_exit5(monkeypatch, tmp_path):
    monkeypatch.setattr(F, "_count_app_cache", lambda *a, **k: (0, 301, 0))
    code = _run_main(monkeypatch, _market_argv(tmp_path, market_success=300), jobs=(500, 500, GIB, 0))
    assert code == EXIT_UNREADABLE


def test_main_market_coldmiss_log_unreadable_exit5(monkeypatch, tmp_path):
    monkeypatch.setattr(F, "_count_app_cache", lambda *a, **k: None)   # gcloud lỗi → 5
    code = _run_main(monkeypatch, _market_argv(tmp_path), jobs=(500, 500, GIB, 0))
    assert code == EXIT_UNREADABLE


def test_main_market_coldmiss_manual_without_evidence_invalid(monkeypatch, tmp_path):
    # manual pass mà KHÔNG có --app-cache-evidence → lối tắt bị chặn (exit 3)
    code = _run_main(monkeypatch, _market_argv(tmp_path, extra=["--app-cache-check", "pass"]),
                     jobs=(500, 500, GIB, 0))
    assert code == EXIT_INVALID


def test_main_market_coldmiss_manual_pass_with_evidence_is_still_invalid(monkeypatch, tmp_path):
    code = _run_main(monkeypatch, _market_argv(
        tmp_path, extra=["--app-cache-check", "pass", "--app-cache-evidence", "gs://logs/market-miss.txt"]),
        jobs=(500, 500, GIB, 0))
    assert code == EXIT_INVALID


def test_main_budget_only_zero_jobs_invalid(monkeypatch, tmp_path):
    out = tmp_path / "suite.json"
    # 0 job trong cửa sổ (window/location sai?) → KHÔNG PASS ngầm dù bytes=0 < budget
    code = _run_main(monkeypatch, [
        "--project", "p", "--budget-gib", "5", "--service", "x",
        "--start", "S", "--end", "E", "--suite-id", "suite-1",
        "--budget-only", "--out", str(out)],
        jobs=(0, 0, 0, 0))
    assert code == EXIT_INVALID
    assert json.loads(out.read_text(encoding="utf-8"))["pass"] is False


@pytest.mark.parametrize("budget", ["0", "-1", "nan", "inf"])
def test_main_rejects_non_positive_or_non_finite_budget(budget):
    with pytest.raises(SystemExit) as exc:
        F.main(["--project", "p", "--budget-gib", budget, "--budget-only",
                "--start", "S", "--end", "E", "--suite-id", "suite"])
    assert exc.value.code == EXIT_INVALID


def test_finalize_cli_parse_error_is_invalid_test():
    with pytest.raises(SystemExit) as exc:
        F.main(["--app-cache-check", "typo"])
    assert exc.value.code == EXIT_INVALID
