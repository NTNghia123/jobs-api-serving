# ADR-015: Topology docker-compose — thứ tự khởi động & kho dùng chung

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 7]
- Người quyết định: [Điền tên]

## Bối cảnh
Kế hoạch Tuần 7 yêu cầu `docker compose up` trên máy sạch là có cả nguồn MySQL + API chạy hoàn chỉnh.
Khó ở chỗ: API đọc kho DuckDB ở chế độ `read_only=True` (ADR-003) nên kho **phải tồn tại trước**, mà kho
lại do job ELT sinh ra từ MySQL — tức có một chuỗi phụ thuộc thật sự, không phải khởi động song song.

## Quyết định
- **Bốn service**: `mysql` (nguồn), `redis` (cache + rate-limit chia sẻ), `elt-init` (job ELT one-shot),
  `api` (tầng phục vụ). `elt-init` và `api` dùng CHUNG image (`build: .`).
- **Thứ tự bằng healthcheck + điều kiện depends_on**:
  - `elt-init` chờ `mysql` `service_healthy` rồi chạy `build_silver && build_gold`, xong thoát 0.
  - `api` chờ `elt-init` `service_completed_successfully` và `redis` `service_healthy`.
- **Kho DuckDB qua named volume `duckdb_data`** mount ở `/data`: `elt-init` ghi, `api` mount `:ro`.
  Đường dẫn đặt bằng `JOBS_API_DUCKDB_PATH=/data/analytics.duckdb`.
- **Least privilege giữ nguyên hai ranh giới**: ELT nối MySQL bằng user `reader` (GRANT SELECT); API
  không nối MySQL, chỉ đọc DuckDB `:ro`.
- **MySQL không publish cổng ra host**: chỉ nói chuyện trong mạng nội bộ compose (bề mặt nhỏ hơn).

## Phương án đã cân nhắc
- **Chạy ELT trong entrypoint của api** — gộp cho gọn, nhưng trộn "khởi tạo dữ liệu" vào "phục vụ":
  api restart là chạy lại ELT, và không tách được lỗi ELT khỏi lỗi serving. Tách `elt-init` rõ ràng hơn.
- **Bake sẵn analytics.duckdb vào image** — nhanh nhưng image ôm dữ liệu, đổi dữ liệu phải build lại
  (ngược ADR-014). Bỏ.
- **`depends_on` không điều kiện** (mặc định) — chỉ đợi container "đã khởi động", không đợi MySQL sẵn
  sàng nhận kết nối → ELT chạy quá sớm, fail. Phải dùng `condition: service_healthy`.

## Hệ quả
- Tích cực: một lệnh dựng cả hệ; thứ tự tất định; kho chỉ sinh một lần, api mở read-only an toàn.
- Đánh đổi / rủi ro: named volume `duckdb_data` kế thừa quyền của `/data` trong image (đã `chown appuser`),
  nếu đổi uid trong Dockerfile phải xem lại quyền ghi. Chạy lại ELT khi dữ liệu nguồn đổi cần
  `docker compose up` lại `elt-init` (hoặc `--force-recreate`).
- `elt-init` idempotent (CREATE OR REPLACE — ADR-004) nên chạy lại nhiều lần vẫn an toàn.
