# ADR-008: Kiểm soát chi phí kiểu DuckDB (hoãn dry-run & maximum_bytes_billed)

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 4]
- Người quyết định: [Điền tên]

## Bối cảnh
Curriculum/mentor Tuần 4 nhắc `maximum_bytes_billed` và **dry run** để chặn/ước lượng chi phí — nhưng
đây là cơ chế **đặc thù BigQuery** (tính tiền theo byte quét). Dự án đang chạy **DuckDB cục bộ**, nơi
"chi phí" là thời gian và bộ nhớ, không phải tiền. Lộ trình đặt việc lên cloud ở Tuần 7 (tuỳ chọn).

## Quyết định
Ở Tuần 4 dùng **kiểm soát chi phí phù hợp DuckDB**: trần số dòng (`MAX_LIMIT`) + **column projection**
(chỉ SELECT cột cần, không `SELECT *`) + lọc sớm. **Hoãn** `dry run` và `maximum_bytes_billed` tới khi
thực sự thêm `BigQueryAdapter` (Tuần 7).

## Phương án đã cân nhắc
- **Dựng BigQueryAdapter + dry-run + max_bytes_billed ngay Tuần 4** — sát prod nhất; nhưng cần tài
  khoản GCP, tải dữ liệu lên, có thể phát sinh chi phí, và lệch trước lộ trình (cloud vốn để Tuần 7).
  Loại ở tuần này.
- **Bỏ qua kiểm soát chi phí vì cục bộ "miễn phí"** — sai thói quen; loại. Vẫn giữ trần dòng + projection
  để khi lên cloud không phải sửa tư duy.

## Hệ quả
- Tích cực: giữ dự án cục bộ, miễn phí; thói quen "lọc sớm, chọn đúng cột" đã sẵn cho ngày lên BigQuery
  (nơi nó thành tiền thật).
- Đánh đổi: chưa có trần chi phí theo byte và chưa ước lượng trước truy vấn. Khi lên BigQuery (Tuần 7),
  **bắt buộc** thêm `maximum_bytes_billed` + `dry run` trong `BigQueryAdapter.run_query` — ghi lại đây
  như một mục việc treo.

## Lưu ý liên quan
- LIMIT **không** phải cơ chế kiểm soát chi phí trên BigQuery bảng không phân cụm (engine vẫn quét cột
  rồi mới cắt dòng). Trên DuckDB, LIMIT giúp giảm thời gian trả kết quả nhưng nguyên tắc vẫn là **lọc
  sớm + chỉ chọn cột cần**.
