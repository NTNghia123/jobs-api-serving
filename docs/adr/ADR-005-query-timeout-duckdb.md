# ADR-005: Tạm chưa cưỡng chế query timeout ở DuckDB cục bộ

- Trạng thái: **Bị thay thế bởi [ADR-016](ADR-016-query-timeout-enforced.md) (Tuần 7)** — query timeout
  nay ĐÃ được cưỡng chế thật bằng thread + interrupt. Nội dung dưới giữ lại làm bối cảnh lịch sử.
- Ngày: [Điền ngày — tuần 3]
- Người quyết định: [Điền tên]

## Bối cảnh
Curriculum Tuần 3 liệt kê "query timeout handling" như một mục cần học. Trên BigQuery, timeout gắn vào
`job.result(timeout=...)` và có `maximum_bytes_billed` để chặn chi phí. DuckDB cục bộ **không** có
`statement_timeout` gắn sẵn tương đương; muốn cưỡng chế phải chạy truy vấn trong một thread rồi gọi
`con.interrupt()` khi quá hạn.

## Quyết định
**Chưa** cưỡng chế timeout cho backend DuckDB ở giai đoạn học tập. Giữ *khái niệm* timeout ở tầng
adapter (tham số/interface) để backend BigQuery dùng khi lên cloud, và ghi rõ hạn chế này thay vì giả
vờ đã làm.

## Phương án đã cân nhắc
- **Bọc thread + `con.interrupt()`** — cưỡng chế được timeout thật; nhưng thêm phức tạp và rủi ro
  đồng bộ, trong khi dữ liệu chỉ 76 dòng (mọi truy vấn ~vài ms). Chưa xứng công ở giai đoạn này.
- **Đặt `SET` giới hạn tài nguyên của DuckDB** (memory_limit, threads) — hữu ích cho ổn định nhưng
  không phải timeout theo thời gian. Có thể thêm sau, độc lập với quyết định này.

## Hệ quả
- Tích cực: giữ code đơn giản; interface đã sẵn chỗ cắm timeout cho BigQuery.
- Đánh đổi / rủi ro: nếu sau này dữ liệu lớn hoặc có truy vấn nặng, một truy vấn treo sẽ không tự bị
  cắt ở DuckDB — cần bật lại cơ chế thread+interrupt. Đây là **nợ kỹ thuật có ý thức**, không phải bỏ sót.
