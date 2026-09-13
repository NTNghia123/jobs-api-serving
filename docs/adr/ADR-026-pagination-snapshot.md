# ADR-026: Pagination snapshot — token gắn batch, metadata theo batch, cache key theo batch_id

- Trạng thái: Đã chấp nhận
- Ngày: 2026-09-13
- Người quyết định: [Điền tên]
- Liên quan: ADR-020 (BigQuery serving), ADR-025 (atomic publication), ADR-006 (keyset), ADR-002
  (POST search), ADR-009 (cache); impl `app/domain/pagination.py`, `app/infrastructure/warehouse/
  read_mapping.py` (build_next_cursor), `bigquery_jobs.py`/`duckdb_jobs.py`, `app/api/market.py`.

## Bối cảnh
Kho versioned theo `batch_id`, publish nguyên tử đổi pointer (ADR-025). Một phiên phân trang có
thể kéo dài qua lúc một batch MỚI được publish. Nếu trang 2 đọc "batch hiện thời" thì kết quả trộn
hai snapshot khác nhau (job trùng/nhảy, `as_of` đổi giữa chừng). Consumer là máy → phải tất định.
Đồng thời `/market/metrics` cache theo mốc thời gian dễ **collision** khi hai batch cùng ngày.

## Quyết định
- **page_token = HMAC-signed** (ADR-002/`pagination.py`) bọc `{cursor, filter_fingerprint}`; sửa
  token → chữ ký sai → **400 `INVALID_PAGE_TOKEN`**. Token mờ với consumer (không tự tạo/sửa).
- **Cursor gắn batch (adapter dựng, `build_next_cursor`):** `{batch_id, as_of, last_sort, last_job_id}`.
  - `batch_id`: mọi query silver buộc `batch_id = @batch_id` → **trang 2 đọc ĐÚNG batch của token**,
    không phải batch hiện thời (batch B vừa publish không lọt vào phiên đang phân trang batch A).
  - `as_of`: **snapshot** data_as_of_at của batch (bất biến) → trang 2+ KHỎI query lại
    `warehouse_batches`; response `as_of` của mọi trang trong phiên GIỐNG nhau (của batch trong token).
  - `last_sort` (đã COALESCE khớp ORDER BY) + `last_job_id`: vị trí keyset (ADR-006); date → ISO khi
    encode (JSON), adapter coerce lại khi đọc.
- **filter_fingerprint** (handler, băm `filters+sort`): đổi filter mà dùng token cũ → **400** (không
  âm thầm trộn hai truy vấn). Trang 1 (không token) mới phân giải batch hiện thời từ pointer.
- **Metadata đọc theo `batch_id`** (từ `warehouse_batches`), KHÔNG theo current state → trang 2 batch A
  trả đúng `as_of` A; rollback chỉ đổi pointer, phiên đang mở không bị kéo sang batch khác.
- **Cursor lỗi:** **400 `INVALID_PAGE_TOKEN`** (sai chữ ký / hỏng định dạng / lệch fingerprint) ·
  **410 `CURSOR_EXPIRED`** khi batch trong token đã bị dọn (TTL). Cleanup/retention tự động để [SAU];
  hiện batch bất biến & chưa dọn → nhánh 410 đã thiết kế, chưa kích hoạt.
- **Cache key metrics gắn `batch_id`:** `{env}:metrics:v1:{batch_id}:{window}:{dimension}` — thay
  surrogate `as_of_date` cũ (hai batch cùng ngày collision). Handler đọc `current_batch()` MỘT lần →
  cùng `batch_id` cho cache key lẫn `market_metrics(...)` (không lệch nếu batch mới publish giữa chừng).
  Publish batch mới → `batch_id` mới → cache cũ tự nhiên không trúng (không cần invalidation tường minh).
  `/v1/jobs/search` KHÔNG cache (chỉ metrics).

## Phương án đã cân nhắc
- **Trang 2 đọc batch hiện thời** — trộn snapshot khi có publish giữa chừng (job trùng/nhảy, `as_of`
  đổi); loại.
- **Không snapshot `as_of` trong cursor, query lại mỗi trang** — thêm round-trip mỗi trang; batch bất
  biến nên snapshot an toàn và rẻ hơn.
- **Token đổi filter âm thầm** — client tưởng phân trang tiếp nhưng thực ra truy vấn khác; fingerprint
  → 400 tường minh.
- **Cache key theo `as_of_date`** — hai batch cùng ngày đụng key → trả số cũ; `batch_id` duy nhất.
- **Cache cả search** — kết quả phân trang gắn token, ít tái dùng, dễ lệch khi đổi batch; chỉ cache
  metrics (đọc-nhiều, tổng hợp sẵn).

## Hệ quả
- Tích cực: phiên phân trang tất định (page 2 = đúng batch + `as_of` của page 1); rollback/publish giữa
  chừng an toàn; đổi filter → 400 rõ ràng; cache metrics đúng theo batch, tự hết hiệu lực khi có batch
  mới; parity fake ↔ duckdb ↔ BQ (cùng `build_next_cursor`).
- Đánh đổi: `as_of` snapshot trong token → token dài hơn chút (đã ký, chống sửa); nhánh 410 phụ thuộc
  cleanup/retention [SAU]; TTL token phải ≤ thời gian giữ batch (không dọn batch còn trong TTL) — thực
  thi khi làm cleanup.
