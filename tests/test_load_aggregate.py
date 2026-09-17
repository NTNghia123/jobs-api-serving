from __future__ import annotations

import json

import pytest

from tests.load import aggregate as A

SUITE = "suite01"
START = "2026-09-01T00:00:00Z"
END = "2026-09-01T00:10:00Z"


def _result(name, code=0):
    check_names = [
        "has_jobs", "budget", "bq_cache_hit", "revision_matches_100pct",
        "deployment_config_matches", "dataset_snapshot_matches", "locust_client_verdict",
    ]
    if name == "market_coldmiss":
        check_names.append("app_cache_miss")
    if name in {"realistic", "bq_cold_search"}:
        check_names.append("instance_evidence")
    result = {
        "scenario": name, "run_id": f"{SUITE}-{name}", "locust_exit_code": code,
        "locust_process_exit_code": code,
        "server_exit_code": 0, "exit_code": code, "pass": code == 0,
        "window": {"start": START, "end": END}, "n_total": 10,
        "billable_job_count": 10,
        "total_bytes_billed": 100,
        "target": {"project": "p", "service": "svc", "region": "r",
                   "bq_location": "asia-southeast1", "bq_dataset": "snapshot"},
        "deployment": {
            "traffic_100pct": True, "base_url": "https://svc.example",
            "bq_maximum_bytes_billed": "2000000000", "bq_project": "p",
            "bq_dataset": "snapshot", "bq_location": "asia-southeast1",
            "query_timeout_s": "10", "cache_ttl_seconds": "300",
            "rate_limit_per_minute": "100000", "rate_limit_burst": "100000",
            "run_concurrency": 40, "cpu_limit": "1000m", "memory_limit": "512Mi",
            "min_instances": "1", "max_instances": "3",
            "service_min_instances": "0", "service_max_instances": "default",
            "scaling_mode": "automatic", "manual_instance_count": None,
        },
        "expect_revision": "rev-1",
        "dataset_snapshot": {"batch_id": "batch-1", "as_of": START},
        "test_provenance": {
            "image_digest": "img@sha256:abc", "corpus_hash": "abc123",
            "load_seed": 1234, "locust_version": "2.32.4",
        },
        "checks": [{"check": item, "pass": True} for item in check_names],
        "instance_evidence": None,
    }
    if name in {"realistic", "bq_cold_search"}:
        required_phases = ("step2", "step3") if name == "realistic" else ("step2",)
        result["instance_evidence"] = {
            "source": "cloud_monitoring", "sample_points": 3,
            "peak_instances": 2.0, "verdict": "report_only",
            "phases": {
                phase: {
                    "source": "cloud_monitoring", "sample_points": 3,
                    "peak_instances": 2.0, "scale_out_observed": True,
                    "revision": "rev-1", "verdict": "report_only",
                }
                for phase in required_phases
            },
            "scale_out_observed": True,
        }
        result["instance_evidence"]["sample_points"] = 3 * len(required_phases)
    return result


def _budget(**overrides):
    value = {
        "mode": "budget_only", "suite_id": SUITE, "pass": True, "exit_code": 0,
        "window": {"start": "2026-08-31T23:59:00Z", "end": "2026-09-01T00:11:00Z"},
        "project": "p", "location": "asia-southeast1",
        "n_total": 50, "total_bytes_billed": 500, "budget_bytes": 1000,
    }
    value.update(overrides)
    return value


def _scenarios(code=0):
    return [(f"{name}.json", _result(name, code)) for name in A.VALID_SCENARIOS]


def test_aggregate_passes_only_when_all_scenarios_and_budget_pass():
    result, code = A.aggregate_results(
        SUITE, _scenarios(), _budget())
    assert code == 0 and result["pass"] is True


def test_aggregate_propagates_scenario_failure():
    scenarios = _scenarios()
    scenarios[0][1]["locust_exit_code"] = 4
    scenarios[0][1]["locust_process_exit_code"] = 4
    scenarios[0][1]["exit_code"] = 4
    scenarios[0][1]["pass"] = False
    next(item for item in scenarios[0][1]["checks"]
         if item["check"] == "locust_client_verdict")["pass"] = False
    result, code = A.aggregate_results(
        SUITE, scenarios, _budget())
    assert code == 4 and result["pass"] is False


def test_aggregate_missing_or_duplicate_scenario_is_invalid():
    scenarios = _scenarios()[:-1]
    scenarios.append(scenarios[0])
    result, code = A.aggregate_results(
        SUITE, scenarios, _budget())
    assert code == 3 and result["pass"] is False
    assert result["missing_scenarios"] and result["duplicate_scenarios"]


def test_aggregate_rejects_old_or_inconsistent_scenario_artifact():
    scenarios = _scenarios()
    scenarios[0][1].pop("locust_exit_code")
    result, code = A.aggregate_results(
        SUITE, scenarios, _budget())
    assert code == 3 and result["pass"] is False
    assert result["invalid_scenario_artifacts"] == [scenarios[0][0]]


def test_aggregate_rejects_artifact_from_another_suite():
    scenarios = _scenarios()
    scenarios[0][1]["run_id"] = f"old-suite-{scenarios[0][1]['scenario']}"
    result, code = A.aggregate_results(
        SUITE, scenarios, _budget())
    assert code == 3 and result["invalid_scenario_artifacts"] == [scenarios[0][0]]


def test_aggregate_rejects_narrow_budget_window_or_undercounted_totals():
    scenarios = _scenarios()
    narrow = _budget(window={"start": "2026-09-01T00:05:00Z", "end": END})
    result, code = A.aggregate_results(SUITE, scenarios, narrow)
    assert code == 3 and result["budget_covers_scenarios"] is False

    undercounted = _budget(n_total=49, total_bytes_billed=499)
    result, code = A.aggregate_results(SUITE, scenarios, undercounted)
    assert code == 3 and result["budget_totals_cover_scenarios"] is False

    overcounted = _budget(n_total=51, total_bytes_billed=501)
    result, code = A.aggregate_results(SUITE, scenarios, overcounted)
    assert code == 3 and result["budget_totals_cover_scenarios"] is False


def test_aggregate_rejects_minimal_exit_code_only_artifacts():
    scenarios = [(name, {
        "scenario": name, "run_id": f"{SUITE}-{name}",
        "locust_exit_code": 0, "server_exit_code": 0, "exit_code": 0,
    }) for name in A.VALID_SCENARIOS]
    result, code = A.aggregate_results(SUITE, scenarios, _budget())
    assert code == 3 and len(result["invalid_scenario_artifacts"]) == len(A.VALID_SCENARIOS)


def test_aggregate_rejects_cross_target_artifacts_or_budget():
    scenarios = _scenarios()
    scenarios[0][1]["target"]["service"] = "another-service"
    result, code = A.aggregate_results(SUITE, scenarios, _budget())
    assert code == 3 and result["targets_match"] is False

    scenarios = _scenarios()
    scenarios[0][1]["target"]["bq_dataset"] = "another-snapshot"
    result, code = A.aggregate_results(SUITE, scenarios, _budget())
    assert code == 3 and result["targets_match"] is False

    result, code = A.aggregate_results(
        SUITE, _scenarios(), _budget(project="another-project"))
    assert code == 3 and result["targets_match"] is False


def test_aggregate_rejects_different_published_snapshot_across_scenarios():
    scenarios = _scenarios()
    scenarios[0][1]["dataset_snapshot"]["batch_id"] = "another-batch"
    result, code = A.aggregate_results(SUITE, scenarios, _budget())
    assert code == 3 and result["snapshots_match"] is False


def test_aggregate_rejects_different_code_or_workload_provenance():
    scenarios = _scenarios()
    scenarios[0][1]["test_provenance"]["load_seed"] = 999
    result, code = A.aggregate_results(SUITE, scenarios, _budget())
    assert code == 3 and result["test_provenance_matches"] is False


def test_aggregate_rejects_different_stable_infrastructure():
    scenarios = _scenarios()
    scenarios[0][1]["deployment"]["run_concurrency"] = 80
    result, code = A.aggregate_results(SUITE, scenarios, _budget())
    assert code == 3 and result["stable_infrastructure_matches"] is False


def test_aggregate_cli_unreadable_input_exit5(tmp_path):
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps(_budget()),
                      encoding="utf-8")
    out = tmp_path / "final.json"
    with pytest.raises(SystemExit) as exc:
        A.main(["--suite-id", SUITE, "--scenario-result", str(tmp_path / "missing.json"),
                "--budget-result", str(budget), "--out", str(out)])
    assert exc.value.code == 5
    assert json.loads(out.read_text(encoding="utf-8"))["pass"] is False
