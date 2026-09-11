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
| [ADR-005](ADR-005-query-timeout-duckdb.md) | Tạm chưa cưỡng chế query timeout ở DuckDB cục bộ | 3 | Bị thay thế bởi ADR-016 |
| [ADR-007](ADR-007-bo-required-date-filter.md) | Bỏ required date filter, giữ nguyên cơ chế | 4 | Đã chấp nhận |
| [ADR-008](ADR-008-cost-control-duckdb.md) | Kiểm soát chi phí kiểu DuckDB (hoãn dry-run/max_bytes) | 4 | Đã chấp nhận |
| [ADR-009](ADR-009-cache-in-process.md) | Cache qua interface (in-memory/Redis), chọn bằng config | 5 | Đã chấp nhận |
| [ADR-010](ADR-010-luong-dai-dien-trung-diem.md) | "Lương đại diện" = trung điểm; benchmark bằng median | 5 | Đã chấp nhận |
| [ADR-011](ADR-011-refactor-hexagonal.md) | Refactor sang cấu trúc hexagonal (ports & adapters) | 5 | Đã chấp nhận |
| [ADR-012](ADR-012-api-key-hashed-vs-jwt.md) | Xác thực API key (lưu hash + hạn dùng), không JWT | 6 | Đã chấp nhận |
| [ADR-013](ADR-013-rate-limit-in-process.md) | Rate-limit token bucket in-process; Redis để sau | 6 | Đã chấp nhận |
| [ADR-014](ADR-014-docker-multistage-non-root.md) | Đóng gói Docker multi-stage, chạy non-root | 7 | Đã chấp nhận |
| [ADR-015](ADR-015-docker-compose-topology.md) | Topology docker-compose: thứ tự khởi động + kho dùng chung | 7 | Đã chấp nhận |
| [ADR-016](ADR-016-query-timeout-enforced.md) | Cưỡng chế query timeout ở DuckDB (thread + interrupt) | 7 | Đã chấp nhận |
| [ADR-017](ADR-017-redis-rate-limiter-lua.md) | RedisRateLimiter token bucket nguyên tử bằng Lua | 7 | Đã chấp nhận |
| [ADR-018](ADR-018-bigquery-warehouse.md) | Kho = BigQuery (prod) · DuckDB dev (tạm tắt) · fake test | Migration | Đã chấp nhận |
| [ADR-019](ADR-019-mongo-source-data-contract.md) | Nguồn MongoDB & data contract (schema mapping matrix) | Migration | Đã chấp nhận |
| ADR-020 · BigQuery serving | keyset, window neo as_of, count defs, cursor 400/410, category EXISTS | Migration | **Kế hoạch (chưa tạo)** |
| ADR-021 · Dagster ownership | Plan A wrapper, CAS+lock+idempotent+bootstrap | Migration | **Kế hoạch (chưa tạo)** |
| ADR-022 · Deployment | Cloud Run + WIF, SA tách môi trường, staging/prod | Migration | **Kế hoạch (chưa tạo)** |
| ADR-023 · Session mgmt | storage-state là secret, refresh thủ công | Migration | **Kế hoạch (chưa tạo)** |
| ADR-024 · Salary normalization | FX 25.500, VND theo triệu, one-sided, period scope | Migration | **Kế hoạch (chưa tạo)** |
| ADR-025 · Atomic publication | warehouse_state + warehouse_batches, transaction, rollback | Migration | **Kế hoạch (chưa tạo)** |
| ADR-026 · Pagination snapshot | page token gắn batch, 410 expired, cache key gắn batch | Migration | **Kế hoạch (chưa tạo)** |

> Chỉ mục xếp theo tuần/giai đoạn ra quyết định. ADR-020..026 được tham chiếu trong plan/ADR khác nhưng
> **chưa tạo file** — sẽ viết ở đúng phase migration tương ứng.
