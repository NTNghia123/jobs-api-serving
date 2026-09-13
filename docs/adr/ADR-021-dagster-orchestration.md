# ADR-021: Dagster orchestration — Plan A wrapper, concurrency key, kill cây process

- Trạng thái: Đã chấp nhận
- Ngày: 2026-09-13
- Người quyết định: [Điền tên]
- Liên quan: ADR-020 (BQ serving), ADR-022 (deployment), ADR-024 (salary), ADR-025 (atomic
  publication), ADR-026 (pagination). Impl ở **repo scraper** `job-scraper-1` (Dagster sống cạnh
  crawler): `orchestration/job_scraper_dagster/{runner,assets_serving,assets_topdev,
  assets_vietnamworks,__init__}.py`; ELT CLI `app/elt/serving/run_serving_elt.py` (repo này).

## Bối cảnh
Dagster đã có ở repo scraper (assets crawl partitioned theo category cho TopDev/VNW + reporting
Metabase). Cần điều phối luồng hằng ngày **crawl → ELT Mongo→BigQuery** sao cho: ELT chỉ chạy khi
crawl xong-và-ok; crawl thủ công/backfill không chạy chồng lên batch (đụng Mongo); Terminate trên
UI giết được cả cây process (pnpm→node→chromium) kể cả trên VM Linux. Bản thân ELT đã tự-gate chất
lượng + publish nguyên tử (ADR-025), nên orchestrator KHÔNG cần kiểm lại chất lượng.

## Quyết định
- **Plan A = `@job` ops tuần tự** `daily_serving_batch`: `crawl_all_op` → `serving_elt_op`.
  `crawl_all_op` crawl HẾT category 2 platform (mỗi category một process `pnpm start`), cộng dồn
  run_metric. **Cổng fail bằng phụ thuộc op**: crawl_all_op raise (một category exit≠0) → serving_elt_op
  KHÔNG chạy ("lỗi crawl → không ELT"). `crawl_batch_id` sinh đầu job (timestamp+run_id), truyền
  xuống ELT làm lineage. **Giữ nguyên partitioned assets** (`topdev_jobs`/`vietnamworks_jobs`) cho
  manual/backfill trên UI — wrapper KHÔNG dùng chúng.
- **ELT chạy qua CLI cross-repo** (giống `run_reporting` shell sang job-reporting): `serving_elt_op`
  shell `<serving-api venv>/python -m app.elt.serving.run_serving_elt --environment <env>
  --crawl-batch-id <id> --dagster-run-id <run_id>`, cwd/venv theo `SERVING_API_PROJECT_PATH`, kế
  thừa ENV `JOBS_MONGO_*`/`JOBS_BQ_*`. Exit code ELT: 0 ok/no-op · 2 `BATCH_ALREADY_PUBLISHED` ·
  3 quality fail · 1 lỗi → ≠0 làm op fail. ELT emit 1 dòng JSON `run_metric` (silver/gold/quarantine/
  source_total) → runner parse thành metadata hiển thị UI.
- **Chất lượng KHÔNG lặp trong Dagster (một nguồn sự thật):** `checks.py` + publish CAS (ADR-025) đã
  enforce reconciliation `source==silver+quarantine`, `distinct==count`, `sample≤disclosed≤posting`,
  và **pointer chỉ flip khi mọi check pass** (bất biến trong transaction publish, không thể tái tạo
  đúng ngoài ELT). Dagster surface pass/fail qua exit code + run_metric, KHÔNG viết `@asset_check`
  query lại BigQuery (tránh 2 bản logic trôi khỏi nhau, tránh cấp BQ cho Dagster).
- **Concurrency key `serving_pipeline`** (tag `dagster/concurrency_key`) trên: `daily_serving_batch`
  + hai op của nó + hai partitioned crawl asset (`op_tags`). Giới hạn đặt ở **dagster.yaml** (deploy):
  ```yaml
  concurrency:
    pools:
      granularity: op
    default_pool_limit: null
  # hoặc run/op concurrency theo tag key 'serving_pipeline' = 1
  ```
  → crawl thủ công/backfill + wrapper + ELT không chạy đồng thời (đọc Mongo giữa lúc crawl / double-crawl).
- **Kill cây process (runner.py):** `_popen` mở `start_new_session=True` trên POSIX → process group
  riêng; `_kill_process_tree` POSIX `os.killpg(SIGTERM)` graceful, hết 30s mới `SIGKILL` (Windows giữ
  `taskkill /F /T`). Sửa bug cũ: `process.kill()` chỉ giết pnpm, để node/chromium mồ côi trên Linux.
- **Sequencing ≠ data-deps:** ELT đọc THẲNG Mongo (không nhận dữ liệu qua Dagster IO); phụ thuộc op
  chỉ để *sắp thứ tự chạy* (crawl trước, ELT sau) — không phải luồng dữ liệu. Tương tự `reporting_tables`.
- **Bỏ `assets_facebook`** (Facebook ngoài phạm vi — ADR-019).

## Phương án đã cân nhắc
- **Asset-based job** (materialize toàn bộ partition + asset ELT downstream) — fan-in "chờ tất cả
  partition ok" + trộn partitioned/non-partitioned phức tạp; ops @job biểu diễn cổng fail gọn hơn.
- **`@asset_check` query BQ trong Dagster** — lặp logic `checks.py` (2 nguồn dễ trôi), cần BQ access
  cho Dagster; ELT-gate + exit code đủ và một nguồn sự thật.
- **`process.kill()`/`terminate()`** — để lại node/chromium mồ côi trên Linux; killpg group giết sạch.
- **Sensor/run_key + full asset-check + freshness policy** — hoãn [SAU]; Plan A schedule (STOPPED) đủ.

## Hệ quả
- Tích cực: một lệnh (job) chạy crawl→ELT hằng ngày, ELT chỉ chạy khi crawl ok; không chạy chồng
  (concurrency key); Terminate sạch process trên VM; chất lượng một nguồn sự thật (ELT); partitioned
  assets vẫn dùng tay/backfill được.
- Đánh đổi: `crawl_all_op` crawl tuần tự (không song song partition) — an toàn hơn cho anti-bot/Mongo,
  chậm hơn; UI không có ô asset-check riêng cho chất lượng (chỉ op đỏ + run_metric); giới hạn
  concurrency phải cấu hình tay trong dagster.yaml; schedule để STOPPED, bật khi hạ tầng sẵn sàng.
  Sensor/run_key/freshness để [SAU].
