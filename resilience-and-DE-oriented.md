# Jobs Serving API

## 15. Bằng chứng năng lực Data Engineering

Phần này không chỉ liệt kê công nghệ. Nó giải thích các failure mode, data contract, invariant,
test, version và phương án vận hành đã được cân nhắc trong project. Khi thuyết trình, có thể dùng
thông điệp mở đầu sau:

> Tôi xem pipeline dữ liệu là một hệ thống phải bảo vệ **tính đúng, khả năng truy vết và khả năng
> phục hồi**, chứ không chỉ là đoạn code chuyển dữ liệu từ MongoDB sang BigQuery. Vì vậy tôi thiết kế
> nhiều lớp kiểm soát: record xấu được quarantine, batch xấu không được publish, publish cạnh tranh
> bị rollback, API luôn đọc một snapshot nhất quán, và mỗi quyết định quan trọng đều có test hoặc
> runbook đi kèm.

### 15.1 Cách đọc mức độ hoàn thành

Để không tuyên bố quá khả năng thực tế, phần này dùng ba mức:

| Mức | Ý nghĩa |
| --- | --- |
| **Implemented** | Đã có code/config/runbook trong repository hoặc repo crawler được nêu rõ. |
| **Tested** | Có automated test đã chạy hoặc quality gate thực thi được. |
| **Operational proof pending** | Thiết kế đã có nhưng cần log/artifact từ hạ tầng thật để chứng minh. |

Điểm này quan trọng khi phỏng vấn: **code backup không đồng nghĩa với đã chứng minh restore**; unit
test BigQuery không đồng nghĩa với đã nghiệm thu IAM, quota và network thật.

### 15.2 Resilience — hệ thống phản ứng thế nào khi lỗi

Nguyên tắc chính là **fail closed với dữ liệu và bảo mật, fail open có kiểm soát với thành phần chỉ
để tăng tốc**. Cache hỏng có thể chậm hơn nhưng không được làm dữ liệu biến mất; ngược lại, quality
check hỏng thì tuyệt đối không publish.

| Failure mode | Cơ chế hiện có | Kết quả quan sát được | Mức |
| --- | --- | --- | --- |
| Crawler có category lỗi | Dagster đặt crawl trước ELT; crawl exit khác `0` thì ELT không chạy | Run đỏ, warehouse không đổi | Implemented ở repo crawler; mô tả tại ADR-021 |
| Hai batch crawl/ELT chạy chồng | Concurrency key `serving_pipeline=1` | Tránh đọc Mongo giữa lúc crawl và tránh double-run có chủ đích | Implemented ở repo crawler |
| Mongo không kết nối được | `serverSelectionTimeoutMS=10000`; client luôn đóng trong `finally` | Run fail trước publish; current batch cũ tiếp tục phục vụ | Implemented |
| Một record nguồn thiếu/hỏng trường cốt lõi | LEFT JOIN không làm mất job; mapper đưa record vào quarantine với reason code | Dữ liệu tốt vẫn đi tiếp, record xấu truy vết được | Implemented + Tested |
| Trường không cốt lõi parse lỗi | Deadline/experience/salary có parse status; giá trị không đáng tin trở thành `NULL` thay vì đoán | Giữ job trong corpus nhưng không dùng số rác cho metric | Implemented + Tested |
| Reconciliation hoặc invariant gold sai | Sáu quality checks chạy trước khi chạm bảng published | CLI exit `3`; pointer giữ batch tốt trước đó | Implemented + Tested |
| Process chết khi load work table | Mỗi run có candidate riêng; candidate hết hạn sau một ngày | Không lộ candidate cho API; rác tự dọn | Implemented |
| Load candidate hoặc publish lỗi | Published tables chỉ được sửa trong transaction; API chọn dữ liệu qua current pointer | API vẫn đọc batch trước | Implemented + phần transaction Tested |
| Hai publisher cùng thắng đua | Compare-and-swap trên pointer và `@@row_count == 1` | Run thua rollback cả DELETE/INSERT/catalog | Implemented + Tested |
| Retry cùng `batch_id` | State machine `NO_OP / ALREADY_PUBLISHED / RELOAD_PARTIAL / NEW_BATCH` | Retry tất định, không nhân đôi dữ liệu | Implemented + Tested |
| Cleanup candidate lỗi sau commit | Cleanup best-effort, không che kết quả publish; expiration là lưới an toàn | Không báo “publish fail” giả sau khi đã commit | Implemented |
| Publish mới xảy ra giữa trang 1 và 2 | Page token chứa `batch_id`, `as_of`, cursor và fingerprint; token được ký HMAC | Các trang vẫn thuộc cùng snapshot, không trùng/nhảy do đổi batch | Implemented + Tested |
| Chưa có published batch | Repository trả `503 UPSTREAM_UNAVAILABLE`, không giả vờ trả danh sách rỗng | Consumer phân biệt “không có dữ liệu” với “kho chưa sẵn sàng” | Implemented + Tested |
| Query quá chậm | DuckDB dùng thread + `interrupt`; BigQuery timeout rồi cancel best-effort | API trả `504 QUERY_TIMEOUT` với error envelope ổn định | Implemented + Tested |
| Redis cache hỏng hoặc network blackhole | Socket timeout `0.5s`; cache fail-open: get thành miss, set bị bỏ qua và log WARN | Request vẫn đọc BigQuery | Implemented + Tested |
| Redis rate limiter hỏng | Socket timeout `0.5s`; limiter fail-open và log WARN | Availability được giữ, tạm chấp nhận mất rate-limit | Implemented + Tested; đây là trade-off có chủ đích |
| Cấu hình nguy hiểm/sai | Settings và deployment script fail-fast trước khi phục vụ request | Không boot với backend lạ, thiếu BQ config, secret dev ngoài local hoặc secret version rỗng | Implemented + Tested |
| Exception ngoài dự kiến | Handler chung log nội bộ, không trả stack trace/tên bảng; mọi lỗi có `request_id` | Consumer nhận envelope ổn định, operator tra log bằng request ID | Implemented + Tested |

Một câu trả lời ngắn khi được hỏi “resilience nằm ở đâu?” là:

> Resilience của project không dựa vào retry mù. Nó dựa vào **snapshot bất biến + quality gate trước
> publish + transaction/CAS khi publish + pointer giữ last-known-good + retry theo state machine**.
> Với dependency phụ như Redis thì fail-open có timeout; với dữ liệu hoặc auth thì fail-closed.

### 15.3 Validation từ mọi nguồn dữ liệu và mọi bước

Validation không chỉ nằm ở API. Nó được đặt tại từng ranh giới nơi dữ liệu có thể đổi hình dạng:

```text
Mongo source contract
  → extract scope + actual counts
  → row mapping + parse status/quarantine
  → batch reconciliation + gold invariants
  → candidate schema/load
  → atomic publication + immutable catalog
  → BigQuery/DuckDB read constraints
  → request policy + response contract
  → operational reconciliation/restore evidence
```

| Biên kiểm tra | Cái được validate | Khi không đạt | Bằng chứng chính |
| --- | --- | --- | --- |
| MongoDB source | Chỉ `topdev` và `vietnamworks`; join theo `(platformId, externalId)`; đếm trực tiếp từng source trong cùng lần extract | Kết nối lỗi làm run fail; thiếu detail vẫn được giữ để quarantine | `extract_mongo.py` |
| Mapping identity | `externalId`, detail `completed`, title, ngày đăng hiệu lực và URL nguồn là bắt buộc | Quarantine với mã ổn định, không làm rơi record im lặng | `mapper.py`, `silver.py` |
| Date | ISO/offset và một số định dạng fallback; naive datetime được gắn timezone; `postedAt` lỗi thì dùng `firstSeenAt` | Cả hai nguồn ngày đăng lỗi mới quarantine; deadline lỗi chỉ `NULL + invalid` | `dates.py` |
| Salary | Suy đơn vị từ raw text; VND triệu/USD; một cận/hai cận; `min <= max`; trần 10 tỷ VND/tháng; giữ raw và version tỷ giá | Dải đảo hoặc magnitude vô lý thành `invalid`, hai cận chuẩn hoá `NULL`, không làm sai median | `salary.py` |
| Experience | “không yêu cầu”, điểm, khoảng, lower-bound, upper-bound; lưu parse status | Nhãn lạ thành `unparsed`; khoảng đảo được chuẩn hoá lại theo thứ tự tăng | `experience.py` |
| Seniority/category/company | Mapping seniority có version; category key kèm source và dedupe; company đọc theo shape từng nguồn | Giá trị không chắc chắn vào bucket `unknown`, không đoán bừa | `seniority.py`, `categories.py`, `company.py` |
| Silver batch | `extract = silver + quarantine` theo source; `COUNT(*) = COUNT(DISTINCT job_id)`; tất cả row đúng batch | Quality report fail, exit `3`, không publish | `checks.py` |
| Gold batch | `posting >= disclosed >= sample`; key metric duy nhất; median `NULL` khi và chỉ khi sample bằng `0` | Quality report fail, không publish | `checks.py`, `gold.py` |
| BigQuery schema | Field/mode/type tập trung; key fields bắt buộc; drift test so schema với `to_bq_row()` | Test/load fail trước khi batch trở thành current | `schema.py`, `test_elt_schema.py` |
| Candidate/publish | Candidate per-run, `WRITE_TRUNCATE`; mọi giá trị publish qua typed params; CAS chỉ cho đúng expected pointer | Transaction rollback, batch cũ vẫn current | `bigquery_writer.py`, `publish.py` |
| BigQuery read | Identifier được validate/backtick; values là typed query params; projection allowlist; bắt buộc partition filter; maximum bytes billed | Request bị từ chối/timeout thay vì quét không giới hạn | `bigquery_read_sql.py`, `bigquery_exec.py` |
| DuckDB read | Cùng filter/sort/keyset semantics với fake/BigQuery; query parameterized; read-only | Parity test phát hiện khác biệt giữa môi trường local và production | `duckdb_*`, parity tests |
| HTTP request | Pydantic `extra="forbid"`, enum, type/range; `posted_after` bắt buộc; policy validator default-deny | `400` với code/field ổn định | `models/`, `domain/validator.py` |
| HTTP response/privacy | Response model cấm field thừa; output-column allowlist sinh từ `JobItem`; danh sách PII bị chặn | Không trả raw/contact/description dài/field lạ | `validator.py`, `read_mapping.py`, contract tests |
| Metrics privacy | Median chỉ lộ khi `salary_sample_count >= k` | Median trả `NULL`, count thật vẫn giữ để audit | `market.py` |
| Backup/restore | Object phải tồn tại và có size; restore vào DB tạm; đối chiếu document count | Drill không đạt thì chưa được tuyên bố recoverable | `README-DEPLOY-GCP.md` mục 19 |

#### Vì sao không quarantine mọi lỗi parse?

Project tách **critical field** và **analytical field**:

- Thiếu ID/title/effective date/URL làm record không thể định danh hoặc phục vụ đúng, nên quarantine.
- Deadline, experience hay salary lỗi không làm mất toàn bộ giá trị của job. Pipeline giữ record nhưng
  đánh `missing/unparsed/invalid`, đưa cận số về `NULL` và loại nó khỏi phép tính cần số hợp lệ.

Đây là lựa chọn cân bằng giữa data quality và data completeness; quan trọng nhất là không âm thầm
biến dữ liệu lỗi thành một con số có vẻ hợp lệ.

### 15.4 Các invariant và điều kiện chặn

| Nhóm | Điều kiện | Vị trí chặn | Lỗi được phòng tránh |
| --- | --- | --- | --- |
| Timeout lồng nhau | `query 10s < request 20s < Cloud Run 25s < client khuyến nghị 30s` | Settings chặn `query >= request`; deploy config giữ edge timeout; query adapter cưỡng chế query | Query con sống lâu hơn request cha, request bị cắt mà query còn chạy |
| Date range | `posted_before >= posted_after` | Pydantic field validator | Khoảng đảo trả tập rỗng khó hiểu |
| Partition guard | `posted_after` luôn bắt buộc | Request model + QueryValidator + BigQuery `require_partition_filter` | Full table scan và chi phí ngoài dự kiến |
| Pagination limit | `1 <= limit <= 100` | Pydantic + QueryValidator | Response/quét quá lớn |
| Salary source range | Khi có hai cận: `min <= max`; mỗi cận `<= 10 tỷ VND/tháng` | Salary normalizer | Dải đảo và lỗi đơn vị/magnitude làm hỏng metric |
| Salary filter | `0 <= salary_min <= 10 tỷ` | Pydantic | Query vô nghĩa hoặc cực đoan |
| Experience range | Khoảng nguồn đảo được sắp lại `lo <= hi`; filter `0..50` năm | Parser + Pydantic | Dải kinh nghiệm đảo và input phi thực tế |
| Metric subset | `sample <= disclosed <= posting` | Gold construction + quality gate | Median/count mâu thuẫn |
| Median consistency | `sample == 0` khi và chỉ khi `median IS NULL` trong gold | Quality gate | Median tồn tại không có mẫu hoặc mất median có mẫu |
| K-anonymity | `sample < metrics_min_sample_size` thì API che median | API validator | Suy luận từ nhóm quá nhỏ |
| Reconciliation | Với từng source: `extract = silver + quarantine` | Pre-publish quality gate | Record biến mất giữa extract và load |
| Silver grain | Một `job_id` đúng một dòng | Pre-publish quality gate | Join/category làm nhân bản job |
| Gold grain | `(batch_id, window, dimension, dimension_value)` duy nhất | Pre-publish quality gate | Metric trùng và dashboard double-count |
| Batch ownership | Mọi silver/gold/quarantine row mang batch đang publish | Pre-publish quality gate | Trộn dữ liệu giữa hai run |
| Publish CAS | Pointer phải vẫn bằng `expected_prev`; `@@row_count == 1` | BigQuery transaction | Lost update khi publish đồng thời |
| Batch immutability | Batch đã ở catalog không được ghi lại; current thì `NO_OP`, không-current thì exit `2` | Publish state machine | Sửa lịch sử và mất khả năng audit |
| Source/dimension/window/sort | Chỉ giá trị trong enum/allowlist | Pydantic + domain validator | SQL injection, semantic drift, truy vấn không hỗ trợ |
| SQL safety | Identifier chỉ từ allowlist; user value luôn là parameter | SQL builders | Injection qua filter/sort/table identifier |
| Page-token integrity | HMAC đúng, fingerprint đúng filter/sort, cursor giữ batch | Pagination domain | Sửa cursor, tái dùng token cho query khác, trộn snapshot |
| Output privacy | Cột trả ra phải thuộc `JobItem` và không nằm trong denylist PII | QueryValidator + projection cố định | Rò raw payload/contact/session |
| Production config | Không dùng secret dev/empty keys; BigQuery cần project+dataset; env writer chỉ `staging|prod` | Boot/deploy/CLI guard | Boot được nhưng request đầu mới 500; ghi nhầm production |
| Cost | `maximum_bytes_billed >= 1`, partition prune, precomputed gold, không `COUNT(*)` mỗi search | Settings/BigQuery/job design | Một request gây scan và bill không giới hạn |

Lưu ý khi trình bày: với khoảng kinh nghiệm đảo, project **normalize bằng cách đổi thứ tự**, còn với
lương đảo project **đánh invalid**. Hai quyết định khác nhau vì experience text thường có thể bị đảo
do format nguồn, trong khi lương đảo có rủi ro sai đơn vị và không nên tự suy đoán.

### 15.5 Test strategy và từng file kiểm tra gì

Ngày **2026-09-15**, test suite thu thập **297 case** sau parameterization. Kết quả chạy local với
`pytest -q --basetemp=<workspace-temp>` là **285 passed, 12 skipped**. Mười hai case skip là test tích
hợp có guard: **9 BigQuery thật** và **3 Redis thật**; không phải unit-test fail. CI chạy `ruff` và
`pytest` làm cổng cứng; `mypy` và `pip-audit` hiện là advisory.

#### Nhóm A — ELT, mapping và data quality (106 case)

Mục đích: chứng minh cùng input sẽ tạo cùng silver/gold, record xấu được phân loại đúng và batch xấu
không thể vượt quality gate/publish.

| File | Case | Nội dung được bảo vệ |
| --- | ---: | --- |
| `test_elt_transforms.py` | 42 | Salary VND/USD/mislabel/one-sided/invalid; date/timezone/fallback; deadline; experience; category dedupe/fallback; seniority; company mapping. |
| `test_elt_mapper.py` | 15 | Mapping TopDev/VietnamWorks; mọi reason quarantine; thứ tự gate; URL bắt buộc; reconciliation và duplicate job ID. |
| `test_elt_gold.py` | 6 | Count/median theo source, seniority, category; unknown bucket; dedupe multi-category; cửa sổ 90 ngày; gold giữ median thật. |
| `test_elt_checks.py` | 7 | Happy path và từng quality invariant bị cố ý phá: reconciliation, distinct ID, batch ID, count ordering, key unique, median consistency. |
| `test_elt_schema.py` | 5 | Drift giữa dataclass row và BigQuery schema, nested category fields, key/partition/cluster fields bắt buộc. |
| `test_elt_targets.py` | 12 | Writer guard staging/prod, không trỏ staging sang prod, reject env lạ, identifier, suffix integration và env config. |
| `test_elt_publish.py` | 13 | Bốn nhánh state machine; DML nằm trong transaction; bootstrap/CAS NULL-safe; assert; parameter coverage; không nội suy value. |
| `test_elt_run.py` | 6 | Ghép transform→gold/quarantine, run metric, batch metadata/version/lineage và CLI bắt buộc environment. |

#### Nhóm B — API contract, policy, auth và pagination (63 case)

Mục đích: bảo vệ hợp đồng với consumer, kiểm soát input/output và đảm bảo lỗi có hành vi ổn định.

| File | Case | Nội dung được bảo vệ |
| --- | ---: | --- |
| `test_contract.py` | 13 | Shape search, không PII, required/unknown filter, enum, filter semantics, category không nhân dòng, limit, date range và error envelope. |
| `test_auth.py` | 9 | Public health, thiếu/sai/đúng key, 429, client ID trong request state/log kể cả khi bị limit. |
| `test_apikey_expiry_tz.py` | 2 | Expiry datetime naive được chuẩn hoá UTC; key tương lai không 500, key quá hạn trả 401. |
| `test_pagination.py` | 5 | Token roundtrip, chống sửa, fingerprint filter, không trùng/không sót, từ chối đổi filter giữa trang. |
| `test_validator.py` | 9 | Limit/filter/required filter, k-anonymity, allowlist output, denylist PII và dimension/metric. |
| `test_settings.py` | 10 | Ba backend hợp lệ, reject backend lạ, BigQuery bắt buộc project/dataset, fake không cần BQ. |
| `test_market.py` | 5 | Shape/count metric, window, k-anonymity, unknown bucket và reject dimension/window lạ. |
| `test_metadata.py` | 4 | Catalog filter/dimension/window công bố đúng, filter cũ bị loại, metadata có `as_of`. |
| `test_health.py` | 3 | Liveness và request ID do server sinh hoặc tôn trọng từ client. |
| `test_openapi.py` | 3 | OpenAPI sinh được, mọi endpoint có operation ID và enum xuất hiện trong contract. |

#### Nhóm C — warehouse SQL, adapter và cross-backend parity (83 case)

Mục đích: chứng minh SQL an toàn và fake/DuckDB/BigQuery có cùng semantics, không chỉ “mỗi backend tự
chạy được”.

| File | Case | Nội dung được bảo vệ |
| --- | ---: | --- |
| `test_bigquery_sql.py` | 27 | Fully-qualified identifier, reject identifier độc hại, projection/filter/category `EXISTS`, NULL semantics, sort/keyset, typed date cursor, metadata và metrics SQL. |
| `test_bigquery_jobs.py` | 9 | Row→`JobItem`, bỏ cột ngoài allowlist, cursor cho mọi sort/NULL và typed BQ params. |
| `test_bigquery_metrics.py` | 2 | Mapping metric giữ số thật và xử lý median `NULL`. |
| `test_bq_integration.py` | 9 | Roundtrip BigQuery `_test`, parity sort/paging/category, `as_of`, metrics và maximum-bytes guard; skip mặc định nếu không bật guard/credential. |
| `test_duckdb_sql.py` | 10 | Parameterized SQL, category không explode, NULL semantics, sort/keyset, date cursor, metadata/metrics SQL. |
| `test_duckdb_repo.py` | 26 | Parity với fake cho sort/filter/paging, category grain, limit+1, composite ID, snapshot batch/as_of, chưa publish→503 và metric invariants. |

#### Nhóm D — resilience, distributed state và observability (25 case)

Mục đích: kiểm tra failure path, không chỉ happy path.

| File | Case | Nội dung được bảo vệ |
| --- | ---: | --- |
| `test_query_timeout.py` | 4 | Fast query, DuckDB interrupt khi chậm, timeout→504 và invariant query `<` request. |
| `test_cache_redis_failopen.py` | 3 | Redis cache get/set lỗi không làm request fail và client có timeout giới hạn. |
| `test_ratelimit.py` | 3 | Token bucket memory: capacity, refill theo thời gian và bucket riêng từng client. |
| `test_ratelimit_redis.py` | 3 | Cùng hành vi trên Redis thật; skip nếu Redis không sẵn sàng. |
| `test_ratelimit_redis_failopen.py` | 2 | Redis limiter lỗi thì cho qua có chủ đích; network call có timeout. |
| `test_tracing.py` | 10 | BQ span attributes/timeout, cancel lỗi không che 504, log↔trace correlation, exporter wiring và singleton config. |

#### Nhóm E — consumer/reference client (20 case)

| File | Case | Nội dung được bảo vệ |
| --- | ---: | --- |
| `test_client_demo.py` | 20 | Client truyền token qua nhiều trang, format field/salary, mọi dạng error, max-pages, timeout/base URL hợp lệ và fail-fast khi thiếu key. |

Các file hỗ trợ test cũng có vai trò kiến trúc:

- `tests/conftest.py`: ép test mặc định thành hermetic (`fake + memory`), không vô tình chạm cloud.
- `tests/fixtures/mongo_like.py`: fixture giống shape Mongo thật nhưng đã sanitize, chứa edge case có chủ đích.
- `tests/fixtures/duckdb_fixture.py`: tạo snapshot DuckDB có schema mới để kiểm parity và snapshot.

### 15.6 Versioning — version nào giải quyết vấn đề gì

| Lớp version | Cơ chế hiện có | Giá trị khi vận hành |
| --- | --- | --- |
| Source code | Git commit/branch + CI | Truy ngược thay đổi và review quyết định. |
| Runtime artifact | Docker image tag bằng full commit SHA; deploy script kiểm image tồn tại | Cùng SHA là cùng artifact, tránh “latest” trôi. |
| Runtime revision | Mỗi deploy tạo Cloud Run revision; secret được pin vào một version cụ thể | Có thể xác định image + secret nào tạo ra hành vi của revision. |
| API contract | Prefix `/v1`, FastAPI version `1.0.0`, OpenAPI contract test | Consumer không bị breaking change âm thầm; breaking change cần `/v2`. |
| Data snapshot | `batch_id` bất biến + `warehouse_batches` catalog + `warehouse_state` pointer | Audit lịch sử, phân trang snapshot và last-known-good. |
| Data lineage | `crawl_batch_id`, `dagster_run_id`, `data_as_of_at`, `published_at` | Đi từ API result về batch, orchestration run và crawl nguồn. |
| Business rules | `seniority-2026-09-v1`, `fx-2026-09-v1`; version ghi cả row và batch metadata | So sánh được batch trước/sau khi mapping hoặc tỷ giá thay đổi. |
| Schema | Schema khai báo trong code + drift tests | Thêm/bớt field mà quên serializer/schema sẽ làm CI đỏ. |
| Dependencies | Pin chính xác nhóm FastAPI/OpenTelemetry nhạy tương thích | Build tái lập và tránh nâng một package làm vỡ instrumentation. |
| Secrets | Secret Manager lưu nhiều version; revision pin version ENABLED được chọn lúc deploy | Rotate có lịch sử và rollback revision không phụ thuộc “latest” thay đổi sau đó. |

Điểm cần nói rõ: project **chưa có schema-migration framework độc lập** cho BigQuery và **chưa có
lệnh promote/rollback pointer đóng gói sẵn**. Batch bất biến + pointer tạo nền tảng rollback dữ liệu,
nhưng thao tác production vẫn cần runbook/utility có guard, audit log và kiểm tra batch tồn tại trước
khi đổi pointer.

### 15.7 Backup, restore và restore drill

#### Phạm vi bảo vệ hiện tại

| Tài sản | Cách bảo vệ | Trạng thái |
| --- | --- | --- |
| MongoDB raw/source | `mongodump --archive --gzip` hằng ngày 03:30, upload GCS theo timestamp | Script/timer nằm ở repo crawler; cần log hạ tầng thật để chứng minh lịch chạy |
| GCS backup | Uniform access, public access prevention, lifecycle 30 ngày | Hạ tầng có trong `infra/gcp/80-backup-gcs.sh` |
| Backup writer | `sa-dagster-elt`: objectCreator + objectViewer, không có delete | Writer không thể xoá/ghi đè backup đã tạo |
| Restore reader | `sa-backup-restore`: objectViewer riêng | Separation of duties; restore không dùng runtime writer identity |
| BigQuery curated data | Published batch bất biến và current pointer | Có thể tái phục vụ batch cũ còn giữ hoặc rebuild từ Mongo backup; chưa có export backup BQ riêng |
| API artifact/config | Image theo Git SHA, Cloud Run revisions, secret versions | Có thể truy ngược runtime; rollback tự động chưa được triển khai |

Lifecycle 30 ngày là **retention theo chi phí**, không phải retention lock. Điều đó có nghĩa object cũ
được tự xoá theo lifecycle và admin đủ quyền vẫn có thể xoá; không nên gọi đây là WORM/compliance
backup.

#### Restore drill an toàn

Một drill đạt phải tạo bằng chứng, không chỉ chạy lệnh không lỗi:

1. Chọn object cụ thể, ghi URI, timestamp, size và checksum nếu công cụ cung cấp.
2. Dùng identity `sa-backup-restore`, không dùng backup writer.
3. Restore vào `job_crawler_restore_test`, tuyệt đối không đè `job_crawler`.
4. Đối chiếu count cho **mọi collection dùng bởi ELT**: `jobs`, `job_details`, `job_categories`, không
   chỉ `jobs`.
5. Lấy mẫu khóa `(platformId, externalId)`, kiểm record tồn tại ở cả source/restore và kiểm vài field
   quan trọng; count bằng nhau chưa chứng minh nội dung bằng nhau.
6. Chạy ELT `--dry-run` trên DB restore, xác nhận reconciliation và sáu quality checks pass. Đây là
   kiểm tra backup có thể dùng để tái dựng warehouse, không chỉ Mongo có thể mở.
7. Ghi thời gian từ lúc bắt đầu tới khi query được DB restore để đo **RTO thực tế**; lấy khoảng cách
   từ backup tới sự cố giả lập để đo **RPO thực tế**.
8. Lưu evidence: ngày, object, identity, source/restore counts, sample/hash result, dry-run result,
   RTO, RPO, người thực hiện và kết luận; sau đó dọn DB tạm có kiểm soát.

Mẫu biên bản drill:

| Trường | Giá trị cần ghi |
| --- | --- |
| Drill date / operator |  |
| Backup object / created time / size |  |
| Restore identity / target DB |  |
| `jobs` source vs restore |  |
| `job_details` source vs restore |  |
| `job_categories` source vs restore |  |
| Sample key/content check |  |
| ELT dry-run + quality report |  |
| Measured RPO / RTO |  |
| Result / issue / owner / due date |  |

**Trạng thái trung thực tại thời điểm viết:** `migrate-report/phase-7/README.md` vẫn ghi restore test
lần đầu **chưa thực hiện**. Vì vậy khi thuyết trình, hãy nói “đã thiết kế và có runbook, operational
proof pending” cho tới khi bảng evidence được điền. Đây là một điểm cộng về engineering judgment,
không phải điểm yếu cần che giấu.

### 15.8 Scaling — khi dữ liệu và traffic tăng

Scaling nên dựa trên bottleneck đo được, không chỉ tăng máy. Lộ trình dưới đây giữ nguyên tính đúng
của dữ liệu khi tăng tải.

| Ngưỡng/vấn đề | Cơ chế đã có | Bước scale tiếp theo | Tín hiệu ra quyết định |
| --- | --- | --- | --- |
| API nhiều request hơn | Cloud Run stateless, concurrency `40`, max `3` instance | Load test; điều chỉnh concurrency/CPU/RAM/max instances theo p95 và saturation | request rate, p95/p99, 5xx, instance count, CPU/RAM |
| Nhiều instance làm cache/rate-limit lệch | Có Redis adapter shared-state và Lua token bucket nguyên tử | Bật Memorystore/Redis HA phù hợp; dùng Redis cho cả cache và limiter | cache hit, Redis latency/error, 429 theo client |
| Search scan lớn hơn | Partition theo ngày, cluster theo batch/source/seniority/job; required date filter; keyset; projection; max bytes | Theo dõi bytes/query, xem lại partition/cluster theo query thực, tách bảng detail/text lớn | bytes billed, slot time, latency theo filter |
| Metrics được đọc nhiều | Gold đã precompute; cache key gắn batch | Tăng TTL có cân nhắc, warm popular keys sau publish, CDN chỉ khi auth/privacy cho phép | cache hit, BQ query count, freshness requirement |
| Mongo corpus lớn hơn RAM ELT | Hiện extract dựng index detail/category và toàn bộ output trong memory | Chuyển sang incremental watermark/change feed; đọc theo batch/cursor; spill/stage; bulk load theo chunk | peak RAM, extract duration, changed/total ratio |
| Transform CPU tăng | Hàm transform thuần, tách theo record; publication đã tách khỏi computation | Partition theo source/date và parallel transform, nhưng chỉ một coordinator quality-check + publish pointer | CPU time/record, skew theo source, batch SLA |
| Gold cardinality tăng | Dedupe bằng dict trong memory | Aggregate bằng BigQuery SQL/incremental table khi category/cardinality vượt ngưỡng memory | group cardinality, RAM, gold build duration |
| Publish transaction lớn | Candidate riêng và transaction bảo vệ atomicity | Benchmark DML size; cân nhắc versioned physical tables/view-pointer hoặc table swap pattern | DML duration, transaction abort/quota |
| Mongo/VM thành single point | Mongo bind local trên một VM, backup hằng ngày | Replica set/managed Mongo, disk snapshot và tested failover khi RPO/RTO yêu cầu | downtime budget, write rate, backup/restore time |
| Nhiều pipeline/backfill | Concurrency `1` bảo vệ nguồn và pointer | Cho phép parallel compute theo partition, serialize riêng bước publish; priority/backpressure cho backfill | queue time, overlap incidents, source load |
| Nhiều consumer | API key + per-client limiter | Quota theo tier, usage dashboard, client-specific SLO và capacity planning | RPS/client, 429, cost/client, noisy-neighbor |

Hai bottleneck đáng nói thẳng:

1. `extract_mongo.py` hiện nạp toàn bộ `job_details` và `job_categories` vào dictionary, sau đó giữ
   records/silver/gold trong memory. Cách này đơn giản và phù hợp corpus hiện tại nhưng không phải thiết
   kế cho hàng chục triệu record.
2. Memory cache/rate limiter chỉ đúng trong phạm vi một instance. Khi tăng Cloud Run instance, phải
   bật Redis trước khi kỳ vọng cache và quota có semantics toàn cục.

Scale plan ưu tiên:

```text
Đo baseline → đặt SLO/capacity trigger → tối ưu query/grain → bật shared state
→ tăng ngang API → incremental/chunked ELT → scale nguồn và publication pattern
```

### 15.9 Những năng lực Data Engineering khác nên nhấn mạnh

#### Data modeling và grain

- Silver giữ **một job = một dòng**; categories là repeated record để không phá grain.
- Gold precompute theo dimension/window để serving không phải median trên mỗi request.
- `unknown`, `missing`, `unparsed`, `invalid`, `quarantine` là các trạng thái khác nhau; không gom
  mọi thứ thành `NULL` rồi mất ý nghĩa.

#### Lineage và reproducibility

- `batch_id → crawl_batch_id → dagster_run_id` nối serving data với orchestration/source run.
- `data_as_of_at` tách khỏi `published_at`: một cái là cutoff dữ liệu, một cái là thời điểm phát hành.
- Raw salary, currency, FX rate và mapping version được giữ để giải thích số đã chuẩn hoá.

#### Observability và operability

- Structured log, request ID, W3C trace, span BigQuery với job ID/bytes/cache hit/rows/latency.
- Quarantine reason và run metric biến data quality thành số đo vận hành được.
- ADR ghi cả phương án bị loại và trade-off; runbook tách thao tác local/staging/prod.

#### Security, privacy và cost như một phần của data platform

- API chỉ đọc, least-privilege IAM theo dataset, staging/prod tách SA/secret/dataset.
- WIF/OIDC thay JSON key; API key chỉ lưu SHA-256; container non-root.
- Projection allowlist và PII denylist; không log giá trị filter.
- Partition filter, max bytes billed, max instances, lifecycle và Redis opt-in là cost guards.

#### Consumer-first data product

- OpenAPI + `/metadata` cho client tự khám phá contract.
- `as_of` làm freshness explicit; error code ổn định để máy xử lý.
- Snapshot pagination đảm bảo consumer không thấy dữ liệu tự thay đổi giữa một lần duyệt.

### 15.10 Khoảng trống đã nhận diện và thứ tự cải thiện

Nhận ra giới hạn của hệ thống cũng là bằng chứng về seniority. Các việc nên ưu tiên:

| Ưu tiên | Khoảng trống hiện tại | Cải thiện đề xuất | Definition of done |
| --- | --- | --- | --- |
| P0 | Restore drill thật chưa có evidence | Chạy drill mục 15.7 và lưu biên bản | Counts/content/dry-run đạt; có RPO/RTO đo được |
| P0 | `firstSeenAt` là REQUIRED trong silver nhưng mapper chưa quarantine rõ trường hợp posted date hợp lệ mà `firstSeenAt` thiếu | Thêm source-contract check/reason code và regression test | Không còn lỗi serialize/load cả batch vì một row thiếu `firstSeenAt` |
| P1 | Chưa có freshness/completeness threshold theo baseline | Alert khi batch trễ, source count giảm bất thường, quarantine ratio tăng | SLO + alert có owner/runbook |
| P1 | BigQuery integration và Redis integration skip mặc định | Tạo scheduled/ephemeral integration environment trong CI | Có kết quả định kỳ, cleanup tự động, không dùng prod dataset |
| P1 | Chưa có utility rollback/promote batch có guard | Viết CLI kiểm catalog/count/schema rồi CAS pointer, có dry-run/audit | Rollback diễn tập được và không sửa batch |
| P1 | ELT đang full-snapshot/in-memory | Thiết kế watermark/incremental + late-arrival/backfill policy | Kết quả incremental parity với full rebuild |
| P2 | BigQuery curated data chưa có retention/export policy riêng | Chốt thời gian giữ batch, snapshot/export cần thiết và token TTL tương ứng | Policy được code hoá + restore test |
| P2 | `mypy` và `pip-audit` mới advisory | Dọn technical debt, baseline và chuyển dần thành hard gate | CI chặn regression mới |
| P2 | Chưa có load/capacity test | Xây workload đại diện và capacity report | Biết RPS bền vững, p95, cost/request và scale trigger |

### 15.11 Dàn ý thuyết trình 10–12 phút

1. **1 phút — Bài toán và grain:** Mongo raw từ hai nguồn → silver một job/một dòng → gold metric →
   API read-only.
2. **2 phút — Data quality:** critical field đi quarantine; analytical field có parse status; đối
   soát `extract = silver + quarantine`; gold invariants.
3. **2 phút — Resilience:** candidate, transaction, CAS, last-known-good pointer và state-machine retry.
4. **1 phút — Consistency cho consumer:** signed keyset token gắn batch, `as_of`, no mixed snapshot.
5. **1.5 phút — Test:** 297 case; nói năm nhóm test và demo một test phá invariant khiến publish bị
   chặn.
6. **1 phút — Version/lineage:** Git SHA → image/revision; batch/crawl/Dagster IDs; rule versions.
7. **1 phút — Backup/restore:** separation of duties, restore vào DB tạm; nói đúng trạng thái drill.
8. **1 phút — Scaling:** shared Redis + Cloud Run trước; incremental/chunked ELT khi corpus tăng.
9. **0.5–1.5 phút — Ownership:** observability, cost/privacy guard và P0/P1 roadmap có definition of done.

Kết luận gợi ý:

> Điểm tôi muốn chứng minh không phải là tôi đã dùng bao nhiêu công cụ, mà là tôi biết đặt data
> contract ở đâu, biết failure nào phải chặn và failure nào có thể degrade, biết giữ lịch sử để
> audit/rollback, và biết dùng test cùng operational evidence để chứng minh pipeline đáng tin cậy.
