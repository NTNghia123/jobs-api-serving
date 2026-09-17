"""Tạo verdict cuối duy nhất cho toàn bộ load-test suite.

Đọc đúng một kết quả cho mỗi scenario nghiệm thu và một kết quả budget-only. Thiếu, trùng,
JSON hỏng hoặc bất kỳ exit_code khác 0 đều làm suite không PASS.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from tests.load.config import InvalidTestArgumentParser
from tests.load.finalize import EXIT_INVALID, EXIT_PASS, EXIT_UNREADABLE, VALID_SCENARIOS


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _timestamp(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except ValueError:
        return None


def _window(value) -> tuple[datetime, datetime] | None:
    if not isinstance(value, dict):
        return None
    start, end = _timestamp(value.get("start")), _timestamp(value.get("end"))
    return (start, end) if start and end and start < end else None


def _scenario_artifact_valid(suite_id: str, scenario: str, result: dict) -> bool:
    locust = result.get("locust_exit_code")
    locust_process = result.get("locust_process_exit_code")
    server = result.get("server_exit_code")
    combined = result.get("exit_code")
    checks = result.get("checks")
    required_checks = {
        "has_jobs", "budget", "bq_cache_hit", "revision_matches_100pct",
        "deployment_config_matches", "dataset_snapshot_matches", "locust_client_verdict",
    }
    if scenario == "market_coldmiss":
        required_checks.add("app_cache_miss")
    if scenario in {"realistic", "bq_cold_search"}:
        required_checks.add("instance_evidence")
    check_names = {
        item.get("check") for item in checks or [] if isinstance(item, dict)
    }
    checks_well_formed = (
        isinstance(checks, list)
        and bool(checks)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("check"), str)
            and isinstance(item.get("pass"), bool)
            for item in checks
        )
        and len(check_names) == len(checks)
    )
    check_map = {
        item["check"]: item for item in checks
    } if checks_well_formed else {}
    evidence = result.get("instance_evidence")
    required_phases = {
        "realistic": {"step2", "step3"},
        "bq_cold_search": {"step2"},
    }.get(scenario, set())
    phases = evidence.get("phases") if isinstance(evidence, dict) else None
    instance_ok = not required_phases or (
        isinstance(evidence, dict)
        and evidence.get("source") == "cloud_monitoring"
        and evidence.get("verdict") == "report_only"
        and isinstance(phases, dict)
        and set(phases) == required_phases
        and all(
            isinstance(item, dict)
            and item.get("source") == "cloud_monitoring"
            and type(item.get("sample_points")) is int
            and item["sample_points"] > 0
            and type(item.get("peak_instances")) in (int, float)
            and item["peak_instances"] >= 0
            and isinstance(item.get("scale_out_observed"), bool)
            and item.get("revision") == result.get("expect_revision")
            and item.get("verdict") == "report_only"
            for item in phases.values()
        )
        and evidence.get("sample_points") == sum(
            item["sample_points"] for item in phases.values())
        and evidence.get("peak_instances") == max(
            item["peak_instances"] for item in phases.values())
        and evidence.get("scale_out_observed") == any(
            item["scale_out_observed"] for item in phases.values())
    )
    base_valid = (
        result.get("run_id") == f"{suite_id}-{scenario}"
        and type(locust) is int and locust in (0, 2, 3, 4)
        and type(locust_process) is int and locust_process == locust
        and type(server) is int and server in (0, 2, 3)
        and type(combined) is int and combined == max(locust, server)
        and isinstance(result.get("pass"), bool)
        and result["pass"] == (combined == 0)
        and _window(result.get("window")) is not None
        and type(result.get("n_total")) is int and result["n_total"] > 0
        and type(result.get("billable_job_count")) is int
        and result["billable_job_count"] >= result["n_total"]
        and type(result.get("total_bytes_billed")) is int
        and result["total_bytes_billed"] >= 0
        and isinstance(result.get("target"), dict)
        and all(result["target"].get(field) for field in (
            "project", "service", "region", "bq_location", "bq_dataset"))
        and isinstance(result.get("deployment"), dict)
        and all(result["deployment"].get(field) not in (None, "") for field in (
            "base_url", "bq_maximum_bytes_billed", "bq_project", "bq_dataset", "bq_location",
            "query_timeout_s", "cache_ttl_seconds", "rate_limit_per_minute", "rate_limit_burst",
            "run_concurrency", "cpu_limit", "memory_limit", "min_instances", "max_instances",
            "service_min_instances", "service_max_instances", "scaling_mode",
        ))
        and isinstance(result.get("dataset_snapshot"), dict)
        and bool(result["dataset_snapshot"].get("batch_id"))
        and bool(result["dataset_snapshot"].get("as_of"))
        and isinstance(result.get("test_provenance"), dict)
        and isinstance(result["test_provenance"].get("image_digest"), str)
        and bool(result["test_provenance"]["image_digest"])
        and isinstance(result["test_provenance"].get("corpus_hash"), str)
        and bool(result["test_provenance"]["corpus_hash"])
        and type(result["test_provenance"].get("load_seed")) is int
        and isinstance(result["test_provenance"].get("locust_version"), str)
        and bool(result["test_provenance"]["locust_version"])
        and isinstance(result.get("expect_revision"), str) and bool(result["expect_revision"])
        and checks_well_formed and required_checks <= check_names
        and check_map["locust_client_verdict"]["pass"] == (locust == 0)
        and instance_ok
    )
    if not base_valid:
        return False
    # Artifact PASS phải chứng minh mọi check đều PASS; report-only vẫn mang pass=True vì evidence tồn tại.
    return combined != 0 or all(item["pass"] is True for item in checks)


def aggregate_results(suite_id: str, scenario_results: list[tuple[str, dict]],
                      budget: dict) -> tuple[dict, int]:
    expected = set(VALID_SCENARIOS)
    seen: dict[str, dict] = {}
    duplicates: list[str] = []
    invalid_artifacts: list[str] = []
    for path, result in scenario_results:
        scenario = result.get("scenario")
        if scenario in seen:
            duplicates.append(str(scenario))
        elif isinstance(scenario, str):
            valid = _scenario_artifact_valid(suite_id, scenario, result)
            if not valid:
                invalid_artifacts.append(path)
            seen[scenario] = {
                "path": path, "locust_exit_code": result.get("locust_exit_code"),
                "locust_process_exit_code": result.get("locust_process_exit_code"),
                "server_exit_code": result.get("server_exit_code"),
                "exit_code": result.get("exit_code"), "run_id": result.get("run_id"),
                "window": result.get("window"), "n_total": result.get("n_total"),
                "billable_job_count": result.get("billable_job_count"),
                "total_bytes_billed": result.get("total_bytes_billed"),
                "target": result.get("target"),
            }

    missing = sorted(expected - set(seen))
    unexpected = sorted(set(seen) - expected)
    scenario_codes_valid = all(
        type(item["exit_code"]) is int and item["exit_code"] in (0, 2, 3, 4, 5)
        for item in seen.values()
    )
    budget_valid = (
        budget.get("mode") == "budget_only"
        and budget.get("suite_id") == suite_id
        and type(budget.get("exit_code")) is int
        and budget.get("exit_code") in (0, 2, 3, 5)
        and isinstance(budget.get("pass"), bool)
        and budget.get("pass") == (budget.get("exit_code") == 0)
        and _window(budget.get("window")) is not None
        and type(budget.get("n_total")) is int and budget["n_total"] > 0
        and type(budget.get("total_bytes_billed")) is int
        and budget["total_bytes_billed"] >= 0
        and type(budget.get("budget_bytes")) is int and budget["budget_bytes"] >= 0
        and isinstance(budget.get("project"), str) and bool(budget["project"])
        and isinstance(budget.get("location"), str) and bool(budget["location"])
    )

    coverage_ok = totals_ok = target_ok = snapshot_ok = provenance_ok = infra_ok = False
    if not missing and not unexpected and not invalid_artifacts and budget_valid:
        scenario_windows = [_window(item["window"]) for item in seen.values()]
        budget_window = _window(budget["window"])
        coverage_ok = (
            budget_window[0] <= min(window[0] for window in scenario_windows)
            and budget_window[1] >= max(window[1] for window in scenario_windows)
        )
        totals_ok = (
            budget["n_total"] == sum(item["billable_job_count"] for item in seen.values())
            and budget["total_bytes_billed"] == sum(
                item["total_bytes_billed"] for item in seen.values())
        )
        target_keys = ("project", "service", "region", "bq_location", "bq_dataset")
        targets = [tuple(item["target"].get(key) for key in target_keys)
                   for item in seen.values()]
        target_ok = (
            len(set(targets)) == 1
            and budget["project"] == targets[0][0]
            and budget["location"] == targets[0][3]
        )
        snapshots = [(
            result.get("dataset_snapshot", {}).get("batch_id"),
            result.get("dataset_snapshot", {}).get("as_of"),
        ) for _, result in scenario_results]
        snapshot_ok = len(snapshots) == len(expected) and len(set(snapshots)) == 1
        provenances = [tuple(sorted(result["test_provenance"].items()))
                       for _, result in scenario_results]
        provenance_ok = len(provenances) == len(expected) and len(set(provenances)) == 1
        stable_infra_fields = (
            "base_url", "bq_maximum_bytes_billed", "bq_project", "bq_dataset", "bq_location",
            "query_timeout_s", "cache_ttl_seconds", "rate_limit_per_minute", "rate_limit_burst",
            "run_concurrency", "cpu_limit", "memory_limit", "min_instances", "max_instances",
            "service_min_instances", "service_max_instances", "scaling_mode",
        )
        infra_profiles = [
            tuple(str(result["deployment"].get(field)).strip().lower()
                  for field in stable_infra_fields)
            for _, result in scenario_results
        ]
        infra_ok = len(infra_profiles) == len(expected) and len(set(infra_profiles)) == 1

    if (missing or unexpected or duplicates or invalid_artifacts
            or not scenario_codes_valid or not budget_valid or not coverage_ok or not totals_ok
            or not target_ok or not snapshot_ok or not provenance_ok or not infra_ok):
        code = EXIT_INVALID
    else:
        codes = [item["exit_code"] for item in seen.values()] + [budget["exit_code"]]
        code = max(codes, default=EXIT_PASS)

    result = {
        "mode": "suite_aggregate",
        "suite_id": suite_id,
        "required_scenarios": sorted(expected),
        "scenarios": seen,
        "budget": {"exit_code": budget.get("exit_code"), "pass": budget.get("pass"),
                   "window": budget.get("window"), "n_total": budget.get("n_total"),
                   "total_bytes_billed": budget.get("total_bytes_billed"),
                   "project": budget.get("project"), "location": budget.get("location")},
        "budget_covers_scenarios": coverage_ok,
        "budget_totals_cover_scenarios": totals_ok,
        "targets_match": target_ok,
        "snapshots_match": snapshot_ok,
        "test_provenance_matches": provenance_ok,
        "stable_infrastructure_matches": infra_ok,
        "missing_scenarios": missing,
        "unexpected_scenarios": unexpected,
        "duplicate_scenarios": sorted(set(duplicates)),
        "invalid_scenario_artifacts": invalid_artifacts,
        "pass": code == EXIT_PASS,
        "exit_code": code,
    }
    return result, code


def main(argv: list[str] | None = None) -> None:
    ap = InvalidTestArgumentParser()
    ap.add_argument("--scenario-result", action="append", default=[],
                    help="suite-result.json của một scenario; truyền đúng 1 lần/scenario")
    ap.add_argument("--suite-id", required=True,
                    help="suite UUID; mọi run_id/artifact ngân sách phải thuộc đúng suite này")
    ap.add_argument("--budget-result", required=True)
    ap.add_argument("--out", default="final-suite-result.json")
    args = ap.parse_args(argv)

    loaded = []
    unreadable = []
    for path in args.scenario_result:
        data = _read(path)
        if data is None:
            unreadable.append(path)
        else:
            loaded.append((path, data))
    budget = _read(args.budget_result)
    if unreadable or budget is None:
        result = {
            "mode": "suite_aggregate", "pass": False, "exit_code": EXIT_UNREADABLE,
            "unreadable": unreadable + ([] if budget is not None else [args.budget_result]),
        }
        code = EXIT_UNREADABLE
    else:
        result, code = aggregate_results(args.suite_id, loaded, budget)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
