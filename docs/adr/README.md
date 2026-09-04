# Nhật ký quyết định kiến trúc (ADR)

**ADR = Architecture Decision Record.** Mỗi file ghi lại *một* quyết định thiết kế quan trọng:
bối cảnh, phương án đã chọn, các phương án bị loại, và hệ quả.

## Quy ước
- Mỗi quyết định một file: `ADR-NNN-tieu-de.md`, đánh số tăng dần, **không xoá/sửa nội dung file cũ**.
- Khi đổi ý: viết ADR mới, đặt trạng thái ADR cũ thành **Bị thay thế bởi ADR-XYZ** (giữ lịch sử).
- Trạng thái hợp lệ: `Đề xuất` · `Đã chấp nhận` · `Bị thay thế` · `Bị loại`.
- Giữ ngắn — nửa trang là đủ. Giá trị nằm ở phần *vì sao* và *phương án đã cân nhắc*.

## Mẫu (copy khi tạo ADR mới)
```
# ADR-NNN: <tiêu đề quyết định>
- Trạng thái: Đề xuất | Đã chấp nhận | Bị thay thế | Bị loại
- Ngày: YYYY-MM-DD
- Người quyết định: <tên>

## Bối cảnh
<vấn đề, ràng buộc, điều đã biết tại thời điểm quyết định>

## Quyết định
<đã chọn gì, một câu rõ ràng>

## Phương án đã cân nhắc
- <phương án A> — vì sao loại
- <phương án B> — vì sao loại

## Hệ quả
- Tích cực: ...
- Đánh đổi / rủi ro: ...
```

## Chỉ mục
| ADR | Quyết định | Tuần | Trạng thái |
| --- | --- | --- | --- |
| [ADR-001](ADR-001-api-thay-vi-sql-truc-tiep.md) | Tầng API hướng nghiệp vụ thay vì cấp quyền SQL trực tiếp | 1 | Đã chấp nhận |
| [ADR-002](ADR-002-post-cho-jobs-search.md) | `/jobs/search` dùng POST dù chỉ đọc | 2 | Đã chấp nhận |
| [ADR-006](ADR-006-keyset-thay-vi-offset.md) | Phân trang keyset (cursor ký HMAC) thay vì offset | 2 | Đã chấp nhận |
| [ADR-003](ADR-003-duckdb-va-adapter-pattern.md) | DuckDB làm kho phân tích cục bộ + Warehouse adapter | 3 | Đã chấp nhận |
| [ADR-004](ADR-004-elt-idempotent-truncate-then-load.md) | ELT idempotent bằng truncate-then-load | 3 | Đã chấp nhận |
| [ADR-005](ADR-005-query-timeout-duckdb.md) | Tạm chưa cưỡng chế query timeout ở DuckDB cục bộ | 3 | Đã chấp nhận |
| [ADR-007](ADR-007-bo-required-date-filter.md) | Bỏ required date filter, giữ nguyên cơ chế | 4 | Đã chấp nhận |
| [ADR-008](ADR-008-cost-control-duckdb.md) | Kiểm soát chi phí kiểu DuckDB (hoãn dry-run/max_bytes) | 4 | Đã chấp nhận |
| [ADR-009](ADR-009-cache-in-process.md) | Cache in-process (TTL); hoãn Redis tới Cloud Run | 5 | Đã chấp nhận |
| [ADR-010](ADR-010-luong-dai-dien-trung-diem.md) | "Lương đại diện" = trung điểm; benchmark bằng median | 5 | Đã chấp nhận |

> Chỉ mục xếp theo tuần ra quyết định (số ADR chỉ là thứ tự tạo file, không cần liên tục theo tuần).
> Còn để dành cho các tuần sau: API key vs JWT (Tuần 6); Cloud Run concurrency/cost + BigQuery
> dry-run/max_bytes_billed + chuyển cache sang Redis (Tuần 7).
