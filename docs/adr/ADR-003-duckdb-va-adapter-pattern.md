# ADR-003: DuckDB làm kho phân tích cục bộ + Warehouse adapter pattern

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 3]
- Người quyết định: [Điền tên]

## Bối cảnh
Nguồn là MySQL OLTP (chứa PII, không tối ưu cho tổng hợp). Cần một kho để phục vụ đọc nhanh, tách khỏi
nguồn. Dự án là học tập, chạy cục bộ, muốn miễn phí; nhưng phải để ngỏ đường lên BigQuery khi triển
khai thật (Tuần 7, tuỳ chọn) mà không phải viết lại tầng API.

## Quyết định
Dùng **DuckDB** làm kho phân tích cục bộ (file `analytics.duckdb`), và truy cập kho qua một **interface
adapter** (`JobRepository` trong `app/warehouse/base.py`). Handler chỉ phụ thuộc interface; đổi backend
chỉ sửa `app/api/deps.py` (một nhánh `if`) + biến môi trường `JOBS_API_WAREHOUSE_BACKEND`.

## Phương án đã cân nhắc
- **Phục vụ thẳng từ MySQL** — không cần kho mới; nhưng không tách được nguồn OLTP khỏi tải phục vụ,
  và PII vẫn nằm cùng chỗ với dữ liệu phục vụ. Loại.
- **BigQuery ngay từ đầu** — giống prod nhất; nhưng tốn tiền, cần mạng/cloud, độ trễ tối thiểu 0,5–2s,
  không hợp cho học cục bộ. Hoãn sang Tuần 7 (tuỳ chọn), để sẵn `BigQueryJobRepository`.
- **SQLite** — cũng cục bộ; nhưng DuckDB tối ưu cho truy vấn phân tích cột và đọc pandas/parquet mượt
  hơn, hợp với vai trò "kho phân tích". Chọn DuckDB.

## Hệ quả
- Tích cực: học cục bộ miễn phí; ranh giới rõ (ELT↔MySQL, API↔DuckDB); thêm BigQuery sau chỉ là một
  adapter nữa, không đụng `app/api/` hay `app/models/`.
- Đánh đổi: hành vi DuckDB cục bộ khác BigQuery ở vài điểm (kiểu dữ liệu, timeout, tính tiền theo byte)
  → cần test đối chiếu và một số thói quen (column projection) "để dành" cho ngày lên cloud.
