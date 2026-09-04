# ADR-002: `/jobs/search` dùng POST dù là thao tác chỉ đọc

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 2]
- Người quyết định: [Điền tên]

> File này được tham chiếu trực tiếp trong mã: `app/api/jobs.py` (mô tả endpoint "xem ADR-002").

## Bối cảnh
Tìm kiếm là thao tác đọc, REST thuần gợi ý dùng `GET`. Nhưng bộ filter dự kiến mở rộng thành cấu trúc
lồng nhau (nhiều khoảng lương, nhiều cấp bậc), và giá trị filter có thể chứa dữ liệu nhạy cảm — không
nên nằm trong URL vì URL lọt vào access log, lịch sử trình duyệt, referer.

## Quyết định
Dùng **`POST /v1/jobs/search`** với body JSON, dù endpoint không thay đổi trạng thái (chỉ đọc).

## Phương án đã cân nhắc
- **`GET` với query string** — cache HTTP tự nhiên, đúng chuẩn REST cho thao tác đọc; nhưng filter lồng
  nhau khó biểu diễn trên query string, và giá trị filter lọt vào log/URL. Loại.
- **`GET` với body** — nhiều proxy/thư viện bỏ qua body của GET; không đáng tin. Loại.

## Hệ quả
- Tích cực: filter lồng nhau biểu diễn tự nhiên; giá trị filter không vào URL/access log.
- Đánh đổi: **mất HTTP caching** ở tầng CDN/proxy — sẽ bù bằng cache phía server (Tuần 5). Vì POST không
  tự nói lên rằng thao tác an toàn để retry, điều này được ghi rõ trong mô tả OpenAPI để consumer biết
  gọi lại là an toàn (idempotent về mặt tác dụng).
