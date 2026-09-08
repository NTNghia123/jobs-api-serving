# ADR-012: Xác thực bằng API key (lưu hash + hạn dùng), không dùng JWT

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 6]
- Người quyết định: [Điền tên]

## Bối cảnh
Cần bảo vệ mọi endpoint `/v1` (trừ `/health`), nhận diện từng client và rate-limit theo client.
Consumer là **team AI nội bộ** (máy gọi máy), số lượng ít, cùng tổ chức. Hai lựa chọn phổ biến:
API key hay JWT.

## Quyết định
Dùng **API key** gửi qua header `X-API-Key`. Server **chỉ lưu SHA-256 của key + hạn dùng**
(`expires_at`), không lưu key thô. So khớp bằng `hmac.compare_digest` (hằng-thời-gian). Key phát bằng
`scripts/issue_key.py` (in key thô một lần cho consumer, lưu hash vào config).

## Phương án đã cân nhắc
- **JWT (claims + expiry, ký số)** — mạnh khi cần nhiều scope/phân quyền chi tiết và xác thực không cần
  tra store; nhưng phức tạp hơn (khoá ký, xoay khoá, thư viện) và **thừa** cho một nhóm consumer nội bộ
  đồng nhất. Hoãn — có thể thêm sau nếu xuất hiện nhu cầu scope.
- **Dict key plaintext trong code ("đồ chơi")** — đơn giản nhưng rò key nếu lộ source/log, không có hạn
  dùng/xoay vòng. Loại: lưu **hash + expiry**, key thô không bao giờ nằm trong code/log.
- **Xác thực service-to-service bằng OIDC (Cloud Run)** — tốt nhất khi consumer cùng GCP; nhưng gắn với
  cloud. Để dành Tuần 7 nếu triển khai lên Cloud Run.

## Hệ quả
- Tích cực: đơn giản, đủ mạnh cho nội bộ; không lưu bí mật thô; dễ phát **test token** cho Tuần 8
  (chính là API key). Hạn dùng + xoay vòng bằng cách phát key mới và thay hash trong config.
- Đánh đổi: không mang được claim/scope trong token (phải tra store mỗi request — rẻ ở quy mô này);
  cần bảo vệ danh sách hash trong config (env/Secret Manager), và **tuyệt đối không log key**.
- Prod bắt buộc đặt `JOBS_API_API_KEYS` (settings từ chối chạy prod không có key); local tự seed 1 key
  dev để `/docs` và test chạy được ngay.
