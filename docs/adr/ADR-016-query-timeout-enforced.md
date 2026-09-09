# ADR-016: Cưỡng chế query timeout ở DuckDB (thread + interrupt) — thay thế ADR-005

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 7]
- Người quyết định: [Điền tên]
- Thay thế: ADR-005 (tạm chưa cưỡng chế query timeout)

## Bối cảnh
Tuần 7 là tuần độ tin cậy, yêu cầu ngân sách timeout **lồng nhau**: `client > request > query`. ADR-005
trước đây cố ý hoãn cưỡng chế query timeout trên DuckDB (dữ liệu 76 dòng, mỗi truy vấn vài ms). Giờ
ta muốn cơ chế 504 chạy THẬT — vừa đúng chủ đề tuần, vừa dùng tới `QueryTimeoutError` (504) và response
504 mà handler `/v1/jobs/search` đã khai sẵn từ trước.

Ràng buộc kỹ thuật: handler là sync `def` (chạy trong threadpool) nên **không thể huỷ** từ tầng ASGI.
Chỗ duy nhất dừng được công việc trong tiến trình là tầng truy vấn — DuckDB cho `connection.interrupt()`.

## Quyết định
- **Cưỡng chế query timeout thật**: helper `execute_with_timeout` (app/infrastructure/warehouse/
  `_duckdb_timeout.py`) chạy `execute + fetchall` trong một thread; quá `query_timeout_s` giây thì gọi
  `cur.interrupt()` và ném `QueryTimeoutError` → HTTP 504 `QUERY_TIMEOUT`. Dùng cho cả
  `DuckDBJobRepository` và `DuckDBMetricsRepository`, mỗi request một cursor riêng.
- **Cấu hình + bất biến lồng nhau**: thêm `request_timeout_s`, `query_timeout_s` vào settings; validator
  **fail-fast** nếu `query_timeout_s >= request_timeout_s`. `request_timeout_s` là ngân sách tầng edge
  (Cloud Run/proxy) — phải nhỏ hơn client timeout (client mẫu Tuần 8 để 30s).

## Phương án đã cân nhắc
- **Giữ ADR-005** (chỉ khung ngân sách, không interrupt) — đơn giản nhưng 504 với DuckDB không bao giờ
  xảy ra thật; không dạy được cơ chế. Bỏ vì tuần này chủ đề đúng là độ tin cậy.
- **Timeout ở tầng ASGI/uvicorn** — không dừng được thread sync đang chạy (chỉ cắt kết nối, công việc
  vẫn chạy tiếp). Không phải cơ chế abort thật.

## Hệ quả
- Tích cực: một truy vấn treo bị cắt trong `query_timeout_s` và trả 504 sạch theo envelope lỗi chung;
  cấu hình sai thứ tự timeout bị chặn ngay lúc boot.
- Đánh đổi / rủi ro: mỗi truy vấn tốn một thread phụ (chi phí nhỏ ở quy mô này). Với data 76 dòng,
  để THẤY 504 phải ép truy vấn chậm hoặc đặt `query_timeout_s` rất nhỏ — test sẽ làm vậy (§9).
- BigQueryAdapter sau này cưỡng chế timeout bằng `job.result(timeout=...)` thay cho thread+interrupt,
  nhưng cùng một hợp đồng `query_timeout_s`.
