# ADR-011: Refactor sang cấu trúc hexagonal (ports & adapters)

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 5]
- Người quyết định: [Điền tên]

## Bối cảnh
Sau Tuần 5, các abstraction bị đặt lệch chỗ và không nhất quán: interface `JobRepository` nằm CHUNG
folder với impl (`app/warehouse/`), còn cache (cả interface lẫn impl) lại nằm trong `app/domain/`. Hệ
quả: `domain` (lõi) chứa luôn code nói chuyện với hệ thống ngoài (Redis) — sai vai trò của lõi.

## Quyết định
Tách theo **Ports & Adapters**:
- **INTERFACE (port) → `app/domain/ports/`**: `job_repository`, `metrics_repository`, `cache` (kèm DTO
  `SearchResult`, `MetricRow`).
- **IMPLEMENTATION (adapter) → `app/infrastructure/`**: `cache/{memory,redis}.py`,
  `warehouse/{duckdb_jobs,duckdb_metrics,fake_jobs,fake_metrics}.py` + `warehouse/sql/`.
- **Luật phụ thuộc:** mọi phụ thuộc chĩa vào lõi; `domain` không import `infrastructure`; **chỉ
  `app/api/deps.py` (composition root)** được import các adapter cụ thể.

## Phương án đã cân nhắc
- **Giữ nguyên** (interface + impl chung folder như `warehouse/`) — đơn giản nhưng không nhất quán và để
  code hạ tầng lẫn vào lõi. Loại.
- **Thêm lớp `application/` với ports + services** — đúng Clean Architecture đầy đủ; nhưng hiện điều phối
  nằm ở handler mỏng, dựng thêm lớp service rỗng là thừa tầng. Hoãn tới khi logic phình to; tạm đặt ports
  ngay trong `domain/`.

## Hệ quả
- Tích cực: lõi độc lập với Redis/DuckDB; đổi/thêm adapter (vd BigQuery) không đụng lõi; cấu trúc nhất
  quán cho cả 3 abstraction, dễ đọc.
- Đánh đổi: đổi nhiều đường import trong một lần; `domain/ports` vẫn import `models` (Pydantic) — chấp
  nhận ở mức học tập (chưa tách domain entity thuần khỏi DTO).
- **Không đổi hành vi:** 42 test xanh cả trước lẫn sau refactor (đối chiếu để chắc chắn).
