# ADR-009: Cache in-process (TTL) cho /market/metrics — chấp nhận hạn chế Cloud Run

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 5]
- Người quyết định: [Điền tên]

## Bối cảnh
`/market/metrics` đọc từ gold table đã tổng hợp; cùng một `dimension` được gọi lặp lại nhiều lần. Cần
caching để giảm độ trễ và tải. Câu hỏi: cache ở đâu — trong tiến trình (in-process) hay ngoài (Redis/
Memorystore)?

## Quyết định
Dùng **một interface `CacheBackend`** với hai hiện thực, chọn bằng config `JOBS_API_CACHE_BACKEND`:
- **`InMemoryCache`** (mặc định) — dùng thư viện `cachetools.TTLCache`, in-process, không cần server.
- **`RedisCache`** — dùng thư viện `redis`, chia sẻ giữa nhiều instance; bật khi `=redis` và có Redis server.

Cache lưu **chuỗi JSON** (Redis chỉ giữ chuỗi/bytes → cho hai backend cùng một hợp đồng); endpoint tự
`model_dump_json()` khi ghi và `model_validate_json()` khi đọc. Cục bộ chạy `memory` (không cần hạ tầng);
lên Cloud Run đổi sang `redis`.

> Cập nhật so với bản đầu: ban đầu dự định tự viết TTLCache và hoãn Redis tới Tuần 7. Đã đổi: dùng thư
> viện (`cachetools`) và **kéo Redis lên sớm** dưới dạng backend tuỳ chọn qua adapter pattern — vẫn giữ
> dev không cần server, nhưng sẵn sàng cho nhiều instance mà không phải sửa handler sau này.

## Phương án đã cân nhắc
- **Tự viết TTLCache** — minh bạch nhưng phải tự bảo trì; thay bằng `cachetools` cho gọn và đáng tin.
- **Redis-only ngay** — đúng cho nhiều instance nhưng bắt dev/test luôn phải có Redis server. Loại; để
  Redis là tuỳ chọn qua config, mặc định memory.
- **Không cache** — bỏ lỡ deliverable "caching" và không đo được cải thiện độ trễ. Loại.

## Hệ quả
- Tích cực: đơn giản, không thêm hạ tầng; đo được độ trễ giảm rõ khi cache hit (tham khảo: hit nhanh hơn
  miss hàng nghìn lần vì miss phải đọc gold qua cursor).
- Đánh đổi / rủi ro: **cache in-process SAI trên Cloud Run nhiều instance** — mỗi instance một bộ nhớ
  riêng nên 10 instance = 10 bản cache khác nhau, hit rate tệ, và độ tươi không đồng nhất. Khi lên
  cloud **bắt buộc** chuyển sang Redis/Memorystore (hoặc chấp nhận có ý thức). Đây là nợ kỹ thuật đã
  ghi, không phải bỏ sót.

## Lưu ý liên quan
- TTL nên gắn với độ tươi dữ liệu (gold nướng lại sau mỗi ELT). `as_of` trong response giúp consumer
  biết dữ liệu cũ/mới ngay cả khi cache còn hiệu lực.
