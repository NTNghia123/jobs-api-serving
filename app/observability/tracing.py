"""Tracing OpenTelemetry — export OTLP → Google Telemetry API → Cloud Trace.

[FILE MỚI — TUẦN 8]

Vì sao OTLP (không dùng opentelemetry-exporter-gcp-trace):
    Google đã deprecate Cloud Trace API exporter, khuyến nghị gửi OTLP thẳng tới
    Telemetry API (telemetry.googleapis.com). Xem ADR-027.

Ba chế độ (JOBS_API_OTEL_TRACES_EXPORTER):
    none    — không instrument (mặc định; test/CI hermetic, không phụ thuộc mạng).
    console — in span ra stdout (dev/nghiệm thu local).
    otlp    — OTLP/gRPC tới Telemetry API (Cloud Run).

Provider là SINGLETON cấp process: OpenTelemetry chỉ cho set global TracerProvider một
lần. configure_tracing() bọc toàn bộ chuỗi trong khoá để tránh race khi create_app() được
gọi nhiều lần (import app + fixture test), và raise rõ ràng khi cấu hình xung đột.
"""
from __future__ import annotations

import threading

import google.auth
import google.auth.transport.requests
import grpc
from google.auth.transport.grpc import AuthMetadataPlugin
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from app.observability.logging import set_trace_project_id

# Endpoint OTLP của Google Telemetry API (theo sample OTLP chính thức của Google).
_TELEMETRY_ENDPOINT = "https://telemetry.googleapis.com:443/v1/traces"

# --- singleton cấp process (bảo vệ bằng khoá) ---
_lock = threading.Lock()
_provider: TracerProvider | None = None
_fingerprint: tuple | None = None


def build_provider(
    *,
    span_processor: SpanProcessor,
    service_name: str,
    ratio: float,
    project_id: str | None = None,
    shutdown_on_exit: bool = True,
) -> TracerProvider:
    """Dựng TracerProvider từ span_processor được INJECT (test truyền In-Memory).

    - Nhận span_processor (không nhận exporter) để test dùng SimpleSpanProcessor +
      InMemorySpanExporter mà không tạo thread/atexit của BatchSpanProcessor.
    - Sampler ParentBased(TraceIdRatioBased): tôn trọng quyết định sample của upstream
      (traceparent), chỉ áp ratio ở root.
    - shutdown_on_exit=False cho test (mỗi provider mặc định tự đăng ký atexit).
    """
    attrs = {"service.name": service_name}
    if project_id:
        attrs["gcp.project_id"] = project_id
    provider = TracerProvider(
        resource=Resource.create(attrs),
        sampler=ParentBased(TraceIdRatioBased(ratio)),
        shutdown_on_exit=shutdown_on_exit,
    )
    provider.add_span_processor(span_processor)
    return provider


def _build_otlp_exporter(credentials) -> OTLPSpanExporter:
    """OTLP/gRPC exporter với auth Google TỰ REFRESH (không lấy token một lần).

    AuthMetadataPlugin gắn access token vào mỗi call và tự làm mới khi hết hạn — khác với
    việc đọc token một lần lúc khởi động (sẽ chết sau ~1 giờ). Không tự đặt
    x-goog-user-project: SA trên Cloud Run không cần, và đặt qua OTEL headers gây trùng.
    """
    request = google.auth.transport.requests.Request()
    plugin = AuthMetadataPlugin(credentials=credentials, request=request)
    channel_creds = grpc.composite_channel_credentials(
        grpc.ssl_channel_credentials(),
        grpc.metadata_call_credentials(plugin),
    )
    return OTLPSpanExporter(credentials=channel_creds, endpoint=_TELEMETRY_ENDPOINT)


def configure_tracing(settings) -> TracerProvider | None:
    """Cấu hình tracing một lần cho cả process. Trả provider (hoặc None nếu exporter=none).

    Provider được set làm global (để manual span như bq.query lấy qua get_tracer thấy được)
    VÀ trả về để truyền tường minh cho FastAPIInstrumentor.
    """
    global _provider, _fingerprint
    exporter_mode = settings.otel_traces_exporter

    with _lock:
        # Resolve project chỉ khi otlp (console/none không cần).
        credentials = None
        project_id: str | None = None
        if exporter_mode == "otlp":
            credentials, adc_project = google.auth.default()
            project_id = adc_project or (settings.bq_project or None)
            if not project_id:
                raise ValueError(
                    "OTLP exporter cần GCP project_id (không lấy được từ ADC hay JOBS_API_BQ_PROJECT)."
                )

        fingerprint = (exporter_mode, settings.otel_sampling_ratio, settings.service_name, project_id)

        # none: chưa có provider → None; đã có provider non-none → xung đột (raise TRƯỚC khi đụng logging).
        if exporter_mode == "none":
            if _provider is not None:
                raise RuntimeError(
                    "Tracing đã cấu hình non-none trong process này; không thể chuyển sang 'none'."
                )
            set_trace_project_id(None)
            return None

        # non-none nhưng provider đã tồn tại: cùng fingerprint → dùng lại; khác → raise.
        if _provider is not None:
            if fingerprint == _fingerprint:
                return _provider
            raise RuntimeError(
                f"Tracing đã cấu hình {_fingerprint}, không thể cấu hình lại thành {fingerprint} "
                "(global TracerProvider chỉ set được một lần)."
            )

        # non-none lần đầu: tạo, set global một lần, rồi báo project cho logging.
        if exporter_mode == "console":
            span_exporter = ConsoleSpanExporter()
        else:  # otlp
            span_exporter = _build_otlp_exporter(credentials)

        provider = build_provider(
            span_processor=BatchSpanProcessor(span_exporter),
            service_name=settings.service_name,
            ratio=settings.otel_sampling_ratio,
            project_id=project_id,
        )
        trace.set_tracer_provider(provider)
        set_trace_project_id(project_id)
        _provider = provider
        _fingerprint = fingerprint
        return provider
