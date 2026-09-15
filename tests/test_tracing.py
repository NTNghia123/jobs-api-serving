"""Test tracing OpenTelemetry — TUẦN 8.

Nguyên tắc: dùng InMemorySpanExporter + SimpleSpanProcessor (đồng bộ, không flaky như
ConsoleSpanExporter + BatchSpanProcessor). Provider tạo với shutdown_on_exit=False và
shutdown() ở teardown để không đăng ký atexit thừa. Test singleton monkeypatch build_provider
+ set_tracer_provider để không ô nhiễm global OTel thật của pytest.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import TimeoutError as FutureTimeoutError
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

import app.observability.logging as alog
import app.observability.tracing as tr
from app.errors import QueryTimeoutError
from app.infrastructure.warehouse.bigquery_exec import _CANCEL_TIMEOUT_S, BigQueryExecutor
from app.infrastructure.warehouse.bigquery_read_sql import ReadTarget, SqlAndParams


@pytest.fixture(autouse=True)
def _reset_tracing_state():
    """Sau mỗi test: dọn state global (settings cache + singleton provider + project id)
    để test độc lập thứ tự, không rò cấu hình console sang test khác."""
    yield
    from app.settings import get_settings
    get_settings.cache_clear()
    tr._provider = None
    tr._fingerprint = None
    alog.set_trace_project_id(None)


@pytest.fixture
def provider_exporter():
    """Provider In-Memory (giữ tham chiếu exporter để đọc span). shutdown ở teardown."""
    exporter = InMemorySpanExporter()
    provider = tr.build_provider(
        span_processor=SimpleSpanProcessor(exporter),
        service_name="jobs-serving-api",
        ratio=1.0,
        project_id="test-project",
        shutdown_on_exit=False,
    )
    yield provider, exporter
    provider.shutdown()


# ---- Span bq.query --------------------------------------------------------------------

class _FakeJob:
    job_id = "job-123"
    total_bytes_billed = 2048
    cache_hit = False

    def result(self, timeout=None):
        return [{"x": 1}, {"x": 2}]

    def cancel(self):
        pass


class _FakeClient:
    def query(self, sql, job_config=None):
        return _FakeJob()


def test_bq_query_span_co_attribute(provider_exporter):
    provider, exporter = provider_exporter
    target = ReadTarget(project="proj", dataset="jobs_test")
    ex = BigQueryExecutor(target, client=_FakeClient(), tracer=provider.get_tracer("test"))

    ex.run(SqlAndParams(sql="SELECT 1", params=[]), op="search")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "bq.query"
    attrs = dict(span.attributes)
    assert attrs["op"] == "search"
    assert attrs["bq.job_id"] == "job-123"
    assert attrs["bq.total_bytes_billed"] == 2048
    assert attrs["bq.cache_hit"] is False
    assert attrs["bq.rows"] == 2
    assert "bq.elapsed_ms" in attrs


class _TimeoutJob:
    job_id = "job-timeout"

    def __init__(self, cancel_error=None):
        self._cancel_error = cancel_error
        self.cancelled = False

    def result(self, timeout=None):
        raise FutureTimeoutError()

    def cancel(self, retry="__unset__", timeout=None):
        self.cancelled = True
        self.cancel_timeout = timeout
        self.cancel_retry = retry
        if self._cancel_error:
            raise self._cancel_error


def _client_returning(job):
    class _C:
        def query(self, sql, job_config=None):
            return job
    return _C()


def test_bq_query_span_timeout(provider_exporter):
    provider, exporter = provider_exporter
    job = _TimeoutJob()
    ex = BigQueryExecutor(ReadTarget(project="p", dataset="d"),
                          client=_client_returning(job),
                          tracer=provider.get_tracer("test"))
    with pytest.raises(QueryTimeoutError):
        ex.run(SqlAndParams(sql="SELECT 1", params=[]), op="search")
    assert job.cancelled is True         # job.cancel() ĐƯỢC gọi (không để job chạy tốn tiền)
    assert job.cancel_timeout == _CANCEL_TIMEOUT_S  # cancel có TRẦN thời gian per-request (đúng 5s)
    assert job.cancel_retry is None       # retry=None → không retry theo deadline ~10 phút (chặn TỔNG)
    span = exporter.get_finished_spans()[0]
    assert dict(span.attributes)["timeout_s"] == 10   # default query_timeout_s
    assert span.status.status_code == StatusCode.ERROR


def test_bq_timeout_cancel_loi_khong_che_504(provider_exporter):
    # job.cancel() lỗi (gọi mạng) KHÔNG được che QueryTimeoutError → API vẫn 504, không 500.
    provider, _ = provider_exporter
    job = _TimeoutJob(cancel_error=RuntimeError("network down"))
    ex = BigQueryExecutor(ReadTarget(project="p", dataset="d"),
                          client=_client_returning(job),
                          tracer=provider.get_tracer("test"))
    with pytest.raises(QueryTimeoutError):
        ex.run(SqlAndParams(sql="SELECT 1", params=[]), op="search")
    assert job.cancelled is True   # đã THỬ cancel (dù lỗi) — lỗi cancel bị nuốt, không che 504


# ---- Log correlation ------------------------------------------------------------------

def _format_record(fmt: logging.Formatter) -> dict:
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
    return json.loads(fmt.format(rec))


def test_log_correlation_trong_span_co_project(provider_exporter):
    provider, _ = provider_exporter
    alog.set_trace_project_id("test-project")
    fmt = alog.JsonFormatter()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("s"):
        out = _format_record(fmt)
    assert out["logging.googleapis.com/trace"].startswith("projects/test-project/traces/")
    trace_id = out["logging.googleapis.com/trace"].rsplit("/", 1)[-1]
    assert len(trace_id) == 32 and int(trace_id, 16) >= 0
    assert len(out["logging.googleapis.com/spanId"]) == 16
    assert out["logging.googleapis.com/trace_sampled"] is True


def test_log_correlation_ngoai_span_khong_co_field():
    alog.set_trace_project_id("test-project")
    out = _format_record(alog.JsonFormatter())   # không có span active
    assert "logging.googleapis.com/trace" not in out
    assert "logging.googleapis.com/spanId" not in out


def test_log_correlation_khong_project_thi_trace_id_tran(provider_exporter):
    provider, _ = provider_exporter
    alog.set_trace_project_id(None)   # local/console: chưa có project
    fmt = alog.JsonFormatter()
    with provider.get_tracer("test").start_as_current_span("s"):
        out = _format_record(fmt)
    trace_field = out["logging.googleapis.com/trace"]
    assert "projects/" not in trace_field           # KHÔNG "projects//traces/..."
    assert len(trace_field) == 32


# ---- exporter=none không instrument ---------------------------------------------------

def test_exporter_none_khong_instrument(monkeypatch):
    from unittest.mock import Mock

    import opentelemetry.instrumentation.fastapi as otel_fastapi

    from app.settings import get_settings

    mock_instrument = Mock()
    monkeypatch.setattr(otel_fastapi.FastAPIInstrumentor, "instrument_app", mock_instrument)
    # đảm bảo state sạch + settings exporter=none (mặc định test)
    monkeypatch.setattr(tr, "_provider", None)
    monkeypatch.setattr(tr, "_fingerprint", None)
    get_settings.cache_clear()

    from app.main import create_app
    create_app()

    assert mock_instrument.call_count == 0


def test_exporter_console_co_instrument(monkeypatch):
    # Chiều DƯƠNG (khóa regression wiring): exporter=console → instrument_app ĐƯỢC gọi,
    # provider truyền TƯỜNG MINH. Monkeypatch để không tạo BatchSpanProcessor/global thật.
    from unittest.mock import Mock

    import opentelemetry.instrumentation.fastapi as otel_fastapi

    from app.settings import get_settings

    mock_instrument = Mock()
    monkeypatch.setattr(otel_fastapi.FastAPIInstrumentor, "instrument_app", mock_instrument)
    sentinel = object()
    monkeypatch.setattr(tr, "build_provider", lambda **kwargs: sentinel)
    monkeypatch.setattr(tr, "BatchSpanProcessor", lambda *a, **k: object())
    monkeypatch.setattr(tr, "ConsoleSpanExporter", lambda *a, **k: object())
    monkeypatch.setattr(tr.trace, "set_tracer_provider", lambda p: None)
    monkeypatch.setattr(tr, "_provider", None)
    monkeypatch.setattr(tr, "_fingerprint", None)
    monkeypatch.setenv("JOBS_API_OTEL_TRACES_EXPORTER", "console")
    get_settings.cache_clear()

    from app.main import create_app
    create_app()

    assert mock_instrument.call_count == 1
    assert mock_instrument.call_args.kwargs.get("tracer_provider") is sentinel


def test_create_app_wiring_sinh_server_span(provider_exporter, monkeypatch):
    # Đi qua WIRING PRODUCTION: create_app() thật (configure_tracing + instrument_app + middleware).
    # Inject provider InMemory qua build_provider để bắt span; set_tracer_provider noop để cô lập global.
    from fastapi.testclient import TestClient
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    from app.settings import get_settings

    provider, exporter = provider_exporter
    monkeypatch.setattr(tr, "build_provider", lambda **kwargs: provider)
    monkeypatch.setattr(tr, "BatchSpanProcessor", lambda *a, **k: object())
    monkeypatch.setattr(tr, "ConsoleSpanExporter", lambda *a, **k: object())
    monkeypatch.setattr(tr.trace, "set_tracer_provider", lambda p: None)
    monkeypatch.setattr(tr, "_provider", None)
    monkeypatch.setattr(tr, "_fingerprint", None)
    monkeypatch.setenv("JOBS_API_OTEL_TRACES_EXPORTER", "console")
    get_settings.cache_clear()

    from app.main import create_app
    app = create_app()
    try:
        assert TestClient(app).get("/health").status_code == 200
        spans = exporter.get_finished_spans()
        # Phải có SERVER span cho /health (chứng minh instrument HTTP server, không phải span khác).
        assert any(s.kind == SpanKind.SERVER and "/health" in s.name for s in spans)
    finally:
        FastAPIInstrumentor.uninstrument_app(app)


# ---- Singleton ------------------------------------------------------------------------

def test_singleton_fingerprint(monkeypatch):
    # Reset module state; monkeypatch build_provider + exporter/processor + set_tracer_provider
    # để KHÔNG tạo BatchSpanProcessor/atexit và không ô nhiễm global OTel thật.
    monkeypatch.setattr(tr, "_provider", None)
    monkeypatch.setattr(tr, "_fingerprint", None)
    set_calls: list = []
    monkeypatch.setattr(tr.trace, "set_tracer_provider", set_calls.append)
    sentinel = object()
    monkeypatch.setattr(tr, "build_provider", lambda **kwargs: sentinel)
    monkeypatch.setattr(tr, "BatchSpanProcessor", lambda *a, **k: object())
    monkeypatch.setattr(tr, "ConsoleSpanExporter", lambda *a, **k: object())

    s = SimpleNamespace(otel_traces_exporter="console", otel_sampling_ratio=1.0,
                        service_name="svc", bq_project="")
    p1 = tr.configure_tracing(s)
    p2 = tr.configure_tracing(s)         # cùng fingerprint → reuse
    assert p1 is sentinel and p2 is sentinel
    assert len(set_calls) == 1           # set_tracer_provider chỉ gọi một lần

    # khác fingerprint (ratio) → raise
    s2 = SimpleNamespace(otel_traces_exporter="console", otel_sampling_ratio=0.5,
                         service_name="svc", bq_project="")
    with pytest.raises(RuntimeError):
        tr.configure_tracing(s2)

    # chuyển sang none sau khi đã có provider → raise
    s3 = SimpleNamespace(otel_traces_exporter="none", otel_sampling_ratio=1.0,
                         service_name="svc", bq_project="")
    with pytest.raises(RuntimeError):
        tr.configure_tracing(s3)
