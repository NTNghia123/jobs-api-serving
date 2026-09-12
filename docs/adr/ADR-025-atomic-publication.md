# ADR-025: Publish nguyên tử — pointer + catalog bất biến, CAS trong transaction

- Trạng thái: Đã chấp nhận
- Ngày: 2026-09-13
- Người quyết định: [Điền tên]
- Liên quan: ADR-018 (BigQuery), ADR-026 (pagination snapshot); impl `app/elt/serving/publish.py`,
  `bigquery_writer.py`, `schema.py`

## Bối cảnh
API chỉ được đọc batch **hoàn chỉnh** (silver + gold + metadata nhất quán), và phải trả đúng `as_of`
của batch đang phục vụ kể cả khi một batch mới đang được load. ELT có thể chạy lại (retry, crash giữa
chừng, hai lần gọi đồng thời) → cần publish **nguyên tử, idempotent, có rollback rẻ**, giữ **≥2 batch**
để rollback được.

## Quyết định
- **Hai bảng metadata:** `warehouse_state` (pointer singleton: `warehouse_name`, `published_batch_id`;
  bootstrap 1 dòng `NULL`) + `warehouse_batches` (catalog **BẤT BIẾN**: batch_id + data_as_of_at +
  as_of_date + published_at + counts + lineage + version). **Có mặt trong catalog = đã publish.**
- **Bảng dữ liệu versioned theo `batch_id`, ELT APPEND.** `WRITE_TRUNCATE` chỉ cho bảng `*_candidate`
  (work table), KHÔNG cho bảng published. Giữ ≥2 batch.
- **Publish = INSERT catalog + CAS pointer trong CÙNG một BigQuery transaction:**
  `UPDATE warehouse_state SET published_batch_id=@batch WHERE published_batch_id=@expected_prev`
  (NULL-safe lần đầu) → **assert `@@row_count=1`** trước COMMIT, sai → `RAISE` → ROLLBACK.
  `published_at` sinh trong transaction. Append silver/gold NGOÀI transaction (OK).
- **Idempotency = state machine theo catalog** (không theo rows): có&==current→NO_OP; có&≠current→
  `BATCH_ALREADY_PUBLISHED` (bất biến, không sửa gì); chưa có & có rows dở dang→RELOAD_PARTIAL (xoá batch
  rồi load lại); chưa có & không rows→NEW_BATCH. Abort do publish đồng thời → đọc lại state, áp lại
  state machine (không retry mù `expected_prev` mới). CLI: `BATCH_ALREADY_PUBLISHED`→exit non-zero.
- **Commit pointer SAU khi silver&gold qua quality checks** → API không bao giờ thấy batch lỗi.
  Rollback = chỉ đổi pointer về batch cũ (dữ liệu cũ còn nguyên).

## Phương án đã cân nhắc
- **Một bảng + cột `status`** — sửa trạng thái tại chỗ không nguyên tử với nhiều bảng; khó rollback.
- **WRITE_TRUNCATE bảng published mỗi lần** — phá versioning, mất ≥2 batch, hỏng pagination snapshot (ADR-026).
- **Idempotency dựa trên "silver có rows"** — crash giữa append và commit để lại rows mồ côi; dùng
  catalog làm trạng thái bất biến tường minh hơn.
- **Transaction bao cả append silver/gold** — BQ DML transaction tốn/giới hạn hơn; append ngoài +
  catalog-gated đủ đảm bảo "chỉ đọc batch đã publish".

## Hệ quả
- Tích cực: publish nguyên tử; retry/đồng thời an toàn (CAS); rollback rẻ (đổi pointer); page 2 đọc đúng
  batch + `as_of` của trang 1 (ADR-026); batch đã publish bất biến (audit/lineage).
- Đánh đổi: rollback/promote phải tường minh (chưa tự động); cleanup/retention batch cũ để [SAU];
  `elt_batches` đầy đủ + incremental MERGE để [SAU].
