"""LOAD-TEST — kiểm chứng các thay đổi app phục vụ load test.

1) Setting bq_use_query_cache: default true; env parse false; chảy đúng vào QueryJobConfig.use_query_cache.
2) Cache backend 'none' (NoOpCache): set() không ném lỗi, get() luôn None (miss).
3) Guard: env=prod + cache_backend=none → KHÔNG boot (fail-fast).
"""
from __future__ import annotations

import pytest

from app.infrastructure.cache.none import NoOpCache
from app.infrastructure.warehouse.bigquery_exec import BigQueryExecutor, load_test_run_label
from app.infrastructure.warehouse.bigquery_read_sql import ReadTarget, SqlAndParams
from app.settings import Settings
from tests.load import config as load_config
from tests.load.dryrun import guard_bytes


# ── 1) Setting bq_use_query_cache ─────────────────────────────────────────────
def test_bq_use_query_cache_default_true():
    assert Settings(warehouse_backend="fake").bq_use_query_cache is True


def test_bq_use_query_cache_env_parse_false(monkeypatch):
    monkeypatch.setenv("JOBS_API_BQ_USE_QUERY_CACHE", "false")
    assert Settings().bq_use_query_cache is False


class _FakeJob:
    job_id = "job-1"
    total_bytes_billed = 10
    cache_hit = False

    def result(self, timeout=None):
        return []


class _CapturingClient:
    """Ghi lại job_config truyền vào query() để assert use_query_cache."""

    def __init__(self):
        self.captured_cfg = None

    def query(self, sql, job_config=None):
        self.captured_cfg = job_config
        return _FakeJob()


@pytest.mark.parametrize("use_cache", [True, False])
def test_use_query_cache_flows_into_queryjobconfig(use_cache):
    client = _CapturingClient()
    ex = BigQueryExecutor(
        ReadTarget(project="p", dataset="d", use_query_cache=use_cache),
        client=client,
    )
    ex.run(SqlAndParams(sql="SELECT 1", params=[]), op="search")
    assert client.captured_cfg.use_query_cache is use_cache


def test_load_test_run_label_is_bounded_and_bq_safe():
    assert load_test_run_label("lt_Suite_01-realistic:step1:abc") == "suite_01-realistic"
    assert load_test_run_label("normal-request-id") is None
    assert load_test_run_label("normal:request:id") is None
    assert len(load_test_run_label("lt_" + "A" * 100 + ":phase:req")) == 63


def test_load_request_labels_bigquery_job(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(
        "app.infrastructure.warehouse.bigquery_exec.get_request_id",
        lambda: "lt_suite01-realistic:step1:req1",
    )
    ex = BigQueryExecutor(ReadTarget(project="p", dataset="d"), client=client)
    ex.run(SqlAndParams(sql="SELECT 1", params=[]), op="search")
    assert client.captured_cfg.labels == {"load_test_run": "suite01-realistic"}


# ── 2) NoOpCache ──────────────────────────────────────────────────────────────
def test_noop_cache_always_miss():
    c = NoOpCache()
    c.set("k", "v")          # KHÔNG được ném lỗi
    assert c.get("k") is None  # vẫn miss sau khi set


# ── 3) Guard prod ─────────────────────────────────────────────────────────────
def test_cache_backend_none_bi_cam_o_prod():
    with pytest.raises(ValueError) as e:
        Settings(
            env="prod",
            cache_backend="none",
            page_token_secret="a-real-secret",
            api_keys={"t": {"key_sha256": "x"}},
        )
    assert "none" in str(e.value) and "prod" in str(e.value)


def test_cache_backend_none_duoc_phep_ngoai_prod():
    assert Settings(env="local", cache_backend="none").cache_backend == "none"


def test_bq_cache_off_bi_cam_o_prod():
    with pytest.raises(ValueError) as exc:
        Settings(
            env="prod", bq_use_query_cache=False,
            page_token_secret="a-real-secret", api_keys={"t": {"key_sha256": "x"}},
        )
    assert "bq_use_query_cache=false" in str(exc.value)


@pytest.mark.parametrize("bad", ["disk", "noop", ""])
def test_cache_backend_la_bi_chan(bad):
    with pytest.raises(ValueError) as e:
        Settings(cache_backend=bad)
    assert "không hỗ trợ" in str(e.value)


# ── env là Literal → 'production'/'PROD'/typo bị từ chối (guard prod không bị bypass) ─────
@pytest.mark.parametrize("bad_env", ["production", "PROD", "Prod", "dev", "test"])
def test_env_khong_hop_le_bi_chan(bad_env):
    with pytest.raises(ValueError):
        Settings(env=bad_env)


def test_env_production_khong_bypass_duoc_noop_guard():
    # trước đây env='production' + cache_backend='none' lọt guard; giờ phải bị chặn ngay ở env.
    with pytest.raises(ValueError):
        Settings(env="production", cache_backend="none")


@pytest.mark.parametrize("ok_env", ["local", "staging", "prod"])
def test_env_hop_le(ok_env):
    kw = {}
    if ok_env != "local":
        kw = {"page_token_secret": "a-real-secret", "api_keys": {"t": {"key_sha256": "x"}}}
    assert Settings(env=ok_env, **kw).env == ok_env


def test_acceptance_scenario_requires_unique_run_id(monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://example.test")
    monkeypatch.setenv("LOAD_SCENARIO", "realistic")
    monkeypatch.delenv("RUN_ID", raising=False)
    with pytest.raises(SystemExit) as exc:
        load_config.load_run_config()
    assert exc.value.code == 3


def test_smoke_may_default_to_local_run_id(monkeypatch):
    monkeypatch.setenv("BASE_URL", "http://127.0.0.1:8010")
    monkeypatch.setenv("LOAD_SCENARIO", "smoke")
    monkeypatch.delenv("RUN_ID", raising=False)
    assert load_config.load_run_config().run_id == "local"


@pytest.mark.parametrize("base_url,seed", [("svc.example", "1"), ("https://svc.example", "bad")])
def test_run_config_rejects_bad_url_or_seed(monkeypatch, base_url, seed):
    monkeypatch.setenv("BASE_URL", base_url)
    monkeypatch.setenv("LOAD_SCENARIO", "smoke")
    monkeypatch.setenv("LOAD_SEED", seed)
    with pytest.raises(SystemExit) as exc:
        load_config.load_run_config()
    assert exc.value.code == 3


@pytest.mark.parametrize("bad", ["UPPER", "has:colon", "has space", "a" * 64])
def test_run_id_must_map_uniquely_to_bigquery_label(monkeypatch, bad):
    monkeypatch.setenv("LOAD_SCENARIO", "realistic")
    monkeypatch.setenv("BASE_URL", "https://example.test")
    monkeypatch.setenv("RUN_ID", bad)
    with pytest.raises(SystemExit) as exc:
        load_config.load_run_config()
    assert exc.value.code == 3


@pytest.mark.parametrize("scenario", ["realistic", "spike_recovery", "ops_baseline"])
def test_warm_scenarios_require_realistic_cache_mode(scenario):
    assert load_config.cache_mode_error(scenario, "memory", "true") is None
    assert load_config.cache_mode_error(scenario, "memory", "false")


def test_cold_scenarios_require_their_exact_cache_mode():
    assert load_config.cache_mode_error("bq_cold_search", "memory", "false") is None
    assert load_config.cache_mode_error("bq_cold_search", "memory", "true")
    assert load_config.cache_mode_error("market_coldmiss", "none", "false") is None
    assert load_config.cache_mode_error("market_coldmiss", "memory", "false")


def test_dryrun_guard_requires_plan_margin_and_positive_estimate():
    assert guard_bytes(101, 0.2) == 122
    for max_bytes, margin in [(0, 0.3), (100, 0.19), (100, 0.31)]:
        with pytest.raises(SystemExit) as exc:
            guard_bytes(max_bytes, margin)
        assert exc.value.code == 3
