# ADR-027: Tracing bằng OpenTelemetry — export OTLP tới Google Telemetry API → Cloud Trace

- Trạng thái: Đã chấp nhận
- Ngày: 2026-09-14
- Người quyết định: [Điền tên]
- Liên quan: Tuần 8 (Observability & bàn giao). Impl `app/observability/tracing.py`,
  `app/observability/logging.py` (correlate), `app/main.py` (wiring),
  `app/infrastructure/warehouse/bigquery_exec.py` (span `bq.query`), `app/settings.py`,
  `infra/gcp/{01-enable-apis,21-iam-bindings,deploy-cloud-run,smoke}.sh`.

## Bối cảnh
Tuần 8 cần distributed tracing để nhìn được đường đi một request và phần tốn thời gian/chi phí (truy
vấn BigQuery). Đã có structured log + `request_id` + query-stats; còn thiếu trace và liên kết log↔trace.
Ràng buộc: chạy trên Cloud Run + BigQuery thật; test/CI phải hermetic (không phụ thuộc mạng GCP);
`create_app()` được gọi nhiều lần (import app + fixture test) nhưng OpenTelemetry chỉ cho set global
TracerProvider **một lần**.

## Quyết định
- **Export OTLP/gRPC → Telemetry API (`telemetry.googleapis.com`) → Cloud Trace.** KHÔNG dùng
  `opentelemetry-exporter-gcp-trace` (Google đã deprecate, khuyến nghị OTLP). Auth bằng
  `AuthMetadataPlugin` (google-auth) gắn vào channel credentials → **token tự refresh**; KHÔNG tự đặt
  `x-goog-user-project`.
- **Exporter cấu hình được** `JOBS_API_OTEL_TRACES_EXPORTER = none | console | otlp` (mặc định `none`
  cho test/CI). Sampler `ParentBased(TraceIdRatioBased(ratio))` — tôn trọng cờ sample upstream; ratio mặc
  định 0.1, đặt 1.0 khi demo. Giữ **W3C Trace Context propagator** mặc định (Cloud Run truyền
  `traceparent`), không cài GCP propagator legacy.
- **Provider = singleton cấp process** (khoá `threading.Lock`, fingerprint = exporter+ratio+service_name+
  project_id): set global một lần + truyền tường minh cho `FastAPIInstrumentor`; gọi lại cùng cấu hình →
  reuse, khác cấu hình (kể cả chuyển sang `none`) → `RuntimeError`. Shutdown dựa
  `TracerProvider(shutdown_on_exit=True)` mặc định, không tự đăng ký `atexit`.
- **Correlate log↔trace:** khi có span, log JSON thêm `logging.googleapis.com/trace` (resource name đầy
  đủ `projects/<pid>/traces/<id>` khi biết project — mới tạo deep-link trong Console; local → trace id
  trần, tránh `projects//traces/`), `spanId`, `trace_sampled`. `project_id` resolve một lần lúc
  `configure_tracing` và bơm sang logging qua `set_trace_project_id` (formatter không nhận settings).
- **Span `bq.query`** bọc cả vòng đời `client.query`→`result` (attribute `op`, `bq.job_id` ngay sau
  submit để trace timeout vẫn điều tra được, `total_bytes_billed`/`cache_hit`/`rows`/`elapsed_ms`);
  context manager tự record exception, không ghi thủ công (tránh trùng).
- **Hạ tầng:** bật `telemetry.googleapis.com` + `cloudtrace.googleapis.com`; runtime SA cần
  `roles/telemetry.tracesWriter` + `roles/serviceusage.serviceUsageConsumer`.

## Phương án đã cân nhắc
- **`opentelemetry-exporter-gcp-trace` (Cloud Trace API exporter)** — đơn giản hơn nhưng đã bị Google
  deprecate; đi ngược khuyến nghị hiện hành → loại.
- **Lấy access token một lần lúc khởi động, nhét vào OTLP headers** — token hết hạn ~1h thì exporter
  chết; `x-goog-user-project` đặt tay dễ trùng header → loại, dùng `AuthMetadataPlugin` tự refresh.
- **GCP propagator (`X-Cloud-Trace-Context`) làm global** — Cloud Run đã truyền `traceparent` W3C chuẩn;
  thay propagator legacy làm mất liên kết trace từ client gửi W3C → loại.
- **Không dùng singleton, mỗi `create_app` set global** — lần set thứ hai bị OTel bỏ qua (warning), và
  reconfigure không tất định → chọn singleton + raise khi xung đột.

## Hệ quả
- Tích cực: theo đường khuyến nghị hiện hành của Google (OTLP); test hermetic (`none`/InMemory) không
  chạm mạng; log và trace liên kết được trong Cloud Console; thấy chi phí/độ trễ BigQuery theo span.
- Đánh đổi / rủi ro: bộ OTel (core + contrib) version độc lập, phải pin một bộ đã `pip check` (hiện core
  `1.44.0` / contrib `0.65b0`; bản contrib cũ 0.50b0 crash với FastAPI 0.141+); OTLP→Telemetry API cần
  ADC + enable API + IAM đúng — chỉ nghiệm thu được sau khi deploy thật; nghiệm thu trace phải gửi
  `traceparent -01` chủ đích (Cloud Run không sample mọi request).
