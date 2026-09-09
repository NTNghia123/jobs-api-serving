# ADR-014: Đóng gói bằng Docker multi-stage, chạy non-root

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 7]
- Người quyết định: [Điền tên]

## Bối cảnh
Tuần 7 cần đóng gói API để chạy được ở mọi máy bằng một image duy nhất. Hai câu hỏi: (1) làm sao để
image gọn và không mang theo toolchain build thừa; (2) chạy container bằng user nào.

## Quyết định
- **Dockerfile multi-stage**: tầng `builder` (python:3.12-slim) cài phụ thuộc vào một venv `/opt/venv`;
  tầng `runtime` chỉ `COPY --from=builder /opt/venv` sang một base slim mới rồi copy `app/`. Runtime
  KHÔNG có `pip`/toolchain build.
- **Chạy non-root**: tạo `appuser` (uid 10001), `USER appuser` trước `CMD`. `CMD` chạy
  `uvicorn app.main:app` cổng 8080.
- **HEALTHCHECK gọi `/health`** — endpoint duy nhất không cần auth (Tuần 2), nên healthcheck không vướng 401.
- **`.dockerignore`** loại `.env`, `*.duckdb`, `tests/`, `docs/`, `Report/` khỏi build context.

## Phương án đã cân nhắc
- **Single-stage** (cài thẳng vào image cuối) — đơn giản hơn nhưng image mang cả pip cache/toolchain,
  to hơn và bề mặt tấn công lớn hơn. Chọn multi-stage.
- **Chạy bằng root** (mặc định) — tiện nhưng nếu API bị RCE thì có root trong container; đi ngược
  khuyến nghị của Cloud Run. Chọn non-root.

## Hệ quả
- Tích cực: image nhỏ, khởi động nhanh; ít bề mặt tấn công; sẵn sàng cho Cloud Run (yêu cầu non-root).
- Đánh đổi / rủi ro: kho **`analytics.duckdb` KHÔNG bake vào image** (nó là dữ liệu do ELT sinh) — phải
  mount qua volume ở `docker-compose` (ADR-015). Nếu quên mount, API mở kho rỗng.
- `requirements.txt` hiện gộp cả deps ELT (pandas/duckdb/sqlalchemy/pymysql) lẫn deps API; runtime API
  không cần nhóm ELT. Nợ nhỏ: có thể tách `requirements-api.txt` sau để image gọn hơn nữa.
