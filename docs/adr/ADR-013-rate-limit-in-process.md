# ADR-013: Rate-limit in-process (token bucket) — port + adapter memory, redis để sau

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 6]
- Người quyết định: [Điền tên]

## Bối cảnh
Cần giới hạn tốc độ theo từng client để một consumer không làm nghẽn cả hệ thống (và để quy trách
nhiệm chi phí). Câu hỏi: thuật toán nào, và lưu trạng thái ở đâu (in-process hay chia sẻ).

## Quyết định
- **Thuật toán: token bucket** — mỗi client một "xô" token, hồi đầy theo thời gian (rate/giây), mỗi
  request tốn 1; hết token → 429. Cho phép burst tự nhiên (sức chứa = `rate_limit_burst` hoặc
  `rate_limit_per_minute`).
- **Khuôn Ports & Adapters** (giống cache — ADR-009/011): port `RateLimiter` ở `domain/ports/`, adapter
  `InMemoryRateLimiter` ở `infrastructure/ratelimit/memory.py`. **Chỉ làm adapter memory ở Tuần 6**;
  `RedisRateLimiter` để Tuần 7 khi lên nhiều instance.

## Phương án đã cân nhắc
- **Fixed window counter** — đơn giản nhưng gây "đúp biên" (2× ở ranh giới cửa sổ). Token bucket mượt
  hơn và cho burst có kiểm soát. Chọn token bucket.
- **Làm luôn RedisRateLimiter (đối xứng với cache)** — sẵn cho nhiều instance ngay; nhưng token bucket
  chuẩn trên Redis cần script Lua để atomic (đọc-tính-ghi một bước), phức tạp hơn đáng kể. Hoãn tới
  Tuần 7. Port đã có nên thêm sau không đụng handler.

## Hệ quả
- Tích cực: đơn giản, không hạ tầng; test được bằng đồng hồ giả (tiêm `now`).
- Đánh đổi / rủi ro: **in-process SAI trên Cloud Run nhiều instance** — mỗi instance một xô riêng nên
  hạn mức thực tế = quota × số instance, và một client có thể vượt trần toàn cục. Khi lên cloud **bắt
  buộc** chuyển sang `RedisRateLimiter` (Memorystore) — cùng nợ kỹ thuật với cache in-process (ADR-009).
- Client bị chặn nhận `429 RATE_LIMIT_EXCEEDED` theo đúng envelope lỗi chung.
