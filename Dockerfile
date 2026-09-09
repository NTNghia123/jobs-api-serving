# syntax=docker/dockerfile:1
# Image phục vụ API (Tuần 7). Hai tầng:
#   - builder : cài phụ thuộc vào một venv riêng (có sẵn toolchain nếu cần build wheel).
#   - runtime : chỉ COPY venv sang base slim mới, chạy bằng user thường (non-root).
# Xem docs/adr/ADR-014 (multi-stage + non-root).

# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# venv riêng để tầng runtime chỉ cần copy đúng thư mục này (không kéo theo pip cache/toolchain).
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime
# PYTHONUNBUFFERED: log ra stdout ngay, không buffer -> observability (Tuần 8) đọc được.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app

# Copy venv đã cài từ builder. Runtime KHÔNG có pip/gcc -> bề mặt tấn công nhỏ.
COPY --from=builder /opt/venv /opt/venv

# User thường, KHÔNG chạy root (nếu bị RCE cũng không có quyền root trong container).
RUN useradd --create-home --uid 10001 appuser
# Thư mục kho DuckDB dùng chung (elt-init GHI, api ĐỌC) — tạo sẵn & cấp quyền cho appuser
# để khi docker-compose mount named volume vào /data, volume kế thừa quyền này (ADR-015).
RUN mkdir -p /data && chown appuser:appuser /data
COPY --chown=appuser:appuser app/ ./app/
USER appuser

EXPOSE 8080

# Healthcheck gọi /health — endpoint DUY NHẤT không cần auth (Tuần 2), nên không vướng 401.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
