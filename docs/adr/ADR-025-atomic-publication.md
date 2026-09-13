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
  seed 1 dòng `NULL` **TRONG publish transaction** — INSERT…WHERE NOT EXISTS trước CAS, tránh hai
  first-publish đồng thời chèn 2 dòng ⇒ CAS `@@row_count=2` hỏng vĩnh viễn) +
  `warehouse_batches` (catalog **BẤT BIẾN**: batch_id + data_as_of_at +
  as_of_date + published_at + counts + lineage + version). **Có mặt trong catalog = đã publish.**
- **Bảng dữ liệu versioned theo `batch_id`, ELT APPEND.** `WRITE_TRUNCATE`/`DROP` chỉ cho **work
  table**, KHÔNG cho bảng published. Giữ ≥2 batch.
- **Work table (candidate) đặt tên RIÊNG theo run** `*_candidate__<batch_id_sạch>_<random>` — KHÔNG
  dùng chung (sửa 2026-09-13). Candidate cố định + `WRITE_TRUNCATE` → hai run song song có thể ghi đè
  work table của nhau. Mỗi run tạo→load→**DROP** work table riêng (kèm expiration 1 ngày làm lưới an
  toàn nếu drop lỗi). Load candidate NGOÀI transaction (load job không chạy trong txn được).
- **Publish = DELETE+INSERT dữ liệu + INSERT catalog + CAS pointer trong CÙNG một transaction**
  (sửa 2026-09-13 — trước đây append silver/gold ĐỨNG NGOÀI transaction; hai run cùng `batch_id` có
  thể để lại rows trùng của run thua CAS, và NO_OP sau đó không dọn). Nay trong txn:
  `DELETE FROM {silver,gold,quarantine} WHERE batch_id=@b` (idempotent + gộp RELOAD_PARTIAL) →
  `INSERT … SELECT * FROM <candidate>` → `UPDATE warehouse_state … WHERE published_batch_id=@expected_prev`
  (NULL-safe lần đầu) → **assert `@@row_count=1` NGAY SAU UPDATE**, sai → `RAISE` → ROLLBACK **toàn bộ**
  (kể cả INSERT dữ liệu) → INSERT catalog. `published_at` sinh trong transaction. Run thua CAS / bị
  abort do concurrent mutation ⇒ rollback sạch, KHÔNG rows trùng.
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
- **Append silver/gold NGOÀI transaction (chỉ catalog+pointer trong txn)** — ĐÃ BỎ (2026-09-13): tuy
  rẻ hơn, nhưng hai run cùng `batch_id` để lại rows trùng của run thua CAS (rows ngoài txn không
  rollback) và NO_OP sau không dọn. Nay **DELETE+INSERT dữ liệu + seed singleton nằm TRONG cùng
  transaction** với CAS — đắt hơn chút nhưng nguyên tử thật sự dưới đồng thời (xem "Quyết định").

## Hệ quả
- Tích cực: publish nguyên tử; retry/đồng thời an toàn (CAS); rollback rẻ (đổi pointer); page 2 đọc đúng
  batch + `as_of` của trang 1 (ADR-026); batch đã publish bất biến (audit/lineage).
- Đánh đổi: rollback/promote phải tường minh (chưa tự động); cleanup/retention batch cũ để [SAU];
  `elt_batches` đầy đủ + incremental MERGE để [SAU].
