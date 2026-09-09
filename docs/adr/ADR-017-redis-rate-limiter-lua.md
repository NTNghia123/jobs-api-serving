# ADR-017: RedisRateLimiter — token bucket nguyên tử bằng Lua (trả nợ ADR-013)

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 7]
- Người quyết định: [Điền tên]
- Liên quan: hoàn tất phần "để Tuần 7" của ADR-013; song song với cache Redis (ADR-009)

## Bối cảnh
ADR-013 chọn token bucket in-process cho Tuần 6 và ghi nợ: **sai trên nhiều instance** (mỗi instance
một xô → hạn mức thực = quota × số instance). Tuần 7 lên container/nhiều instance nên phải chuyển trạng
thái rate-limit sang kho chia sẻ, giống cache đã có RedisCache (ADR-009).

## Quyết định
- **Thêm `RedisRateLimiter`** (infrastructure/ratelimit/redis.py) cùng hợp đồng port `RateLimiter`
  và cùng thuật toán token bucket, nhưng trạng thái (`tokens`, `ts`) sống trong Redis → mọi instance
  dùng chung một xô cho mỗi client.
- **Nguyên tử bằng Lua**: token bucket là chuỗi đọc-tính-ghi; chạy bằng nhiều lệnh rời rạc sẽ bị race
  giữa các request song song. Gói vào một script Lua (`register_script` → EVALSHA) chạy nguyên tử trên
  server Redis.
- **Đồng hồ lấy từ `redis TIME`** (không phải đồng hồ từng máy) để các instance cùng một nguồn thời gian.
- **Chọn backend qua `JOBS_API_RATE_LIMITER_BACKEND`** (memory | redis) ở composition root (deps.py);
  handler không đổi. TTL của khoá = thời gian hồi đầy xô + biên, để client rảnh tự dọn.

## Phương án đã cân nhắc
- **Nhiều lệnh Redis (GET/SET) không Lua** — đơn giản nhưng có race, đúng thứ đang cần tránh. Bỏ.
- **INCR + EXPIRE (fixed window)** — nguyên tử dễ hơn nhưng là fixed-window (đúp biên), lệch thuật toán
  với adapter memory → hai backend hành xử khác nhau. Giữ token bucket cho nhất quán (ADR-013).
- **Thư viện ngoài (redis-py Lua có sẵn / limits)** — thêm phụ thuộc cho một script 20 dòng. Tự viết.

## Hệ quả
- Tích cực: hạn mức đúng khi chạy nhiều instance; cùng thuật toán với memory nên test hợp đồng dùng chung.
- Đánh đổi / rủi ro: mỗi request thêm một round-trip Redis (chấp nhận được); phụ thuộc Redis sẵn sàng —
  nếu Redis chết, cần quyết định fail-open hay fail-closed (hiện để lỗi nổi lên, chưa xử lý mềm — nợ nhỏ).
- Local mặc định vẫn `memory` để dev không cần Redis; compose đặt `redis`.
