# Migration Report — Phase 2: ELT MongoDB → BigQuery

## 1. Tổng quan

Phase 2 xây pipeline ELT đưa dữ liệu tuyển dụng thật từ MongoDB `job_crawler` sang warehouse
BigQuery theo data contract đã khóa ở Phase 0 và hạ tầng đã chuẩn bị ở Phase 1.

```text
MongoDB job_crawler
├── jobs
├── job_details
└── job_categories
        │
        │ extract + LEFT JOIN theo (platformId, externalId)
        ▼
Mapper và các phép chuẩn hoá thuần
├── SilverRow
└── QuarantineRecord
        │
        ├── build gold: source/seniority/category × 90d/all_time
        └── quality gate
                 │
                 ▼
       BigQuery candidate tables
                 │ kiểm tra đạt
                 ▼
       APPEND immutable batch data
                 │
                 ▼
  Transaction: CAS pointer + INSERT batch catalog
                 │
                 ▼
       Batch mới trở thành published
```

Kết quả cốt lõi là: API trong các phase sau chỉ cần đọc `batch_id` đang được publish, thay vì nhìn
thấy dữ liệu đang load dở hoặc dữ liệu không qua kiểm tra chất lượng.

### Trạng thái

| Phạm vi | Trạng thái | Bằng chứng/ý nghĩa |
| --- | --- | --- |
| Extract, transform, gold và quarantine | **Hoàn thành trong source** | Có implementation và sanitized Mongo-like fixtures |
| BigQuery schema, writer guard và publish state machine | **Hoàn thành trong source** | Có schema khai báo, writer và transaction SQL |
| Kiểm thử logic Phase 2 | **Xanh** | `101 passed` cho `tests/test_elt_*.py`; toàn suite `159 passed, 3 skipped` |
| Khảo sát/dry-run dữ liệu Mongo thật | **Có số liệu ghi trong ADR-024** | Số liệu phụ thuộc snapshot nguồn và cần chạy lại khi corpus thay đổi |
| BigQuery integration trên project cụ thể | **Cần xác minh** | Unit test không thay thế việc kiểm tra load/query/transaction trên BigQuery thật |
| API đọc BigQuery | **Chưa thuộc Phase 2** | API vẫn dùng backend `fake`; BigQuery read adapter thuộc Phase 3 |

> “Phase 2 hoàn thành” nghĩa là pipeline ELT và cơ chế publish đã được hiện thực hoá. Để xác nhận
> vận hành đầu-cuối, vẫn cần chạy với MongoDB và dataset staging/test thật rồi kiểm tra inventory
> BigQuery theo mục 9.

---

## 2. Phase 2 giải quyết vấn đề gì?

Dữ liệu crawler không thể đưa thẳng ra API vì:

- job list có thể chưa có detail hoặc detail chưa crawl xong;
- hai nguồn dùng format ngày, cấp bậc, category và lương khác nhau;
- field lương số của VietnamWorks có trường hợp sai thang đo hoặc gắn nhầm currency;
- raw payload có dữ liệu không cần phục vụ và có nguy cơ chứa PII;
- một lần load thất bại giữa chừng có thể làm silver, gold và metadata lệch nhau;
- API cần một snapshot ổn định để phân trang và trả `as_of` nhất quán.

Phase 2 tạo một ranh giới rõ ràng:

```text
raw Mongo không đồng nhất
        ↓ chuẩn hoá + kiểm tra
silver: một job sạch = một dòng
        ↓ tổng hợp
gold: metric nhỏ, đọc nhanh
        ↓ publish nguyên tử
warehouse snapshot ổn định
```

---

## 3. Luồng xử lý chi tiết

### 3.1. Chọn đích ghi bằng writer guard

CLI bắt buộc nhận `--environment staging|prod`. Dataset được suy ra từ map cấu hình cố định, không
nhận tên dataset tuỳ ý từ request:

```text
staging → JOBS_BQ_DATASET_STAGING → jobs_staging
prod    → JOBS_BQ_DATASET_PROD    → jobs_prod
```

Các giá trị `dev`, `production`, `PROD`, chuỗi rỗng hoặc environment lạ đều bị từ chối trước khi
chạm BigQuery. Khi ghi prod, người vận hành phải truyền rõ `--environment prod`.

`--dataset-suffix _test` chỉ dành cho integration test, ví dụ `jobs_staging_test`. Dataset có hậu
tố phải được tạo sẵn; writer chỉ tạo bảng bên trong dataset, không tạo dataset.

### 3.2. Extract MongoDB

Pipeline chỉ lấy hai nguồn trong phạm vi hiện tại:

- `topdev`;
- `vietnamworks`.

Luồng extract:

1. Đọc `job_details` và lập index theo `(platformId, externalId)`.
2. Đọc taxonomy `job_categories` để làm fallback category.
3. Duyệt `jobs` và **LEFT JOIN** detail theo cùng composite key.
4. Giữ cả job thiếu detail để mapper đưa vào quarantine, không làm mất âm thầm.
5. Đếm số job thực tế theo từng source trong chính extraction run để đối soát.

Không dùng count do Dagster/crawler truyền vào làm mẫu số reconciliation vì count đó có thể thuộc
một thời điểm khác với snapshot Mongo vừa đọc.

### 3.3. Map và chuẩn hoá thành silver

Mỗi job hợp lệ tạo đúng một `SilverRow` với `job_id`:

```text
<source>:<external_id>
```

Ví dụ `topdev:td1001`. Không dùng Mongo `_id`, tránh collision khi hai nguồn dùng cùng external ID.

| Nhóm dữ liệu | Quy tắc chính |
| --- | --- |
| Title | Ưu tiên detail, fallback list record; thiếu cả hai thì quarantine |
| Company | Lấy từ raw theo nguồn; không có thì `null`, không tự tạo `Unknown` |
| Posted date | Parse `postedAt`; lỗi/thiếu thì fallback `firstSeenAt`; cả hai lỗi thì quarantine |
| Deadline | Parse độc lập; lỗi thì để `null`, không loại cả job |
| Experience | Chuẩn hoá thành min/max năm và giữ parse status |
| Seniority | Map level thành `junior`, `mid`, `senior` hoặc `null`; không suy từ title |
| Categories | Repeated STRUCT, key có prefix source, deduplicate theo `category_key` |
| Salary | Chuẩn hoá về VND/tháng, giữ cả raw/original/status/version |
| Batch | Mọi dòng mang `batch_id` để snapshot bất biến và truy vết lineage |

Silver không chứa raw payload, contact, session hoặc các text lớn chưa cần cho serving. Việc loại
chúng ngay ở ELT khiến PII không đi vào bảng serving, thay vì chỉ trông chờ API che cột.

### 3.4. Quarantine

Job không đủ điều kiện phục vụ được ghi vào `warehouse_quarantine` với mã lý do ổn định:

| Thứ tự | Reason code | Khi nào xảy ra |
| --- | --- | --- |
| 1 | `missing_external_id` | Không dựng được composite `job_id` |
| 2 | `missing_detail` | Không có detail hoặc status khác `completed` |
| 3 | `missing_title` | Cả detail và list record đều không có title |
| 4 | `invalid_posted_date` | Không parse được posted date và cũng không có first-seen hợp lệ |

Lương negotiable hoặc lương invalid **không làm mất job**. Job vẫn ở silver nhưng hai cận lương
chuẩn hoá có thể là `null`; điều này giữ corpus tìm kiếm và chỉ loại bản ghi khỏi mẫu median.

Quarantine chỉ lưu mã lỗi và chi tiết đã sanitize, không lưu nguyên raw payload.

### 3.5. Chuẩn hoá lương

Nguồn đơn vị được suy từ chuỗi lương hiển thị vì dữ liệu số/currency của nguồn có trường hợp mâu
thuẫn:

- có `tr`/`triệu`, ký hiệu VND hoặc không có dấu USD thật → coi số là triệu VND, nhân `1.000.000`;
- có `$`/`USD` và không có `tr`/`triệu` → coi là USD, nhân `25.500`;
- `Từ X` giữ cận min; `Tới/Đến X` giữ cận max;
- min lớn hơn max hoặc cận vượt 10 tỷ VND/tháng → `invalid`;
- không công khai/thoả thuận → `negotiable`.

Tỷ giá được version hoá bằng `fx-2026-09-v1`. Khi thay đổi quy tắc hoặc tỷ giá, version phải đổi để
batch cũ vẫn giải thích được.

ADR-024 ghi nhận kết quả dry-run trên snapshot Mongo đã khảo sát:

| Trạng thái | Số dòng ghi nhận |
| --- | ---: |
| Parsed | 7.009 |
| └ VND | 5.624 |
| └ USD | 1.385 |
| Negotiable | 7.438 |
| Invalid | 30 |

Đây là số liệu của snapshot tại thời điểm ADR được viết, không phải invariant. Khi dữ liệu nguồn
thay đổi, hãy ghi lại timestamp, batch ID và kết quả dry-run mới thay vì kỳ vọng các số trên cố định.

### 3.6. Build gold metrics

Gold được dựng từ silver của cùng batch theo ba dimension và hai window:

```text
dimension = source | seniority | category
window    = 90d | all_time
```

Window `90d` bao gồm cả hai đầu mút:

```text
effective_posted_date ∈ [as_of_date - 89 ngày, as_of_date]
```

Mỗi group có ba count:

| Metric | Định nghĩa |
| --- | --- |
| `posting_count` | Số `job_id` khác nhau trong group |
| `salary_disclosed_count` | Số job công khai ít nhất một cận lương |
| `salary_sample_count` | Số job có đủ min và max để tính midpoint/median |
| `median_salary_vnd_month` | Median của `(min + max) / 2` trên các job đủ hai cận |

Bất biến luôn phải đúng:

```text
posting_count >= salary_disclosed_count >= salary_sample_count
```

Job multi-category xuất hiện trong từng category tương ứng nhưng được deduplicate theo `job_id`
trong mỗi bucket. Job không có category đi vào `<source>:unknown`; seniority không chắc chắn đi vào
`unknown`.

Gold giữ median thật kể cả group nhỏ. K-anonymity được áp ở API dựa trên
`salary_sample_count`, không làm mất dữ liệu audit trong warehouse.

### 3.7. Quality gate

Trước khi append vào bảng published, pipeline kiểm tra sáu invariant:

1. Với từng source: `source_jobs_at_extract = silver_rows + quarantined_rows`.
2. `COUNT(silver) = COUNT(DISTINCT job_id)`.
3. Tất cả silver, gold và quarantine mang đúng `batch_id`.
4. Mọi gold row thỏa `posting >= disclosed >= sample`.
5. Khoá `(batch_id, window, dimension, dimension_value)` của gold là duy nhất.
6. `salary_sample_count == 0` khi và chỉ khi median là `null`.

Nếu bất kỳ check nào fail, pipeline trả exit code `3` và không publish batch mới.

---

## 4. Mô hình bảng BigQuery

| Bảng | Kiểu ghi | Vai trò |
| --- | --- | --- |
| `silver_jobs_candidate` | `WRITE_TRUNCATE` | Work table chứa silver của lần chạy hiện tại |
| `gold_market_metrics_candidate` | `WRITE_TRUNCATE` | Work table chứa gold của lần chạy hiện tại |
| `silver_jobs` | `APPEND` theo `batch_id` | Dữ liệu job đã chuẩn hoá và versioned |
| `gold_market_metrics` | `APPEND` theo `batch_id` | Metric đã tổng hợp và versioned |
| `warehouse_quarantine` | `APPEND` | Dòng bị loại, lý do và batch lineage |
| `warehouse_batches` | `INSERT`, bất biến | Catalog của các batch đã publish |
| `warehouse_state` | Pointer singleton | Chỉ ra batch đang được phục vụ |

`silver_jobs` partition theo `effective_posted_date`, cluster theo:

```text
batch_id, source, seniority_normalized, job_id
```

Bảng published yêu cầu partition filter. Candidate table không yêu cầu partition filter để việc
kiểm tra trước publish đơn giản hơn. Gold cluster theo `batch_id`, `dimension`, `window`.

Schema được khai báo độc lập trong `schema.py`; drift tests so sánh tên field với output của
`to_bq_row()` để phát hiện sớm trường hợp dataclass và BigQuery schema lệch nhau.

---

## 5. Publish nguyên tử và idempotency

### 5.1. Vì sao cần pointer?

Silver và gold được append ngoài transaction. Trong lúc dữ liệu batch mới đang được ghi, API vẫn
đọc batch cũ thông qua `warehouse_state.published_batch_id`. Batch mới chỉ hiển thị sau khi catalog
và pointer được cập nhật thành công.

### 5.2. Transaction publish

Hai thao tác sau nằm trong cùng BigQuery transaction:

1. CAS update `warehouse_state` nếu pointer vẫn bằng `expected_prev`.
2. Insert metadata bất biến vào `warehouse_batches`.

Transaction yêu cầu đúng một state row được cập nhật. Nếu một publisher khác đã đổi pointer, câu
lệnh `RAISE` làm transaction rollback thay vì ghi đè mù.

### 5.3. State machine khi chạy lại

| Trạng thái quan sát được | Hành động |
| --- | --- |
| Batch có trong catalog và là current | `NO_OP`, kết thúc thành công |
| Batch có trong catalog nhưng không còn current | `ALREADY_PUBLISHED`, không sửa batch bất biến |
| Batch chưa có trong catalog nhưng đã có data rows | `RELOAD_PARTIAL`, xoá rows dở và load lại |
| Batch chưa có trong catalog và chưa có rows | `NEW_BATCH` |

Catalog là nguồn sự thật cho trạng thái “đã publish”; chỉ có rows silver/gold chưa đủ để kết luận.

Rollback trong thiết kế này rẻ vì batch cũ vẫn còn nguyên: chỉ cần một thao tác promote/đổi pointer
tường minh ở phase vận hành sau. Phase 2 chưa cung cấp CLI rollback tự động.

---

## 6. Cấu trúc implementation Phase 2

| File | Chức năng |
| --- | --- |
| `app/elt/serving/extract_mongo.py` | Đọc Mongo, LEFT JOIN detail, fallback taxonomy và đếm theo source |
| `app/elt/serving/mapper.py` | Cổng quyết định silver hay quarantine |
| `app/elt/serving/silver.py` | Dataclass silver/category/quarantine và serialization |
| `app/elt/serving/salary.py` | Chuẩn hoá lương VND/USD/tháng |
| `app/elt/serving/dates.py` | Parse posted/deadline theo timezone Việt Nam |
| `app/elt/serving/experience.py` | Parse khoảng năm kinh nghiệm |
| `app/elt/serving/seniority.py` | Map cấp bậc có version |
| `app/elt/serving/categories.py` | Dựng repeated categories có source-qualified key |
| `app/elt/serving/company.py` | Lấy company name từ raw theo từng source |
| `app/elt/serving/gold.py` | Tổng hợp metric theo dimension/window |
| `app/elt/serving/checks.py` | Sáu quality checks trước publish |
| `app/elt/serving/schema.py` | BigQuery schema, partition, cluster và tên bảng |
| `app/elt/serving/targets.py` | Writer guard và phân giải project/dataset |
| `app/elt/serving/publish.py` | Publish state machine và transaction SQL |
| `app/elt/serving/bigquery_writer.py` | I/O BigQuery: ensure/load/append/publish |
| `app/elt/serving/run_serving_elt.py` | CLI ghép toàn bộ pipeline |
| `tests/fixtures/mongo_like.py` | Fixture raw đã sanitize cho TopDev/VietnamWorks và edge cases |

Quyết định quan trọng được ghi tại:

- `docs/adr/ADR-019-mongo-source-data-contract.md`;
- `docs/adr/ADR-024-salary-normalization.md`;
- `docs/adr/ADR-025-atomic-publication.md`.

---

## 7. Cấu hình và quyền cần có

### 7.1. Biến môi trường

| Biến | Bắt buộc | Mặc định | Ý nghĩa |
| --- | --- | --- | --- |
| `JOBS_MONGO_URI` | Có | Không có | Connection string MongoDB nguồn |
| `JOBS_MONGO_DATABASE` | Không | `job_crawler` | Database nguồn |
| `JOBS_BQ_PROJECT` | Có | Không có | GCP project đích |
| `JOBS_BQ_DATASET_STAGING` | Không | `jobs_staging` | Dataset staging allowlisted |
| `JOBS_BQ_DATASET_PROD` | Không | `jobs_prod` | Dataset prod allowlisted |
| `JOBS_BQ_LOCATION` | Không | `asia-southeast1` | Location của query/load job |
| `JOBS_BQ_MAX_BYTES_BILLED` | Không | `2000000000` | Trần 2 GB cho mỗi query do writer chạy |

Không commit `JOBS_MONGO_URI` hoặc credential vào `.env`, log hay README.

### 7.2. Authentication

- Mongo URI phải có quyền đọc `jobs`, `job_details` và `job_categories`.
- Khi chạy thật trên VM, dùng ADC của `sa-dagster-elt` đã tạo ở Phase 1.
- Service account ELT cần `roles/bigquery.dataEditor` ở dataset đích và
  `roles/bigquery.jobUser` ở project.
- Không dùng file JSON key nếu workload chạy trên GCP; gắn service account trực tiếp cho VM.

---

## 8. Cách chạy

Các lệnh dưới đây dành cho Bash/Google Cloud Shell hoặc trusted VM.

### 8.1. Cài dependency

```bash
cd jobs-serving-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

`requirements.txt` đã có `pymongo` và `google-cloud-bigquery` cho pipeline mới.

### 8.2. Cấu hình phiên chạy

```bash
export JOBS_MONGO_URI='<mongo-connection-string>'
export JOBS_MONGO_DATABASE='job_crawler'
export JOBS_BQ_PROJECT='<gcp-project-id>'
export JOBS_BQ_DATASET_STAGING='jobs_staging'
export JOBS_BQ_DATASET_PROD='jobs_prod'
export JOBS_BQ_LOCATION='asia-southeast1'
export JOBS_BQ_MAX_BYTES_BILLED='2000000000'
```

### 8.3. Dry-run transform và quality checks

Dry-run vẫn đọc snapshot Mongo thật nhưng không ghi BigQuery:

```bash
python -m app.elt.serving.run_serving_elt \
  --environment staging \
  --batch-id phase2-dry-run \
  --dry-run
```

Kỳ vọng: log có count theo source và output kết thúc bằng `[OK] quality checks`.

### 8.4. Integration trên dataset test

Tạo dataset test cùng location trước:

```bash
bq --project_id="$JOBS_BQ_PROJECT" mk \
  --dataset \
  --location="$JOBS_BQ_LOCATION" \
  "$JOBS_BQ_PROJECT:jobs_staging_test"
```

Sau đó chạy:

```bash
python -m app.elt.serving.run_serving_elt \
  --environment staging \
  --dataset-suffix _test \
  --batch-id phase2-integration-001
```

### 8.5. Publish staging

Chỉ chạy sau khi dry-run và integration dataset test đạt:

```bash
python -m app.elt.serving.run_serving_elt \
  --environment staging \
  --batch-id "batch-$(date -u +%Y%m%dT%H%M%SZ)" \
  --crawl-batch-id '<crawl-batch-id>' \
  --dagster-run-id '<dagster-run-id>'
```

Không chạy prod như một bước thử nghiệm. Prod bắt buộc chọn tường minh:

```bash
python -m app.elt.serving.run_serving_elt --environment prod ...
```

### 8.6. Exit codes

| Exit code | Ý nghĩa |
| ---: | --- |
| `0` | Publish thành công hoặc `NO_OP` |
| `1` | Cấu hình sai hoặc lỗi không được phân loại riêng |
| `2` | Batch đã publish nhưng không phải current batch |
| `3` | Quality checks không đạt |

---

## 9. Cách xác minh trên BigQuery

Thay ba giá trị dưới đây theo lần chạy:

```bash
export VERIFY_PROJECT='<gcp-project-id>'
export VERIFY_DATASET='jobs_staging_test'
export VERIFY_BATCH='phase2-integration-001'
```

### 9.1. Kiểm tra bảng được tạo

```bash
bq ls --project_id="$VERIFY_PROJECT" "$VERIFY_PROJECT:$VERIFY_DATASET"
```

Kỳ vọng có đủ bảy bảng ở mục 4.

### 9.2. Kiểm tra catalog và current pointer

```bash
bq query --use_legacy_sql=false \
  "SELECT * FROM \`$VERIFY_PROJECT.$VERIFY_DATASET.warehouse_state\`"

bq query --use_legacy_sql=false \
  --parameter="batch_id:STRING:$VERIFY_BATCH" \
  "SELECT * FROM \`$VERIFY_PROJECT.$VERIFY_DATASET.warehouse_batches\`
   WHERE batch_id = @batch_id"
```

Kỳ vọng pointer trỏ đúng `VERIFY_BATCH` và catalog có đúng một metadata row cho batch.

### 9.3. Kiểm tra reconciliation

`silver_jobs` yêu cầu partition filter, vì vậy truy vấn phải có điều kiện ngày:

```bash
bq query --use_legacy_sql=false \
  --parameter="batch_id:STRING:$VERIFY_BATCH" \
  "SELECT source, COUNT(*) AS silver_rows, COUNT(DISTINCT job_id) AS distinct_jobs
   FROM \`$VERIFY_PROJECT.$VERIFY_DATASET.silver_jobs\`
   WHERE effective_posted_date >= DATE '2000-01-01'
     AND batch_id = @batch_id
   GROUP BY source"

bq query --use_legacy_sql=false \
  --parameter="batch_id:STRING:$VERIFY_BATCH" \
  "SELECT source, COUNT(*) AS quarantined_rows
   FROM \`$VERIFY_PROJECT.$VERIFY_DATASET.warehouse_quarantine\`
   WHERE batch_id = @batch_id
   GROUP BY source"
```

Đối chiếu các count này với `topdev_source_jobs`, `vietnamworks_source_jobs`, `silver_rows` và
`quarantined_rows` trong `warehouse_batches`.

### 9.4. Kiểm tra gold invariants

```bash
bq query --use_legacy_sql=false \
  --parameter="batch_id:STRING:$VERIFY_BATCH" \
  "SELECT COUNT(*) AS invalid_rows
   FROM \`$VERIFY_PROJECT.$VERIFY_DATASET.gold_market_metrics\`
   WHERE batch_id = @batch_id
     AND NOT (posting_count >= salary_disclosed_count
              AND salary_disclosed_count >= salary_sample_count)"
```

Kỳ vọng `invalid_rows = 0`.

### 9.5. Kiểm tra idempotency

Chạy lại cùng lệnh và cùng `--batch-id`. Khi batch vẫn là current, kết quả phải là `NO_OP`, không
tạo thêm catalog row hoặc nhân đôi dữ liệu published.

---

## 10. Kiểm thử

Chạy riêng test Phase 2:

```bash
pytest -q tests/test_elt_*.py
```

Phạm vi đang được bảo vệ:

- parse salary/date/experience/seniority/category/company;
- mapper và thứ tự quarantine;
- multi-category, unknown bucket, median và window 90 ngày;
- schema drift giữa dataclass và BigQuery fields;
- writer guard, dataset suffix và fully-qualified identifiers;
- sáu quality checks;
- publish state machine, query parameters, CAS và transaction SQL;
- CLI argument parsing, transform và batch metadata.

Kết quả kiểm chứng khi viết báo cáo:

```text
Phase 2 tests: 101 passed
Full suite:    159 passed, 3 skipped
```

Ba test skip thuộc các tích hợp tùy môi trường; kết quả unit test không xác nhận IAM, network,
Mongo availability hoặc hành vi transaction trên một dataset BigQuery thật.

---

## 11. Definition of Done

### Source code và kiểm thử cục bộ

- [x] Có sanitized Mongo-like fixtures cho TopDev/VietnamWorks và các edge case quan trọng.
- [x] Extract chỉ nhận hai source và LEFT JOIN job detail theo composite key.
- [x] Có mapper duy nhất quyết định silver/quarantine.
- [x] Chuẩn hoá salary/date/experience/seniority/category/company có parse status/version.
- [x] Silver giữ một job một dòng và categories là repeated STRUCT.
- [x] Gold có hai window, ba dimension, ba count và median midpoint.
- [x] Có quarantine table contract và reconciliation theo source.
- [x] Có writer guard cho staging/prod và dataset suffix cho integration test.
- [x] Có candidate/published tables, immutable batch catalog và current pointer.
- [x] Có quality gate và publish transaction dùng CAS.
- [x] Unit tests Phase 2 và toàn test suite xanh.

### Trên môi trường tích hợp thật

- [ ] Dry-run mới nhất đọc Mongo thành công và quality report `OK`.
- [ ] Integration run ghi thành công vào dataset hậu tố `_test`.
- [ ] BigQuery tables, partition, cluster và schema khớp khai báo.
- [ ] Reconciliation metadata khớp count silver + quarantine theo từng source.
- [ ] Current pointer và batch catalog được cập nhật đúng một lần.
- [ ] Chạy lại cùng current `batch_id` trả `NO_OP` và không nhân đôi dữ liệu.
- [ ] Quality failure hoặc lỗi publish không làm pointer chuyển sang batch lỗi.
- [ ] Staging writer không chạm dataset prod; prod chỉ được chạy khi chọn tường minh.

---

## 12. Giới hạn và việc để các phase sau

- API chưa đọc BigQuery; `warehouse_backend=bigquery` vẫn chưa khả dụng — **Phase 3**.
- Chưa có keyset pagination SQL gắn `batch_id`, metadata theo batch, timeout/cancel và query
  observability cho serving — **Phase 3**.
- BigQuery integration/parity test tự động với fake và DuckDB — **Phase 4**.
- Dagster chưa bọc crawler + ELT thành daily batch có concurrency lock — **Phase 5**.
- Candidate tables dùng chung, vì vậy không chạy nhiều ELT writer đồng thời trước khi có lock Phase 5.
- Chưa có incremental MERGE, tombstone, retention/cleanup batch cũ hoặc CLI rollback/promote.
- Chưa canonicalize category giữa hai nguồn; key vẫn source-qualified.
- K-anonymity không áp trong gold; API chịu trách nhiệm che median của nhóm nhỏ.
- `data_as_of_at` là cutoff của snapshot sau crawl, không phải thời điểm publish.

---

## 13. Bàn giao sang Phase 3

Phase 3 sẽ xây BigQuery read adapters cho `JobRepository` và `MetricsRepository`. Adapter phải:

- đọc `warehouse_state` để lấy current `batch_id`;
- đọc `data_as_of_at`/`as_of_date` từ `warehouse_batches` của đúng batch;
- luôn query silver/gold theo batch, kể cả khi pointer đổi giữa hai request;
- đẩy keyset pagination, filters và `LIMIT + 1` xuống SQL;
- dùng named parameters cho values và allowlist cho identifiers;
- áp `maximum_bytes_billed`, timeout/cancel và log query stats;
- đưa `batch_id` vào page token và cache key.

Tài liệu liên quan:

- Data contract: [`ADR-019`](../../docs/adr/ADR-019-mongo-source-data-contract.md)
- Salary normalization: [`ADR-024`](../../docs/adr/ADR-024-salary-normalization.md)
- Atomic publication: [`ADR-025`](../../docs/adr/ADR-025-atomic-publication.md)
- Kế hoạch migration: [`migration-mongo-bigquery-plan.md`](../../docs/migration-mongo-bigquery-plan.md)
- Báo cáo Phase 1: [`phase-1/README.md`](../phase-1/README.md)
