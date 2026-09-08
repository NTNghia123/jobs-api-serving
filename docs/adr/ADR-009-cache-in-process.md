# ADR-009: Cache qua interface (in-memory / Redis), chọn bằng config

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 5]
- Người quyết định: [Điền tên]

## Bối cảnh
`/market/metrics` đọc từ gold table; cùng một `dimension` được gọi lặp lại. Cần caching để giảm độ trễ
và tải. Câu hỏi: cache ở đâu — trong tiến trình (in-process) hay ngoài (Redis/Memorystore)?

## Quyết định
Dùng **một interface `CacheBackend`** (ở `app/domain/ports/cache.py`) với hai hiện thực ở
`app/infrastructure/cache/`, chọn bằng `JOBS_API_CACHE_BACKEND`:
- **`InMemoryCache`** (mặc định) — dùng thư viện `cachetools.TTLCache`, in-process, không cần server.
- **`RedisCache`** — dùng thư viện `redis`, chia sẻ giữa nhiều instance; bật khi `=redis` và có server.

Cache lưu **chuỗi JSON** (Redis chỉ giữ chuỗi/bytes → cho hai backend cùng một hợp đồng); endpoint tự
`model_dump_json()` khi ghi, `model_validate_json()` khi đọc, và **làm mới `request_id`** khi cache hit.

## Phương án đã cân nhắc
- **Tự viết TTLCache** — minh bạch nhưng phải tự bảo trì; thay bằng `cachetools`.
- **Redis-only ngay** — bắt dev/test luôn phải có Redis server. Loại; Redis là tuỳ chọn qua config.
- **Không cache** — bỏ lỡ deliverable "caching" và không đo được cải thiện độ trễ. Loại.

## Hệ quả
- Tích cực: dev chạy không cần hạ tầng; đo được độ trễ giảm rõ khi hit; sẵn sàng cho nhiều instance.
- Đánh đổi: cache **in-process** sai trên Cloud Run nhiều instance (mỗi instance một bộ nhớ) → lên cloud
  đổi `=redis`. TTL nên gắn với độ tươi gold; `as_of` trong response cho consumer biết dữ liệu cũ/mới.
