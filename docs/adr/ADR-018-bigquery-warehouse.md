# ADR-018: BigQuery làm kho phân tích production (DuckDB dev, fake test)

- Trạng thái: Đã chấp nhận
- Ngày: 2026-09-11
- Người quyết định: [Điền tên]
- Liên quan: mở rộng ADR-003 (DuckDB + adapter pattern); xem `docs/migration-mongo-bigquery-plan.md`

## Bối cảnh
Dự án chuyển từ dữ liệu demo (MySQL 76 dòng → DuckDB cục bộ) sang phục vụ **dữ liệu việc làm thật** do
`job-scraper-1` crawl vào MongoDB (~14.485 job, TopDev + VietnamWorks). Curriculum gốc của mentor
(DE - Serving Platform) hoàn toàn xoay quanh **BigQuery + Cloud Run**: warehouse query integration,
cost control theo byte (`maximum_bytes_billed`, dry-run), gold table, Cloud Run deploy. DuckDB chỉ là
bản thay thế free/local để học.

## Quyết định
- **BigQuery là kho phân tích production.** ELT ghi silver + gold vào BigQuery; API đọc read-only.
- **Giữ adapter pattern (ADR-003/011):** `warehouse_backend = fake | duckdb | bigquery`.
  - `fake` — contract/unit test nhanh, không cần hạ tầng.
  - `duckdb` — phát triển cục bộ, không cần GCP credentials.
  - `bigquery` — production.
  Ba backend **cùng một contract** (bộ contract test parameterize chạy chung — xem ADR-020).
- Cost control là **tiền thật**: mọi query API đặt `maximum_bytes_billed`; partition + cluster để prune;
  column projection (không `SELECT *`). Chi tiết ở ADR-020.

## Phương án đã cân nhắc
- **Postgres/OpenSearch làm kho phục vụ** — tốt cho full-text search low-latency, nhưng lệch curriculum
  (mentor hướng BigQuery) và thêm một hệ phải vận hành. Giữ lại như phương án [SAU] nếu độ trễ/relevance
  của BigQuery cho `/jobs/search` thật sự thành vấn đề (ADR-020 ghi rõ ngưỡng cân nhắc).
- **Chỉ DuckDB (kể cả prod)** — miễn phí nhưng không dạy được kỹ năng BigQuery/cost-control mà curriculum
  yêu cầu, và không chạy tốt trên Cloud Run (kho là file cục bộ, stateless/ephemeral). Bỏ.

## Hệ quả
- Tích cực: đúng curriculum + kỹ năng GCP; cost-control thành cơ chế thật; API mỏng (chỉ đọc gold/silver).
- Đánh đổi / rủi ro: BigQuery **không phải DB phục vụ low-latency** (độ trễ 0,5–2s, không ranking văn bản
  tốt) → `/jobs/search` phải dựa vào gold + cache + partition prune; nếu chưa đủ, thêm lớp serving
  Postgres/OpenSearch (ADR-020, [SAU]). BigQuery tính theo byte → sai cost-control là ra tiền.
- **DuckDB TẠM tắt trong migration:** adapter DuckDB còn schema silver CŨ (years_exp/country/job_id:int)
  chưa khớp contract mới → `settings` + `deps` **từ chối rõ** `warehouse_backend='duckdb'` (fail-fast, không
  boot-rồi-500). Dev/test dùng `fake`; DuckDB parity với schema mới **khôi phục ở Phase 4**. `_AS_OF` hard-code
  của nó chỉ hợp cho dev (prod dùng `as_of` động từ `warehouse_batches` — ADR-025/026).
