# ADR-019: Nguồn MongoDB & data contract (schema mapping matrix)

- Trạng thái: Đã chấp nhận
- Ngày: 2026-09-11
- Người quyết định: [Điền tên]
- Liên quan: ADR-018 (BigQuery), ADR-024 (salary), ADR-025/026 (batch); `docs/migration-mongo-bigquery-plan.md`

## Bối cảnh
Nguồn thật là MongoDB `job_crawler` do `job-scraper-1` ghi. Đã khảo sát code scraper (`src/core/jobs/models/`,
parser TopDev/VietnamWorks) và dữ liệu Mongo:
- **Collections:** `jobs` (ExternalJob: list record), `job_details` (JobDetail: normalized detail), `job_categories`
  (taxonomy master). Khóa tự nhiên `(platformId, externalId)` (unique index — `MongoJobStore.ts`).
- **Khối lượng hiện tại (baseline, KHÔNG hard-code làm invariant):** TopDev 3.430 / VNW 11.055; 8 job TopDev
  thiếu `job_details`.
- Scraper **đã normalize sẵn** `JobSalary{raw,min,max,currency,unit}` (VND theo **triệu**, USD as-is, negotiable→
  min/max undefined) và `JobCategory{key,name,code,group,level1-3}` (`group` = path, vd `g14~j22`).
- `JobCompanyInfo` **không có `name`** → company name chỉ ở `raw`.
- `lastSeenAt == firstSeenAt` gần như toàn bộ (crawler bỏ qua job cũ) → **KHÔNG dựng `is_active`** từ nó.

## Quyết định
- **API phục vụ toàn bộ corpus đã crawl** (không phải "job đang tuyển"); `as_of` = data cutoff của batch.
- **Extract `jobs` LEFT JOIN `job_details`** trên `(platformId, externalId)` → giữ cả 8 job thiếu detail;
  field từ detail **nullable**, ưu tiên fallback từ `jobs` trước khi để null.
- **`job_id` composite** `"<source>:<external_id>"` (source canonical slug lowercase), dựng từ `(platformId,
  externalId)` — KHÔNG dùng Mongo `_id`; giữ `external_id` riêng.
- **`company_name`:** **KHÔNG đổi schema scraper** — ELT **luôn lấy từ `raw`**: TopDev
  `company_detail.display_name` / `company.display_name`; VNW `companyName`; không có → **null**
  (KHÔNG "Unknown"). *(Quyết định: không thêm `JobCompanyInfo.name` upstream.)*
- **categories:** silver lưu **repeated STRUCT** (không explode → 1 job = 1 silver row); `category_key =
  "<source>:<group-path>"` (dùng `group`, vd `topdev:g14~j22` — KHÔNG bare numeric ID). Không có category →
  `categories=[]`; bucket `<source>:unknown` chỉ là presentation của metrics (search không hỗ trợ filter
  `category=<source>:unknown`).
- **seniority:** `seniority_raw` = `commonInfo.level`; `seniority_normalized` (null nếu không chắc; không suy
  từ title) + `seniority_mapping_version`. Null → bucket `unknown` ở metrics.
- **experience:** text (TopDev `experiences_str`; VNW `yearsOfExperience` label) → `experience_raw` +
  `experience_min_years`/`max_years` + `experience_parse_status`.
- **dates:** parse **theo source**; hai status ĐỘC LẬP (không gộp):
  - `posted_date_parse_status`: `parsed` (posted_at parse OK → `effective_posted_date = DATE(posted_at)`) ·
    `fallback_first_seen` (posted_at thiếu/lỗi nhưng first_seen_at hợp lệ → `effective = DATE(first_seen_at)`) ·
    `invalid` (cả hai thiếu/lỗi → **quarantine**, không vào silver).
  - `deadline_date_parse_status`: `parsed` (deadline parse OK) · `missing` (không có deadline → `deadline_date=null`) ·
    `invalid` (có nhưng parse lỗi → `deadline_date=null`). Deadline KHÔNG bao giờ gây quarantine.

## Schema mapping matrix (silver_jobs — serving-lean; heavy text để [SAU])
| Silver field | BQ type | Nullable | TopDev | VietnamWorks | Fallback |
| --- | --- | --- | --- | --- | --- |
| job_id | STRING | No | `topdev:`+id | `vietnamworks:`+jobId | — (quarantine nếu thiếu id) |
| source | STRING | No | `"topdev"` | `"vietnamworks"` | — |
| external_id | STRING | No | source.id | source.jobId | — |
| source_url | STRING | Yes | JobDetail.sourceUrl | sourceUrl/jobUrl | jobs.detailUrl |
| detail_status | STRING | No | stored detail status | stored detail status | `"pending"` nếu thiếu detail |
| title | STRING | No¹ | detail.title | detail.jobTitle | jobs.title → nếu vẫn trống: quarantine |
| company_name | STRING | Yes | raw.company_detail.display_name / company.display_name → null | raw.companyName → null | null |
| location_text | STRING | Yes | JobDetail.address / companyInfo.address | address | null |
| seniority_raw | STRING | Yes | commonInfo.level (job_levels_str) | jobLevelVI | null |
| seniority_normalized | STRING | Yes² | map(seniority_raw) | map(seniority_raw) | null→`unknown` (metrics) |
| experience_raw | STRING | Yes | experiences_str | yearsOfExperience label | null |
| experience_min_years / max_years | NUMERIC | Yes | parse(experiences_str) | parse | null (status=unparsed) |
| experience_parse_status | STRING | No | — | — | `missing` nếu trống |
| salary_raw | STRING | Yes | JobSalary.raw | JobSalary.raw | null |
| salary_min_original / max_original | NUMERIC | Yes | JobSalary.min/max (triệu\|USD) | JobSalary.min/max | null |
| salary_currency | STRING | Yes | JobSalary.currency | JobSalary.currency | null |
| salary_period | STRING | Yes | JobSalary.unit (`month`) | unit | null |
| salary_min_vnd_month / max_vnd_month | NUMERIC | Yes | VND×1e6 / USD×25500 | idem | **theo quy tắc one-sided bên dưới** (negotiable/invalid→cả hai NULL; one-sided→giữ cận hợp lệ, cận thiếu NULL) |
| fx_rate_to_vnd / salary_fx_version | NUMERIC/STRING | Yes | 25500 khi USD | idem | — |
| salary_normalization_status | STRING | No | parsed\|negotiable\|invalid | idem | — |
| categories | ARRAY<STRUCT<category_key,category_name,category_code,category_path,level1_id,level2_id,level3_id>> | **REPEATED** (không NULL; thiếu → `[]`) | JobDetail.categories | JobDetail.categories | jobs.category → `[]` |
| posted_at | TIMESTAMP | Yes | parse(jobs.postedAt, source) | parse | null |
| effective_posted_date | DATE | No | COALESCE(DATE(posted_at),DATE(first_seen_at)) | idem | — (cả 2 invalid → quarantine) |
| deadline_date | DATE | Yes | parse(expires.date) | parse(expiredOn) | null |
| posted_date_parse_status | STRING | No | `parsed` \| `fallback_first_seen` \| `invalid` | idem | — |
| deadline_date_parse_status | STRING | No | `parsed` \| `missing` \| `invalid` | idem | — |
| first_seen_at / last_seen_at | TIMESTAMP | No/Yes | stored job metadata | idem | — |
| batch_id | STRING | No | ELT batch | ELT batch | — |

¹ title trống ở cả detail lẫn list → quarantine (reason). ² null ở silver, hiện dưới bucket `unknown` khi group metrics.

**Quy tắc salary một-phía (rõ ràng — KHÔNG null cả hai cho lương một phía):**
- có min, thiếu max → `salary_min_vnd_month` = normalized min, `salary_max_vnd_month` = **null**;
- thiếu min, có max → `salary_min_vnd_month` = **null**, `salary_max_vnd_month` = normalized max;
- negotiable → **cả hai null**, `salary_normalization_status='negotiable'`;
- min>max / âm / currency-period không hỗ trợ → **cả hai null**, `status='invalid'` (không dùng ngầm).

**Phạm vi currency/period — ĐÃ KHOÁ (xem ADR-024 planned):** chỉ hỗ trợ **currency ∈ {VND, USD}** và
**period = `month`** (đúng phạm vi quan sát được; `JobSalary.unit` cũng chỉ nhận `"month"`). Record có period
`year/day/hour` hoặc currency khác → **`salary_normalization_status='invalid'`, cả hai cận null** (KHÔNG tự bịa
hệ số quy đổi ngày-làm/giờ). Mở rộng multi-period là **phase sau**.

**KHÔNG đưa vào silver serving / API:** `raw` payload; contact info; session data; và (v1) các field text lớn
`description/benefit/requireCandidate` + `knowledge/tags` (skills) — để dành bảng `silver_job_details` [SAU]
khi có endpoint job-detail. Lý do: giảm bytes scanned, giảm rủi ro lộ dữ liệu, contract search gọn.

## Phương án đã cân nhắc
- **INNER JOIN jobs×job_details** — mất 8 job thiếu detail. Bỏ, dùng LEFT JOIN.
- **Giữ numeric job_id** — external id chỉ unique trong từng platform → có thể trùng cross-platform. Đổi composite.
- **Explode categories thành nhiều dòng silver** — phá invariant 1 job = 1 row + hỏng keyset pagination. Dùng repeated field, chỉ UNNEST khi build gold.

## Hệ quả
- Tích cực: contract khớp dữ liệu thật; giữ đủ corpus; category giữ hierarchy; PII/text nặng không rời nguồn.
- Đánh đổi: mapping **source-specific** (date/experience/company khác nhau TopDev vs VNW). **Fixtures chia hai:**
  Phase 0 = fake **API contract fixtures** (hình dạng JobItem/response, chạy trên `fake`); Phase 2 =
  **sanitized Mongo-like raw fixtures** cho mapper/ELT (nơi mới có consumer). Canonical category cross-source
  [SAU]; province/location parser [SAU].
