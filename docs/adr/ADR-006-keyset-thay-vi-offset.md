# ADR-006: Phân trang keyset (cursor ký HMAC) thay vì offset

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 2]
- Người quyết định: [Điền tên]

> Cơ chế ký/vân tay được hiện thực trong `app/domain/pagination.py`; hình dạng cursor sinh ra ở
> `app/warehouse/*` (`{"k": [giá_trị_sort, job_id]}`).

## Bối cảnh
`/jobs/search` cần phân trang. Consumer là máy (team AI) duyệt tuần tự các trang, không cần "nhảy tới
trang N". Kho có thể thay đổi giữa các lần gọi (ELT chạy lại). Ngoài ra, nội dung con trỏ trang là dữ
liệu **do người gọi gửi lên** — nếu Tuần 3 dùng nó để dựng mệnh đề `WHERE`, một con trỏ bị sửa sẽ đi
vòng qua toàn bộ allowlist (một lỗ injection nằm ngay trong cơ chế phân trang).

## Quyết định
Dùng **phân trang keyset** (con trỏ mang khoá sắp xếp của dòng cuối) qua `page_token`, thay vì
`OFFSET/LIMIT`. Khoá sắp xếp **luôn kèm `job_id`** để thứ tự tất định. `page_token` được **ký
HMAC-SHA256**, so sánh bằng `hmac.compare_digest` (thời gian hằng định, chống timing attack), và gắn
**vân tay của bộ filter** — đổi filter mà giữ token cũ sẽ bị từ chối.

## Phương án đã cân nhắc
- **`OFFSET/LIMIT`** — đơn giản, cho phép nhảy tới trang bất kỳ; nhưng chi phí tăng theo `OFFSET`
  (trên kho lớn/BigQuery phải quét rồi bỏ các dòng trước đó), và **không ổn định** khi dữ liệu đổi:
  thêm/xoá dòng làm mọi dòng dịch chỉ số → trang sau bị **trùng hoặc sót** bản ghi. Loại.
- **`page_token` chỉ base64 JSON (không ký)** — mờ với mắt người nhưng người gọi vẫn sửa được nội dung
  bên trong; khi token dùng để dựng `WHERE` thì đây là lỗ hổng. Loại — bắt buộc phải ký.
- **Khoá sắp xếp không kèm `job_id`** — các dòng cùng mức lương có khoá trùng → phân trang trùng/sót ở
  ranh giới trang. Loại — luôn thêm `job_id` làm khoá phụ tất định.

## Hệ quả
- Tích cực: chi phí không tăng theo số trang; kết quả ổn định khi dữ liệu đổi; token không thể bị giả
  mạo hay tái dùng chéo giữa hai bộ filter khác nhau.
- Đánh đổi: **không** nhảy trực tiếp tới "trang N" (chấp nhận được vì consumer duyệt tuần tự); cần giữ
  bí mật khoá HMAC (`JOBS_API_PAGE_TOKEN_SECRET`) — prod phải lấy từ Secret Manager, không dùng mặc định.
- Kiểm chứng bằng test: duyệt hết bằng `page_token` không trùng/không sót; sửa một ký tự trong token thì
  bị từ chối.
