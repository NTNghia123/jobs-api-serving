# ADR-007: Bỏ required date/snapshot filter, giữ nguyên cơ chế

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 4]
- Người quyết định: [Điền tên]

## Bối cảnh
Curriculum/mentor Tuần 4 yêu cầu "required snapshot/date filter" — một cơ chế ép consumer phải gửi
filter ngày để tránh quét toàn bộ lịch sử (hợp lý trên kho lớn tính tiền theo phân vùng). Nhưng dữ liệu
thật `fulfilen/job-portal` có `createdat` **đồng nhất** (mọi tin = 2017-10-10), nên filter ngày không
lọc được gì. Ở Tuần 2 ta đã bỏ `posted_after` vì lý do này.

## Quyết định
**Không** ép bất kỳ filter ngày bắt buộc nào. Nhưng `QueryValidator` **vẫn hiện thực đầy đủ cơ chế
required-filter** (`REQUIRED_FILTERS` + `check_required()`), chỉ để tập rỗng cho dữ liệu này. Cơ chế
sẵn sàng bật lại chỉ bằng cách thêm tên filter vào `REQUIRED_FILTERS`.

## Phương án đã cân nhắc
- **Ép required date filter cho đủ yêu cầu chung** — nhưng chỉ tạo một ràng buộc bắt consumer gửi field
  vô dụng, không tăng an toàn/hiệu năng thật. Loại.
- **Sinh `createdat` ngẫu nhiên ở bước transform** để filter ngày có nghĩa — luyện đủ cơ chế nhưng làm
  dữ liệu "giả" hơn thật; để dành như một bài tập tuỳ chọn, không đưa vào đường chính.
- **Bỏ luôn cả cơ chế** — mất khả năng minh hoạ và khó bật lại sau. Loại; giữ cơ chế, để rỗng.

## Hệ quả
- Tích cực: an toàn/kiểm soát chi phí căn theo dữ liệu thật; cơ chế required-filter test được và bật lại
  dễ dàng khi cần (vd khi có cột ngày thật).
- Đánh đổi: kiểm soát chi phí chính dựa vào **trần số dòng (MAX_LIMIT) + column projection**, không phải
  filter ngày. Chấp nhận được ở quy mô 76 dòng; khi lên dữ liệu lớn/BigQuery cần xem lại (xem ADR-008).
