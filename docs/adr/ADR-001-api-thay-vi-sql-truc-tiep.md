# ADR-001: Tầng API hướng nghiệp vụ thay vì cấp quyền SQL trực tiếp

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 1]
- Người quyết định: [Điền tên]

## Bối cảnh
Team AI cần dữ liệu tuyển dụng (gợi ý việc, benchmark lương). Toàn bộ dữ liệu nằm trong MySQL
`fulfilen/job-portal` — một DB OLTP của web PHP, chứa PII nặng: `users.email`, `users.password`
(lưu base64 — KHÔNG phải mã hoá), `dob`, `address`, `contactno`; `company` có email/điện thoại liên hệ.
Hai lựa chọn phục vụ: (a) cấp cho team AI quyền truy vấn thẳng kho, hoặc (b) dựng một tầng API trung gian.

## Quyết định
Dựng **tầng API chỉ-đọc phơi bày hoạt động nghiệp vụ** (vd `POST /jobs/search`, `GET /market/metrics`),
không cho consumer viết SQL tuỳ ý và không phơi bày bảng thô.

## Phương án đã cân nhắc
- **Cấp quyền SQL trực tiếp vào kho** — nhanh, không phải xây gì; nhưng: quyền đọc DB là quyền đọc *mọi*
  cột (rò PII ở mức trường), mở đường cho truy vấn ngốn tài nguyên/chi phí, và mỗi lần đổi schema là làm
  vỡ mọi consumer. Loại cho sản phẩm chạy thật (có thể chấp nhận ở sandbox dữ liệu đã ẩn danh).
- **Authorized view + column-level security của BigQuery** — đối thủ đáng cân nhắc nhất; kết luận: dùng
  chính nó làm nền cho API (hai lớp phòng thủ độc lập: IAM + allowlist trong code), không thay cho API.

## Hệ quả
- Tích cực: đổi từ "cấm cột nhạy cảm" (dễ sót) sang "chỉ cho phép cột đã liệt kê" (mặc định an toàn);
  hợp đồng ngoài ổn định trong khi schema kho được tự do tiến hoá; định nghĩa nghiệp vụ chốt một chỗ.
- Đánh đổi: phải tự xây và bảo trì tầng API; thêm filter mới cần sửa code (giảm nhẹ bằng `/metadata` để
  consumer tự khám phá).
