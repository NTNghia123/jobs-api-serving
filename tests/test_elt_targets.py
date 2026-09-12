"""Test writer guard (app/elt/serving/targets.py) — chống ghi nhầm môi trường/dataset."""
from __future__ import annotations

import pytest

from app.elt.serving.targets import (
    WriterConfig,
    WriterGuardError,
    resolve_target,
)

CFG = WriterConfig(project="my-proj", dataset_staging="jobs_staging", dataset_prod="jobs_prod")


def test_resolve_staging_and_prod():
    assert resolve_target("staging", CFG).dataset == "jobs_staging"
    assert resolve_target("prod", CFG).dataset == "jobs_prod"


def test_staging_never_yields_prod_dataset():
    t = resolve_target("staging", CFG)
    assert t.dataset != CFG.dataset_prod
    assert t.environment == "staging"


@pytest.mark.parametrize("env", ["", "PROD", "dev", "production", "test", None])
def test_unknown_environment_rejected(env):
    with pytest.raises(WriterGuardError):
        resolve_target(env, CFG)  # type: ignore[arg-type]


def test_table_id_is_backticked_fully_qualified():
    t = resolve_target("prod", CFG)
    assert t.table_id("silver_jobs") == "`my-proj.jobs_prod.silver_jobs`"
    assert t.table_ref("silver_jobs") == "my-proj.jobs_prod.silver_jobs"


def test_dataset_suffix_for_integration_tests():
    t = resolve_target("staging", CFG, dataset_suffix="_test")
    assert t.dataset == "jobs_staging_test"


def test_from_env_requires_project(monkeypatch):
    monkeypatch.delenv("JOBS_BQ_PROJECT", raising=False)
    with pytest.raises(WriterGuardError):
        WriterConfig.from_env()


def test_from_env_reads_overrides(monkeypatch):
    monkeypatch.setenv("JOBS_BQ_PROJECT", "p1")
    monkeypatch.setenv("JOBS_BQ_DATASET_PROD", "custom_prod")
    monkeypatch.setenv("JOBS_BQ_MAX_BYTES_BILLED", "500")
    cfg = WriterConfig.from_env()
    assert cfg.project == "p1"
    assert resolve_target("prod", cfg).dataset == "custom_prod"
    assert cfg.maximum_bytes_billed == 500
